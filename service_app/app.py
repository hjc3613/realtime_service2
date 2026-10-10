from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .backends import TTSBackend
from .config import SETTINGS
from .session import RealtimeSession

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if SETTINGS.tool_call_ack:
        started_at = time.perf_counter()
        try:
            await TTSBackend(SETTINGS).synthesize_cached(SETTINGS.tool_call_ack)
            logger.info(
                "tool call acknowledgement audio cached latency_ms=%.1f",
                (time.perf_counter() - started_at) * 1000,
            )
        except Exception:
            logger.exception("failed to prewarm tool call acknowledgement audio")
    yield


app = FastAPI(title="Local Qwen Realtime API", version="0.1.0", lifespan=lifespan)
WEB_DIR = Path(__file__).with_name("web")
app.mount("/debug-assets", StaticFiles(directory=WEB_DIR), name="debug-assets")


@app.get("/", include_in_schema=False)
@app.get("/debug", include_in_schema=False)
async def realtime_debugger() -> FileResponse:
    """Serve the dependency-free browser client used to exercise the API."""
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


class ElevenLabsSpeechRequest(BaseModel):
    """Subset of the ElevenLabs request used by tau2's user simulator."""

    text: str
    model_id: str | None = None
    language_code: str | None = None
    voice_settings: dict[str, Any] | None = None
    seed: int | None = None


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/text-to-speech/{voice_id}")
@app.post("/v1/v1/text-to-speech/{voice_id}", include_in_schema=False)
async def elevenlabs_text_to_speech(
    voice_id: str,
    request: ElevenLabsSpeechRequest,
    output_format: str = Query(default="pcm_16000"),
) -> Response:
    """Serve tau2's ElevenLabs call shape using the configured local TTS."""
    if output_format != "pcm_16000":
        raise HTTPException(
            status_code=400,
            detail="The local compatibility endpoint only supports pcm_16000",
        )
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")

    speaker = SETTINGS.elevenlabs_speaker(voice_id)
    started_at = time.perf_counter()
    pcm = await TTSBackend(SETTINGS).synthesize_pcm16(
        request.text,
        speaker=speaker,
    )
    logging.getLogger(__name__).info(
        "compat TTS completed chars=%d audio_bytes=%d latency_ms=%.1f speaker=%s",
        len(request.text),
        len(pcm),
        (time.perf_counter() - started_at) * 1000,
        speaker,
    )
    return Response(
        content=pcm,
        media_type="audio/pcm",
        headers={
            "x-local-tts-model": SETTINGS.tts_model,
            "x-local-tts-speaker": speaker,
        },
    )


@app.websocket("/v1/realtime")
async def realtime(websocket: WebSocket) -> None:
    await websocket.accept()
    client = websocket.client.host if websocket.client else "unknown"
    model = websocket.query_params.get("model", "")
    logger.info("WebSocket accepted client=%s model=%s", client, model)
    session = None
    try:
        session = RealtimeSession(websocket.send_json, SETTINGS)
        await session.start()
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_json",
                            "message": str(exc),
                        },
                    }
                )
                continue
            await session.handle(message)
    except WebSocketDisconnect as exc:
        now = time.perf_counter()
        logger.info(
            "WebSocket disconnected client=%s session=%s code=%s reason=%s "
            "paused=%s client_idle_s=%.1f audio_idle_s=%s",
            client, session.session_id if session else "unknown", exc.code, exc.reason,
            session.debug_paused if session else False,
            now - session.last_client_event_at if session else 0,
            round(now - session.last_audio_received_at, 1)
            if session and session.last_audio_received_at is not None else None,
        )
    except Exception:
        logger.exception("WebSocket session failed client=%s", client)
        await websocket.close(code=1011)
    finally:
        if session is not None:
            await session.close()
