from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from .audio import MODEL_RATE, pcm16_to_ulaw, pcm16_wav, resample_pcm16
from .config import Settings

logger = logging.getLogger(__name__)

_TTS_AUDIO_CACHE: dict[tuple[str, str, str, str, str], bytes] = {}
_TTS_AUDIO_INFLIGHT: dict[tuple[str, str, str, str, str], asyncio.Task[bytes]] = {}
_TTS_AUDIO_CACHE_MAX_ENTRIES = 16


def _content(response: dict[str, Any]) -> str:
    message = response.get("choices", [{}])[0].get("message", {})
    value = message.get("content", "")
    return value if isinstance(value, str) else ""


def audio_message(pcm: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(pcm16_wav(pcm)).decode("ascii")
    return {
        "type": "audio_url",
        "audio_url": {"url": f"data:audio/wav;base64,{encoded}"},
    }


class InteractiveBackend:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def classify(
        self, messages: list[dict[str, Any]], choices: list[str] | None = None
    ) -> str:
        return (
            await self._complete(
                messages,
                base_url=self.settings.interactive_url,
                model=self.settings.interactive_model,
                backend_name="interactive",
                max_tokens=self.settings.classifier_max_tokens,
                choices=choices,
                timeout_s=self.settings.classifier_timeout_s,
            )
        ).lower()

    async def transcribe(self, messages: list[dict[str, Any]]) -> str:
        return await self._complete(
            messages,
            base_url=self.settings.asr_url,
            model=self.settings.asr_model,
            backend_name="ASR",
            max_tokens=self.settings.asr_max_tokens,
            choices=None,
            timeout_s=self.settings.asr_timeout_s,
            language=self.settings.asr_language,
        )

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        base_url: str,
        model: str,
        backend_name: str,
        max_tokens: int,
        choices: list[str] | None,
        timeout_s: float,
        language: str | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "top_p": 0.9,
            "presence_penalty": 1,
            "max_tokens": max_tokens,
        }
        if choices:
            payload["structured_outputs"] = {"choice": choices}
        else:
            payload["top_k"] = 1
        if language and language.lower() != "auto":
            payload["language"] = language

        async with httpx.AsyncClient(
            timeout=timeout_s,
            # trust_env=self.settings.backend_trust_env,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )
            if response.is_error:
                logger.error(
                    "%s backend HTTP %d: %s",
                    backend_name,
                    response.status_code,
                    response.text[:2000],
                )
            response.raise_for_status()
            return _content(response.json()).strip()


class TextBackend:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.settings.text_model,
            "messages": messages,
            "temperature": 0.0,
            "stream": self.settings.text_streaming,
        }
        # DashScope accepts this extra request-body field to disable reasoning.
        payload["enable_thinking"] = self.settings.text_enable_thinking
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.settings.text_api_key}"}
        language = self.settings.tts_language
        if language and language.lower() != "auto":
            payload["language"] = language

        async with httpx.AsyncClient(
            timeout=self.settings.llm_timeout_s,
            trust_env=self.settings.backend_trust_env,
        ) as client:
            if not self.settings.text_streaming:
                response = await client.post(
                    f"{self.settings.text_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if response.is_error:
                    logger.error(
                        "text backend HTTP %d: %s",
                        response.status_code,
                        response.text[:2000],
                    )
                response.raise_for_status()
                choice = response.json().get("choices", [{}])[0]
                finish_reason = choice.get("finish_reason")
                message = choice.get("message", {})
                if finish_reason == "length":
                    raise RuntimeError("Text backend stopped at its token limit")
                if finish_reason == "tool_calls" and not message.get("tool_calls"):
                    raise RuntimeError(
                        "Text backend reported tool_calls without parsed tool calls"
                    )
                yield {
                    "content": message.get("content"),
                    "reasoning_content": message.get("reasoning_content"),
                    "tool_calls": message.get("tool_calls") or [],
                }
                return

            async with client.stream(
                "POST",
                f"{self.settings.text_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.is_error:
                    body = (await response.aread()).decode(errors="replace")
                    logger.error(
                        "text backend HTTP %d: %s",
                        response.status_code,
                        body[:2000],
                    )
                response.raise_for_status()
                saw_tool_calls = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if value and value != "[DONE]":
                        chunk = json.loads(value)
                        if chunk.get("error"):
                            error = chunk["error"]
                            raise RuntimeError(
                                f"Text backend stream error: "
                                f"{error.get('message', error)}"
                            )
                        # choice = chunk.get("choices", [{}])[0]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        if choice.get("finish_reason") == "length":
                            raise RuntimeError(
                                "Text backend stopped at its token limit"
                            )
                        delta = choice.get("delta", {})
                        if delta.get("tool_calls"):
                            saw_tool_calls = True
                        if (
                            choice.get("finish_reason") == "tool_calls"
                            and not saw_tool_calls
                        ):
                            raise RuntimeError(
                                "Text backend reported tool_calls without parsed "
                                "tool calls"
                            )
                        yield delta


@dataclass
class TTSBackend:
    settings: Settings

    async def _request_pcm(self, text: str, speaker: str | None = None) -> bytes:
        payload = {
            "model": self.settings.tts_model,
            "input": text,
            "voice": speaker or self.settings.tts_speaker,
            "response_format": "pcm",
            "sample": self.settings.tts_pcm_sample_rate,
        }
        language = self.settings.tts_language
        if language and language.lower() != "auto":
            payload["language"] = language

        async with httpx.AsyncClient(
            timeout=self.settings.tts_timeout_s,
            # trust_env=self.settings.backend_trust_env,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{self.settings.tts_url}/audio/speech",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )
            if response.is_error:
                request_id = response.headers.get("x-request-id", "unknown")
                detail = response.text[:2000]
                message = (
                    f"TTS backend HTTP {response.status_code} "
                    f"model={payload['model']} voice={payload['voice']} "
                    f"chars={len(text)} request_id={request_id}: {detail}"
                )
                logger.error(
                    "%s", message,
                )
                raise httpx.HTTPStatusError(
                    message, request=response.request, response=response
                )
            response.raise_for_status()
        return response.content

    @staticmethod
    def _validate_pcm16(pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("TTS service returned PCM with an incomplete 16-bit sample")
        return pcm

    async def synthesize_pcm16(self, text: str, speaker: str | None = None) -> bytes:
        """Synthesize raw mono PCM16 at tau2's 16 kHz user-audio rate."""
        pcm = self._validate_pcm16(await self._request_pcm(text, speaker=speaker))
        return resample_pcm16(pcm, self.settings.tts_pcm_sample_rate, MODEL_RATE)

    async def synthesize(self, text: str) -> bytes:
        """Synthesize 8 kHz mu-law for the realtime WebSocket response."""
        pcm = self._validate_pcm16(await self._request_pcm(text))
        ulaw, _ = pcm16_to_ulaw(pcm, self.settings.tts_pcm_sample_rate)
        return ulaw

    async def synthesize_cached(self, text: str) -> bytes:
        """Synthesize reusable realtime audio once across all sessions."""
        key = (
            self.settings.tts_url,
            self.settings.tts_model,
            self.settings.tts_speaker,
            self.settings.tts_language,
            text,
        )
        cached = _TTS_AUDIO_CACHE.get(key)
        if cached is not None:
            return cached

        task = _TTS_AUDIO_INFLIGHT.get(key)
        if task is None:
            task = asyncio.create_task(self.synthesize(text))
            _TTS_AUDIO_INFLIGHT[key] = task
        try:
            audio = await asyncio.shield(task)
        except BaseException:
            if task.done() and _TTS_AUDIO_INFLIGHT.get(key) is task:
                _TTS_AUDIO_INFLIGHT.pop(key, None)
            raise

        _TTS_AUDIO_INFLIGHT.pop(key, None)
        if len(_TTS_AUDIO_CACHE) >= _TTS_AUDIO_CACHE_MAX_ENTRIES:
            _TTS_AUDIO_CACHE.pop(next(iter(_TTS_AUDIO_CACHE)))
        _TTS_AUDIO_CACHE[key] = audio
        return audio


CLAUSE_END_RE = re.compile(r"[;；:：.!?。！？\n]+")


def _has_speech_text(text: str) -> bool:
    # Closing quotes and punctuation can arrive after a sentence boundary,
    # including in a later LLM delta. Sending such a standalone fragment to TTS
    # can make the upstream audio stream fail. Keep letters/numbers in any
    # language, and discard fragments with no speakable text.
    return any(character.isalnum() for character in text)


def streaming_clause_chunks(text: str) -> tuple[list[str], str]:
    """Return punctuation-terminated chunks and the unfinished text tail."""
    chunks: list[str] = []
    start = 0
    for match in CLAUSE_END_RE.finditer(text):
        chunk = text[start : match.end()].strip()
        if _has_speech_text(chunk):
            chunks.append(chunk)
        start = match.end()
    return chunks, text[start:]


def clause_chunks(text: str) -> list[str]:
    chunks, remainder = streaming_clause_chunks(text)
    if _has_speech_text(remainder):
        chunks.append(remainder.strip())
    return chunks
