from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from turnsense_demo.turn_sense_semantic_complete_demo import (
    DEFAULT_AUDIO_SECONDS,
    DEFAULT_FRONTEND_CONF,
    LABELS,
    MAX_AUDIO_SECONDS,
    SAMPLING_RATE,
    AudioClassifierInfer,
)

from .audio import decode_pcm16_wav_data_url, last_audio_data_url
from .config import GatewaySettings

logger = logging.getLogger(__name__)

SEMANTIC_CHOICES = frozenset({"complete", "incomplete"})
INTERRUPT_CHOICES = frozenset({"interrupt", "backchannel", "ignorable"})


def request_choices(payload: dict[str, Any]) -> frozenset[str] | None:
    structured = payload.get("structured_outputs")
    if not isinstance(structured, dict) or "choice" not in structured:
        return None
    choices = structured["choice"]
    if not isinstance(choices, list) or not all(isinstance(x, str) for x in choices):
        raise ValueError("structured_outputs.choice must be a list of strings")
    return frozenset(choices)


def completion_response(content: str, model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1},
    }


class GatewayService:
    def __init__(
        self,
        settings: GatewaySettings,
        classifier: AudioClassifierInfer,
        asr_client: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.classifier = classifier
        self.asr_client = asr_client
        self.semantic_slots = asyncio.Semaphore(settings.semantic_concurrency)
        self.semantic_debug_lock = asyncio.Lock()
        self.semantic_debug_sequence = 0

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any] | Response:
        choices = request_choices(payload)
        if choices == SEMANTIC_CHOICES:
            return await self._semantic(payload)
        if choices == INTERRUPT_CHOICES:
            return completion_response("ignorable", str(payload.get("model", "")))
        if choices is None:
            return await self._asr(payload)
        raise ValueError(f"Unsupported structured output choices: {sorted(choices)}")

    async def _semantic(self, payload: dict[str, Any]) -> dict[str, Any]:
        audio = decode_pcm16_wav_data_url(last_audio_data_url(payload.get("messages")))
        async with self.semantic_slots:
            result = await asyncio.to_thread(self.classifier.predict_audio, audio)
        label = str(result.get("prediction", "")).lower()
        if label not in LABELS:
            raise RuntimeError(f"Semantic model returned an unknown label: {label}")
        model_label = label
        if label == "invalid":
            label = self.settings.semantic_invalid_label
        await self._save_semantic_debug(audio, model_label, label)
        logger.info(
            "semantic classification label=%s audio_ms=%d",
            label,
            len(audio) * 1000 // SAMPLING_RATE,
        )
        return completion_response(label, str(payload.get("model", "")))

    async def _save_semantic_debug(
        self, audio: Any, model_label: str, returned_label: str
    ) -> None:
        debug_dir = self.settings.semantic_debug_dir
        if debug_dir is None:
            return
        try:
            async with self.semantic_debug_lock:
                self.semantic_debug_sequence += 1
                sequence = self.semantic_debug_sequence
                await asyncio.to_thread(
                    _write_semantic_debug,
                    debug_dir,
                    audio,
                    model_label,
                    returned_label,
                    sequence,
                )
        except Exception:
            logger.exception("failed to save semantic debug audio")

    async def _asr(self, payload: dict[str, Any]) -> Response:
        if not self.settings.asr_api_key:
            return JSONResponse(
                status_code=503,
                content={"error": {"message": "ASR_API_KEY is not configured"}},
            )
        upstream_payload = dict(payload)
        upstream_payload["model"] = self.settings.asr_model
        response = await self.asr_client.post(
            f"{self.settings.asr_base_url}/chat/completions",
            json=upstream_payload,
            headers={"Authorization": f"Bearer {self.settings.asr_api_key}"},
        )
        if response.is_error:
            logger.error(
                "ASR upstream HTTP %d: %s",
                response.status_code,
                response.text[:2000],
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
        )


ClassifierFactory = Callable[[GatewaySettings], AudioClassifierInfer]


def _write_semantic_debug(
    debug_dir: Path,
    audio: Any,
    model_label: str,
    returned_label: str,
    sequence: int,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
    filename = f"{timestamp}_{sequence:06d}_{model_label}.wav"
    pcm16_audio = (audio * 32768.0).clip(-32768.0, 32767.0).astype("<i2")
    pcm16 = pcm16_audio.tobytes()
    with wave.open(str(debug_dir / filename), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLING_RATE)
        writer.writeframes(pcm16)

    record = {
        "timestamp": now.isoformat(),
        "sequence": sequence,
        "audio_file": filename,
        "audio_ms": len(audio) * 1000 // SAMPLING_RATE,
        "sample_rate": SAMPLING_RATE,
        "label": model_label,
        "returned_label": returned_label,
    }
    with (debug_dir / "timeline.jsonl").open("a", encoding="utf-8") as timeline:
        timeline.write(json.dumps(record, ensure_ascii=True) + "\n")


def build_classifier(settings: GatewaySettings) -> AudioClassifierInfer:
    if not settings.semantic_onnx_path.is_file():
        raise FileNotFoundError(f"ONNX model not found: {settings.semantic_onnx_path}")
    if not settings.semantic_cmvn_path.is_file():
        raise FileNotFoundError(f"CMVN file not found: {settings.semantic_cmvn_path}")
    frontend_conf = {
        **DEFAULT_FRONTEND_CONF,
        "cmvn_file": str(settings.semantic_cmvn_path),
    }
    return AudioClassifierInfer(
        onnx_path=str(settings.semantic_onnx_path),
        labels=LABELS,
        use_cuda=settings.semantic_use_cuda,
        audio_seconds=DEFAULT_AUDIO_SECONDS,
        max_audio_seconds=MAX_AUDIO_SECONDS,
        sampling_rate=SAMPLING_RATE,
        frontend_conf=frontend_conf,
        clip_mode="tail",
    )


def create_app(
    settings: GatewaySettings | None = None,
    classifier_factory: ClassifierFactory = build_classifier,
) -> FastAPI:
    configured = settings or GatewaySettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("loading semantic model path=%s", configured.semantic_onnx_path)
        classifier = await asyncio.to_thread(classifier_factory, configured)
        async with httpx.AsyncClient(
            timeout=configured.asr_timeout_s,
            trust_env=configured.trust_env,
        ) as client:
            app.state.gateway = GatewayService(configured, classifier, client)
            logger.info("interaction gateway ready")
            yield

    gateway_app = FastAPI(
        title="Interaction Compatibility Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    @gateway_app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "semantic_model_loaded": hasattr(request.app.state, "gateway"),
        }

    @gateway_app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: Request, payload: dict[str, Any]
    ) -> dict[str, Any] | Response:
        try:
            return await request.app.state.gateway.complete(payload)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request_error", "message": str(exc)}},
            )
        except httpx.TimeoutException as exc:
            logger.warning("ASR upstream timed out: %s", exc)
            return JSONResponse(
                status_code=504,
                content={"error": {"type": "upstream_timeout", "message": str(exc)}},
            )
        except httpx.HTTPError as exc:
            logger.warning("ASR upstream failed: %s", exc)
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "upstream_error", "message": str(exc)}},
            )
        except Exception as exc:
            logger.exception("interaction gateway request failed")
            return JSONResponse(
                status_code=500,
                content={"error": {"type": "server_error", "message": str(exc)}},
            )

    return gateway_app


app = create_app()
