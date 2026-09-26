#!/usr/bin/env python3
"""Concurrent benchmark for the OpenAI-compatible TTS proxy on port 8091."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class RequestResult:
    request_id: int
    status_code: int | None
    success: bool
    ttfb_s: float | None
    latency_s: float
    response_bytes: int
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the TTS proxy on port 8091.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8091/v1/audio/speech",
        help="Full OpenAI-compatible speech endpoint",
    )
    parser.add_argument("-n", "--requests", type=int, default=20, help="Measured requests")
    parser.add_argument("-c", "--concurrency", type=int, default=4, help="Concurrent requests")
    parser.add_argument("-w", "--warmup", type=int, default=2, help="Warm-up requests")
    parser.add_argument("--text", default="你好，这是一条语音合成性能测试。")
    parser.add_argument("--model", default="qwen3_tts")
    parser.add_argument("--voice", default="vivian")
    parser.add_argument("--response-format", choices=("pcm", "wav"), default="wav")
    parser.add_argument("--sample-rate", type=int, default=8000)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--api-key",
        default=os.getenv("TTS_PROXY_API_KEY", ""),
        help="8091 proxy Bearer token; defaults to TTS_PROXY_API_KEY",
    )
    parser.add_argument(
        "--vary-text",
        action="store_true",
        help="Append the request number to the text to reduce possible cache effects",
    )
    parser.add_argument(
        "--ignore-env-proxy",
        action="store_true",
        help="Ignore HTTP_PROXY/HTTPS_PROXY environment variables",
    )
    parser.add_argument("--json-output", type=Path, help="Write the full report as JSON")
    args = parser.parse_args()

    if args.requests <= 0:
        parser.error("--requests must be greater than zero")
    if args.concurrency <= 0:
        parser.error("--concurrency must be greater than zero")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")
    if args.sample_rate <= 0:
        parser.error("--sample-rate must be greater than zero")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": min(values),
        "mean": statistics.fmean(values),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


def make_payload(args: argparse.Namespace, request_id: int) -> dict[str, Any]:
    text = args.text
    if args.vary_text:
        text = f"{text} 请求编号 {request_id}。"
    return {
        "model": args.model,
        "input": text,
        "voice": args.voice,
        "response_format": args.response_format,
        "speed": args.speed,
        "sample": args.sample_rate,
    }


async def run_request(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    request_id: int,
) -> RequestResult:
    started = time.perf_counter()
    ttfb: float | None = None
    response_bytes = 0
    status_code: int | None = None
    error_body = bytearray()

    try:
        async with client.stream(
            "POST",
            args.url,
            json=make_payload(args, request_id),
        ) as response:
            status_code = response.status_code
            async for chunk in response.aiter_bytes():
                if ttfb is None:
                    ttfb = time.perf_counter() - started
                response_bytes += len(chunk)
                if response.status_code >= 400 and len(error_body) < 1024:
                    error_body.extend(chunk[: 1024 - len(error_body)])

        latency = time.perf_counter() - started
        success = status_code is not None and 200 <= status_code < 300 and response_bytes > 0
        error = None
        if not success:
            detail = error_body.decode("utf-8", errors="replace").strip()
            error = detail or ("empty response" if status_code and status_code < 300 else None)
        return RequestResult(
            request_id=request_id,
            status_code=status_code,
            success=success,
            ttfb_s=ttfb,
            latency_s=latency,
            response_bytes=response_bytes,
            error=error,
        )
    except httpx.HTTPError as exc:
        return RequestResult(
            request_id=request_id,
            status_code=status_code,
            success=False,
            ttfb_s=ttfb,
            latency_s=time.perf_counter() - started,
            response_bytes=response_bytes,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_batch(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    count: int,
    concurrency: int,
    *,
    show_progress: bool,
) -> list[RequestResult]:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for request_id in range(1, count + 1):
        queue.put_nowait(request_id)

    results: list[RequestResult] = []
    completed = 0
    print_lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal completed
        while True:
            try:
                request_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            result = await run_request(client, args, request_id)
            results.append(result)
            completed += 1
            queue.task_done()
            if show_progress:
                async with print_lock:
                    state = "ok" if result.success else f"failed:{result.status_code or '-'}"
                    print(
                        f"\rCompleted {completed}/{count} ({state}, {result.latency_s:.3f}s)",
                        end="",
                        flush=True,
                    )

    workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, count))]
    await asyncio.gather(*workers)
    if show_progress:
        print()
    return sorted(results, key=lambda item: item.request_id)


def build_report(
    args: argparse.Namespace,
    results: list[RequestResult],
    wall_time_s: float,
) -> dict[str, Any]:
    successful = [result for result in results if result.success]
    latencies = [result.latency_s for result in successful]
    ttfbs = [result.ttfb_s for result in successful if result.ttfb_s is not None]
    response_sizes = [result.response_bytes for result in successful]
    audio_seconds = []
    realtime_factors = []

    for result in successful:
        pcm_bytes = result.response_bytes
        if args.response_format == "wav" and pcm_bytes >= 44:
            pcm_bytes -= 44
        duration = pcm_bytes / (args.sample_rate * 2)
        if duration > 0:
            audio_seconds.append(duration)
            realtime_factors.append(result.latency_s / duration)

    statuses = Counter(
        str(result.status_code) if result.status_code is not None else "transport_error"
        for result in results
    )
    errors = Counter(result.error for result in results if result.error)
    return {
        "configuration": {
            "url": args.url,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "warmup": args.warmup,
            "model": args.model,
            "voice": args.voice,
            "response_format": args.response_format,
            "sample_rate": args.sample_rate,
            "text_characters": len(args.text),
            "vary_text": args.vary_text,
        },
        "summary": {
            "wall_time_s": wall_time_s,
            "requests_per_second": len(results) / wall_time_s if wall_time_s else 0,
            "successful": len(successful),
            "failed": len(results) - len(successful),
            "success_rate": len(successful) / len(results) if results else 0,
            "status_counts": dict(statuses),
            "total_response_bytes": sum(response_sizes),
            "latency_s": metric_summary(latencies),
            "ttfb_s": metric_summary(ttfbs),
            "response_bytes": metric_summary([float(value) for value in response_sizes]),
            "audio_duration_s": metric_summary(audio_seconds),
            "realtime_factor": metric_summary(realtime_factors),
            "errors": dict(errors.most_common(10)),
        },
        "requests": [asdict(result) for result in results],
    }


def format_metric(name: str, values: dict[str, float] | None, unit: str = "s") -> None:
    if values is None:
        print(f"{name}: no successful samples")
        return
    print(
        f"{name}: mean={values['mean']:.3f}{unit} "
        f"p50={values['p50']:.3f}{unit} p90={values['p90']:.3f}{unit} "
        f"p95={values['p95']:.3f}{unit} p99={values['p99']:.3f}{unit} "
        f"min={values['min']:.3f}{unit} max={values['max']:.3f}{unit}"
    )


def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("\nBenchmark result")
    print("=" * 72)
    print(
        f"Requests: {summary['successful']} successful, {summary['failed']} failed; "
        f"success rate: {summary['success_rate'] * 100:.2f}%"
    )
    print(
        f"Wall time: {summary['wall_time_s']:.3f}s; "
        f"throughput: {summary['requests_per_second']:.3f} requests/s"
    )
    print(f"HTTP statuses: {summary['status_counts']}")
    format_metric("Latency", summary["latency_s"])
    format_metric("TTFB", summary["ttfb_s"])
    format_metric("Audio duration", summary["audio_duration_s"])
    format_metric("Real-time factor", summary["realtime_factor"], unit="x")
    if summary["response_bytes"]:
        sizes = summary["response_bytes"]
        print(
            f"Response size: mean={sizes['mean']:.0f} bytes; "
            f"min={sizes['min']:.0f}; max={sizes['max']:.0f}"
        )
    if summary["errors"]:
        print("Errors:")
        for error, count in summary["errors"].items():
            print(f"  {count}x {error}")


async def async_main(args: argparse.Namespace) -> int:
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        limits=limits,
        trust_env=not args.ignore_env_proxy,
    ) as client:
        if args.warmup:
            print(f"Running {args.warmup} warm-up request(s)...")
            warmup_results = await run_batch(
                client,
                args,
                args.warmup,
                min(args.concurrency, args.warmup),
                show_progress=False,
            )
            warmup_failures = [result for result in warmup_results if not result.success]
            if warmup_failures:
                print("Warm-up failed; benchmark stopped.", file=sys.stderr)
                for result in warmup_failures[:3]:
                    print(
                        f"  request {result.request_id}: HTTP {result.status_code}: {result.error}",
                        file=sys.stderr,
                    )
                return 2

        print(
            f"Benchmarking {args.url}: {args.requests} requests, "
            f"concurrency {args.concurrency}"
        )
        started = time.perf_counter()
        results = await run_batch(
            client,
            args,
            args.requests,
            args.concurrency,
            show_progress=True,
        )
        wall_time_s = time.perf_counter() - started

    report = build_report(args, results, wall_time_s)
    print_report(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"JSON report: {args.json_output.resolve()}")

    return 0 if report["summary"]["failed"] == 0 else 1


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nBenchmark interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
