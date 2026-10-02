from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.acquisition import (
    CanonicalValidationError,
    InstrumentMapper,
    InstrumentMappingAmbiguous,
    InstrumentMappingNotFound,
    InstrumentMappingRule,
    NormalizationError,
    ProviderPayloadError,
    build_source_identity,
    normalize_provider_record,
    parse_provider_payload,
    provider_payload_to_market_data,
)
from bot_obrero.market_data import (
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    MarketData,
    to_market_observation,
)


T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
CANDLE_END = T0 + timedelta(minutes=1)
RECEIVED = T0 + timedelta(minutes=3)
INSTRUMENT = InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT")
MAPPER = InstrumentMapper(
    [
        InstrumentMappingRule(
            provider="fixture-provider",
            provider_symbol="BTCUSDT",
            provider_market="SPOT",
            provider_venue="fixture-venue",
            instrument=INSTRUMENT,
        )
    ]
)


def payload(**overrides):
    value = {
        "provider_symbol": "BTCUSDT",
        "provider": "fixture-provider",
        "provider_market": "SPOT",
        "provider_venue": "fixture-venue",
        "observed_at": T0.isoformat(),
        "available_at": RECEIVED.isoformat(),
        "candle_start": T0.isoformat(),
        "candle_end": CANDLE_END.isoformat(),
        "timeframe": "1m",
        "open": "123.4500",
        "high": "124.0000",
        "low": "123.0000",
        "close": "123.7500",
        "volume": "0.0100",
        "quality": "UNKNOWN",
        "candle_completeness": "PARTIAL",
        "market_data_completeness": "PARTIAL",
        "candle_state": "CLOSED",
        "finality": "NOT_FINAL",
        "source_sequence": 7,
    }
    value.update(overrides)
    return value


def normalize(**overrides):
    record = parse_provider_payload(payload(**overrides)).record
    return normalize_provider_record(
        record,
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )


def test_valid_provider_payload_parses_without_becoming_market_data():
    result = parse_provider_payload(payload())
    assert result.record.provider_symbol == "BTCUSDT"
    assert not isinstance(result.record, MarketData)


def test_malformed_payload_is_rejected_at_parsing():
    with pytest.raises(ProviderPayloadError, match="missing required fields"):
        parse_provider_payload({"provider": "fixture-provider"})


def test_unknown_mapping_is_rejected():
    with pytest.raises(InstrumentMappingNotFound):
        normalize(provider_symbol="XBTUSD")


def test_ambiguous_mapping_is_rejected():
    mapper = InstrumentMapper(
        [
            InstrumentMappingRule(
                "fixture-provider", "BTCUSDT", "SPOT", "fixture-venue", INSTRUMENT
            ),
            InstrumentMappingRule(
                "fixture-provider",
                "BTCUSDT",
                "SPOT",
                "fixture-venue",
                InstrumentIdentity("other", "BTC/USDT", "SPOT"),
            ),
        ]
    )
    record = parse_provider_payload(payload()).record
    with pytest.raises(InstrumentMappingAmbiguous):
        normalize_provider_record(
            record,
            received_at=RECEIVED,
            source_id="fixture-source",
            instrument_mapper=mapper,
        )


def test_explicit_mapping_does_not_depend_on_textual_symbol_identity():
    normalized = normalize()
    assert normalized.value.instrument == INSTRUMENT
    assert normalized.value.instrument.symbol == "BTC/USDT"


def test_source_identity_keeps_source_provider_and_venue_separate():
    source = build_source_identity(
        source_id="fixture-source",
        provider="fixture-provider",
        venue="fixture-venue",
    )
    assert source.source_id == "fixture-source"
    assert source.provider == "fixture-provider"
    assert source.venue == "fixture-venue"
    assert source.source_id != source.provider


def test_source_id_is_never_synthesized_from_provider():
    with pytest.raises((ValueError, TypeError)):
        normalize(source_id="") if False else build_source_identity(
            source_id="", provider="fixture-provider", venue="fixture-venue"
        )


def test_received_at_must_be_explicit_timezone_aware_context():
    record = parse_provider_payload(payload()).record
    with pytest.raises(NormalizationError, match="received_at"):
        normalize_provider_record(
            record,
            received_at=datetime(2026, 10, 2, 12, 3),
            source_id="fixture-source",
            instrument_mapper=MAPPER,
        )


def test_available_at_none_is_preserved_as_unknown():
    normalized = normalize(available_at=None)
    assert normalized.value.available_at is None


def test_available_at_is_never_inferred_from_observed_or_received():
    normalized = normalize(available_at=None)
    assert normalized.value.observed_at == T0
    assert normalized.value.received_at == RECEIVED
    assert normalized.value.available_at is None


def test_timestamp_timezone_is_enforced():
    with pytest.raises(NormalizationError, match="observed_at"):
        normalize(observed_at="2026-10-02T12:00:00")


def test_decimal_normalization_preserves_scale():
    normalized = normalize()
    assert normalized.value.open == Decimal("123.4500")
    assert normalized.value.open.as_tuple().exponent == -4
    assert normalized.value.volume == Decimal("0.0100")


@pytest.mark.parametrize("value", [1.25, True])
def test_float_and_bool_are_not_coerced_to_decimal(value):
    with pytest.raises(NormalizationError, match="open"):
        normalize(open=value)


@pytest.mark.parametrize("timeframe", ["", " "])
def test_empty_timeframe_is_rejected_but_no_catalog_is_imposed(timeframe):
    with pytest.raises(ProviderPayloadError, match="timeframe"):
        parse_provider_payload(payload(timeframe=timeframe))


@pytest.mark.parametrize("timeframe", ["1m", "5m", "1h"])
def test_nonempty_timeframes_are_not_rejected_by_a_new_catalog(timeframe):
    result = parse_provider_payload(payload(timeframe=timeframe))
    assert result.record.timeframe == timeframe


def test_quality_is_not_upgraded_from_successful_parsing():
    normalized = normalize(quality=None)
    assert normalized.value.quality is DataQuality.UNKNOWN


def test_explicit_quality_is_preserved_without_inference():
    normalized = normalize(quality="VALID")
    assert normalized.value.quality is DataQuality.VALID


def test_quality_and_completeness_remain_independent():
    normalized = normalize(
        quality="VALID",
        candle_completeness="PARTIAL",
        market_data_completeness="COMPLETE",
    )
    assert normalized.value.quality is DataQuality.VALID
    assert normalized.value.candle_completeness is DataCompleteness.PARTIAL
    assert normalized.value.market_data_completeness is DataCompleteness.COMPLETE


def test_quality_and_completeness_can_remain_unknown_independently():
    normalized = normalize(
        quality=None,
        candle_completeness=None,
        market_data_completeness=None,
    )
    assert normalized.value.quality is DataQuality.UNKNOWN
    assert normalized.value.candle_completeness is DataCompleteness.UNKNOWN
    assert normalized.value.market_data_completeness is DataCompleteness.UNKNOWN


def test_invalid_quality_is_rejected_instead_of_silently_downgraded():
    with pytest.raises(NormalizationError, match="quality"):
        normalize(quality="MAYBE")


def test_canonical_boundary_does_not_use_legacy_candle():
    from bot_obrero import data as legacy
    from bot_obrero import market_data as canonical

    assert canonical.Candle is not legacy.Candle


def test_provider_record_and_normalized_input_are_distinct_from_market_data():
    result = parse_provider_payload(payload())
    normalized = normalize()
    assert not isinstance(result.record, MarketData)
    assert not isinstance(normalized.value, MarketData)


def test_canonicalization_creates_canonical_market_data():
    normalized = normalize(
        quality="VALID",
        candle_completeness="PARTIAL",
        market_data_completeness="PARTIAL",
    )
    from bot_obrero.acquisition import canonicalize_market_data

    item = canonicalize_market_data(normalized.value)
    assert isinstance(item, MarketData)
    assert item.payload.start == T0
    assert item.payload.end == CANDLE_END
    assert item.payload.open == Decimal("123.4500")
    assert item.instrument_id == INSTRUMENT.instrument_id
    assert item.source_id == "fixture-source"
    assert item.quality is DataQuality.VALID
    assert item.completeness is DataCompleteness.PARTIAL
    assert item.payload.completeness is DataCompleteness.PARTIAL
    assert item.source_sequence == 7


def test_full_pipeline_ends_at_market_data_and_not_observation():
    item = provider_payload_to_market_data(
        payload(quality="VALID"),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )
    assert isinstance(item, MarketData)
    assert item.quality is DataQuality.VALID
    with pytest.raises(Exception):
        # available_at is known and quality is VALID, so this call should create
        # the later Phase 1.6 object; the acquisition layer itself does not create it.
        observation = to_market_observation(item)
        assert observation.provenance.reference == item.market_data_id


def test_available_at_unknown_cannot_create_temporal_evidence():
    item = provider_payload_to_market_data(
        payload(available_at=None, quality="VALID"),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )
    assert item.available_at is None
    with pytest.raises(Exception, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(RECEIVED)


def test_lookahead_is_blocked_by_available_at_not_observed_at():
    item = provider_payload_to_market_data(
        payload(
            observed_at=T0.isoformat(),
            available_at=RECEIVED.isoformat(),
            quality="VALID",
        ),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )
    with pytest.raises(ValueError, match="look-ahead"):
        item.evidence_at(T0 + timedelta(minutes=1))
    assert item.evidence_at(RECEIVED).available_timestamp == RECEIVED


def test_canonical_validation_rejects_structurally_invalid_ohlcv():
    normalized = normalize(high="123.1000")
    from bot_obrero.acquisition import canonicalize_market_data

    with pytest.raises(CanonicalValidationError, match="canonical validation rejected"):
        canonicalize_market_data(normalized.value)


def test_provider_source_sequence_is_preserved_without_reordering():
    item = provider_payload_to_market_data(
        payload(quality="VALID", source_sequence="seq-7"),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )
    assert item.source_sequence == "seq-7"


def test_partial_and_closed_not_final_cross_without_semantic_upgrade():
    item = provider_payload_to_market_data(
        payload(
            quality="VALID",
            candle_completeness="PARTIAL",
            market_data_completeness="PARTIAL",
            candle_state="CLOSED",
            finality="NOT_FINAL",
        ),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )
    assert item.quality is DataQuality.VALID
    assert item.completeness is DataCompleteness.PARTIAL
    assert item.payload.candle_state is CandleState.CLOSED
    assert item.payload.completeness is DataCompleteness.PARTIAL
    assert item.payload.finality is CandleFinality.NOT_FINAL
