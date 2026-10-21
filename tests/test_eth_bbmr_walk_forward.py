from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import argparse

from bot_obrero.eth_bbmr_walk_forward import (
    _build_timeline,
    _config,
    _strategy_action,
    _run_prefix_only,
    _signal_flags,
    EMA_RSI_STRATEGY,
    DONCHIAN_STRATEGY,
    parse_aware_datetime,
    validate_start_boundary,
)
from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from bot_obrero.virtual_backtest import BacktestError, run_virtual_backtest, _expected_next_start

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _eth_4h(index: int, close: str, *, open_price: str | None = None) -> MarketData:
    start = _START + timedelta(hours=4 * index)
    end = start + timedelta(hours=4) - timedelta(milliseconds=1)
    price = Decimal(close)
    opening = Decimal(open_price) if open_price is not None else price
    candle = Candle(
        start=start, end=end, timeframe="4h",
        open=opening, high=max(opening, price), low=min(opening, price), close=price,
        volume=Decimal("1"), candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE, finality=CandleFinality.UNKNOWN,
    )
    instrument = InstrumentIdentity(
        instrument_id="binance:SPOT:ETHUSDT", symbol="ETH/USDT", market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT, base_asset="ETH", quote_asset="USDT",
    )
    source = SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE")
    return MarketData(
        instrument=instrument, source=source, data_type="CANDLE",
        observed_at=start, received_at=end, available_at=None,
        payload=candle, quality=DataQuality.VALID, completeness=DataCompleteness.UNKNOWN,
    )


def _candidate_test_series() -> list[MarketData]:
    baseline = [
        str(Decimal("100") + Decimal(index) * Decimal("50") / Decimal("120"))
        for index in range(121)
    ] + ["200"] * 59
    prices = baseline + ["190", "220", "220", "221", "222", "223", "224", "225"]
    return [
        _eth_4h(
            index, close,
            open_price=("190" if index == 181 else "220" if index == 182 else close),
        )
        for index, close in enumerate(prices)
    ]


def test_start_timestamp_requires_timezone_and_4h_boundary():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_aware_datetime("2026-10-01T00:00:00")
    start = parse_aware_datetime("2026-09-30T21:00:00-03:00")
    assert start == datetime(2026, 10, 1, 0, tzinfo=timezone.utc)
    validate_start_boundary(start)
    with pytest.raises(BacktestError, match="align to a 4h"):
        validate_start_boundary(datetime(2026, 10, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (dict(zscore=Decimal("-0.7501"), close=Decimal("101"), ema=Decimal("100"), position=Decimal("0"), recent_entries=0, warmup_ready=True), "BUY_NEXT_OPEN"),
        (dict(zscore=Decimal("-0.75"), close=Decimal("101"), ema=Decimal("100"), position=Decimal("0"), recent_entries=0, warmup_ready=True), "WAIT_NO_SIGNAL"),
        (dict(zscore=Decimal("-1"), close=Decimal("100"), ema=Decimal("100"), position=Decimal("0"), recent_entries=0, warmup_ready=True), "WAIT_NO_SIGNAL"),
        (dict(zscore=Decimal("-1"), close=Decimal("101"), ema=Decimal("100"), position=Decimal("0"), recent_entries=5, warmup_ready=True), "BLOCK_ENTRY_CAP"),
        (dict(zscore=Decimal("2.0001"), close=Decimal("101"), ema=Decimal("100"), position=Decimal("0.5"), recent_entries=1, warmup_ready=True), "SELL_NEXT_OPEN"),
        (dict(zscore=Decimal("1"), close=Decimal("100"), ema=Decimal("100"), position=Decimal("0.5"), recent_entries=1, warmup_ready=True), "SELL_NEXT_OPEN"),
        (dict(zscore=Decimal("1"), close=Decimal("101"), ema=Decimal("100"), position=Decimal("0.5"), recent_entries=1, warmup_ready=True), "HOLD_POSITION"),
        (dict(zscore=None, close=Decimal("100"), ema=None, position=Decimal("0"), recent_entries=0, warmup_ready=False), "WAIT_WARMUP"),
    ],
)
def test_one_tick_decision_rules_are_frozen_and_explicit(values, expected):
    assert _strategy_action(**values) == expected


def test_clock_driven_timeline_processes_a_signal_only_on_prefix_and_fills_next_open():
    all_items = _candidate_test_series()
    evaluation_start = all_items[180].payload.start
    evaluation_end = all_items[183].payload.start
    # Only the first three candles belong to this simulation; the rest are referee-only future data.
    eval_items = all_items[180:183]
    config = _config(Decimal("60"), (Decimal("0"), Decimal("0"), Decimal("0")), len(eval_items))
    result = run_virtual_backtest(
        all_items, config,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    clocked_result = _run_prefix_only(all_items=all_items, eval_items=eval_items, config=config)
    assert clocked_result == result
    timeline = _build_timeline(
        all_items=all_items,
        eval_items=eval_items,
        config=config,
        result=clocked_result,
    )
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert timeline[0]["decision"] == "BUY_NEXT_OPEN"
    assert timeline[0]["future_candles_passed_to_decision"] == 0
    assert timeline[0]["visible_history_end_utc"] == eval_items[0].payload.end.isoformat()
    assert timeline[1]["executions_at_open"][0]["side"] == "BUY"
    assert timeline[1]["previous_close_signal_execution_status"] == "FILLED_AT_THIS_OPEN"
    assert timeline[1]["decision"] == "SELL_NEXT_OPEN"
    assert timeline[2]["executions_at_open"][0]["side"] == "SELL"
    assert timeline[2]["future_candles_passed_to_decision"] == 0


def test_decision_trace_is_invariant_to_prices_strictly_after_cutoff():
    baseline = _candidate_test_series()
    perturbed = list(baseline)
    for index, close in ((183, "900"), (184, "0.05"), (185, "700"), (186, "0.01"), (187, "5000")):
        perturbed[index] = _eth_4h(index, close)
    start = baseline[180].payload.start
    end = baseline[183].payload.start
    eval_items = baseline[180:183]
    config = _config(Decimal("60"), (Decimal("0"), Decimal("0"), Decimal("0")), len(eval_items))
    baseline_result = run_virtual_backtest(baseline, config, evaluation_start=start, evaluation_end=end)
    perturbed_result = run_virtual_backtest(perturbed, config, evaluation_start=start, evaluation_end=end)
    baseline_clocked = _run_prefix_only(all_items=baseline, eval_items=eval_items, config=config)
    perturbed_clocked = _run_prefix_only(all_items=perturbed, eval_items=eval_items, config=config)
    baseline_trace = _build_timeline(
        all_items=baseline, eval_items=eval_items, config=config, result=baseline_clocked,
    )
    perturbed_trace = _build_timeline(
        all_items=perturbed, eval_items=eval_items, config=config, result=perturbed_clocked,
    )
    assert baseline_result == perturbed_result
    assert baseline_clocked == perturbed_clocked
    assert baseline_trace == perturbed_trace


def _donchian_test_series() -> list[MarketData]:
    closes = ["100"] * 30 + ["120", "80"] + ["80"] * 34
    return [
        _eth_4h(
            index, close,
            open_price=("121" if index == 31 else "79" if index == 32 else close),
        )
        for index, close in enumerate(closes)
    ]


def test_donchian_20_10_is_frozen_and_causal_and_matches_reference_accounting():
    all_items = _donchian_test_series()
    eval_items = all_items[30:58]
    config = _config(
        Decimal("60"), (Decimal("0"), Decimal("0"), Decimal("0")),
        len(eval_items), strategy=DONCHIAN_STRATEGY,
    )
    start = eval_items[0].payload.start
    end = all_items[58].payload.start
    batch = run_virtual_backtest(all_items, config, evaluation_start=start, evaluation_end=end)
    clocked = _run_prefix_only(all_items=all_items, eval_items=eval_items, config=config)
    assert clocked == batch
    assert [fill.side for fill in clocked.fills] == ["BUY", "SELL"]
    assert clocked.fills[0].timestamp == eval_items[1].payload.start
    assert "Donchian 20-bar high breakout" in clocked.fills[0].reason
    assert clocked.fills[1].timestamp == eval_items[2].payload.start
    assert "Donchian 10-bar low breakdown" in clocked.fills[1].reason

    perturbed = list(all_items)
    for index, close in ((58, "900"), (59, "0.01"), (60, "800"), (61, "0.02")):
        perturbed[index] = _eth_4h(index, close)
    changed = _run_prefix_only(all_items=perturbed, eval_items=eval_items, config=config)
    assert changed == clocked


def test_ema_rsi_frozen_alternative_matches_reference_on_same_evaluation_window():
    closes = [
        str(Decimal("100") + Decimal(index) / Decimal("8") + Decimal((index % 9) - 4) * Decimal("1.7"))
        for index in range(100)
    ]
    all_items = [_eth_4h(index, close) for index, close in enumerate(closes)]
    eval_items = all_items[40:68]
    config = _config(
        Decimal("60"), (Decimal("0.001"), Decimal("10"), Decimal("5")),
        len(eval_items), strategy=EMA_RSI_STRATEGY,
    )
    start = eval_items[0].payload.start
    end = all_items[68].payload.start
    batch = run_virtual_backtest(all_items, config, evaluation_start=start, evaluation_end=end)
    clocked = _run_prefix_only(all_items=all_items, eval_items=eval_items, config=config)
    assert clocked == batch


def test_strategy_signals_use_only_prior_candles_for_donchian_channel():
    items = _donchian_test_series()
    visible = items[:31]
    config = _config(
        Decimal("60"), (Decimal("0"), Decimal("0"), Decimal("0")), 28,
        strategy=DONCHIAN_STRATEGY,
    )
    accepted = visible[-1]
    previous_values = {"fast_ema": Decimal("100"), "slow_ema": Decimal("100"), "rsi": Decimal("50")}
    current_values = dict(previous_values)
    buy, sell, entry_reason, exit_reason = _signal_flags(
        config, current_values, previous_values, accepted.payload, visible,
    )
    assert buy is True
    assert sell is False
    assert "current candle excluded" in entry_reason
    assert "current candle excluded" in exit_reason
