"""OpenAI-compatible proxy for an internal TTS service.

The proxy exposes ``POST /v1/audio/speech`` and translates the request to the
company TTS endpoint.  It is intentionally small so it can run beside an
existing vLLM-omni deployment or replace it as the client's API base.

Environment variables:
    TTS_UPSTREAM_URL: company host, ``/v1`` base, or full endpoint (required)
    TTS_UPSTREAM_API_KEY: API key sent to the company endpoint (optional)
    TTS_UPSTREAM_MODEL: company model name (default: nex-tts-3-6-hd)
    TTS_PUBLIC_MODEL: model name advertised to clients (default: qwen3_tts)
    TTS_DEFAULT_SAMPLE_RATE: sample rate sent upstream (default: 8000)
    TTS_PCM_CHANNELS: number of channels in upstream PCM (default: 1)
    TTS_PCM_BITS_PER_SAMPLE: upstream PCM bit depth (default: 16)
    TTS_PCM_SPOOL_MAX_MEMORY: bytes buffered before using disk (default: 8 MiB)
    TTS_PROXY_API_KEY: key accepted from clients (optional; disabled if empty)
    TTS_PROXY_HOST / TTS_PROXY_PORT: bind address (default 0.0.0.0:8091)

Run:
    TTS_UPSTREAM_URL=http://api-cybotforge-pre.brapp.com/v1 \
      TTS_UPSTREAM_API_KEY=... python openai_compat_proxy.py
"""

from __future__ import annotations

import os
import struct
import tempfile
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field


UPSTREAM_URL = os.environ.get("TTS_UPSTREAM_URL", "").rstrip("/")
UPSTREAM_API_KEY = os.environ.get("TTS_UPSTREAM_API_KEY", "")
UPSTREAM_MODEL = os.environ.get("TTS_UPSTREAM_MODEL", "nex-tts-3-6-hd")
PUBLIC_MODEL = os.environ.get("TTS_PUBLIC_MODEL", "qwen3_tts")
DEFAULT_SAMPLE_RATE = int(os.environ.get("TTS_DEFAULT_SAMPLE_RATE", "8000"))
PCM_CHANNELS = int(os.environ.get("TTS_PCM_CHANNELS", "1"))
PCM_BITS_PER_SAMPLE = int(os.environ.get("TTS_PCM_BITS_PER_SAMPLE", "16"))
PCM_SPOOL_MAX_MEMORY = int(os.environ.get("TTS_PCM_SPOOL_MAX_MEMORY", str(8 * 1024 * 1024)))
PROXY_API_KEY = os.environ.get("TTS_PROXY_API_KEY", "")

app = FastAPI(title="OpenAI-compatible TTS proxy")


class SpeechRequest(BaseModel):
    """Fields used by OpenAI and vLLM-omni clients, plus vendor extensions."""

    class Config:
        extra = "allow"

    model: Optional[str] = None
    input: str = Field(min_length=1)
    voice: Optional[str] = None
    speaker: Optional[str] = None
    response_format: str = "wav"
    instructions: Optional[str] = None
    speed: Optional[float] = None
    sample: Optional[int] = None


def upstream_speech_url() -> str:
    if not UPSTREAM_URL:
        raise HTTPException(500, "TTS_UPSTREAM_URL is not configured")
    if UPSTREAM_URL.endswith("/audio/speech"):
        return UPSTREAM_URL
    if UPSTREAM_URL.endswith("/v1"):
        return f"{UPSTREAM_URL}/audio/speech"
    return f"{UPSTREAM_URL}/v1/audio/speech"


def check_client_key(authorization: Optional[str]) -> None:
    if not PROXY_API_KEY:
        return
    expected = f"Bearer {PROXY_API_KEY}"
    if authorization != expected:
        raise HTTPException(401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"})


def make_upstream_payload(body: SpeechRequest) -> Dict[str, Any]:
    # Only send fields demonstrated as supported by the company endpoint. vLLM
    # extensions such as task_type/ref_audio are accepted but intentionally not
    # forwarded because the internal model has no equivalent semantics.
    return {
        "model": UPSTREAM_MODEL,
        "input": body.input,
        # Preserve the client-provided voice name exactly. ``speaker`` is only
        # a vLLM client alias for the vendor API's ``voice`` field.
        "voice": body.voice if body.voice is not None else body.speaker,
        # The company service only supports raw PCM. The adapter owns any
        # conversion requested by its vLLM/OpenAI-compatible clients.
        "response_format": "pcm",
        "speed": body.speed if body.speed is not None else 1.0,
        "sample": body.sample if body.sample is not None else DEFAULT_SAMPLE_RATE,
    }


def make_wav_header(
    pcm_size: int,
    sample_rate: int,
    channels: int = PCM_CHANNELS,
    bits_per_sample: int = PCM_BITS_PER_SAMPLE,
) -> bytes:
    """Build a canonical 44-byte RIFF/WAVE header for little-endian PCM."""
    if sample_rate <= 0 or channels <= 0:
        raise ValueError("sample rate and channel count must be positive")
    if bits_per_sample <= 0 or bits_per_sample % 8 != 0:
        raise ValueError("PCM bit depth must be a positive multiple of 8")

    block_align = channels * bits_per_sample // 8
    if pcm_size % block_align != 0:
        raise ValueError("PCM byte count is not aligned to complete audio frames")
    if pcm_size > 0xFFFFFFFF - 36:
        raise ValueError("PCM output is too large for a standard RIFF/WAVE file")

    byte_rate = sample_rate * block_align
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + pcm_size),
            b"WAVE",
            b"fmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
            ),
            b"data",
            struct.pack("<I", pcm_size),
        ]
    )


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def models(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    check_client_key(authorization)
    return {
        "object": "list",
        "data": [
            {
                "id": PUBLIC_MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "company-tts",
            }
        ],
    }


@app.post("/v1/audio/speech")
async def speech(
    body: SpeechRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Any:
    check_client_key(authorization)
    response_format = body.response_format.lower()
    if response_format not in {"pcm", "wav"}:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": (
                        f"response_format '{body.response_format}' is not supported; "
                        "this adapter supports pcm and wav"
                    ),
                    "type": "invalid_request_error",
                }
            },
        )

    headers = {"Content-Type": "application/json"}
    if UPSTREAM_API_KEY:
        headers["Authorization"] = f"Bearer {UPSTREAM_API_KEY}"

    # Keep the upstream connection open for the lifetime of the response.
    speech_url = upstream_speech_url()
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0), trust_env=False)
    try:
        upstream_request = client.build_request(
            "POST", speech_url, json=make_upstream_payload(body), headers=headers
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"Unable to reach TTS upstream: {exc}") from exc

    if upstream.status_code >= 400:
        detail = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
        await client.aclose()
        return JSONResponse(status_code=upstream.status_code, content={"error": detail})

    if response_format == "pcm":
        async def stream_pcm() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(stream_pcm(), media_type="application/octet-stream")

    # A valid RIFF/WAVE header contains the final PCM byte count. The upstream
    # can be chunked, so spool it first; large responses automatically use a
    # temporary file instead of continuing to grow process memory.
    pcm_file = tempfile.SpooledTemporaryFile(max_size=PCM_SPOOL_MAX_MEMORY, mode="w+b")
    try:
        async for chunk in upstream.aiter_bytes():
            if await request.is_disconnected():
                raise HTTPException(499, "Client disconnected while generating audio")
            pcm_file.write(chunk)
    except HTTPException:
        pcm_file.close()
        raise
    except httpx.HTTPError as exc:
        pcm_file.close()
        raise HTTPException(502, f"TTS upstream stream failed: {exc}") from exc
    except BaseException:
        pcm_file.close()
        raise
    finally:
        await upstream.aclose()
        await client.aclose()

    pcm_size = pcm_file.tell()
    sample_rate = body.sample if body.sample is not None else DEFAULT_SAMPLE_RATE
    try:
        wav_header = make_wav_header(pcm_size, sample_rate)
    except ValueError as exc:
        pcm_file.close()
        raise HTTPException(502, f"Invalid PCM returned by TTS upstream: {exc}") from exc
    pcm_file.seek(0)

    async def stream_wav() -> AsyncIterator[bytes]:
        try:
            yield wav_header
            while True:
                if await request.is_disconnected():
                    break
                chunk = pcm_file.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            pcm_file.close()

    return StreamingResponse(
        stream_wav(),
        media_type="audio/wav",
        headers={"Content-Length": str(len(wav_header) + pcm_size)},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("TTS_PROXY_HOST", "0.0.0.0"),
        port=int(os.environ.get("TTS_PROXY_PORT", "8090")),
    )
