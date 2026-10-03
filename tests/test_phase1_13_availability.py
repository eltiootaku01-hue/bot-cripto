from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.acquisition import (
    CanonicalValidationError,
    InstrumentMapper,
    InstrumentMappingRule,
    apply_availability_evidence,
    normalize_provider_record,
    parse_provider_payload,
    provider_payload_to_market_data_with_availability,
)
from bot_obrero.availability import (
    AvailabilityEvidence,
    AvailabilityEvidenceError,
    AvailabilityEvidenceKind,
    resolve_availability,
)
from bot_obrero.market_data import (
    DataQuality,
    InstrumentIdentity,
    MarketData,
    to_market_observation,
)


T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
RECEIVED = T0 + timedelta(seconds=2)
HANDOFF = T0 + timedelta(seconds=3)
DECISION_BEFORE = T0 + timedelta(seconds=1)
DECISION_AT = HANDOFF
DECISION_AFTER = HANDOFF + timedelta(seconds=1)
INSTRUMENT = InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT", base_asset="BTC", quote_asset="USDT")

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
        "available_at": None,
        "candle_start": T0.isoformat(),
        "candle_end": (T0 + timedelta(minutes=1)).isoformat(),
        "timeframe": "1m",
        "open": "123.4500",
        "high": "124.0000",
        "low": "123.0000",
        "close": "123.7500",
        "volume": "0.0100",
        "quality": "VALID",
        "candle_completeness": "COMPLETE",
        "market_data_completeness": "COMPLETE",
        "candle_state": "CLOSED",
        "finality": "UNKNOWN",
    }
    value.update(overrides)
    return value


def normalized_payload(**overrides):
    record = parse_provider_payload(payload(**overrides)).record
    return normalize_provider_record(
        record,
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
    )


def test_unknown_evidence_produces_none():
    evidence = AvailabilityEvidence.unknown()
    assert evidence.kind is AvailabilityEvidenceKind.UNKNOWN
    assert resolve_availability(evidence, received_at=RECEIVED) is None


def test_unknown_evidence_does_not_fallback_to_received_at():
    normalized = normalized_payload(available_at=RECEIVED.isoformat())
    applied = apply_availability_evidence(
        normalized.value,
        AvailabilityEvidence.unknown(),
    )
    assert applied.value.available_at is None


def test_unknown_evidence_never_falls_back_to_observed_at():
    applied = apply_availability_evidence(
        normalized_payload().value,
        AvailabilityEvidence.unknown(),
    )
    assert applied.value.available_at is None
    assert applied.value.observed_at == T0


def test_received_and_available_requires_explicit_kind():
    evidence = AvailabilityEvidence.received_and_available(
        RECEIVED,
        evidence_reference="http-response-complete",
        consumer_scope="fixture-consumer",
    )
    assert evidence.kind is AvailabilityEvidenceKind.RECEIVED_AND_AVAILABLE
    assert resolve_availability(evidence, received_at=RECEIVED) == RECEIVED


def test_received_and_available_is_not_accepted_as_an_implicit_equality():
    evidence = AvailabilityEvidence(
        kind=AvailabilityEvidenceKind.RECEIVED_AND_AVAILABLE,
        available_at=HANDOFF,
    )
    with pytest.raises(AvailabilityEvidenceError, match="received_at"):
        resolve_availability(evidence, received_at=RECEIVED)


def test_consumer_handoff_supports_later_availability():
    evidence = AvailabilityEvidence.consumer_handoff(
        received_at=RECEIVED,
        available_at=HANDOFF,
        evidence_reference="consumer-handoff-1",
        consumer_scope="fixture-consumer",
    )
    assert evidence.kind is AvailabilityEvidenceKind.CONSUMER_HANDOFF
    assert resolve_availability(evidence, received_at=RECEIVED) == HANDOFF


def test_consumer_handoff_rejects_availability_before_received():
    with pytest.raises(AvailabilityEvidenceError, match="cannot precede"):
        AvailabilityEvidence.consumer_handoff(
            received_at=RECEIVED,
            available_at=T0 + timedelta(seconds=1),
            evidence_reference="bad-handoff",
            consumer_scope="fixture-consumer",
        )


def test_external_evidence_is_explicit_and_does_not_get_rewritten():
    earlier = T0 + timedelta(seconds=1)
    evidence = AvailabilityEvidence.explicit_external_evidence(
        available_at=earlier,
        evidence_reference="provider-contract-availability",
        consumer_scope="fixture-consumer",
    )
    assert evidence.kind is AvailabilityEvidenceKind.EXPLICIT_EXTERNAL_EVIDENCE
    assert resolve_availability(evidence, received_at=RECEIVED) == earlier


def test_external_evidence_requires_reference_and_consumer_scope():
    with pytest.raises(AvailabilityEvidenceError, match="evidence_reference"):
        AvailabilityEvidence(
            kind=AvailabilityEvidenceKind.EXPLICIT_EXTERNAL_EVIDENCE,
            available_at=RECEIVED,
            consumer_scope="fixture-consumer",
        )
    with pytest.raises(AvailabilityEvidenceError, match="consumer_scope"):
        AvailabilityEvidence(
            kind=AvailabilityEvidenceKind.EXPLICIT_EXTERNAL_EVIDENCE,
            available_at=RECEIVED,
            evidence_reference="contract",
        )


def test_explicit_external_evidence_may_be_before_received_without_automatic_rejection():
    earlier = T0 + timedelta(seconds=1)
    evidence = AvailabilityEvidence.explicit_external_evidence(
        available_at=earlier,
        evidence_reference="explicit-system-availability-proof",
        consumer_scope="fixture-consumer",
    )
    assert resolve_availability(evidence, received_at=RECEIVED) == earlier


def test_received_and_available_rejects_naive_timestamp():
    naive = datetime(2026, 10, 2, 12, 0, 2)
    with pytest.raises(AvailabilityEvidenceError, match="received_at"):
        AvailabilityEvidence.received_and_available(naive)


def test_unknown_rejects_an_attached_timestamp():
    with pytest.raises(AvailabilityEvidenceError, match="UNKNOWN"):
        AvailabilityEvidence(
            kind=AvailabilityEvidenceKind.UNKNOWN,
            available_at=RECEIVED,
        )


def test_apply_availability_evidence_is_a_separate_stage():
    normalized = normalized_payload()
    assert normalized.value.available_at is None
    applied = apply_availability_evidence(
        normalized.value,
        AvailabilityEvidence.received_and_available(RECEIVED),
    )
    assert applied.value.available_at == RECEIVED


def test_integration_known_availability_reaches_market_data():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.received_and_available(
            RECEIVED,
            evidence_reference="fixture-received-boundary",
            consumer_scope="fixture-consumer",
        ),
    )
    assert isinstance(item, MarketData)
    assert item.available_at == RECEIVED
    assert item.quality is DataQuality.VALID


def test_integration_unknown_availability_reaches_market_data_as_none():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.unknown(),
    )
    assert isinstance(item, MarketData)
    assert item.available_at is None
    with pytest.raises(ValueError, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(DECISION_AFTER)


def test_received_and_available_can_support_decision_at_same_instant():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.received_and_available(RECEIVED),
    )
    assert item.evidence_at(RECEIVED).available_timestamp == RECEIVED


def test_consumer_handoff_allows_decision_after_availability():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.consumer_handoff(
            received_at=RECEIVED,
            available_at=HANDOFF,
        ),
    )
    assert item.evidence_at(DECISION_AFTER).available_timestamp == HANDOFF


def test_consumer_handoff_blocks_decision_before_availability_using_existing_guard():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.consumer_handoff(
            received_at=RECEIVED,
            available_at=HANDOFF,
        ),
    )
    with pytest.raises(ValueError, match="look-ahead"):
        item.evidence_at(DECISION_BEFORE)


def test_consumer_handoff_allows_decision_at_exact_availability():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.consumer_handoff(
            received_at=RECEIVED,
            available_at=HANDOFF,
        ),
    )
    assert item.evidence_at(DECISION_AT).decision_timestamp == DECISION_AT


def test_unknown_blocks_market_observation_promotion():
    item = provider_payload_to_market_data_with_availability(
        payload(),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.unknown(),
    )
    with pytest.raises(ValueError, match="AVAILABLE_AT_UNKNOWN"):
        to_market_observation(item)


def test_quality_still_blocks_promotion():
    item = provider_payload_to_market_data_with_availability(
        payload(quality="UNKNOWN"),
        received_at=RECEIVED,
        source_id="fixture-source",
        instrument_mapper=MAPPER,
        availability_evidence=AvailabilityEvidence.received_and_available(RECEIVED),
    )
    with pytest.raises(ValueError, match="QUALITY_NOT_VALID"):
        to_market_observation(item)


def test_legacy_candle_remains_outside_availability_boundary():
    from bot_obrero import data as legacy
    from bot_obrero import market_data as canonical

    assert canonical.Candle is not legacy.Candle


def test_existing_canonical_validation_still_runs_after_availability_stage():
    with pytest.raises(CanonicalValidationError, match="canonical validation rejected"):
        provider_payload_to_market_data_with_availability(
            payload(high="123.1000"),
            received_at=RECEIVED,
            source_id="fixture-source",
            instrument_mapper=MAPPER,
            availability_evidence=AvailabilityEvidence.received_and_available(RECEIVED),
        )


def test_decision_timestamp_is_not_part_of_availability_evidence():
    evidence = AvailabilityEvidence.consumer_handoff(
        received_at=RECEIVED,
        available_at=HANDOFF,
    )
    assert not hasattr(evidence, "decision_timestamp")


def test_provider_observed_time_never_drives_availability():
    normalized = normalized_payload(available_at=T0.isoformat())
    applied = apply_availability_evidence(
        normalized.value,
        AvailabilityEvidence.unknown(),
    )
    assert applied.value.available_at is None
