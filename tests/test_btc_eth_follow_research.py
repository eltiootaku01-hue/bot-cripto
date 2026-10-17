from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.btc_eth_follow_research import (
    BTC, ETH, BTCETHResearchError, COSTS, _config, _evaluation_plan,
    _filter_from_record, _filter_to_record, _load_bundle, _make_bundle,
    _print_result, _require_market_filters, _run_result, rolling_correlation_summary,
)
from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from bot_obrero.product_v2 import InstrumentFilters, historical_btc_entry_admission


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



def test_zero_market_minimum_is_accepted_when_other_market_constraints_are_valid():
    filters = InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0.001"), min_notional=Decimal("5"),
        max_qty=Decimal("1000"), market_min_qty=Decimal("0"),
        market_step_size=Decimal("0.001"), market_max_qty=Decimal("100"),
        market_lot_size_present=True,
    )
    _require_market_filters("ETHUSDT", filters)



def test_zero_market_fields_fall_back_to_valid_lot_size_constraints():
    filters = InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0.001"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0"), market_step_size=Decimal("0"),
        market_max_qty=Decimal("0"), market_lot_size_present=True,
    )
    _require_market_filters("ETHUSDT", filters)


def test_zero_market_fields_without_fallback_are_blocked():
    filters = InstrumentFilters(
        min_qty=None, step_size=None, min_notional=Decimal("5"), max_qty=None,
        market_min_qty=Decimal("0"), market_step_size=Decimal("0"),
        market_max_qty=Decimal("0"), market_lot_size_present=True,
    )
    with pytest.raises(BTCETHResearchError, match="MARKET_LOT_SIZE_INVALID_OR_INCOMPLETE"):
        _require_market_filters("ETHUSDT", filters)


def _paired_market_history(*, bars: int, cutoff: int, perturb_future: bool = False):
    """Create fully closed, temporally aligned synthetic BTC/ETH hourly histories."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    btc_price, eth_price = Decimal("30000"), Decimal("2000")
    btc_pattern = tuple(map(Decimal, ("0.0010", "-0.0007", "0.0014", "-0.0011", "0.0005", "-0.0002", "0.0009")))
    noise = tuple(map(Decimal, ("0.00005", "-0.00003", "0.00002", "-0.00004", "0.00001")))
    btc_items, eth_items = [], []

    def make_item(symbol: str, index: int, prior: Decimal, close: Decimal, stamp: datetime):
        end = stamp + timedelta(hours=1) - timedelta(milliseconds=1)
        instrument = InstrumentIdentity(
            instrument_id=f"binance:SPOT:{symbol.replace('/', '')}",
            symbol=symbol,
            market="SPOT",
            instrument_type=InstrumentType.CRYPTO_SPOT,
            base_asset=symbol.split("/")[0],
            quote_asset=symbol.split("/")[1],
        )
        candle = Candle(
            start=stamp,
            end=end,
            timeframe="1h",
            open=prior,
            high=max(prior, close) + Decimal("1"),
            low=min(prior, close) - Decimal("1"),
            close=close,
            volume=Decimal("1"),
            candle_state=CandleState.CLOSED,
            completeness=DataCompleteness.COMPLETE,
            finality=CandleFinality.FINAL,
        )
        return MarketData(
            instrument=instrument,
            source=SourceIdentity("binance-spot-rest", "binance", "BINANCE"),
            data_type="CANDLE",
            observed_at=end,
            received_at=end + timedelta(seconds=1),
            # The fixture does not claim point-in-time historical availability.
            available_at=None,
            payload=candle,
            quality=DataQuality.VALID,
            completeness=DataCompleteness.UNKNOWN,
            market_data_id=f"{symbol.replace('/', '')}-1h-{index:05d}",
        )

    btc_previous, eth_previous = btc_price, eth_price
    for index in range(bars):
        if index:
            btc_return = btc_pattern[(index - 1) % len(btc_pattern)]
            eth_return = btc_return * Decimal("0.82") + noise[(index - 1) % len(noise)]
            if perturb_future and index > cutoff:
                # Only the unseen suffix differs. The prefix through cutoff is byte-for-byte equal.
                btc_return = Decimal("0.035") if index % 2 else Decimal("-0.027")
                eth_return = Decimal("-0.021") if index % 3 else Decimal("0.029")
            btc_previous, eth_previous = btc_price, eth_price
            btc_price *= Decimal("1") + btc_return
            eth_price *= Decimal("1") + eth_return
        else:
            btc_previous, eth_previous = btc_price, eth_price
        stamp = start + timedelta(hours=index)
        btc_items.append(make_item("BTC/USDT", index, btc_previous, btc_price, stamp))
        eth_items.append(make_item("ETH/USDT", index, eth_previous, eth_price, stamp))
    return btc_items, eth_items


def test_historical_btc_gate_is_invariant_to_prices_after_the_decision_cutoff():
    """A paired BTC→ETH decision may only see the prefix ending at its own bar."""
    cutoff = 2165  # >90 days of 1h history (2160 bars) are available at decision time.
    btc, eth = _paired_market_history(bars=2180, cutoff=cutoff, perturb_future=False)
    changed_btc, changed_eth = _paired_market_history(bars=2180, cutoff=cutoff, perturb_future=True)

    # Assert the fixture only changes the suffix; the decision-time prefix is identical.
    assert [x.payload.close for x in btc[:cutoff + 1]] == [
        x.payload.close for x in changed_btc[:cutoff + 1]
    ]
    assert [x.payload.close for x in eth[:cutoff + 1]] == [
        x.payload.close for x in changed_eth[:cutoff + 1]
    ]
    assert any(x.payload.close != y.payload.close for x, y in zip(
        btc[cutoff + 1:], changed_btc[cutoff + 1:]
    ))
    gate = lambda candidate, benchmark: historical_btc_entry_admission(
        candidate, benchmark,
        fast_ema_period=12,
        slow_ema_period=26,
        rsi_period=14,
        rsi_buy_min=Decimal("50"),
        minimum_corr30d=Decimal("0.50"),
        minimum_corr90d=Decimal("0.60"),
        periods_per_day=24,
    )
    baseline_decision = gate(eth, btc)(cutoff, ())
    perturbed_decision = gate(changed_eth, changed_btc)(cutoff, ())
    assert baseline_decision == perturbed_decision



def _annual_research_test_filters() -> InstrumentFilters:
    return InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0.001"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0.001"), market_step_size=Decimal("0.001"),
        market_max_qty=Decimal("1000"), market_lot_size_present=True,
    )


def _render_scope_result(scope: str, strategy: str, result) -> str:
    from contextlib import redirect_stdout
    from io import StringIO

    buffer = StringIO()
    with redirect_stdout(buffer):
        _print_result(scope, strategy, "BASE", result)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("scope_index", "scope_name"),
    [
        (0, "EVALUATION"),
        (1, "VALIDATION_1"),
        (2, "VALIDATION_2"),
        (3, "VALIDATION_3"),
    ],
)
def test_annual_scope_metrics_and_fill_report_are_invariant_to_later_price_suffix(
    scope_index: int, scope_name: str
):
    """Each report window must be unchanged when only later prices are altered.

    The dataset/configuration hashes are deliberately not compared here: a new
    suffix must produce a different dataset identity. This test compares only
    the scope-local metrics, entries, fills, fees and terminal liquidation.
    """
    scope_start = 90 * 24 + scope_index * 48
    scope_end = scope_start + 48
    # Keep 90 days of causal warm-up plus four sequential 48-hour scopes.
    bars = scope_end + 24
    btc, eth = _paired_market_history(bars=bars, cutoff=scope_end, perturb_future=False)
    future_btc, future_eth = _paired_market_history(
        bars=bars, cutoff=scope_end, perturb_future=True
    )
    assert [item.payload.close for item in btc[:scope_end + 1]] == [
        item.payload.close for item in future_btc[:scope_end + 1]
    ]
    assert [item.payload.close for item in eth[:scope_end + 1]] == [
        item.payload.close for item in future_eth[:scope_end + 1]
    ]
    assert any(
        left.payload.close != right.payload.close
        for left, right in zip(btc[scope_end + 1:], future_btc[scope_end + 1:])
    )
    assert any(
        left.payload.close != right.payload.close
        for left, right in zip(eth[scope_end + 1:], future_eth[scope_end + 1:])
    )

    filters = _annual_research_test_filters()
    filter_map = {BTC: filters, ETH: filters}

    def gate_for(candidate, benchmark):
        return historical_btc_entry_admission(
            candidate, benchmark,
            fast_ema_period=12, slow_ema_period=26, rsi_period=14,
            rsi_buy_min=Decimal("50"),
            minimum_corr30d=Decimal("0.50"),
            minimum_corr90d=Decimal("0.60"),
            periods_per_day=24,
        )

    variants = (
        ("BASELINE", None),
        ("BTC_GATED", "gate"),
    )
    for strategy, gated in variants:
        original_gate = gate_for(eth, btc) if gated else None
        changed_gate = gate_for(future_eth, future_btc) if gated else None
        original_result = _run_result(
            eth, filter_map[ETH], days=2, capital=Decimal("31"),
            fee=COSTS[0][1], spread=COSTS[0][2], slippage=COSTS[0][3],
            start_at=eth[scope_start].payload.start,
            end_at=eth[scope_end].payload.start, gate=original_gate,
        )
        changed_result = _run_result(
            future_eth, filter_map[ETH], days=2, capital=Decimal("31"),
            fee=COSTS[0][1], spread=COSTS[0][2], slippage=COSTS[0][3],
            start_at=future_eth[scope_start].payload.start,
            end_at=future_eth[scope_end].payload.start, gate=changed_gate,
        )
        assert original_result == changed_result
        assert _render_scope_result(scope_name, strategy, original_result) == _render_scope_result(
            scope_name, strategy, changed_result
        )


def test_dataset_bundle_rejects_boolean_schema_version_even_when_hashes_are_valid(tmp_path):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    btc, eth = _paired_market_history(bars=3, cutoff=1, perturb_future=False)
    filters = _annual_research_test_filters()
    bundle = _make_bundle(
        btc, eth, {BTC: filters, ETH: filters},
        days=365, capital=Decimal("31"), acquired_at=start,
    )
    # bool is a subclass of int in Python; JSON true compares equal to integer 1.
    bundle["schema_version"] = True
    path = tmp_path / "boolean-schema-version.json"
    path.write_text(__import__("json").dumps(bundle), encoding="utf-8")
    with pytest.raises(BTCETHResearchError, match="unsupported BTC-ETH bundle schema"):
        _load_bundle(path, days=365, capital=Decimal("31"))
