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
    _same_source_revision,
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
    # This unit test exercises child-process failure handling, not Git provenance.
    # Keep provenance stable so concurrent pushes to origin/main cannot make this
    # test fail before the fake child process is inspected.
    clean_provenance = {
        "status": "VALID",
        "reason": None,
        "git_head_sha": "a" * 40,
        "origin_main_sha": "a" * 40,
        "live_main_sha": "a" * 40,
        "branch": "main",
        "source_status_entries": [],
    }
    result = run_capture_pair(
        output_dir=tmp_path,
        python_executable="python",
        align_to_utc_midnight=False,
        source_provenance_resolver=lambda _cwd: clean_provenance,
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
        SimpleNamespace(returncode=0, stdout=head + "\trefs/heads/main\n"),
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
    assert len(calls) == 6
    assert calls[4][0:2] == ["git", "ls-remote"]


def test_source_provenance_blocks_modified_capture_sources(tmp_path):
    head = "b" * 40
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout=head + "\trefs/heads/main\n"),
        SimpleNamespace(returncode=0, stdout=" M bot_obrero/prospective_capture_pair.py\n"),
    ])

    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )
    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_SOURCE_WORKTREE_DIRTY"
    assert provenance["git_head_sha"] == head
    assert provenance["source_status_entries"] == [" M bot_obrero/prospective_capture_pair.py"]


def test_source_provenance_blocks_stale_local_origin_main_when_remote_advances(tmp_path):
    head = "a" * 40
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout=head + "\n"),
        SimpleNamespace(returncode=0, stdout="b" * 40 + "\trefs/heads/main\n"),
        SimpleNamespace(returncode=0, stdout=""),
    ])

    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )

    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_ORIGIN_MAIN_TRACKING_STALE"
    assert provenance["git_head_sha"] == head
    assert provenance["origin_main_sha"] == head
    assert provenance["live_main_sha"] == "b" * 40


def test_source_provenance_blocks_execution_outside_repository_root(tmp_path):
    outputs = iter([
        SimpleNamespace(returncode=0, stdout=str(tmp_path.parent) + "\n"),
        SimpleNamespace(returncode=0, stdout="c" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout="main\n"),
        SimpleNamespace(returncode=0, stdout="c" * 40 + "\n"),
        SimpleNamespace(returncode=0, stdout="c" * 40 + "\trefs/heads/main\n"),
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
        SimpleNamespace(returncode=0, stdout="e" * 40 + "\trefs/heads/main\n"),
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
        SimpleNamespace(returncode=0, stdout="1" * 40 + "\trefs/heads/main\n"),
        SimpleNamespace(returncode=0, stdout=""),
    ])
    provenance = _capture_source_provenance(
        tmp_path, run_command=lambda _command, **_kwargs: next(outputs)
    )
    assert provenance["status"] == "BLOCKED"
    assert provenance["reason"] == "CAPTURE_MAIN_NOT_UP_TO_DATE"
    assert provenance["git_head_sha"] == "f" * 40
    assert provenance["origin_main_sha"] == "1" * 40



def test_source_revision_match_rejects_changed_revision_or_non_clean_state():
    clean = {
        "status": "VALID",
        "git_head_sha": "a" * 40,
        "origin_main_sha": "a" * 40,
        "live_main_sha": "a" * 40,
        "branch": "main",
        "source_status_entries": [],
    }
    assert _same_source_revision(clean, dict(clean)) is True

    changed_sha = {**clean, "git_head_sha": "b" * 40}
    assert _same_source_revision(clean, changed_sha) is False

    changed_origin = {**clean, "origin_main_sha": "c" * 40}
    assert _same_source_revision(clean, changed_origin) is False

    changed_live_main = {**clean, "live_main_sha": "d" * 40}
    assert _same_source_revision(clean, changed_live_main) is False

    dirty = {**clean, "status": "BLOCKED", "source_status_entries": [" M bot_obrero/"]}
    assert _same_source_revision(clean, dirty) is False


def test_pair_capture_rechecks_source_after_wait_and_blocks_before_opening_sockets(tmp_path):
    clean = {
        "status": "VALID",
        "git_head_sha": "a" * 40,
        "origin_main_sha": "a" * 40,
        "live_main_sha": "a" * 40,
        "branch": "main",
        "source_status_entries": [],
    }
    changed = {
        "status": "BLOCKED",
        "reason": "CAPTURE_SOURCE_WORKTREE_DIRTY",
        "git_head_sha": "a" * 40,
        "origin_main_sha": "a" * 40,
        "live_main_sha": "a" * 40,
        "branch": "main",
        "source_status_entries": [" M bot_obrero/prospective_capture_pair.py"],
    }
    provenance_values = iter([clean, changed])
    result = run_capture_pair(
        output_dir=tmp_path,
        align_to_utc_midnight=False,
        source_provenance_resolver=lambda _cwd: next(provenance_values),
        popen_factory=lambda *_args, **_kwargs: pytest.fail("socket collector must not start"),
        clock=lambda: datetime(2026, 10, 18, 0, 0, 1, tzinfo=UTC),
        sleep=lambda _seconds: None,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CAPTURE_SOURCE_PROVENANCE_CHANGED_BEFORE_LAUNCH"
    manifest = json.loads(Path(result["pair_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source_provenance_at_launch"]["status"] == "BLOCKED"


def test_pair_capture_blocks_if_source_revision_changes_after_children_finish(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("bot_obrero.prospective_capture_pair")
    clean = {
        "status": "VALID",
        "git_head_sha": "a" * 40,
        "origin_main_sha": "a" * 40,
        "live_main_sha": "a" * 40,
        "branch": "main",
        "source_status_entries": [],
    }
    changed = {
        "status": "VALID",
        "git_head_sha": "b" * 40,
        "origin_main_sha": "b" * 40,
        "live_main_sha": "b" * 40,
        "branch": "main",
        "source_status_entries": [],
    }
    provenance_values = iter([clean, clean, changed])

    def fake_popen(command, **_kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        manifest = output_dir / "synthetic-run" / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}\n", encoding="utf-8")
        return FakeProcess(0)

    monkeypatch.setattr(
        module,
        "validate_paired_manifests",
        lambda **_kwargs: pytest.fail("changed source must block before dataset validation"),
    )
    result = run_capture_pair(
        output_dir=tmp_path,
        align_to_utc_midnight=False,
        source_provenance_resolver=lambda _cwd: next(provenance_values),
        popen_factory=fake_popen,
        clock=lambda: datetime(2026, 10, 18, 0, 0, 1, tzinfo=UTC),
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "RuntimeError:CAPTURE_SOURCE_PROVENANCE_CHANGED_DURING_RUN"
    assert result["validation"] is None
    assert result["source_provenance_at_finish"]["git_head_sha"] == "b" * 40
    persisted = json.loads(Path(result["pair_manifest_path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "BLOCKED"
    assert persisted["validation"] is None
