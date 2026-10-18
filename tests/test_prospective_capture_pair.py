from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot_obrero.prospective_capture_pair import (
    _build_commands,
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
