from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


DEFAULT_ELEVENLABS_SPEAKERS = (
    "beier",
    "huangyue",
    "panwanting",
    "wanqing",
    "yaomengru",
    "yumi",
    "zz",
)

DEFAULT_ELEVENLABS_VOICE_MAP = (
    ("EZfwTIuZL0WWIVnjSgTF", "beier"),  # matt_delaney
    ("avQFHuQU7IjJf0u5MMBq", "huangyue"),  # lisa_brenner
    ("oNqrZRHHLWtHYsVNkRqe", "panwanting"),  # mildred_kaplan
    ("m1hMce9ingsjyIjkshRv", "wanqing"),  # arjun_roy
    ("GQ2S7ULnVjrOALFRfnsh", "yaomengru"),  # wei_lin
    ("ET3963lBcRmodt3ZaTBS", "yumi"),  # mamadou_diallo
    ("mnHhNJntmsPxJsZvYVM7", "zz"),  # priya_patil
)

DEFAULT_QWEN3_ELEVENLABS_SPEAKERS = (
    "aiden",
    "dylan",
    "eric",
    "ono_anna",
    "ryan",
    "serena",
    "sohee",
    "uncle_fu",
    "vivian",
)

DEFAULT_QWEN3_ELEVENLABS_VOICE_MAP = (
    ("EZfwTIuZL0WWIVnjSgTF", "ryan"),  # matt_delaney
    ("avQFHuQU7IjJf0u5MMBq", "vivian"),  # lisa_brenner
    ("oNqrZRHHLWtHYsVNkRqe", "ono_anna"),  # mildred_kaplan
    ("m1hMce9ingsjyIjkshRv", "aiden"),  # arjun_roy
    ("GQ2S7ULnVjrOALFRfnsh", "dylan"),  # wei_lin
    ("ET3963lBcRmodt3ZaTBS", "eric"),  # mamadou_diallo
    ("mnHhNJntmsPxJsZvYVM7", "serena"),  # priya_patil
)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).rstrip("/")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _debug_dir(name: str, default: Path) -> Path | None:
    value = os.getenv(name, str(default)).strip()
    return Path(value).expanduser() if value else None


def _optional_dir(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def _elevenlabs_voice_map() -> tuple[tuple[str, str], ...]:
    value = os.getenv("ELEVENLABS_VOICE_MAP")
    if value is None:
        return DEFAULT_ELEVENLABS_VOICE_MAP
    try:
        mapping = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("ELEVENLABS_VOICE_MAP must be valid JSON") from exc
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("ELEVENLABS_VOICE_MAP must be a non-empty JSON object")
    if not all(
        isinstance(voice_id, str) and voice_id and isinstance(speaker, str) and speaker
        for voice_id, speaker in mapping.items()
    ):
        raise ValueError(
            "ELEVENLABS_VOICE_MAP keys and values must be non-empty strings"
        )
    return tuple(mapping.items())


@dataclass(frozen=True)
class Settings:
    interactive_url: str = _env("INTERACTIVE_BASE_URL", "http://127.0.0.1:9004/v1")
    interactive_model: str = os.getenv("INTERACTIVE_MODEL", "interactive_model")
    asr_url: str = _env("ASR_BASE_URL", "http://localhost:5005/v1")
    asr_model: str = os.getenv(
        "ASR_MODEL", os.getenv("INTERACTIVE_MODEL", "interactive_model")
    )
    # Qwen3-ASR accepts an optional language hint independently from TTS.
    asr_language: str | None = os.getenv("ASR_LANGUAGE") or None
    # An explicitly empty value disables context. When unset, session.py uses
    # the model-specific default (empty for Qwen3-ASR, hotwords for Omni).
    asr_context: str | None = os.getenv("ASR_CONTEXT")
    # auto detects qwen3-asr model names and otherwise preserves Omni behavior.
    asr_prompt_mode: str = os.getenv("ASR_PROMPT_MODE", "auto")
    # Disabled unless explicitly configured because recordings contain user audio.
    asr_debug_dir: Path | None = _optional_dir("ASR_DEBUG_DIR")
    text_url: str = _env("TEXT_BASE_URL", "http://127.0.0.1:5004/v1")
    text_model: str = os.getenv("TEXT_MODEL", "qwen3_32b")
    text_streaming: bool = _env_bool("TEXT_STREAMING", True)
    text_enable_thinking: bool = _env_bool("TEXT_ENABLE_THINKING", True)
    text_api_key: str = os.getenv(
        "TEXT_API_KEY", os.getenv("LOCAL_MODEL_API_KEY", "EMPTY")
    )
    server_instruction_suffix: str = os.getenv("SERVER_INSTRUCTION_SUFFIX", "").strip()
    # Telecom text-LLM-only experiment: full, suffix_only, or none.
    telecom_text_prompt_mode: str = os.getenv("TELECOM_TEXT_PROMPT_MODE", "full")
    tts_url: str = _env("TTS_BASE_URL", "http://127.0.0.1:8091/v1")
    tts_model: str = os.getenv("TTS_MODEL", "qwen3_tts")
    # The direct OpenAI-compatible TTS endpoint returns raw mono PCM16.
    tts_pcm_sample_rate: int = int(os.getenv("TTS_PCM_SAMPLE_RATE", "8000"))
    # Shared by interaction, ASR, and TTS. TextBackend uses text_api_key.
    api_key: str = os.getenv("LOCAL_MODEL_API_KEY", "EMPTY")
    # Realtime assistant speech always uses this speaker. ElevenLabs-compatible
    # user speech is selected independently by elevenlabs_speaker().
    tts_speaker: str = os.getenv("TTS_SPEAKER", "dongbai")
    greeting: str = os.getenv("GREETING", "Hi! How can I help you today?")
    elevenlabs_speakers: tuple[str, ...] = DEFAULT_ELEVENLABS_SPEAKERS
    elevenlabs_voice_map: tuple[tuple[str, str], ...] = _elevenlabs_voice_map()
    tts_language: str = os.getenv("TTS_LANGUAGE", "Auto")
    tick_ms: int = int(os.getenv("TICK_MS", "200"))
    classifier_window_ticks: int = int(os.getenv("CLASSIFIER_WINDOW_TICKS", "2"))
    # Before a target turn is locked, classify recent audio independently of
    # the prediction cadence. Zero keeps the legacy candidate-only behavior.
    semantic_window_ms: int = int(os.getenv("SEMANTIC_WINDOW_MS", "1000"))
    interrupt_window_ms: int = int(os.getenv("INTERRUPT_WINDOW_MS", "3000"))
    interrupt_min_speech_ms: int = int(os.getenv("INTERRUPT_MIN_SPEECH_MS", "800"))
    # Parsed for compatibility. Candidate cleanup is model-gated and no longer
    # depends on VAD silence in noisy environments.
    interrupt_end_silence_ms: int = int(os.getenv("INTERRUPT_END_SILENCE_MS", "600"))
    interrupt_negative_confirmations: int = int(
        os.getenv("INTERRUPT_NEGATIVE_CONFIRMATIONS", "2")
    )
    interrupt_confirmations: int = int(os.getenv("INTERRUPT_CONFIRMATIONS", "2"))
    interrupt_ack: str = os.getenv("INTERRUPT_ACK", "Okay. Understood.").strip()
    tool_call_ack: str = os.getenv("TOOL_CALL_ACK", "One moment.").strip()
    # Kept for backwards-compatible environment parsing; the unsafe VAD-only
    # force-interrupt path is intentionally disabled in session.py.
    interrupt_force_speech_ms: int = int(os.getenv("INTERRUPT_FORCE_SPEECH_MS", "0"))
    vad_pre_roll_ms: int = int(os.getenv("VAD_PRE_ROLL_MS", "300"))
    silence_ms: int = int(os.getenv("END_SILENCE_MS", "3000"))
    complete_confirmations: int = int(os.getenv("COMPLETE_CONFIRMATIONS", "2"))
    # Keep recent audio that the interaction model identifies as not addressed
    # to the assistant. This is deliberately separate from the active user turn.
    ignorable_audio_max_s: int = int(os.getenv("IGNORABLE_AUDIO_MAX_S", "15"))
    interrupt_enabled: bool = _env_bool("INTERRUPT_ENABLED", True)
    classifier_timeout_s: float = float(os.getenv("CLASSIFIER_TIMEOUT_S", "3"))
    classifier_max_tokens: int = int(os.getenv("CLASSIFIER_MAX_TOKENS", "5"))
    asr_timeout_s: float = float(os.getenv("ASR_TIMEOUT_S", "30"))
    asr_max_tokens: int = int(os.getenv("ASR_MAX_TOKENS", "256"))
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "120"))
    tts_timeout_s: float = float(os.getenv("TTS_TIMEOUT_S", "60"))
    max_user_audio_s: int = int(os.getenv("MAX_USER_AUDIO_S", "40"))
    interactive_history_messages: int = int(
        os.getenv("INTERACTIVE_HISTORY_MESSAGES", "6")
    )
    backend_trust_env: bool = _env_bool("BACKEND_TRUST_ENV", False)
    interactive_history_user_audio_ms: int = int(
        os.getenv("INTERACTIVE_HISTORY_USER_AUDIO_MS", "5000")
    )
    tts_realtime_pacing: bool = _env_bool("TTS_REALTIME_PACING", True)
    interactive_debug_dir: Path | None = _debug_dir(
        "INTERACTIVE_DEBUG_DIR", PROJECT_ROOT / "service_app" / "interactive_debug"
    )

    def elevenlabs_speaker(self, voice_id: str) -> str:
        """Map an ElevenLabs voice ID to a stable local TTS speaker."""
        if self.tts_model.lower() == "qwen3_tts":
            speakers = DEFAULT_QWEN3_ELEVENLABS_SPEAKERS
            configured = dict(DEFAULT_QWEN3_ELEVENLABS_VOICE_MAP)
        else:
            speakers = self.elevenlabs_speakers
            configured = dict(self.elevenlabs_voice_map)

        if voice_id in configured:
            return configured[voice_id]

        # Custom TAU2_VOICE_ID_* values still get a stable local voice without
        # requiring a matching service configuration entry.
        digest = hashlib.sha256(voice_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(speakers)
        return speakers[index]


SETTINGS = Settings()
