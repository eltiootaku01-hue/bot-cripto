from datetime import datetime, timedelta, timezone
import json

import pytest

from bot_obrero.acquisition import (
    CanonicalValidationError,
    InstrumentMapper,
    InstrumentMappingRule,
)
from bot_obrero.availability import AvailabilityEvidenceError
from bot_obrero.binance_spot import BinanceSpotRestAdapter, BinanceSpotRestConfig
from bot_obrero.market_data import DataCompleteness, DataQuality, InstrumentIdentity, to_market_observation


OPEN_TIME_MS = 1790942400000
CLOSE_TIME_MS = OPEN_TIME_MS + 59_999
RECEIVED = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
HANDOFF = RECEIVED + timedelta(seconds=1)
BEFORE_RECEIVED = RECEIVED - timedelta(seconds=1)
DECISION_BEFORE = HANDOFF - timedelta(microseconds=1)
DECISION_AT = HANDOFF
DECISION_AFTER = HANDOFF + timedelta(microseconds=1)
INSTRUMENT = InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT")


def valid_row(
    *,
    open_value="123.4500",
    high_value="124.0000",
    low_value="123.0000",
    close_value="123.7500",
    volume="0.0100",
):
    return [
        OPEN_TIME_MS,
        open_value,
        high_value,
        low_value,
        close_value,
        volume,
        CLOSE_TIME_MS,
        "1.2345000",
        42,
        "0.0050",
        "0.6172500",
        "0",
    ]


def mapping():
    return InstrumentMapper(
        [
            InstrumentMappingRule(
                provider="binance",
                provider_symbol="BTCUSDT",
                provider_market="SPOT",
                provider_venue="BINANCE",
                instrument=INSTRUMENT,
            )
        ]
    )


def ok_getter(payload):
    body = json.dumps(payload).encode()

    def getter(url, headers, timeout):
        return 200, {"Content-Type": "application/json"}, body

    return getter


def adapter(getter, *, clock=lambda: RECEIVED, consumer_handoff_clock=None):
    return BinanceSpotRestAdapter(
        instrument_mapper=mapping(),
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=getter,
        clock=clock,
        consumer_handoff_clock=consumer_handoff_clock,
    )


def test_binance_availability_defaults_to_unknown_without_explicit_handoff():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.available_at is None
    assert item.received_at == RECEIVED


def test_consumer_handoff_produces_explicit_availability_at_t5():
    items = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_market_data(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert len(items) == 1
    assert items[0].received_at == RECEIVED
    assert items[0].available_at == HANDOFF
    assert items[0].available_at >= items[0].received_at


def test_consumer_handoff_clock_must_be_timezone_aware():
    naive = datetime(2026, 10, 2, 12, 1, 1)
    with pytest.raises(AvailabilityEvidenceError, match="available_at"):
        adapter(
            ok_getter([valid_row()]),
            consumer_handoff_clock=lambda: naive,
        ).fetch_market_data(
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
        )


def test_consumer_handoff_cannot_precede_received_at():
    with pytest.raises(AvailabilityEvidenceError, match="cannot precede"):
        adapter(
            ok_getter([valid_row()]),
            consumer_handoff_clock=lambda: BEFORE_RECEIVED,
        ).fetch_market_data(
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
        )


def test_observed_at_never_becomes_available_at():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.observed_at == item.payload.start
    assert item.available_at is None
    assert item.available_at != item.observed_at


def test_close_time_never_becomes_available_at():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.payload.end.isoformat().startswith("2026-10-02T12:00")
    assert item.available_at is None
    assert item.available_at != item.payload.end


def test_received_at_is_not_an_implicit_availability_fallback():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.received_at == RECEIVED
    assert item.available_at is None


def test_canonical_validation_completes_before_handoff_clock_is_called():
    called = False

    def handoff_clock():
        nonlocal called
        called = True
        return HANDOFF

    with pytest.raises(CanonicalValidationError, match="canonical validation rejected"):
        adapter(
            ok_getter([valid_row(high_value="123.5000")]),
            consumer_handoff_clock=handoff_clock,
        ).fetch_market_data(
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
        )

    assert called is False


def test_known_availability_promotes_to_existing_market_observation():
    item = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.quality is DataQuality.VALID
    assert item.available_at == HANDOFF
    observation = to_market_observation(item)
    assert observation is not None


def test_availability_does_not_change_completeness_semantics():
    item = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.available_at == HANDOFF
    assert item.completeness is DataCompleteness.UNKNOWN


def test_look_ahead_before_availability_is_rejected_by_existing_guard():
    item = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    with pytest.raises(ValueError, match="look-ahead"):
        item.evidence_at(DECISION_BEFORE)


def test_look_ahead_at_exact_availability_is_allowed_by_existing_guard():
    item = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    evidence = item.evidence_at(DECISION_AT)
    assert evidence.available_timestamp == HANDOFF
    assert evidence.decision_timestamp == HANDOFF


def test_look_ahead_after_availability_is_allowed_by_existing_guard():
    item = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    evidence = item.evidence_at(DECISION_AFTER)
    assert evidence.available_timestamp == HANDOFF
    assert evidence.decision_timestamp == DECISION_AFTER


def test_unknown_availability_stays_blocked_for_evidence():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    with pytest.raises(ValueError, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(DECISION_AFTER)


def test_legacy_candle_is_not_used_by_binance_availability_integration():
    from bot_obrero import data as legacy
    from bot_obrero import market_data as canonical

    assert canonical.Candle is not legacy.Candle
    item = adapter(
        ok_getter([valid_row()]),
        consumer_handoff_clock=lambda: HANDOFF,
    ).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert isinstance(item.payload, canonical.Candle)
    assert not isinstance(item.payload, legacy.Candle)
