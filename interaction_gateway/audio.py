from __future__ import annotations

import base64
import binascii
import io
import wave
from typing import Any

import numpy as np


def last_audio_data_url(messages: Any) -> str:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")

    found: str | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "audio_url":
                continue
            audio_url = item.get("audio_url")
            if isinstance(audio_url, dict) and isinstance(audio_url.get("url"), str):
                found = audio_url["url"]

    if found is None:
        raise ValueError("No audio_url content was found in messages")
    return found


def decode_pcm16_wav_data_url(url: str) -> np.ndarray:
    prefix = "data:audio/wav;base64,"
    if not url.startswith(prefix):
        raise ValueError("audio_url must be a base64-encoded audio/wav data URL")
    try:
        wav_bytes = base64.b64decode(url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("audio_url contains invalid base64 data") from exc

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            compression = reader.getcomptype()
            frames = reader.readframes(reader.getnframes())
    except (EOFError, wave.Error) as exc:
        raise ValueError("audio_url does not contain a valid WAV file") from exc

    if channels != 1 or sample_width != 2 or sample_rate != 16000 or compression != "NONE":
        raise ValueError("Semantic audio must be uncompressed mono PCM16 at 16 kHz")
    if not frames:
        raise ValueError("Semantic audio must not be empty")
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
