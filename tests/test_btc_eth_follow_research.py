from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.btc_eth_follow_research import (
    BTCETHResearchError, _config, _evaluation_plan, _filter_from_record,
    _filter_to_record, rolling_correlation_summary,
)
from bot_obrero.product_v2 import InstrumentFilters


def test_annual_evaluation_plan_has_8760_hourly_bars_and_three_60_day_windows():
    warmup, evaluation, validation, bounds = _evaluation_plan(
        90 * 24 + 365 * 24 + 3 * 60 * 24 + 1, 365
    )
    assert warmup == 2160
    assert evaluation == 8760
    assert validation == 1440
    assert bounds == ((10920, 12360), (12360, 13800), (13800, 15240))


def test_evaluation_plan_fails_closed_when_history_is_short():
    with pytest.raises(BTCETHResearchError):
        _evaluation_plan(9000, 365)


def test_filter_record_roundtrips_exact_decimal_and_market_lot_size_provenance():
    source = InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0.001"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0.01"), market_step_size=Decimal("0.01"),
        market_max_qty=Decimal("100"), market_lot_size_present=True,
    )
    decoded = _filter_from_record(_filter_to_record(source))
    assert decoded == source


def test_config_freezes_annual_evaluation_and_cost_scenarios():
    config = _config(365, Decimal("31"))
    assert config["evaluation_days"] == 365
    assert config["interval"] == "1h"
    assert config["initial_usdt"] == "31"
    assert [row["name"] for row in config["costs"]] == ["BASE", "STRESS_1", "STRESS_2"]


class _Candle:
    def __init__(self, close: str, start: datetime):
        self.close = Decimal(close)
        self.start = start


class _Item:
    def __init__(self, close: str, start: datetime):
        self.payload = _Candle(close, start)


def test_rolling_correlation_summary_reports_perfect_relationship_for_identical_returns():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    btc, eth = [], []
    price = Decimal("100")
    for index in range(24 * 100 + 1):
        price *= Decimal("1.001") if index % 3 else Decimal("0.999")
        stamp = start + timedelta(hours=index)
        btc.append(_Item(str(price), stamp))
        eth.append(_Item(str(price * Decimal("2")), stamp))
    report = rolling_correlation_summary(
        btc, eth, evaluation_start_index=24 * 90,
        evaluation_end_index=24 * 100, lookback_days=30,
    )
    assert report["samples"] > 0
    assert report["minimum"] == pytest.approx(1.0)
    assert report["median"] == pytest.approx(1.0)
    assert report["maximum"] == pytest.approx(1.0)
