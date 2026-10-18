from __future__ import annotations

import math

import pytest

from research.backtest_valuation import mark_to_liquidation_equity


def test_liquidation_equity_deducts_modeled_exit_slippage_spread_and_fee():
    gross_mark = mark_to_liquidation_equity(5.0, 2.0, 100.0, 0.0, 0.0)
    net_mark = mark_to_liquidation_equity(5.0, 2.0, 100.0, 0.005, 0.001)

    assert gross_mark == 205.0
    assert net_mark == pytest.approx(5.0 + 2.0 * 100.0 * (1.0 - 0.005) * (1.0 - 0.001))
    assert net_mark < gross_mark


def test_stress_costs_cannot_improve_liquidation_equity():
    base = mark_to_liquidation_equity(1.55, 0.02, 1000.0, 0.001, 0.001)
    stress = mark_to_liquidation_equity(1.55, 0.02, 1000.0, 0.02, 0.002)
    assert stress < base


@pytest.mark.parametrize(
    ("cash", "quantity", "price", "adverse", "fee"),
    [
        (math.nan, 1.0, 100.0, 0.01, 0.001),
        (1.0, math.inf, 100.0, 0.01, 0.001),
        (1.0, -1.0, 100.0, 0.01, 0.001),
        (1.0, 1.0, 100.0, 1.0, 0.001),
        (1.0, 1.0, 100.0, 0.01, 1.0),
        (1.0, 1.0, 0.0, 0.01, 0.001),
        (True, 1.0, 100.0, 0.01, 0.001),
    ],
)
def test_liquidation_valuation_rejects_invalid_inputs(cash, quantity, price, adverse, fee):
    with pytest.raises(ValueError):
        mark_to_liquidation_equity(cash, quantity, price, adverse, fee)


def test_zero_position_returns_cash_without_inventing_market_value():
    assert mark_to_liquidation_equity(30.0, 0.0, 0.0, 0.05, 0.01) == 30.0
