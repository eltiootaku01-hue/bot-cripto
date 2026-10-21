"""Golden arithmetic contract tests for TP/SL net accounting (research-only).

These tests use synthetic OHLC candles and the existing simulator functions.
They do not load the frozen V1 forecast ledger or historical market-data artifact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from research.tp_sl_barrier_backtest_v1 import (
    COSTS,
    POSITION_FRACTION,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    simulate_portfolio,
)


# Golden vectors are calculated independently from the frozen contract:
# P_entry = 100 * (1 + e)
# trigger = P_entry * (1 + 1% for TP, 1 - 0.5% for SL)
# P_exit = trigger * (1 - e)
# quantity = 950 / (P_entry * (1 + fee_rate))
# entry_fee = quantity * P_entry * fee_rate
# exit_fee = quantity * P_exit * fee_rate
# net_pnl = quantity * P_exit - exit_fee - 950
# net_return = 100 * net_pnl / 950
# Expected values below are literal golden data, not calculated in the test.
GOLDEN = {
    "BASE": {
        "entry_fill": 100.100000000000,
        "take_profit_trigger": 101.101000000000,
        "stop_loss_trigger": 99.599500000000,
        "quantity": 9.481028462047446,
        "TP": {
            "exit_fill": 100.999899000000,
            "entry_fee_usdt": 0.9490509490509493,
            "exit_fee_usdt": 0.9575829170829173,
            "total_fees_usdt": 1.9066338661338667,
            "net_pnl_usdt": 6.625334165834147,
            "net_return_on_entry_outlay_pct": 0.6974035964035943,
            "portfolio_return_pct": 0.6625334165834147,
            "gross_price_return_pct": 1.101000000000,
            "gross_return_from_entry_fill_pct": 1.000000000000,
        },
        "SL": {
            "exit_fill": 99.499900500000,
            "entry_fee_usdt": 0.9490509490509493,
            "exit_fee_usdt": 0.9433613886113889,
            "total_fees_usdt": 1.8924123376623383,
            "net_pnl_usdt": -7.581972777222745,
            "net_return_on_entry_outlay_pct": -0.7981023976023940,
            "portfolio_return_pct": -0.7581972777222745,
            "gross_price_return_pct": -0.400500000000,
            "gross_return_from_entry_fill_pct": -0.500000000000,
        },
    },
    "STRESS_1": {
        "entry_fill": 100.225000000000,
        "take_profit_trigger": 101.227250000000,
        "stop_loss_trigger": 99.723875000000,
        "quantity": 9.469203781999990,
        "TP": {
            "exit_fill": 100.999488687500,
            "entry_fee_usdt": 0.9490509490509492,
            "exit_fee_usdt": 0.9563847402597404,
            "total_fees_usdt": 1.9054356893106896,
            "net_pnl_usdt": 5.428355519480533,
            "net_return_on_entry_outlay_pct": 0.5714058441558455,
            "portfolio_return_pct": 0.5428355519480533,
            "gross_price_return_pct": 1.227250000000,
            "gross_return_from_entry_fill_pct": 1.000000000000,
        },
        "SL": {
            "exit_fill": 99.499496281250,
            "entry_fee_usdt": 0.9490509490509492,
            "exit_fee_usdt": 0.9421810064935066,
            "total_fees_usdt": 1.8912319555444560,
            "net_pnl_usdt": -8.761174512987054,
            "net_return_on_entry_outlay_pct": -0.9222288961039004,
            "portfolio_return_pct": -0.8761174512987054,
            "gross_price_return_pct": -0.276125000000,
            "gross_return_from_entry_fill_pct": -0.500000000000,
        },
    },
    "STRESS_2": {
        "entry_fill": 100.450000000000,
        "take_profit_trigger": 101.454500000000,
        "stop_loss_trigger": 99.947750000000,
        "quantity": 9.438564384421799,
        "TP": {
            "exit_fill": 100.997954750000,
            "entry_fee_usdt": 1.8962075848303392,
            "exit_fee_usdt": 1.9065513972055890,
            "total_fees_usdt": 3.8027589820359280,
            "net_pnl_usdt": 1.3691472055890017,
            "net_return_on_entry_outlay_pct": 0.1441207584830528,
            "portfolio_return_pct": 0.13691472055890017,
            "gross_price_return_pct": 1.454500000000,
            "gross_return_from_entry_fill_pct": 1.000000000000,
        },
        "SL": {
            "exit_fill": 99.497985125000,
            "entry_fee_usdt": 1.8962075848303392,
            "exit_fee_usdt": 1.8782362774451096,
            "total_fees_usdt": 3.7744438622754490,
            "net_pnl_usdt": -12.760097554890194,
            "net_return_on_entry_outlay_pct": -1.3431681636726522,
            "portfolio_return_pct": -1.2760097554890194,
            "gross_price_return_pct": -0.052250000000,
            "gross_return_from_entry_fill_pct": -0.500000000000,
        },
    },
}


def _synthetic_candles(outcome: str) -> list[dict[str, object]]:
    """Five hourly candles: UP signal at candle 0, entry and exit at candle 1."""
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, object]] = []
    for index in range(5):
        start = origin + timedelta(hours=index)
        # The entry candle touches only the selected barrier, never both.
        if index == 1 and outcome == "TP":
            high, low = 102.0, 100.0
        elif index == 1 and outcome == "SL":
            high, low = 100.5, 99.0
        else:
            high, low = 100.5, 99.5
        rows.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "open": 100.0,
                "high": high,
                "low": low,
                "close": 100.0,
            }
        )
    return rows


@pytest.mark.parametrize(
    ("scenario", "outcome"),
    [(scenario, outcome) for scenario in GOLDEN for outcome in ("TP", "SL")],
)
def test_tp_sl_net_accounting_matches_golden_vectors(scenario: str, outcome: str) -> None:
    """Exercise actual entry/exit logic, price bases, and both-leg fee accounting."""
    assert TAKE_PROFIT_PCT == pytest.approx(0.01)
    assert STOP_LOSS_PCT == pytest.approx(0.005)
    assert POSITION_FRACTION == pytest.approx(0.95)

    rows = _synthetic_candles(outcome)
    index_by_end = {pd.Timestamp(row["end"]): index for index, row in enumerate(rows)}
    predictions = pd.DataFrame(
        [
            {
                "asset": "BTCUSDT",
                "strategy": "MOMENTUM_4H",
                "horizon_hours": 4,
                "decision_time_utc": pd.Timestamp(rows[0]["end"]),
                "prediction": "UP",
            }
        ]
    )

    summary, trades, _curve = simulate_portfolio(
        asset="BTCUSDT",
        strategy="MOMENTUM_4H",
        horizon=4,
        rows=rows,
        predictions=predictions,
        initial_capital=1000.0,
        cost_name=scenario,
        cost=COSTS[scenario],
        index_by_end=index_by_end,
        eval_start_idx=0,
    )

    assert len(trades) == 1
    trade = trades[0]
    expected = GOLDEN[scenario]
    expected_outcome = expected[outcome]

    assert trade["outcome"] == outcome
    assert trade["entry_open_raw"] == pytest.approx(100.0, abs=1e-10)
    assert trade["entry_fill"] == pytest.approx(expected["entry_fill"], abs=1e-9)
    assert trade["take_profit_trigger"] == pytest.approx(expected["take_profit_trigger"], abs=1e-9)
    assert trade["stop_loss_trigger"] == pytest.approx(expected["stop_loss_trigger"], abs=1e-9)
    assert trade["quantity"] == pytest.approx(expected["quantity"], abs=1e-9)
    assert trade["entry_cash_outlay_usdt"] == pytest.approx(950.0, abs=1e-8)

    expected_trigger = expected["take_profit_trigger"] if outcome == "TP" else expected["stop_loss_trigger"]
    assert trade["exit_trigger_raw"] == pytest.approx(expected_trigger, abs=1e-9)
    assert trade["exit_fill"] == pytest.approx(expected_outcome["exit_fill"], abs=1e-9)

    # Fee is charged on entry notional and exit notional, not on price change alone.
    assert trade["entry_fee_usdt"] == pytest.approx(expected_outcome["entry_fee_usdt"], abs=1e-9)
    assert trade["exit_fee_usdt"] == pytest.approx(expected_outcome["exit_fee_usdt"], abs=1e-9)
    assert trade["total_fees_usdt"] == pytest.approx(expected_outcome["total_fees_usdt"], abs=1e-9)
    assert trade["total_fees_usdt"] == pytest.approx(
        trade["entry_fee_usdt"] + trade["exit_fee_usdt"], abs=1e-9
    )

    # Current legacy field is gross vs raw entry open. The barrier return is based on entry_fill.
    gross_return_from_fill = 100.0 * (trade["exit_trigger_raw"] / trade["entry_fill"] - 1.0)
    assert gross_return_from_fill == pytest.approx(
        expected_outcome["gross_return_from_entry_fill_pct"], abs=1e-9
    )
    assert trade["gross_price_return_pct"] == pytest.approx(
        expected_outcome["gross_price_return_pct"], abs=1e-9
    )
    assert trade["gross_price_return_pct"] != pytest.approx(gross_return_from_fill, abs=1e-5)

    # Accounting identity: sell proceeds net of exit fee minus entry outlay incl. entry fee.
    expected_net_pnl = (
        trade["quantity"] * trade["exit_fill"]
        - trade["exit_fee_usdt"]
        - trade["entry_cash_outlay_usdt"]
    )
    assert trade["net_pnl_usdt"] == pytest.approx(expected_outcome["net_pnl_usdt"], abs=1e-8)
    assert trade["net_pnl_usdt"] == pytest.approx(expected_net_pnl, abs=1e-8)
    assert trade["net_return_on_entry_outlay_pct"] == pytest.approx(
        expected_outcome["net_return_on_entry_outlay_pct"], abs=1e-8
    )
    assert summary["executed_trades"] == 1
    assert summary["tp_exits"] == (1 if outcome == "TP" else 0)
    assert summary["sl_exits"] == (1 if outcome == "SL" else 0)
    assert summary["time_exits"] == 0
    assert summary["total_fees_usdt"] == pytest.approx(trade["total_fees_usdt"], abs=1e-9)
    assert summary["net_pnl_usdt"] == pytest.approx(trade["net_pnl_usdt"], abs=1e-8)
    # Portfolio return uses initial capital (1,000 USDT), not only the 950 USDT trade outlay.
    assert summary["initial_capital_usdt"] == pytest.approx(1000.0, abs=1e-10)
    assert summary["return_pct"] == pytest.approx(expected_outcome["portfolio_return_pct"], abs=1e-8)
    assert summary["return_pct"] == pytest.approx(
        100.0 * summary["net_pnl_usdt"] / summary["initial_capital_usdt"], abs=1e-8
    )
    assert summary["return_pct"] != pytest.approx(
        trade["net_return_on_entry_outlay_pct"], abs=1e-5
    )
    assert summary["final_equity_usdt"] - summary["initial_capital_usdt"] == pytest.approx(
        summary["net_pnl_usdt"], abs=1e-8
    )
