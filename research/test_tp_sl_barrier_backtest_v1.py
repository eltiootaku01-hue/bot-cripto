
from __future__ import annotations

import pytest

from research.tp_sl_barrier_backtest_v1 import (
    COSTS,
    POSITION_FRACTION,
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    barrier_exit,
    max_drawdown_pct,
)


def test_same_ohlc_bar_hits_both_barriers_and_stop_wins_conservatively() -> None:
    result = barrier_exit(
        open_price=100.0, high=102.0, low=98.0, close=101.0,
        take_profit=101.0, stop_loss=99.5, position_open_before_bar=True,
    )
    assert result == (99.5, "SL_BOTH_TOUCHED_CONSERVATIVE")


def test_stop_gap_executes_at_worse_bar_open() -> None:
    result = barrier_exit(
        open_price=98.0, high=101.0, low=97.5, close=99.0,
        take_profit=101.0, stop_loss=99.5, position_open_before_bar=True,
    )
    assert result == (98.0, "SL_GAP")


def test_target_gap_does_not_credit_improved_open_price() -> None:
    result = barrier_exit(
        open_price=102.0, high=103.0, low=100.5, close=102.2,
        take_profit=101.0, stop_loss=99.5, position_open_before_bar=True,
    )
    assert result == (101.0, "TP_GAP_CONSERVATIVE")


def test_bar_without_barrier_touch_returns_none() -> None:
    assert barrier_exit(100.0, 100.8, 99.6, 100.2, 101.0, 99.5, True) is None


def test_frozen_trade_and_cost_contracts() -> None:
    assert TAKE_PROFIT_PCT == pytest.approx(0.01)
    assert STOP_LOSS_PCT == pytest.approx(0.005)
    assert POSITION_FRACTION == pytest.approx(0.95)
    assert set(COSTS) == {"BASE", "STRESS_1", "STRESS_2"}
    assert COSTS["BASE"]["fee_rate"] == pytest.approx(0.001)
    assert COSTS["STRESS_2"]["fee_rate"] == pytest.approx(0.002)


def test_drawdown_is_peak_to_trough_on_equity_curve() -> None:
    assert max_drawdown_pct([100.0, 110.0, 99.0, 105.0]) == pytest.approx(-10.0)
    assert max_drawdown_pct([60.0, 61.0, 62.0]) == pytest.approx(0.0)
