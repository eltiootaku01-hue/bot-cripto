"""One-command orchestration for paired prospective BTC/ETH public data capture.

This coordinates the existing Kline and bookTicker collectors. It uses public
market data only, launches both collectors against a shared UTC-midnight target,
stops the remaining process if either collector fails, and accepts the pair only
when the existing independent validators pass. It never creates or submits orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .prospective_book_ticker_capture import (
    DEFAULT_CAPTURE_GRACE_SECONDS,
    DEFAULT_DURATION_SECONDS as BOOK_TICKER_DURATION_SECONDS,
    validate_paired_manifests,
)
from .prospective_capture import (
    DEFAULT_DURATION_SECONDS as KLINE_BASE_DURATION_SECONDS,
    DEFAULT_GRACE_SECONDS as KLINE_GRACE_SECONDS,
)

SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("evidence/prospective-btc-eth-pair")
DEFAULT_MONITOR_INTERVAL_SECONDS = 1.0
UTC_START_TOLERANCE_SECONDS = 10.0
# Keep the pair's common overlap above 24 hours despite sequential child startup.
PAIR_OVERLAP_GUARD_SECONDS = 60
KLINE_DURATION_SECONDS = KLINE_BASE_DURATION_SECONDS + PAIR_OVERLAP_GUARD_SECONDS

Clock = Callable[[], datetime]
Sleep = Callable[[float], None]
PopenFactory = Callable[..., Any]


class _CapturePairBlocked(RuntimeError):
    """Expected fail-closed outcome before a paired capture can be accepted."""


def _capture_source_provenance(
    cwd: Path,
    *,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Bind capture evidence to a clean revision and the live remote main SHA."""
    execute = subprocess.run if run_command is None else run_command
    working_directory = Path(cwd).resolve()
    try:
        top_result = execute(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(working_directory), capture_output=True, text=True, check=False,
        )
        head_result = execute(
            ["git", "rev-parse", "HEAD"],
            cwd=str(working_directory), capture_output=True, text=True, check=False,
        )
        branch_result = execute(
            ["git", "branch", "--show-current"],
            cwd=str(working_directory), capture_output=True, text=True, check=False,
        )
        origin_main_result = execute(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=str(working_directory), capture_output=True, text=True, check=False,
        )
        live_main_result = execute(
            ["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"],
            cwd=str(working_directory), capture_output=True, text=True, check=False,
        )
        status_result = execute(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--",
             "bot_obrero/", "pyproject.toml"],
            cwd=str(working_directory), capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        return {
            "status": "UNKNOWN",
            "reason": f"GIT_PROVENANCE_UNAVAILABLE:{type(exc).__name__}",
            "git_head_sha": None,
            "branch": None,
            "source_status_entries": [],
        }

    results = (top_result, head_result, branch_result, origin_main_result, live_main_result, status_result)
    if any(getattr(result, "returncode", 1) != 0 for result in results):
        return {
            "status": "UNKNOWN",
            "reason": "GIT_PROVENANCE_COMMAND_FAILED",
            "git_head_sha": None,
            "branch": None,
            "source_status_entries": [],
        }

    repo_root_text = str(getattr(top_result, "stdout", "")).strip()
    head_sha = str(getattr(head_result, "stdout", "")).strip().lower()
    branch = str(getattr(branch_result, "stdout", "")).strip() or None
    origin_main_sha = str(getattr(origin_main_result, "stdout", "")).strip().lower()
    live_main_lines = str(getattr(live_main_result, "stdout", "")).strip().splitlines()
    live_main_parts = live_main_lines[0].split() if len(live_main_lines) == 1 else []
    live_main_sha = live_main_parts[0].lower() if (
        len(live_main_parts) == 2 and live_main_parts[1] == "refs/heads/main"
    ) else ""
    status_text = str(getattr(status_result, "stdout", "")).rstrip()
    status_entries = status_text.splitlines() if status_text else []

    if not repo_root_text or Path(repo_root_text).resolve() != working_directory:
        return {
            "status": "BLOCKED",
            "reason": "CAPTURE_REQUIRES_REPOSITORY_ROOT",
            "git_head_sha": head_sha or None,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head_sha):
        return {
            "status": "UNKNOWN",
            "reason": "GIT_HEAD_SHA_INVALID",
            "git_head_sha": None,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", origin_main_sha):
        return {
            "status": "UNKNOWN",
            "reason": "GIT_ORIGIN_MAIN_SHA_INVALID",
            "git_head_sha": head_sha,
            "origin_main_sha": None,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", live_main_sha):
        return {
            "status": "UNKNOWN",
            "reason": "GIT_LIVE_ORIGIN_MAIN_SHA_INVALID",
            "git_head_sha": head_sha,
            "origin_main_sha": origin_main_sha,
            "live_main_sha": None,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if branch != "main":
        return {
            "status": "BLOCKED",
            "reason": "CAPTURE_REQUIRES_MAIN_BRANCH",
            "git_head_sha": head_sha,
            "origin_main_sha": origin_main_sha,
            "live_main_sha": live_main_sha,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if origin_main_sha != live_main_sha:
        return {
            "status": "BLOCKED",
            "reason": "CAPTURE_ORIGIN_MAIN_TRACKING_STALE",
            "git_head_sha": head_sha,
            "origin_main_sha": origin_main_sha,
            "live_main_sha": live_main_sha,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if head_sha != origin_main_sha:
        return {
            "status": "BLOCKED",
            "reason": "CAPTURE_MAIN_NOT_UP_TO_DATE",
            "git_head_sha": head_sha,
            "origin_main_sha": origin_main_sha,
            "live_main_sha": live_main_sha,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    if status_entries:
        return {
            "status": "BLOCKED",
            "reason": "CAPTURE_SOURCE_WORKTREE_DIRTY",
            "git_head_sha": head_sha,
            "origin_main_sha": origin_main_sha,
            "live_main_sha": live_main_sha,
            "branch": branch,
            "source_status_entries": status_entries,
        }
    return {
        "status": "VALID",
        "reason": None,
        "git_head_sha": head_sha,
        "origin_main_sha": origin_main_sha,
        "live_main_sha": live_main_sha,
        "branch": branch,
        "source_status_entries": [],
    }


def _same_source_revision(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> bool:
    """Require stable clean HEAD, local tracking ref, and live origin/main at each boundary."""
    if expected.get("status") != "VALID" or observed.get("status") != "VALID":
        return False
    if expected.get("branch") != "main" or observed.get("branch") != "main":
        return False
    if expected.get("git_head_sha") != observed.get("git_head_sha"):
        return False
    if expected.get("origin_main_sha") != observed.get("origin_main_sha"):
        return False
    if expected.get("live_main_sha") != observed.get("live_main_sha"):
        return False
    if observed.get("origin_main_sha") != observed.get("live_main_sha"):
        return False
    if observed.get("git_head_sha") != observed.get("origin_main_sha"):
        return False
    if expected.get("source_status_entries") or observed.get("source_status_entries"):
        return False
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _next_utc_start(value: datetime, *, immediate_tolerance_seconds: float = 5.0) -> datetime:
    """Use this midnight only when we're already within five seconds of it."""
    stamp = _aware_utc(value, "current_time")
    midnight = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (stamp - midnight).total_seconds()
    if elapsed <= immediate_tolerance_seconds:
        return midnight
    return midnight + timedelta(days=1)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _build_commands(run_dir: Path, python_executable: str = sys.executable) -> dict[str, list[str]]:
    """Build fixed, public-data-only commands for the canonical BTC/ETH pair."""
    root = Path(run_dir).resolve()
    kline_dir = root / "kline"
    quote_dir = root / "bookticker"
    kline = [
        python_executable, "-m", "bot_obrero.prospective_capture", "capture",
        "--output-dir", str(kline_dir),
        "--symbols", "BTCUSDT", "ETHUSDT",
        "--interval", "1h",
        "--duration-seconds", str(KLINE_DURATION_SECONDS),
        "--grace-seconds", str(KLINE_GRACE_SECONDS),
        "--no-align-next-utc-midnight",
    ]
    bookticker = [
        python_executable, "-m", "bot_obrero.prospective_book_ticker_capture", "capture",
        "--output-dir", str(quote_dir),
        "--symbols", "BTCUSDT", "ETHUSDT",
        "--duration-seconds", str(BOOK_TICKER_DURATION_SECONDS),
        "--grace-seconds", str(DEFAULT_CAPTURE_GRACE_SECONDS),
        "--no-align-next-utc-midnight",
    ]
    return {"kline": kline, "bookticker": bookticker}


def _stop_processes(processes: Mapping[str, Any]) -> None:
    active = [process for process in processes.values() if process.poll() is None]
    for process in active:
        try:
            process.terminate()
        except (OSError, ProcessLookupError):
            pass
    for process in active:
        try:
            process.wait(timeout=10)
        except (subprocess.TimeoutExpired, TimeoutError):
            try:
                process.kill()
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=10)
            except (subprocess.TimeoutExpired, TimeoutError):
                pass


def run_capture_pair(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    python_executable: str = sys.executable,
    monitor_interval_seconds: float = DEFAULT_MONITOR_INTERVAL_SECONDS,
    align_to_utc_midnight: bool = True,
    popen_factory: PopenFactory = subprocess.Popen,
    source_provenance_resolver: Callable[[Path], Mapping[str, Any]] | None = None,
    clock: Clock = _now,
    sleep: Sleep = time.sleep,
) -> dict[str, Any]:
    """Run both collectors and return a durable pair manifest and validation result."""
    if type(python_executable) is not str or not python_executable.strip():
        raise ValueError("PYTHON_EXECUTABLE_INVALID")
    if not isinstance(monitor_interval_seconds, (int, float)) or isinstance(monitor_interval_seconds, bool):
        raise ValueError("MONITOR_INTERVAL_INVALID")
    if not 0 < monitor_interval_seconds <= 30:
        raise ValueError("MONITOR_INTERVAL_OUT_OF_RANGE")

    now = _aware_utc(clock(), "current_time")
    provenance_resolver = _capture_source_provenance if source_provenance_resolver is None else source_provenance_resolver
    source_provenance = dict(provenance_resolver(Path.cwd()))
    run_id = "btceth-pair-" + now.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = Path(output_dir).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir()
    pair_manifest_path = run_dir / "pair-manifest.json"

    commands = _build_commands(run_dir, python_executable)
    configuration = {
        "schema_version": SCHEMA_VERSION,
        "capture_kind": "PROSPECTIVE_BINANCE_SPOT_KLINE_AND_BOOK_TICKER_PAIR",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "interval": "1h",
        "target_duration_seconds": BOOK_TICKER_DURATION_SECONDS,
        "kline_duration_seconds": KLINE_DURATION_SECONDS,
        "bookticker_duration_seconds": BOOK_TICKER_DURATION_SECONDS,
        "pair_overlap_guard_seconds": PAIR_OVERLAP_GUARD_SECONDS,
        "kline_grace_seconds": KLINE_GRACE_SECONDS,
        "bookticker_grace_seconds": DEFAULT_CAPTURE_GRACE_SECONDS,
        "minimum_overlap_seconds": BOOK_TICKER_DURATION_SECONDS,
        "source": "binance-public:spot-websocket",
        "source_provenance": source_provenance,
        "trading_enabled": False,
    }
    scheduled_start = _next_utc_start(now) if align_to_utc_midnight else now
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "WAITING_FOR_UTC_MIDNIGHT" if align_to_utc_midnight else "STARTING",
        "created_at": now.isoformat(),
        "scheduled_start_at": scheduled_start.isoformat(),
        "configuration": configuration,
        "configuration_sha256": hashlib.sha256(_canonical_json(configuration)).hexdigest(),
        "source_provenance": source_provenance,
        "logs": {"kline": "logs/kline.log", "bookticker": "logs/bookticker.log"},
        "kline_manifest": None,
        "bookticker_manifest": None,
        "validation": None,
        "child_exit_codes": {},
        "reason": None,
    }
    _atomic_write_json(pair_manifest_path, summary)

    processes: dict[str, Any] = {}
    handles: dict[str, Any] = {}
    exit_code = 2
    try:
        if source_provenance.get("status") != "VALID":
            reason = source_provenance.get("reason") or "SOURCE_PROVENANCE_INVALID"
            summary["status"] = "BLOCKED"
            summary["reason"] = reason
            raise _CapturePairBlocked(str(reason))
        while align_to_utc_midnight:
            remaining = (scheduled_start - _aware_utc(clock(), "current_time")).total_seconds()
            if remaining <= 0:
                break
            sleep(min(1.0, max(0.05, remaining)))

        launch_provenance = dict(provenance_resolver(Path.cwd()))
        summary["source_provenance_at_launch"] = launch_provenance
        if not _same_source_revision(source_provenance, launch_provenance):
            summary["status"] = "BLOCKED"
            summary["reason"] = "CAPTURE_SOURCE_PROVENANCE_CHANGED_BEFORE_LAUNCH"
            raise _CapturePairBlocked(summary["reason"])

        start_observed_at = _aware_utc(clock(), "current_time")
        start_lateness = (start_observed_at - scheduled_start).total_seconds()
        if align_to_utc_midnight and (
            start_lateness < -0.25 or start_lateness > UTC_START_TOLERANCE_SECONDS
        ):
            summary["status"] = "BLOCKED"
            summary["reason"] = "UTC_START_WINDOW_MISSED"
            summary["actual_start_at"] = start_observed_at.isoformat()
            summary["start_lateness_seconds"] = round(start_lateness, 3)
            raise _CapturePairBlocked("UTC_START_WINDOW_MISSED")

        summary["status"] = "RUNNING"
        summary["actual_start_at"] = start_observed_at.isoformat()
        summary["start_lateness_seconds"] = round(start_lateness, 3)
        _atomic_write_json(pair_manifest_path, summary)

        for name in ("kline", "bookticker"):
            handles[name] = (logs_dir / f"{name}.log").open("x", encoding="utf-8", newline="\n")
            processes[name] = popen_factory(
                commands[name],
                cwd=str(Path.cwd()),
                stdout=handles[name],
                stderr=subprocess.STDOUT,
                shell=False,
            )

        # Child startup/imports must not move the capture start outside the day boundary.
        launch_finished_at = _aware_utc(clock(), "current_time")
        launch_lateness = (launch_finished_at - scheduled_start).total_seconds()
        if align_to_utc_midnight and launch_lateness > UTC_START_TOLERANCE_SECONDS:
            _stop_processes(processes)
            summary["status"] = "BLOCKED"
            summary["reason"] = "UTC_START_WINDOW_MISSED_DURING_LAUNCH"
            summary["launch_lateness_seconds"] = round(launch_lateness, 3)
            summary["child_exit_codes"] = {name: process.poll() for name, process in processes.items()}
            raise _CapturePairBlocked("UTC_START_WINDOW_MISSED_DURING_LAUNCH")

        while True:
            codes = {name: process.poll() for name, process in processes.items()}
            failed = {name: code for name, code in codes.items() if code is not None and code != 0}
            if failed:
                _stop_processes(processes)
                summary["status"] = "BLOCKED"
                summary["reason"] = "CHILD_CAPTURE_FAILED"
                summary["failed_children"] = failed
                break
            if len(codes) == 2 and all(code == 0 for code in codes.values()):
                summary["child_exit_codes"] = codes
                manifests = {
                    "kline": list((run_dir / "kline").glob("*/manifest.json")),
                    "bookticker": list((run_dir / "bookticker").glob("*/manifest.json")),
                }
                if any(len(paths) != 1 for paths in manifests.values()):
                    raise RuntimeError("EXPECTED_EXACTLY_ONE_MANIFEST_PER_CAPTURE")
                summary["kline_manifest"] = str(manifests["kline"][0].relative_to(run_dir))
                summary["bookticker_manifest"] = str(manifests["bookticker"][0].relative_to(run_dir))
                finish_provenance = dict(provenance_resolver(Path.cwd()))
                summary["source_provenance_at_finish"] = finish_provenance
                if not _same_source_revision(source_provenance, finish_provenance):
                    raise RuntimeError("CAPTURE_SOURCE_PROVENANCE_CHANGED_DURING_RUN")
                validation = validate_paired_manifests(
                    kline_manifest_path=manifests["kline"][0],
                    bookticker_manifest_path=manifests["bookticker"][0],
                    minimum_overlap_seconds=BOOK_TICKER_DURATION_SECONDS,
                )
                if validation.get("status") != "VALID":
                    raise RuntimeError("PAIRED_CAPTURE_VALIDATION_NOT_VALID")
                validation["kline"]["manifest_path"] = str(manifests["kline"][0].relative_to(run_dir))
                validation["bookticker"]["manifest_path"] = str(manifests["bookticker"][0].relative_to(run_dir))
                summary["status"] = "VALID"
                summary["validation"] = validation
                summary["reason"] = None
                exit_code = 0
                break
            sleep(float(monitor_interval_seconds))
    except _CapturePairBlocked as exc:
        summary["status"] = "BLOCKED"
        summary["reason"] = str(exc)
    except KeyboardInterrupt:
        _stop_processes(processes)
        summary["status"] = "BLOCKED"
        summary["reason"] = "INTERRUPTED_BY_OPERATOR"
        exit_code = 130
    except Exception as exc:
        _stop_processes(processes)
        summary["status"] = "BLOCKED"
        summary["reason"] = f"{type(exc).__name__}:{exc}"
    finally:
        summary["finished_at"] = _aware_utc(clock(), "current_time").isoformat()
        summary["child_exit_codes"] = {name: process.poll() for name, process in processes.items()}
        for handle in handles.values():
            try:
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
        # Replace the existing RUNNING summary atomically.
        temporary = pair_manifest_path.with_name(pair_manifest_path.name + ".tmp")
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(summary, stream, sort_keys=True, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, pair_manifest_path)
    summary["pair_manifest_path"] = str(pair_manifest_path)
    summary["exit_code"] = exit_code
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run paired prospective Binance Spot Kline + bookTicker capture; no trading."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture", help="wait for UTC midnight, capture paired datasets with overlap guard, then validate")
    capture.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    capture.add_argument("--monitor-interval-seconds", type=float, default=DEFAULT_MONITOR_INTERVAL_SECONDS)
    args = parser.parse_args(argv)
    try:
        result = run_capture_pair(
            output_dir=args.output_dir,
            monitor_interval_seconds=args.monitor_interval_seconds,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    if result.get("status") == "VALID":
        return 0
    return 130 if result.get("reason") == "INTERRUPTED_BY_OPERATOR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
