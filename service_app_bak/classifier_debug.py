from __future__ import annotations

import asyncio
import json
import logging
import wave
from datetime import datetime, timezone
from pathlib import Path

from .audio import MODEL_RATE

logger = logging.getLogger(__name__)


class ClassifierDebugRecorder:
    def __init__(self, debug_dir: Path | None, session_id: str):
        self.debug_dir = debug_dir / session_id if debug_dir is not None else None
        self.session_id = session_id
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def save(self, pcm: bytes, kind: str, label: str) -> None:
        if self.debug_dir is None:
            return
        try:
            async with self.lock:
                self.sequence += 1
                await asyncio.to_thread(
                    _write_record,
                    self.debug_dir,
                    self.session_id,
                    self.sequence,
                    pcm,
                    kind,
                    label,
                )
        except Exception:
            logger.exception(
                "session=%s failed to save classifier debug audio", self.session_id
            )


def _write_record(
    debug_dir: Path,
    session_id: str,
    sequence: int,
    pcm: bytes,
    kind: str,
    label: str,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
    filename = f"{timestamp}_{sequence:06d}_{kind}_{label}.wav"
    with wave.open(str(debug_dir / filename), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(MODEL_RATE)
        writer.writeframes(pcm)

    record = {
        "timestamp": now.isoformat(),
        "sequence": sequence,
        "session_id": session_id,
        "kind": kind,
        "label": label,
        "audio_file": filename,
        "audio_ms": len(pcm) * 1000 // (MODEL_RATE * 2),
        "sample_rate": MODEL_RATE,
    }
    with (debug_dir / "timeline.jsonl").open("a", encoding="utf-8") as timeline:
        timeline.write(json.dumps(record, ensure_ascii=True) + "\n")
