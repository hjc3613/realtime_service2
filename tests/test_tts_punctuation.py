from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from service_app.backends import TTSBackend, clause_chunks, streaming_clause_chunks
from service_app.config import Settings
from service_app.session import RealtimeSession


REPORTED_REPLY = (
    '没问题呀。比如“Hello, how are you?”，意思是“你好，今天过得怎么样？”；'
    '还有“Nice to meet you”，意思是“很高兴认识你”。你觉得这两个好记吗？'
)


class PunctuationChunkTests(unittest.TestCase):
    def test_reported_reply_has_no_punctuation_only_clause(self):
        chunks = clause_chunks(REPORTED_REPLY)
        self.assertEqual(len(chunks), 5)
        self.assertNotIn('”；', chunks)
        self.assertTrue(all(any(c.isalnum() for c in chunk) for chunk in chunks))

    def test_every_stream_boundary_discards_punctuation_without_losing_words(self):
        expected = ''.join(c for c in REPORTED_REPLY if c.isalnum())
        for boundary in range(len(REPORTED_REPLY) + 1):
            first, pending = streaming_clause_chunks(REPORTED_REPLY[:boundary])
            second, pending = streaming_clause_chunks(pending + REPORTED_REPLY[boundary:])
            chunks = first + second + clause_chunks(pending)
            self.assertTrue(all(any(c.isalnum() for c in chunk) for chunk in chunks))
            self.assertEqual(''.join(c for chunk in chunks for c in chunk if c.isalnum()), expected)

    def test_character_stream_and_trailing_quote_are_filtered(self):
        chunks, pending = [], ''
        for char in REPORTED_REPLY + '”':
            ready, pending = streaming_clause_chunks(pending + char)
            chunks.extend(ready)
        chunks.extend(clause_chunks(pending))
        self.assertEqual(len(chunks), 5)
        self.assertEqual(clause_chunks('”； 。！？…\n'), [])
        self.assertEqual(clause_chunks('42。中文。Hello!'), ['42。', '中文。', 'Hello!'])


class TTSFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefetch_never_sends_the_reported_punctuation_input(self):
        captured = []

        def handler(request):
            text = json.loads(request.content)['input']
            captured.append(text)
            if not any(c.isalnum() for c in text):
                return httpx.Response(502, json={'category': 'upstream_failure'})
            return httpx.Response(200, content=b'\x00\x00' * 160)

        clients = [httpx.AsyncClient(transport=httpx.MockTransport(handler)) for _ in range(5)]
        settings = Settings(tts_url='http://tts-test.invalid/v1', interactive_debug_dir=None)
        session = RealtimeSession(lambda event: asyncio.sleep(0), settings)
        queue = asyncio.Queue()
        for chunk in clause_chunks(REPORTED_REPLY):
            queue.put_nowait(chunk)
        queue.put_nowait(None)
        with patch('service_app.backends.httpx.AsyncClient', side_effect=clients):
            while await session._next_synthesized_clause(queue) is not None:
                pass
        self.assertEqual(len(captured), 5)
        self.assertNotIn('”；', captured)
        await session.close()

    async def test_upstream_failure_retains_status_reason_and_request_id(self):
        def handler(request):
            return httpx.Response(
                502,
                json={'category': 'upstream_failure', 'message': 'error decoding response body'},
                headers={'x-request-id': 'test-upstream-request'},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backend = TTSBackend(Settings(tts_url='http://tts-test.invalid/v1'))
        with patch('service_app.backends.httpx.AsyncClient', return_value=client):
            with self.assertRaises(httpx.HTTPStatusError) as raised:
                await backend.synthesize('Hello.')
        self.assertEqual(raised.exception.response.status_code, 502)
        self.assertIn('test-upstream-request', str(raised.exception))
        self.assertIn('error decoding response body', str(raised.exception))
        self.assertNotIn(backend.settings.api_key, str(raised.exception))
