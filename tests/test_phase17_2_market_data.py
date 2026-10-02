from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from bot_obrero.market_data import (
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    InstrumentType,
    MarketData,
    MarketDataError,
    SourceIdentity,
    to_market_observation,
)

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
INSTRUMENT = InstrumentIdentity("btc-spot", "BTCUSDT", "SPOT")
SOURCE = SourceIdentity("feed-1", "provider-a", "venue-a")


def candle(**overrides):
    values = dict(
        start=T,
        end=T + timedelta(minutes=1),
        timeframe="1m",
        open="123.4500",
        high="124.0000",
        low="123.0000",
        close="123.7500",
        volume="0.0100",
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.NOT_FINAL,
    )
    values.update(overrides)
    return Candle(**values)


def market_data(**overrides):
    values = dict(
        market_data_id="md-1",
        instrument=INSTRUMENT,
        source=SOURCE,
        data_type="CANDLE",
        observed_at=T,
        received_at=T + timedelta(seconds=3),
        available_at=T + timedelta(seconds=3),
        payload=candle(),
        quality=DataQuality.VALID,
        completeness=DataCompleteness.COMPLETE,
        source_sequence=17,
    )
    values.update(overrides)
    return MarketData(**values)


def test_ohlcv_is_decimal_exact_and_preserves_scale():
    item = candle()
    assert item.open == Decimal("123.4500")
    assert item.open.as_tuple().exponent == -4
    assert all(isinstance(getattr(item, name), Decimal) for name in ("open", "high", "low", "close", "volume"))


@pytest.mark.parametrize("value", [float(1.25), True])
def test_float_and_bool_are_not_accepted_as_exact_decimal_inputs(value):
    with pytest.raises(MarketDataError):
        candle(open=value)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_ohlcv_is_rejected_not_zeroed(value):
    with pytest.raises(MarketDataError):
        candle(open=value)


def test_instrument_type_is_limited_to_crypto_spot():
    assert INSTRUMENT.instrument_type is InstrumentType.CRYPTO_SPOT
    with pytest.raises(MarketDataError):
        InstrumentIdentity("x", "BTCUSDT", "SPOT", "CRYPTO_PERPETUAL")


def test_source_identity_keeps_provider_and_venue_separate():
    assert SOURCE.source_id != SOURCE.provider
    assert SOURCE.venue == "venue-a"


def test_timestamps_must_be_timezone_aware():
    with pytest.raises(MarketDataError):
        candle(start=datetime(2026, 10, 1))
    with pytest.raises(MarketDataError):
        market_data(received_at=datetime(2026, 10, 1))


def test_historical_unknown_availability_is_not_inferred_from_received_or_observed():
    item = market_data(available_at=None)
    assert item.observed_at == T
    assert item.received_at == T + timedelta(seconds=3)
    assert item.available_at is None
    restored = MarketData.from_json(item.to_json())
    assert restored.available_at is None
    with pytest.raises(MarketDataError, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(T + timedelta(seconds=10))
    with pytest.raises(MarketDataError, match="AVAILABLE_AT_UNKNOWN"):
        to_market_observation(item)


def test_available_at_is_checked_against_decision_using_phase16_guard():
    item = market_data()
    with pytest.raises(ValueError, match="look-ahead"):
        item.evidence_at(T + timedelta(seconds=2))
    assert item.evidence_at(T + timedelta(seconds=3)).available_timestamp == T + timedelta(seconds=3)


def test_quality_and_completeness_are_independent():
    assert market_data(quality=DataQuality.VALID, completeness=DataCompleteness.PARTIAL)
    assert market_data(quality=DataQuality.UNKNOWN, completeness=DataCompleteness.COMPLETE)
    assert market_data(quality=DataQuality.INVALID, completeness=DataCompleteness.UNKNOWN)


def test_candle_closed_is_not_final_and_partial_is_not_invalid():
    item = candle(
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.PARTIAL,
        finality=CandleFinality.NOT_FINAL,
    )
    assert item.candle_state is CandleState.CLOSED
    assert item.finality is CandleFinality.NOT_FINAL
    assert item.completeness is DataCompleteness.PARTIAL


def test_candle_identity_excludes_source_identity():
    first = market_data(source=SourceIdentity("source-a", "provider-a"), market_data_id="md-a")
    second = market_data(source=SourceIdentity("source-b", "provider-b"), market_data_id="md-b")
    assert first.candle_identity == second.candle_identity
    assert first.source_id != second.source_id
    assert first.market_data_id != second.market_data_id


def test_market_data_serialization_round_trips_decimal_as_text():
    original = market_data()
    encoded = original.to_json()
    decoded_json = json.loads(encoded)
    assert decoded_json["payload"]["open"] == "123.4500"
    assert "decision_at" not in decoded_json
    assert "retrieved_at" not in decoded_json
    restored = MarketData.from_json(encoded)
    assert restored == original
    assert isinstance(restored.payload.open, Decimal)


def test_observation_promotion_preserves_market_data_identity_and_phase16_guard():
    item = market_data()
    observation = to_market_observation(item)
    assert observation.provenance.reference == item.market_data_id
    assert observation.provenance.source == item.source_id
    assert observation.provenance.metadata["instrument_id"] == item.instrument_id
    assert observation.observation_id != item.market_data_id
    assert observation.evidence_at(T + timedelta(seconds=3)).available_timestamp == item.available_at
    with pytest.raises((AttributeError, TypeError)):
        observation.values["close"] = Decimal("0")


def test_invalid_market_data_cannot_be_promoted_to_observation():
    item = market_data(quality=DataQuality.INVALID)
    with pytest.raises(MarketDataError, match="MARKET_DATA_QUALITY_NOT_VALID"):
        to_market_observation(item)
    unknown = market_data(quality=DataQuality.UNKNOWN)
    with pytest.raises(MarketDataError, match="MARKET_DATA_QUALITY_NOT_VALID"):
        to_market_observation(unknown)


def test_missing_payload_does_not_create_a_synthetic_zero_candle():
    with pytest.raises(MarketDataError, match="payload must be canonical Candle"):
        market_data(payload=None)


def test_market_data_and_candle_are_immutable():
    item = market_data()
    with pytest.raises((AttributeError, TypeError)):
        item.payload.close = Decimal("0")
    with pytest.raises((AttributeError, TypeError)):
        item.available_at = None


def test_ohlcv_valid_values_with_different_decimal_scales():
    item = candle(open="100.0", high="102.000", low="99", close="101.25", volume="0.0000100")
    assert item.open == Decimal("100.0")
    assert item.high == Decimal("102.000")
    assert item.low == Decimal("99")
    assert item.volume == Decimal("0.0000100")


def test_partial_candle_can_be_structurally_valid():
    item = candle(completeness=DataCompleteness.PARTIAL, candle_state=CandleState.OPEN)
    assert item.completeness is DataCompleteness.PARTIAL
    assert item.candle_state is CandleState.OPEN


def test_closed_candle_can_be_non_final_and_structurally_valid():
    item = candle(candle_state=CandleState.CLOSED, finality=CandleFinality.NOT_FINAL)
    assert item.candle_state is CandleState.CLOSED
    assert item.finality is CandleFinality.NOT_FINAL


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("high", "123.4499", "high must be >= open"),
        ("high", "123.7499", "high must be >= close"),
        ("low", "123.4501", "low must be <= open"),
        ("volume", "-0.0001", "volume must be >= 0"),
    ],
)
def test_structurally_impossible_ohlcv_is_rejected(field, value, message):
    with pytest.raises(MarketDataError, match=message):
        candle(**{field: value})


def test_candle_data_type_is_explicitly_admitted():
    from bot_obrero.market_data import CANDLE_DATA_TYPE

    assert CANDLE_DATA_TYPE == "CANDLE"
    assert market_data(data_type=CANDLE_DATA_TYPE).data_type == "CANDLE"


@pytest.mark.parametrize("data_type", ["", " ", "TRADE", "candle", "OHLCV"])
def test_empty_or_unsupported_data_type_is_rejected(data_type):
    with pytest.raises(MarketDataError):
        market_data(data_type=data_type)


def test_candle_discriminator_rejects_incompatible_payload_type():
    with pytest.raises(MarketDataError, match="payload must be canonical Candle"):
        market_data(data_type="CANDLE", payload={"open": "1", "high": "2", "low": "1", "close": "2", "volume": "1"})


def test_unknown_availability_never_becomes_phase16_temporal_evidence():
    item = market_data(available_at=None)
    with pytest.raises(MarketDataError, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(T + timedelta(days=1))
    with pytest.raises(MarketDataError, match="AVAILABLE_AT_UNKNOWN"):
        to_market_observation(item)
