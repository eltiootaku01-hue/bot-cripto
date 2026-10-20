from __future__ import annotations

import json

import pytest

from bot_obrero import operator_cli


@pytest.mark.parametrize(
    ("command", "module_name"),
    [
        ("analyze", "analysis_cli"),
        ("backtest", "virtual_backtest"),
        ("research-btc-eth", "btc_eth_follow_research"),
        ("prospective-v2", "prospective_btc_eth_v2"),
    ],
)
def test_operator_cli_forwards_arguments_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    module_name: str,
) -> None:
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 23

    monkeypatch.setattr(getattr(operator_cli, module_name), "main", fake_main)

    assert operator_cli.main([command, "--sentinel", "value"]) == 23
    assert calls == [["--sentinel", "value"]]


def test_capture_command_delegates_to_pair_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(operator_cli.prospective_capture_pair, "main", fake_main)

    assert operator_cli.main(["capture-24h", "--output-dir", "evidence/pair"]) == 0
    assert calls == [["capture", "--output-dir", "evidence/pair"]]


def test_status_reports_no_trade_and_implemented_but_unrun_prospective_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert operator_cli.main(["status"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["strategy_decision"] == "NO_TRADE"
    assert output["v2_protocol"] == "IMPLEMENTED / NOT RUN"
    assert output["candidate_verdict"] == "NO_ROBUST_EDGE_FOUND"
    top1 = output["periodic_top1_research_candidate"]
    assert top1["cli_profile"] == "eth-bbmr-top1"
    assert top1["cadence"] == "SIGNAL_DRIVEN_PERIODIC_NOT_FORCED"
    assert top1["runtime_promotion"] == "NOT_ALLOWED"
    assert top1["october_2026_isolated_stress2_return_pct"] == -1.78
    assert output["live_execution"] == "DISABLED_BY_PROJECT_POLICY"
    assert len(output["blocking_gates"]) >= 3


def test_root_help_lists_operator_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        operator_cli.main(["--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "capture-24h" in output
    assert "research-btc-eth" in output
    assert "prospective-v2" in output
    assert "backtest" in output
