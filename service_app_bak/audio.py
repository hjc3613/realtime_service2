from __future__ import annotations

import audioop
import io
import wave
from typing import Optional

try:
    import webrtcvad
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal installs
    webrtcvad = None

TELEPHONY_RATE = 8000
MODEL_RATE = 16000
SILENCE_ULAW = b"\xff"


def ulaw_to_pcm16(
    audio: bytes, state: Optional[tuple] = None
) -> tuple[bytes, tuple | None]:
    pcm8 = audioop.ulaw2lin(audio, 2)
    return audioop.ratecv(pcm8, 2, 1, TELEPHONY_RATE, MODEL_RATE, state)


def pcm16_to_ulaw(
    audio: bytes, source_rate: int, state: Optional[tuple] = None
) -> tuple[bytes, tuple | None]:
    pcm8, new_state = audioop.ratecv(audio, 2, 1, source_rate, TELEPHONY_RATE, state)
    return audioop.lin2ulaw(pcm8, 2), new_state


def resample_pcm16(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    """Resample mono PCM16 audio, returning raw PCM16 bytes."""
    if source_rate == target_rate:
        return audio
    resampled, _ = audioop.ratecv(audio, 2, 1, source_rate, target_rate, None)
    return resampled


def pcm16_wav(audio: bytes, sample_rate: int = MODEL_RATE) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(audio)
    return output.getvalue()


def ulaw_rms(audio: bytes) -> int:
    """Return PCM RMS for raw 8kHz mu-law audio."""
    return audioop.rms(audioop.ulaw2lin(audio, 2), 2) if audio else 0


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(data), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError("TTS audio must be mono PCM16 WAV")
        return reader.readframes(reader.getnframes()), reader.getframerate()


class VoiceDetector:
    """Small WebRTC VAD wrapper; input is the external 8kHz mu-law stream."""

    def __init__(self, mode: int = 2):
        if webrtcvad is None:
            raise RuntimeError(
                "WebRTC VAD is required; install service_app/requirements.txt"
            )
        self._vad = webrtcvad.Vad(mode)
        self._resample_state = None

    def is_speech(self, ulaw_audio: bytes) -> bool:
        pcm, self._resample_state = ulaw_to_pcm16(ulaw_audio, self._resample_state)
        # WebRTC VAD accepts 10, 20, or 30 ms frames at 8/16/32/48kHz.
        frame_bytes = MODEL_RATE * 20 // 1000 * 2
        return any(
            self._vad.is_speech(pcm[i : i + frame_bytes], MODEL_RATE)
            for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes)
        )
