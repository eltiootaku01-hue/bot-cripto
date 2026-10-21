
from __future__ import annotations

import pytest

from research.tp_sl_loss_decomposition_v1 import (
    EXPECTED_COSTED_SUMMARY_SHA256,
    EXPECTED_COSTED_TRADES_SHA256,
    EXPECTED_DATASET_SHA256,
    EXPECTED_FORECAST_SHA256,
    ZERO_COST,
    add_trade_stats,
    empty_trade_stats,
    summary_aggregate,
)


def test_zero_cost_control_changes_only_friction_parameters() -> None:
    assert ZERO_COST == {"fee_rate": 0.0, "spread_bps": 0.0, "slippage_bps": 0.0}
    assert EXPECTED_DATASET_SHA256 == "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
    assert EXPECTED_FORECAST_SHA256 == "b8f555028791d0a9d2f93dc428acf3882d5ec3488ac56221df3136e4841c3d00"
    assert len(EXPECTED_COSTED_SUMMARY_SHA256) == 64
    assert len(EXPECTED_COSTED_TRADES_SHA256) == 64


def test_ledger_decomposition_keeps_gross_price_proxy_separate_from_net_pnl() -> None:
    stats = empty_trade_stats()
    row = {
        "outcome": "SL", "exit_reason": "SL_BOTH_TOUCHED_CONSERVATIVE",
        "quantity": "2.0", "exit_trigger_raw": "99.5", "entry_open_raw": "100.0",
        "net_pnl_usdt": "-1.5", "total_fees_usdt": "0.2",
        "gross_price_return_pct": "-0.5", "net_return_on_entry_outlay_pct": "-0.75",
    }
    add_trade_stats(stats, row)
    assert stats["trades"] == 1
    assert stats["sl"] == 1
    assert stats["both_barriers_same_candle"] == 1
    assert stats["gross_price_pnl_proxy_usdt"] == pytest.approx(-1.0)
    assert stats["execution_friction_residual_usdt"] == pytest.approx(0.5)
    assert stats["fees_usdt"] == pytest.approx(0.2)
    assert stats["net_pnl_usdt"] == pytest.approx(-1.5)


def test_portfolio_summary_aggregates_sum_pnl_and_average_return_separately() -> None:
    rows = [
        {
            "initial_capital_usdt": "60", "return_pct": "-10", "net_pnl_usdt": "-6",
            "executed_trades": "5", "tp_exits": "1", "sl_exits": "3", "time_exits": "1",
            "total_fees_usdt": "0.3", "max_drawdown_hourly_close_pct": "-12",
        },
        {
            "initial_capital_usdt": "100", "return_pct": "0", "net_pnl_usdt": "0",
            "executed_trades": "0", "tp_exits": "0", "sl_exits": "0", "time_exits": "0",
            "total_fees_usdt": "0", "max_drawdown_hourly_close_pct": "0",
        },
    ]
    result = summary_aggregate(rows, "BASE", "BASE")
    assert result["portfolio_count"] == 2
    assert result["initial_capital_total_usdt"] == pytest.approx(160.0)
    assert result["sum_net_pnl_usdt"] == pytest.approx(-6.0)
    assert result["mean_portfolio_return_pct"] == pytest.approx(-5.0)
    assert result["portfolio_ensemble_return_on_initial_capital_pct"] == pytest.approx(-3.75)
    assert result["trades"] == 5
    assert result["tp"] + result["sl"] + result["time_exits"] == result["trades"]
