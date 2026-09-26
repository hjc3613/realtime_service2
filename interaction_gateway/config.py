from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GatewaySettings:
    asr_base_url: str
    asr_api_key: str
    asr_model: str
    asr_timeout_s: float
    trust_env: bool
    semantic_onnx_path: Path
    semantic_cmvn_path: Path
    semantic_use_cuda: bool
    semantic_concurrency: int
    semantic_invalid_label: str
    semantic_debug_dir: Path | None

    @classmethod
    def from_env(cls) -> GatewaySettings:
        settings = cls(
            asr_base_url=os.getenv(
                "ASR_BASE_URL", "http://api-cybotforge-pre.brapp.com/v1"
            ).rstrip("/"),
            asr_api_key=os.getenv("ASR_API_KEY", ""),
            asr_model=os.getenv("ASR_MODEL", "br-audio-waihu-v2"),
            asr_timeout_s=float(os.getenv("ASR_TIMEOUT_S", "30")),
            trust_env=_env_bool("GATEWAY_TRUST_ENV", False),
            semantic_onnx_path=Path(
                os.getenv(
                    "SEMANTIC_ONNX_PATH",
                    str(PROJECT_ROOT / "turnsense_demo" / "models" / "model_int8.onnx"),
                )
            ).expanduser(),
            semantic_cmvn_path=Path(
                os.getenv(
                    "SEMANTIC_CMVN_PATH",
                    str(PROJECT_ROOT / "turnsense_demo" / "models" / "am.mvn"),
                )
            ).expanduser(),
            semantic_use_cuda=_env_bool("SEMANTIC_USE_CUDA", False),
            semantic_concurrency=int(os.getenv("SEMANTIC_CONCURRENCY", "1")),
            semantic_invalid_label=os.getenv(
                "SEMANTIC_INVALID_LABEL", "incomplete"
            ).strip().lower(),
            semantic_debug_dir=_semantic_debug_dir(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.semantic_concurrency < 1:
            raise ValueError("SEMANTIC_CONCURRENCY must be at least 1")
        if self.semantic_invalid_label not in {"complete", "incomplete", "invalid"}:
            raise ValueError(
                "SEMANTIC_INVALID_LABEL must be complete, incomplete, or invalid"
            )


def _semantic_debug_dir() -> Path | None:
    value = os.getenv(
        "SEMANTIC_DEBUG_DIR",
        str(PROJECT_ROOT / "interaction_gateway" / "semantic_debug"),
    ).strip()
    if not value:
        return None
    return Path(value).expanduser()
