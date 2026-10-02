from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.market_data import (
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    MarketDataError,
)

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def build_candle(**overrides):
    values = {
        "start": T,
        "end": T + timedelta(minutes=1),
        "timeframe": "1m",
        "open": "10.00",
        "high": "12.00",
        "low": "9.00",
        "close": "11.00",
        "volume": "1.000",
        "candle_state": CandleState.CLOSED,
        "completeness": DataCompleteness.COMPLETE,
        "finality": CandleFinality.NOT_FINAL,
    }
    values.update(overrides)
    return Candle(**values)


def test_high_below_low_is_rejected_when_other_ohlc_relations_hold():
    with pytest.raises(MarketDataError, match="high must be >= low"):
        build_candle(open="7.00", close="8.00", high="8.50", low="9.00")


def test_low_greater_than_high_is_rejected_when_other_ohlc_relations_hold():
    with pytest.raises(MarketDataError, match="high must be >= low"):
        build_candle(open="10.00", close="11.00", high="12.00", low="13.00")


def test_low_greater_than_close_is_rejected_when_low_is_not_above_open():
    with pytest.raises(MarketDataError, match="low must be <= close"):
        build_candle(open="12.00", close="11.00", high="13.00", low="11.50")
