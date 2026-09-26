from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch
import httpx

from service_app.backends import TTSBackend, TextBackend, streaming_clause_chunks, clause_chunks
from service_app.config import Settings


class _CountingTTSBackend(TTSBackend):
    calls = 0

    async def synthesize(self, text: str) -> bytes:
        self.calls += 1
        await asyncio.sleep(0)
        return text.encode("ascii")


class TTSBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_requests_raw_pcm_at_the_configured_sample_rate(self) -> None:
        captured = []

        def handler(request):
            import json

            captured.append(json.loads(request.content))
            return httpx.Response(200, content=b"\x00\x00\x01\x00")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backend = TTSBackend(
            Settings(
                tts_url="http://tts-test.invalid/v1",
                tts_pcm_sample_rate=16000,
            )
        )
        with patch("service_app.backends.httpx.AsyncClient", return_value=client):
            pcm = await backend.synthesize_pcm16("Hello")

        self.assertEqual(pcm, b"\x00\x00\x01\x00")
        self.assertEqual(captured[0]["response_format"], "pcm")
        self.assertEqual(captured[0]["sample"], 16000)

    async def test_cached_synthesis_is_shared_by_concurrent_callers(self) -> None:
        backend = _CountingTTSBackend(
            Settings(tts_url="http://tts-cache-test.invalid/v1")
        )

        first, second = await asyncio.gather(
            backend.synthesize_cached("One moment."),
            backend.synthesize_cached("One moment."),
        )
        third = await backend.synthesize_cached("One moment.")

        self.assertEqual((first, second, third), (b"One moment.",) * 3)
        self.assertEqual(backend.calls, 1)


class TextBackendTests(unittest.TestCase):
    def test_text_api_key_can_differ_from_shared_backend_key(self) -> None:
        settings = Settings(api_key="shared-key", text_api_key="text-only-key")

        backend = TextBackend(settings)

        self.assertEqual(backend.settings.text_api_key, "text-only-key")
        self.assertEqual(backend.settings.api_key, "shared-key")


class SpeechChunkTests(unittest.TestCase):
    def test_decimal_email_and_time_across_every_possible_stream_boundary(self):
        text = "Pay $54.85. Email a.b@example.com. Meet Dr. Smith at 10:30."
        expected = ["Pay $54.85.", "Email a.b@example.com.", "Meet Dr. Smith at 10:30."]
        for boundary in range(len(text) + 1):
            chunks, pending = streaming_clause_chunks(text[:boundary])
            chunks2, pending = streaming_clause_chunks(pending + text[boundary:])
            self.assertEqual(chunks + chunks2 + clause_chunks(pending), expected,
                             f"boundary={boundary}")

    def test_character_stream_does_not_split_price_or_domain(self):
        output = []
        pending = ""
        for char in "It costs $54.85. Visit https://example.com. Okay!":
            chunks, pending = streaming_clause_chunks(pending + char)
            output.extend(chunks)
        output.extend(clause_chunks(pending))
        self.assertEqual(output, ["It costs $54.85.", "Visit https://example.com.", "Okay!"])


class ToolRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_request_disallows_parallel_calls(self):
        captured = []
        def handler(request):
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backend = TextBackend(Settings(text_url="http://test.invalid/v1", text_streaming=False))
        with patch("service_app.backends.httpx.AsyncClient", return_value=client):
            result = [delta async for delta in backend.stream([], [{"name": "lookup"}])]
        self.assertFalse(captured[0]["parallel_tool_calls"])
        self.assertEqual(result[0]["content"], "ok")
