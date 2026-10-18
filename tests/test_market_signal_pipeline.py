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
    generate_market_signal_from_rest_adapter,
)
from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeSide

HOUR = timedelta(hours=1)
BAR = timedelta(hours=4)
EPSILON = timedelta(milliseconds=1)
INSTRUMENT = InstrumentIdentity(
    "binance:SPOT:BTCUSDT", "BTC/USDT", "SPOT",
    base_asset="BTC", quote_asset="USDT",
)
def _synthetic_decision_time() -> datetime:
    """Use a stable point after the last 4h candle's synthetic handoff."""
    current = datetime.now(timezone.utc)
    current_bar_start = (
        current.replace(minute=0, second=0, microsecond=0)
        - timedelta(hours=current.hour % 4)
    )
    # build_market_data marks the previous closed bar available 2 seconds
    # after its close. Placing decisions 5 minutes after the boundary avoids
    # a wall-clock race when tests run immediately after a 4h boundary.
    return current_bar_start + timedelta(minutes=5)


NOW = _synthetic_decision_time()


def build_market_data(closes, *, now=NOW, finality_override=None, availability=True, timeframe="4h"):
    closes = tuple(Decimal(str(value)) for value in closes)
    # Align the synthetic series with the current UTC 4h boundary. The last
    # completed candle closes exactly 1 ms before that boundary.
    current_bar_start = (
        now.replace(minute=0, second=0, microsecond=0)
        - timedelta(hours=now.hour % 4)
    )
    first_start = current_bar_start - BAR * len(closes)
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
    now = _synthetic_decision_time()
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
    assert result.signal.evidence["latest_candle_finality_basis"] == "BINANCE_WEBSOCKET_KLINE_X_FLAG"
    assert "REST_RECEIVE_TIME_AFTER_CANDLE_CLOSE" in result.signal.evidence["candle_finality_bases"]
    assert result.signal.provenance.metadata["strategy_identity"] == STRATEGY_IDENTITY
    assert result.trade_proposal is not None
    assert result.trade_proposal.signal_id == result.signal.signal_id
    assert result.trade_proposal.strategy_identity == STRATEGY_IDENTITY
    assert result.reason == "PAPER_TRADE_PROPOSAL_BUILT_NOT_SUBMITTED"


def test_synthetic_fixture_availability_precedes_decision_time():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19])
    assert all(item.available_at is not None for item in items)
    assert all(item.available_at <= NOW for item in items)


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



def test_latest_rest_finality_records_local_time_derivation_explicitly():
    now = _synthetic_decision_time()
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        now=now,
    )
    items[-1] = replace(
        items[-1],
        source=SourceIdentity("binance-spot-rest", "binance", "BINANCE"),
    )
    result = generate_market_signal(
        items, decision_timestamp=now, config=config(), expected_symbol="BTC/USDT"
    )
    assert result.status is MarketSignalPipelineStatus.SIGNAL_READY
    assert result.signal is not None
    assert result.signal.evidence["latest_candle_finality_basis"] == "REST_RECEIVE_TIME_AFTER_CANDLE_CLOSE"


def test_unsupported_market_data_source_is_rejected_fail_closed():
    items = build_market_data([30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19])
    items[-1] = replace(
        items[-1],
        source=SourceIdentity("third-party-feed", "other-provider", "OTHER"),
    )
    result = generate_market_signal(
        items, decision_timestamp=NOW, config=config(), expected_symbol="BTC/USDT"
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert result.signal is None
    assert "MARKET_DATA_SOURCE_UNSUPPORTED" in result.reason



class FakeRestAdapter:
    def __init__(self, data=None, error=None):
        self.data = list(data or [])
        self.error = error
        self.calls = []

    def fetch_market_data(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return list(self.data)


def open_trailing_candle(now):
    start = (
        now.replace(minute=0, second=0, microsecond=0)
        - timedelta(hours=now.hour % 4)
    )
    end = start + BAR - EPSILON
    candle = Candle(
        start=start,
        end=end,
        timeframe="4h",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        candle_state=CandleState.OPEN,
        completeness=DataCompleteness.PARTIAL,
        finality=CandleFinality.NOT_FINAL,
    )
    return MarketData(
        instrument=INSTRUMENT,
        source=SourceIdentity("binance-spot-rest", "binance", "BINANCE"),
        data_type=CANDLE_DATA_TYPE,
        observed_at=now,
        received_at=now,
        # The consumer handoff can precede the scheduled close of this open candle.
        available_at=now,
        payload=candle,
        quality=DataQuality.VALID,
        completeness=DataCompleteness.UNKNOWN,
        market_data_id="BTCUSDT-4h-open-tail",
    )


def test_rest_adapter_pipeline_excludes_only_the_incomplete_trailing_candle():
    now = _synthetic_decision_time()
    closed = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        now=now,
    )
    adapter = FakeRestAdapter(closed + [open_trailing_candle(now)])
    result = generate_market_signal_from_rest_adapter(
        adapter,
        provider_symbol="BTCUSDT",
        clock=lambda: now,
        limit=100,
        config=config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPipelineStatus.SIGNAL_READY
    assert result.signal is not None and result.signal.direction == "UP"
    assert result.excluded_market_data_ids == ("BTCUSDT-4h-open-tail",)
    assert adapter.calls == [{"symbol": "BTCUSDT", "interval": "4h", "limit": 100}]


def test_rest_adapter_pipeline_preserves_unknown_availability_as_blocked():
    now = _synthetic_decision_time()
    closed = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        now=now,
        availability=False,
    )
    result = generate_market_signal_from_rest_adapter(
        FakeRestAdapter(closed),
        provider_symbol="BTCUSDT",
        clock=lambda: now,
        config=config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert "AVAILABLE_AT_UNKNOWN" in result.reason
    assert result.signal is None


def test_rest_adapter_failure_is_not_converted_to_no_trade_or_signal():
    result = generate_market_signal_from_rest_adapter(
        FakeRestAdapter(error=TimeoutError("public endpoint timeout")),
        provider_symbol="BTCUSDT",
        clock=lambda: NOW,
        config=config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPipelineStatus.BLOCKED
    assert result.signal is None
    assert result.trade_proposal is None
    assert "PUBLIC_CANDLE_ACQUISITION_FAILED" in result.reason
