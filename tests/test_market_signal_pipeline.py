from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.market_data import (
    CANDLE_DATA_TYPE,
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    MarketData,
    SourceIdentity,
)
from bot_obrero.signal_trade_proposal_bridge import PaperProposalPolicy
from bot_obrero.market_signal_pipeline import (
    EmaRsiCrossoverConfig,
    MarketSignalPipelineStatus,
    STRATEGY_IDENTITY,
    STRATEGY_VERSION,
    generate_market_signal,
)
from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeSide

HOUR = timedelta(hours=1)
BAR = timedelta(hours=4)
EPSILON = timedelta(milliseconds=1)
INSTRUMENT = InstrumentIdentity(
    "binance:SPOT:BTCUSDT", "BTC/USDT", "SPOT",
    base_asset="BTC", quote_asset="USDT",
)
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def build_market_data(closes, *, now=NOW, finality_override=None, availability=True, timeframe="4h"):
    closes = tuple(Decimal(str(value)) for value in closes)
    first_start = now - timedelta(hours=4 * (len(closes) - 1), minutes=30)
    items = []
    previous_close = closes[0]
    for index, close in enumerate(closes):
        start = first_start + BAR * index
        end = start + BAR - EPSILON
        open_price = closes[0] if index == 0 else previous_close
        high = max(open_price, close) + Decimal("1")
        low = min(open_price, close) - Decimal("1")
        if low <= 0:
            low = min(open_price, close) / Decimal("2")
        finality = CandleFinality.FINAL
        if finality_override is not None and index == len(closes) - 1:
            finality = finality_override
        candle = Candle(
            start=start, end=end, timeframe=timeframe,
            open=open_price, high=high, low=low, close=close, volume=Decimal("1"),
            candle_state=CandleState.CLOSED,
            completeness=DataCompleteness.COMPLETE,
            finality=finality,
        )
        received = end + timedelta(seconds=1)
        available = end + timedelta(seconds=2) if availability else None
        source = SourceIdentity(
            "binance-spot-websocket" if index == len(closes) - 1 else "binance-spot-rest",
            "binance", "BINANCE",
        )
        item = MarketData(
            instrument=INSTRUMENT,
            source=source,
            data_type=CANDLE_DATA_TYPE,
            observed_at=end,
            received_at=received,
            available_at=available,
            payload=candle,
            quality=DataQuality.VALID,
            completeness=DataCompleteness.UNKNOWN,
            market_data_id=f"BTCUSDT-4h-{index:03d}",
        )
        items.append(item)
        previous_close = close
    return items


def config():
    return EmaRsiCrossoverConfig(
        fast_ema_period=2,
        slow_ema_period=4,
        rsi_period=2,
        rsi_buy_min=Decimal("50"),
        expiry_hours=4,
    )


def test_closed_final_candles_produce_canonical_up_signal_and_explicit_paper_proposal():
    # Strong final reversal makes the fast EMA cross above the slow EMA on the
    # latest candle; all evidence is synthetic and this is not an edge claim.
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100]
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        now=now,
    )
    policy = PaperProposalPolicy(
        direction_to_side={"UP": TradeSide.BUY, "DOWN": TradeSide.SELL},
        requested_quantity=Decimal("0.01"),
        order_type=TradeOrderType.MARKET,
        price_policy=PricePolicy.MARKET_REFERENCE,
        strategy_identity=STRATEGY_IDENTITY,
        strategy_version=STRATEGY_VERSION,
        max_quote_spend=Decimal("25"),
    )

    result = generate_market_signal(
        items,
        decision_timestamp=now,
        config=config(),
        expected_symbol="BTC/USDT",
        proposal_policy=policy,
    )

    assert result.status is MarketSignalPipelineStatus.PROPOSAL_READY
    assert result.signal is not None
    assert result.signal.direction == "UP"
    assert result.signal.validity.value == "VALID"
    assert result.signal.evidence["timeframe"] == "4h"
    assert result.signal.provenance.metadata["strategy_identity"] == STRATEGY_IDENTITY
    assert result.trade_proposal is not None
    assert result.trade_proposal.signal_id == result.signal.signal_id
    assert result.trade_proposal.strategy_identity == STRATEGY_IDENTITY
    assert result.reason == "PAPER_TRADE_PROPOSAL_BUILT_NOT_SUBMITTED"


def test_no_crossover_returns_no_trade_without_proposal():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19])
    result = generate_market_signal(
        items,
        decision_timestamp=NOW,
        config=config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPipelineStatus.NO_TRADE
    assert result.signal is None
    assert result.trade_proposal is None


def test_latest_candle_finality_unknown_blocks_signal():
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        finality_override=CandleFinality.UNKNOWN,
    )
    result = generate_market_signal(
        items, decision_timestamp=NOW, config=config(), expected_symbol="BTC/USDT"
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert result.signal is None
    assert "LATEST_CANDLE_FINALITY_NOT_PROVEN" in result.reason


def test_unknown_availability_blocks_signal_without_fallback():
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        availability=False,
    )
    result = generate_market_signal(
        items, decision_timestamp=NOW, config=config(), expected_symbol="BTC/USDT"
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert result.signal is None
    assert "AVAILABLE_AT_UNKNOWN" in result.reason


def test_partial_or_open_candle_is_rejected():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19])
    open_candle = replace(
        items[-1],
        payload=replace(
            items[-1].payload,
            candle_state=CandleState.OPEN,
            completeness=DataCompleteness.PARTIAL,
            finality=CandleFinality.NOT_FINAL,
        ),
    )
    result = generate_market_signal(
        items[:-1] + [open_candle],
        decision_timestamp=NOW,
        config=config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert "CANDLE_NOT_CLOSED" in result.reason


def test_internal_gap_or_ordering_error_is_rejected():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19])
    # Shift the final suffix by one bar to create a detectable hole while keeping
    # timestamps internally coherent within that suffix.
    for index in range(7, len(items)):
        item = items[index]
        shifted_start = item.payload.start + BAR
        shifted_end = item.payload.end + BAR
        items[index] = replace(
            item,
            observed_at=item.observed_at + BAR,
            received_at=item.received_at + BAR,
            available_at=item.available_at + BAR if item.available_at is not None else None,
            payload=replace(item.payload, start=shifted_start, end=shifted_end),
        )
    result = generate_market_signal(
        items,
        decision_timestamp=NOW + BAR,
        config=config(),
        expected_symbol="BTC/USDT",
        max_candle_age=timedelta(days=2),
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert "CANDLE_SEQUENCE_GAP_OR_ORDER_ERROR" in result.reason


def test_future_market_evidence_is_rejected():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19])
    result = generate_market_signal(
        items,
        decision_timestamp=NOW - timedelta(days=1),
        config=config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert "MARKET_DATA_NOT_YET_AVAILABLE" in result.reason


def test_wrong_timeframe_and_symbol_are_rejected():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19], timeframe="1h")
    result = generate_market_signal(
        items, decision_timestamp=NOW, config=config(), expected_symbol="BTC/USDT"
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert "TIMEFRAME_MISMATCH" in result.reason


def test_insufficient_observation_count_is_blocked():
    items = build_market_data([30, 29, 28])
    result = generate_market_signal(
        items, decision_timestamp=NOW, config=config(), expected_symbol="BTC/USDT"
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert "INSUFFICIENT_CLOSED_CANDLES" in result.reason


def test_unmapped_or_mismatched_policy_cannot_create_a_proposal():
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100]
    )
    policy = PaperProposalPolicy(
        direction_to_side={"UP": TradeSide.BUY},
        requested_quantity=Decimal("0.01"),
        order_type=TradeOrderType.MARKET,
        price_policy=PricePolicy.MARKET_REFERENCE,
        strategy_identity="different.strategy",
        strategy_version=STRATEGY_VERSION,
        max_quote_spend=Decimal("25"),
    )
    result = generate_market_signal(
        items, decision_timestamp=NOW, config=config(), expected_symbol="BTC/USDT",
        proposal_policy=policy,
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert result.signal is not None
    assert result.trade_proposal is None
    assert "PAPER_PROPOSAL_REJECTED" in result.reason
