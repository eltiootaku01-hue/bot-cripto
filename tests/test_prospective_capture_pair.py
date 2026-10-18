from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from bot_obrero.prospective_capture_pair import (
    _build_commands,
    _capture_source_provenance,
    _next_utc_start,
    run_capture_pair,
)

UTC = timezone.utc


def test_next_utc_start_aligns_near_midnight_and_skips_to_next_day_after_tolerance():
    assert _next_utc_start(datetime(2026, 10, 18, 0, 0, 3, tzinfo=UTC)) == datetime(
        2026, 10, 18, 0, 0, tzinfo=UTC
    )
    assert _next_utc_start(datetime(2026, 10, 18, 0, 0, 6, tzinfo=UTC)) == datetime(
        2026, 10, 19, 0, 0, tzinfo=UTC
    )
    assert _next_utc_start(datetime(2026, 10, 18, 12, 0, tzinfo=UTC)) == datetime(
        2026, 10, 19, 0, 0, tzinfo=UTC
    )


def test_next_utc_start_requires_timezone_aware_clock():
    with pytest.raises(ValueError, match="timezone-aware"):
        _next_utc_start(datetime(2026, 10, 18, 0, 0))


def test_pair_commands_are_public_only_and_share_canonical_symbols_and_duration(tmp_path):
    commands = _build_commands(tmp_path / "pair", "python")
    assert set(commands) == {"kline", "bookticker"}
    for command in commands.values():
        assert command[0] == "python"
        assert "--symbols" in command
        symbol_index = command.index("--symbols")
        assert command[symbol_index + 1:symbol_index + 3] == ["BTCUSDT", "ETHUSDT"]
        assert "--no-align-next-utc-midnight" in command
        assert "--api-key" not in command
        assert "--secret" not in command
        assert "--live" not in command
    for command in commands.values():
        assert command[command.index("--duration-seconds") + 1] == "86400"
        assert command[command.index("--grace-seconds") + 1] == "300"


class FakeProcess:
    def __init__(self, initial_code: int | None):
        self.returncode = initial_code
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_failed_child_stops_the_other_capture_and_persists_blocked_pair_manifest(tmp_path):
    processes = iter([FakeProcess(2), FakeProcess(None)])
    started = []

    def fake_popen(command, **kwargs):
        process = next(processes)
        started.append((command, process))
        return process

    fixed_now = datetime(2026, 10, 18, 0, 0, 1, tzinfo=UTC)
    result = run_capture_pair(
        output_dir=tmp_path,
        python_executable="python",
        align_to_utc_midnight=False,
        popen_factory=fake_popen,
        clock=lambda: fixed_now,
        sleep=lambda _seconds: None,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CHILD_CAPTURE_FAILED"
    assert result["failed_children"] == {"kline": 2}
    assert len(started) == 2
    assert started[1][1].terminated is True
    assert started[1][1].waited is True
    manifest_path = Path(result["pair_manifest_path"])
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "BLOCKED"
    assert persisted["child_exit_codes"]["kline"] == 2
    assert persisted["child_exit_codes"]["bookticker"] == -15


def test_invalid_monitor_interval_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="MONITOR_INTERVAL"):
        run_capture_pair(output_dir=tmp_path, monitor_interval_seconds=0)



def test_source_provenance_binds_a_clean_repository_head_and_branch(tmp_path):
    head = "a" * 40
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout=""),
    ])
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return next(outputs)

    provenance = _capture_source_provenance(tmp_path, run_command=fake_run)
    assert provenance["status"] == "VALID"
    assert provenance["git_head_sha"] == head
    assert provenance["branch"] == "main"
    assert provenance["source_status_entries"] == []
    assert len(calls) == 5


def test_source_provenance_blocks_modified_capture_sources(tmp_path):
    head = "b" * 40
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout=" M bot_obrero/prospective_capture_pair.py\n"),
    ])

    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )
    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_SOURCE_WORKTREE_DIRTY"
    assert provenance["git_head_sha"] == head
    assert provenance["source_status_entries"] == [" M bot_obrero/prospective_capture_pair.py"]


def test_source_provenance_blocks_execution_outside_repository_root(tmp_path):
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path.parent) + "\n"),
        SimpleNamespace(returncode=0, stdout="c" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout="c" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout=""),
    ])
    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )
    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_REQUIRES_REPOSITORY_ROOT"


def test_pair_capture_fails_closed_and_persists_source_provenance_failure(tmp_path):
    result = run_capture_pair(
        output_dir=tmp_path,
        align_to_utc_midnight=False,
        source_provenance_resolver=lambda _cwd: {
            "status": "BLOCKED",
            "reason": "CAPTURE_SOURCE_WORKTREE_DIRTY",
            "git_head_sha": "d" * 40,
            "source_status_entries": [" M bot_obrero/prospective_capture_pair.py"],
        },
        popen_factory=lambda *_args, **_kwargs: pytest.fail("child process must not start"),
        clock=lambda: datetime(2026, 10, 18, 0, 0, 1, tzinfo=UTC),
        sleep=lambda _seconds: None,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CAPTURE_SOURCE_WORKTREE_DIRTY"
    manifest = Path(result["pair_manifest_path"])
    persisted = json.loads(manifest.read_text(encoding="utf-8"))
    assert persisted["source_provenance"]["status"] == "BLOCKED"
    assert persisted["source_provenance"]["git_head_sha"] == "d" * 40
    assert persisted["configuration_sha256"]



def test_source_provenance_blocks_capture_from_non_main_branch(tmp_path):
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        SimpleNamespace(returncode=0, stdout="e" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout="research/probe\n"),
        SimpleNamespace(returncode=0, stdout="e" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout=""),
    ])
    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )
    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_REQUIRES_MAIN_BRANCH"
    assert provenance["branch"] == "research/probe"
    assert provenance["git_head_sha"] == "e" * 40



def test_source_provenance_blocks_main_behind_or_ahead_of_origin_main(tmp_path):
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        SimpleNamespace(returncode=0, stdout="f" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout="1" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout=""),
    ])
    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )
    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_MAIN_NOT_UP_TO_DATE"
    assert provenance["git_head_sha"] == "f" * 40
    assert provenance["origin_main_sha"] == "1" * 40
