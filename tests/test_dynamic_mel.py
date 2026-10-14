from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

import httpx

from service_app.audio import pcm16_wav
from service_app.backends import InteractiveBackend, audio_message
from service_app.config import Settings


class DynamicMelTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, messages, *, enabled=True, asr=False):
        captured = []

        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Complete"}}]}
            )

        backend = InteractiveBackend(Settings(interactive_qwen25_dynamic_mel=enabled))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("service_app.backends.httpx.AsyncClient", return_value=client):
            if asr:
                await backend.transcribe(messages)
            else:
                result = await backend.classify(messages, ["complete", "incomplete"])
                self.assertEqual(result, "complete")
        self.assertEqual(captured[0]["messages"], messages)
        return captured[0]

    async def test_longest_history_audio_and_fft_tail(self):
        messages = [
            {"role": "user", "content": [audio_message(b"\0\0" * 48001)]},
            {"role": "assistant", "content": "Previous answer"},
            {"role": "user", "content": [audio_message(b"\0\0" * 16000)]},
        ]
        payload = await self.request(messages)
        self.assertEqual(payload["mm_processor_kwargs"], {
            "audio_kwargs": {"max_length": 48480, "truncation": False}
        })
        self.assertEqual(payload["structured_outputs"]["choice"], ["complete", "incomplete"])

    async def test_non_16khz_wav_uses_resampled_length(self):
        encoded = base64.b64encode(pcm16_wav(b"\0\0" * 8001, 8000)).decode()
        messages = [{"role": "user", "content": [{
            "type": "audio_url", "audio_url": {"url": f"data:audio/wav;base64,{encoded}"}
        }]}]
        payload = await self.request(messages)
        self.assertEqual(payload["mm_processor_kwargs"]["audio_kwargs"]["max_length"], 16480)

    async def test_disabled_and_asr_do_not_send_processor_kwargs(self):
        messages = [{"role": "user", "content": [audio_message(b"\0\0" * 16000)]}]
        for options in ({"enabled": False}, {"asr": True}):
            payload = await self.request(messages, **options)
            self.assertNotIn("mm_processor_kwargs", payload)

    async def test_no_audio_empty_invalid_and_unknown_audio_use_defaults(self):
        for blocks in (
            [{"type": "text", "text": "hello"}],
            [audio_message(b"")],
            [{"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,invalid"}}],
            [audio_message(b"\0\0" * 16000),
             {"type": "audio_url", "audio_url": {"url": "https://example.com/audio.wav"}}],
        ):
            payload = await self.request([{"role": "user", "content": blocks}])
            self.assertNotIn("mm_processor_kwargs", payload)
