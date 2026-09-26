#!/usr/bin/env python3
"""Locate successful tau-voice simulations and their realtime server logs."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


LOG_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
IMPORTANT_SESSION_EVENTS = (
    "requesting ASR",
    "ASR completed",
    "ASR failed",
    "starting text response",
    "LLM completed",
    "emitting tool call",
    "received tool result",
    "response pipeline failed",
    "classifier failed",
    "interrupt classification=",
    "forcing interrupt",
)


def default_run_dir() -> Path:
    configured = os.getenv("TAU_VOICE_RUN_DIR")
    if configured:
        return Path(configured).expanduser()
    agent_eval_root = Path(__file__).resolve().parents[2]
    return (
        agent_eval_root
        / "tau2-bench"
        / "data"
        / "simulations"
        / "br_voice_retail"
    )


def successful_simulations(run_dir: Path, task_id: str) -> list[tuple[str, Path]]:
    task_dir = run_dir / "artifacts" / f"task_{task_id}"
    matches: list[tuple[str, Path]] = []
    for artifact_dir in sorted(task_dir.glob("sim_*")):
        if not artifact_dir.is_dir():
            continue
        audio_dir = artifact_dir / "audio"
        if not (
            (audio_dir / "assistant_labels.txt").is_file()
            and (audio_dir / "user_labels.txt").is_file()
        ):
            continue
        sim_id = artifact_dir.name.removeprefix("sim_")
        sim_file = run_dir / "simulations" / f"{sim_id}.json"
        if sim_file.is_file():
            matches.append((sim_id, artifact_dir))
    return matches


def load_simulation(task_id: str, run_dir: Path, sim_id: str) -> tuple[Path, dict[str, Any], str]:
    sim_file = run_dir / "simulations" / f"{sim_id}.json"
    user_labels = run_dir / "artifacts" / f"task_{task_id}" / f"sim_{sim_id}" / "audio" / "user_labels.txt"
    try:
        data = json.loads(sim_file.read_text(encoding="utf-8"))
        user_labels_text = user_labels.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read simulation {sim_file}: {exc}") from exc
    return sim_file, data, user_labels_text


def resolve_log_paths(patterns: Iterable[str], repo_root: Path) -> list[Path]:
    expanded: set[Path] = set()
    for pattern in patterns:
        matches = glob.glob(os.path.expanduser(pattern))
        if not matches and not glob.has_magic(pattern):
            matches = [os.path.expanduser(pattern)]
        for match in matches:
            path = Path(match)
            if path.is_file():
                expanded.add(path.resolve())
    if not expanded:
        expanded.update(path.resolve() for path in repo_root.glob("uvicorn*.log"))
    return sorted(expanded)


def iter_log_records(log_file: Path) -> Iterable[str]:
    current: list[str] = []
    try:
        with log_file.open(encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if LOG_RECORD_START.match(line) and current:
                    yield "\n".join(current)
                    current = []
                current.append(line)
    except OSError as exc:
        yield f"ERROR reading {log_file}: {exc}"
        return
    if current:
        yield "\n".join(current)


def session_log_records(
    log_files: Iterable[Path], session_id: str, include_all: bool
) -> list[tuple[Path, str]]:
    marker = f"session={session_id}"
    records: list[tuple[Path, str]] = []
    for log_file in log_files:
        for record in iter_log_records(log_file):
            if marker not in record:
                continue
            if include_all or any(event in record for event in IMPORTANT_SESSION_EVENTS):
                records.append((log_file, record))
    return records


def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def trajectory_lines(simulation: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for tick in simulation.get("ticks") or []:
        tick_id = tick.get("tick_id", "?")
        content = (tick.get("agent_chunk") or {}).get("content") or ""
        if content:
            lines.append(f"[tick {tick_id}] ASSISTANT_TEXT: {content}")
        for call in tick.get("agent_tool_calls") or []:
            lines.append(
                f"[tick {tick_id}] TOOL_CALL: id={call.get('id', '')} "
                f"name={call.get('name', '')} "
                f"arguments={json_compact(call.get('arguments', {}))}"
            )
        for result in tick.get("agent_tool_results") or []:
            lines.append(
                f"[tick {tick_id}] TOOL_RESULT: id={result.get('id', '')} "
                f"error={result.get('error', False)} content={result.get('content', '')}"
            )
    return lines


def render_simulation_report(
    task_id: str,
    run_dir: Path,
    sim_id: str,
    artifact_dir: Path,
    log_files: list[Path],
    include_all_session_events: bool,
) -> str:
    sim_file, simulation, user_labels_text = load_simulation(task_id, run_dir, sim_id)
    session_id = simulation.get("provider_session_id") or ""
    records = (
        session_log_records(log_files, session_id, include_all_session_events)
        if session_id
        else []
    )

    lines = [
        "=" * 88,
        f"TASK_ID: {task_id}",
        f"SIM_ID: {sim_id}",
        f"SESSION_ID: {session_id or '[missing]'}",
        f"TRIAL: {simulation.get('trial', '')}",
        f"START_TIME: {simulation.get('start_time', '')}",
        f"END_TIME: {simulation.get('end_time', '')}",
        f"SIM_FILE: {sim_file}",
        f"ARTIFACT_DIR: {artifact_dir}",
        "",
        "[REALTIME SERVER LOGS: ASR / LLM / TOOL EVENTS]",
    ]
    if records:
        for log_file, record in records:
            lines.extend((f"--- {log_file}", record))
    else:
        lines.append(
            "No matching server records found. The service log may have been "
            "overwritten; tau2 user_transcript is not the service ASR output."
        )

    lines.extend(("", "[TAU2 TRAJECTORY: ASSISTANT TEXT / TOOL CALLS / TOOL RESULTS]"))
    # trajectory = trajectory_lines(simulation)
    # lines.extend(trajectory or ["No assistant trajectory records found."])
    lines.extend(("", "[TAU2 USER LABELS: USER SPEECH SEGMENTS]"))
    lines.append(f"USER_LABELS: {user_labels_text}")
    lines.append(f'audio: {artifact_dir}/audio/both.wav')
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find successful tau-voice simulations by task id, map them to the "
            "provider session id, and extract matching realtime server events."
        )
    )
    parser.add_argument("task_id", help="Task id, for example 19")
    parser.add_argument(
        "--run-dir",
        type=Path,
        # default=default_run_dir(),
        default='../tau2-bench/data/simulations/br_voice_retail3/',
        help="tau2 run directory containing artifacts/ and simulations/",
    )
    parser.add_argument(
        "--service-log",
        action="append",
        default=['uvicorn3.log'],
        metavar="PATH_OR_GLOB",
        help="Service log path/glob; repeat for multiple logs",
    )
    parser.add_argument("--sim-id", help="Restrict output to one simulation id")
    parser.add_argument(
        "--latest",
        default=True,
        help="Use only the most recently modified successful simulation",
    )
    parser.add_argument(
        "--all-session-events",
        action="store_true",
        help="Include every server log record for the session, not only key events",
    )
    parser.add_argument("--output", type=Path, help="Write the report to this file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    matches = successful_simulations(run_dir, args.task_id)
    if args.sim_id:
        matches = [match for match in matches if match[0] == args.sim_id]
    if args.latest and matches:
        matches = [max(matches, key=lambda match: match[1].stat().st_mtime)]
    if not matches:
        print(
            f"No successful simulation found for task_{args.task_id} under "
            f"{run_dir / 'artifacts'}",
            file=sys.stderr,
        )
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    log_files = resolve_log_paths(args.service_log, repo_root)
    reports = [
        render_simulation_report(
            args.task_id,
            run_dir,
            sim_id,
            artifact_dir,
            log_files,
            args.all_session_events,
        )
        for sim_id, artifact_dir in matches
    ]
    report = "\n\n".join(reports) + "\n"
    if args.output:
        output = args.output.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(output.resolve())
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

