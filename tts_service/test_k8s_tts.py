#!/usr/bin/env python3
"""Call the K8s TTS endpoint directly, without the local 8091 proxy."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

import httpx


def speech_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/audio/speech"):
        return url
    if url.endswith("/v1"):
        return f"{url}/audio/speech"
    return f"{url}/v1/audio/speech"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Directly test the K8s TTS service and save its raw PCM response."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("TTS_UPSTREAM_URL", "http://api-cybotforge-pre.brapp.com/v1"),
        help="K8s TTS base URL or full /audio/speech URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("TTS_UPSTREAM_API_KEY", "junchao.hu-104a732c-eaa6-452f-974e-604801e371f7"),
        help="Bearer token; defaults to TTS_UPSTREAM_API_KEY",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("TTS_UPSTREAM_MODEL", "nex-tts-3-6-hd"),
    )
    parser.add_argument("--text", default="你好，这是一条语音服务连通性测试。")
    parser.add_argument("--voice", default="dongbai")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--sample-rate", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", type=Path, default=Path("k8s_tts_output.wav"))
    parser.add_argument(
        "--ignore-env-proxy",
        default=True,
        help="Ignore HTTP_PROXY/HTTPS_PROXY environment variables",
    )
    return parser.parse_args()


def print_dns(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        print(f"Invalid URL: {url}", file=sys.stderr)
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = sorted(
            {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
        print(f"DNS: {host} -> {', '.join(addresses)}")
    except socket.gaierror as exc:
        print(f"DNS lookup failed for {host}: {exc}", file=sys.stderr)


def save_audio(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
    else:
        path.write_bytes(pcm)


def main() -> int:
    args = parse_args()
    url = speech_url(args.url)
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    else:
        print("Warning: no API key supplied", file=sys.stderr)

    payload = {
        "model": args.model,
        "input": args.text,
        "voice": args.voice,
        "response_format": "pcm",
        "speed": args.speed,
        "sample": args.sample_rate,
    }

    print_dns(url)
    print(f"POST: {url}")
    print(f"Model: {args.model}; voice: {args.voice}; sample rate: {args.sample_rate}")

    started = time.perf_counter()
    try:
        timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
        with httpx.Client(timeout=timeout, trust_env=not args.ignore_env_proxy) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - started
        print(f"Request failed after {elapsed:.3f}s: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    elapsed = time.perf_counter() - started
    print(f"HTTP: {response.status_code} {response.reason_phrase}; elapsed: {elapsed:.3f}s")
    for name in ("server", "content-type", "content-length", "via", "x-request-id"):
        if value := response.headers.get(name):
            print(f"{name}: {value}")

    if response.status_code >= 400:
        body = response.text
        if len(body) > 4096:
            body = f"{body[:4096]}\n... response truncated ..."
        print("Response body:", file=sys.stderr)
        print(body, file=sys.stderr)
        return 1

    if not response.content:
        print("The service returned an empty successful response", file=sys.stderr)
        return 1

    save_audio(args.output, response.content, args.sample_rate)
    print(f"Saved: {args.output.resolve()} ({len(response.content)} PCM bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
