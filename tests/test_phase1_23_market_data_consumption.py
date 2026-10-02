from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import FrozenInstanceError

import pytest

from bot_obrero.acquisition_evidence import (
    AcquisitionMode,
    AcquisitionOperationEvidence,
    AcquisitionStatus,
)
from bot_obrero.market_data import (
    Candle,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    MarketData,
    MarketDataError,
    SourceIdentity,
)
from bot_obrero.market_data_consumption import (
    MarketDataConsumptionError,
    MarketDataConsumptionPolicy,
    MarketDataConsumptionResult,
    MarketDataConsumptionStatus,
    evaluate_market_data_consumption,
)
from bot_obrero.temporal import EvidenceTimestamp


UTC = timezone.utc
OBSERVED = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 10, 2, 12, 0, 1, tzinfo=UTC)
AVAILABLE = datetime(2026, 10, 2, 12, 0, 2, tzinfo=UTC)
DECISION_BEFORE = datetime(2026, 10, 2, 12, 0, 1, 500000, tzinfo=UTC)
DECISION_EQUAL = AVAILABLE
DECISION_AFTER = datetime(2026, 10, 2, 12, 0, 3, tzinfo=UTC)


def make_market_data(*, available_at: datetime | None = AVAILABLE) -> MarketData:
    return MarketData(
        instrument=InstrumentIdentity("binance:SPOT:BTCUSDT", "BTC/USDT", "SPOT"),
        source=SourceIdentity("binance-spot-rest", "binance", "BINANCE"),
        data_type="CANDLE",
        observed_at=OBSERVED,
        received_at=RECEIVED,
        available_at=available_at,
        payload=Candle(
            start=OBSERVED,
            end=OBSERVED + timedelta(minutes=1),
            timeframe="1m",
            open="100",
            high="101",
            low="99",
            close="100.5",
            volume="2",
        ),
        quality=DataQuality.VALID,
        completeness=DataCompleteness.COMPLETE,
    )


def make_acquisition_evidence() -> AcquisitionOperationEvidence:
    return AcquisitionOperationEvidence(
        operation_id="op-phase1.23",
        mode=AcquisitionMode.LIVE,
        provider="binance",
        source_id="binance-spot-rest",
        venue="BINANCE",
        market="SPOT",
        symbol="BTCUSDT",
        interval="1m",
        requested_start=OBSERVED,
        requested_end=AVAILABLE,
        limit=1,
        request_count=1,
        retry_count=0,
        page_count=1,
        status=AcquisitionStatus.SUCCESS,
        started_at=OBSERVED,
        finished_at=AVAILABLE,
    )


def test_decision_timestamp_must_be_timezone_aware():
    market_data = make_market_data()
    with pytest.raises(MarketDataConsumptionError, match="timezone-aware"):
        evaluate_market_data_consumption(
            market_data,
            decision_timestamp=datetime(2026, 10, 2, 12, 0, 3),
            policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        )


def test_aware_decision_timestamp_is_accepted():
    result = evaluate_market_data_consumption(
        make_market_data(),
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )
    assert result.decision_timestamp == DECISION_AFTER


def test_invalid_market_data_argument_is_rejected():
    with pytest.raises(MarketDataConsumptionError, match="market_data"):
        evaluate_market_data_consumption(
            object(),
            decision_timestamp=DECISION_AFTER,
            policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        )


def test_invalid_policy_is_rejected():
    with pytest.raises(MarketDataConsumptionError, match="policy"):
        evaluate_market_data_consumption(
            make_market_data(),
            decision_timestamp=DECISION_AFTER,
            policy="NOT_A_POLICY",
        )


@pytest.mark.parametrize(
    "policy",
    [
        MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        MarketDataConsumptionPolicy.ALLOW_UNKNOWN,
    ],
)
def test_unknown_availability_never_becomes_accepted(policy):
    market_data = make_market_data(available_at=None)

    result = evaluate_market_data_consumption(
        market_data,
        decision_timestamp=DECISION_AFTER,
        policy=policy,
    )

    assert result.status is MarketDataConsumptionStatus.UNKNOWN
    assert result.availability_is_unknown is True
    assert result.evidence_timestamp is None
    assert result.reason == "AVAILABLE_AT_UNKNOWN"
    assert result.market_data is market_data


def test_available_before_decision_is_accepted():
    result = evaluate_market_data_consumption(
        make_market_data(),
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    assert result.status is MarketDataConsumptionStatus.ACCEPTED
    assert isinstance(result.evidence_timestamp, EvidenceTimestamp)
    assert result.evidence_timestamp.event_timestamp == OBSERVED
    assert result.evidence_timestamp.available_timestamp == AVAILABLE
    assert result.evidence_timestamp.decision_timestamp == DECISION_AFTER


def test_available_exactly_at_decision_is_accepted():
    result = evaluate_market_data_consumption(
        make_market_data(),
        decision_timestamp=DECISION_EQUAL,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    assert result.status is MarketDataConsumptionStatus.ACCEPTED
    assert isinstance(result.evidence_timestamp, EvidenceTimestamp)


def test_available_after_decision_is_rejected():
    result = evaluate_market_data_consumption(
        make_market_data(),
        decision_timestamp=DECISION_BEFORE,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    assert result.status is MarketDataConsumptionStatus.REJECTED
    assert result.evidence_timestamp is None
    assert result.reason is not None
    assert "look-ahead" in result.reason


def test_allow_unknown_does_not_change_unknown_into_available():
    result = evaluate_market_data_consumption(
        make_market_data(available_at=None),
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.ALLOW_UNKNOWN,
    )

    assert result.status is MarketDataConsumptionStatus.UNKNOWN
    assert result.policy is MarketDataConsumptionPolicy.ALLOW_UNKNOWN


def test_consumer_result_references_original_market_data_identity():
    market_data = make_market_data()
    result = evaluate_market_data_consumption(
        market_data,
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    assert result.market_data is market_data
    assert result.market_data_id == market_data.market_data_id
    assert result.candle_identity == market_data.candle_identity


def test_consumer_result_is_immutable():
    market_data = make_market_data()
    result = evaluate_market_data_consumption(
        market_data,
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    with pytest.raises(FrozenInstanceError):
        result.status = MarketDataConsumptionStatus.REJECTED


def test_acquisition_evidence_is_preserved_as_optional_reference():
    market_data = make_market_data()
    acquisition_evidence = make_acquisition_evidence()

    result = evaluate_market_data_consumption(
        market_data,
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        acquisition_evidence=acquisition_evidence,
    )

    assert result.acquisition_evidence is acquisition_evidence


def test_acquisition_evidence_must_use_the_phase1_22_contract():
    with pytest.raises(MarketDataConsumptionError, match="acquisition_evidence"):
        evaluate_market_data_consumption(
            make_market_data(),
            decision_timestamp=DECISION_AFTER,
            policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
            acquisition_evidence=object(),
        )


def test_existing_market_data_availability_and_identity_remain_unchanged():
    market_data = make_market_data()
    before = market_data.to_dict()

    result = evaluate_market_data_consumption(
        market_data,
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    assert result.market_data is market_data
    assert market_data.to_dict() == before
    assert market_data.available_at == AVAILABLE
    assert market_data.market_data_id == before["market_data_id"]


def test_existing_evidence_timestamp_semantics_are_reused():
    market_data = make_market_data()

    calls: list[datetime] = []
    original = MarketData.evidence_at

    def spy(self: MarketData, decision_at: datetime):
        calls.append(decision_at)
        return original(self, decision_at)

    # The production boundary is deliberately exercised through the existing
    # MarketData.evidence_at contract instead of copying its comparison rule.
    original_method = MarketData.evidence_at
    try:
        MarketData.evidence_at = spy  # type: ignore[method-assign]
        result = evaluate_market_data_consumption(
            market_data,
            decision_timestamp=DECISION_AFTER,
            policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        )
    finally:
        MarketData.evidence_at = original_method  # type: ignore[method-assign]

    assert result.status is MarketDataConsumptionStatus.ACCEPTED
    assert calls == [DECISION_AFTER]


def test_consumer_does_not_use_received_at_as_implicit_availability():
    market_data = make_market_data(available_at=None)

    # received_at is before the decision, but absence of available_at still
    # yields UNKNOWN.
    assert market_data.received_at < DECISION_AFTER

    result = evaluate_market_data_consumption(
        market_data,
        decision_timestamp=DECISION_AFTER,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )

    assert result.status is MarketDataConsumptionStatus.UNKNOWN
    assert result.reason == "AVAILABLE_AT_UNKNOWN"
