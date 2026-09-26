"""Optionally redirect ElevenLabs SDK clients to the local realtime service.

Python imports ``sitecustomize`` during startup when this directory is on
``PYTHONPATH``. The environment gate keeps ordinary Python processes unchanged.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def _normalize_base_url(value: str) -> str:
    """Convert realtime-style URLs to the ElevenLabs SDK's API root."""
    value = value.strip().rstrip("/")
    if not value:
        return ""

    parsed = urlsplit(value)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/")
    for suffix in ("/v1/realtime", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((scheme, parsed.netloc, path, parsed.query, parsed.fragment))


_base_url = _normalize_base_url(os.getenv("TAU2_ELEVENLABS_BASE_URL", ""))

if _base_url:
    import elevenlabs

    _ElevenLabs = elevenlabs.ElevenLabs

    def _local_elevenlabs(*args, **kwargs):
        kwargs.setdefault("base_url", _base_url)
        return _ElevenLabs(*args, **kwargs)

    elevenlabs.ElevenLabs = _local_elevenlabs
