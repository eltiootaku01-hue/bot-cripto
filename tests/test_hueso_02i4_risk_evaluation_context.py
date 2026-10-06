from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from bot_obrero.analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    Provenance,
    Signal,
    SignalValidity,
)
from bot_obrero.effective_capacity import EffectiveCapacity, EffectiveCapacityStatus
from bot_obrero.evidence_binding import (
    AvailabilityBinding,
    AvailabilitySubjectKind,
    RiskLimitResolution,
    RiskLimitResolutionStatus,
    derive_reservation_read_set_id,
)
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
from bot_obrero.reservation import (
    Reservation,
    ReservationReadSet,
    ReservationResourceKind,
    ReservationState,
)
from bot_obrero.risk_contracts import (
    BalanceSnapshot,
    CanonicalAccountState,
    CanonicalExposure,
    CanonicalPosition,
    Completeness,
    RiskLimit,
    RiskLimitSet,
)
from bot_obrero.risk_evaluation_context import (
    RiskEvaluationContext,
    RiskEvaluationContextError,
    RiskEvaluationContextStatus,
)
from bot_obrero.trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
)

BASE = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
EVALUATION = BASE + timedelta(minutes=10)
LATE = EVALUATION + timedelta(seconds=1)
OBSERVED = Provenance("test", ArtifactNature.OBSERVED, reference="fixture")
DERIVED = Provenance("test-derived", ArtifactNature.DERIVED, reference="fixture-derived")

INSTRUMENT = InstrumentIdentity(
    instrument_id="binance:SPOT:BTCUSDT",
    symbol="BTC/USDT",
    market="BINANCE",
    base_asset="BTC",
    quote_asset="USDT",
)


def make_proposal(*, decision_timestamp: datetime = BASE, correlation_id: str = "correlation-1"):
    return TradeProposal(
        signal_id="signal-1",
        symbol="BTC/USDT",
        side=TradeSide.BUY,
        requested_quantity=Decimal("0.01"),
        requested_price=None,
        max_quote_spend=Decimal("1000"),
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        strategy_identity="test.strategy",
        strategy_version="1.0.0",
        decision_timestamp=decision_timestamp,
        correlation_id=correlation_id,
    )


def make_account(
    *,
    available: str = "900",
    completeness: Completeness = Completeness.COMPLETE,
    account_state_id: str = "account-state-1",
):
    return CanonicalAccountState(
        account_state_id=account_state_id,
        account_id="account-1",
        as_of=BASE,
        balances=(
            BalanceSnapshot(
                asset="USDT",
                total=Decimal("1000"),
                available=Decimal(available),
                locked=Decimal("1000") - Decimal(available),
            ),
        ),
        completeness=completeness,
        provenance=OBSERVED,
    )


def make_limits(
    *,
    threshold: str = "500",
    risk_limit_set_id: str = "risk-limit-set-1",
    limit_id: str = "limit-1",
):
    return RiskLimitSet(
        risk_limit_set_id=risk_limit_set_id,
        limits=(
            RiskLimit(
                risk_limit_id=limit_id,
                scope="BTC/USDT",
                metric="MAX_QUOTE",
                threshold=Decimal(threshold),
                unit="USDT",
                effective_from=BASE - timedelta(hours=1),
                effective_until=EVALUATION + timedelta(hours=1),
                provenance=OBSERVED,
            ),
        ),
        as_of=BASE,
        provenance=OBSERVED,
    )


def make_resolution(
    *,
    status: RiskLimitResolutionStatus = RiskLimitResolutionStatus.AVAILABLE,
    risk_limit_set_id: str | None = "risk-limit-set-1",
    applicable_limit_ids: tuple[str, ...] = ("limit-1",),
):
    return RiskLimitResolution(
        status=status,
        risk_limit_set_id=risk_limit_set_id,
        applicable_limit_ids=applicable_limit_ids,
    )


def make_reservation(
    *,
    reservation_id: str = "reservation-1",
    reserved_amount: str = "100",
    state: ReservationState = ReservationState.ACTIVE,
):
    return Reservation(
        reservation_id=reservation_id,
        account_id="account-1",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal(reserved_amount),
        consumed_amount=Decimal("0"),
        remaining_amount=Decimal(reserved_amount),
        state=state,
        proposal_id="proposal-1",
        risk_decision_id="risk-decision-1",
        correlation_id="correlation-1",
        client_order_id=None,
        exchange_order_id=None,
        created_at=BASE,
        updated_at=BASE,
    )


def make_read_set(
    *,
    read_at: datetime = BASE,
    completeness: Completeness = Completeness.COMPLETE,
    reservation: Reservation | None = None,
):
    return ReservationReadSet(
        account_id="account-1",
        reservations=(make_reservation() if reservation is None else reservation,),
        read_at=read_at,
        completeness=completeness,
    )


def make_position(*, completeness: Completeness = Completeness.COMPLETE):
    return CanonicalPosition(
        position_id="position-1",
        account_id="account-1",
        symbol="BTC/USDT",
        quantity=Decimal("0.02"),
        as_of=BASE,
        completeness=completeness,
        provenance=OBSERVED,
    )


def make_exposure(
    *,
    quantity: str = "0.02",
    completeness: Completeness = Completeness.COMPLETE,
):
    return CanonicalExposure(
        exposure_id="exposure-1",
        account_id="account-1",
        symbol="BTC/USDT",
        quantity=Decimal(quantity),
        valuation_price=Decimal("50000"),
        notional=Decimal(quantity) * Decimal("50000"),
        as_of=BASE,
        valuation_as_of=BASE,
        completeness=completeness,
        provenance=OBSERVED,
    )


def make_effective_capacity(*, completeness: Completeness = Completeness.COMPLETE):
    return EffectiveCapacity(
        account_id="account-1",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        canonical_available=Decimal("900"),
        protected_active_reserved=Decimal("100"),
        effective_available=Decimal("800"),
        status=EffectiveCapacityStatus.AVAILABLE,
        completeness=completeness,
    )


def make_market_data(*, market_data_id: str = "market-data-1"):
    start = BASE - timedelta(minutes=2)
    candle = Candle(
        start=start,
        end=start + timedelta(minutes=1),
        timeframe="1m",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.FINAL,
    )
    return MarketData(
        instrument=INSTRUMENT,
        source=SourceIdentity(
            source_id="source-1",
            provider="synthetic",
            venue="SYNTHETIC",
        ),
        data_type=CANDLE_DATA_TYPE,
        observed_at=start + timedelta(seconds=30),
        received_at=BASE - timedelta(seconds=30),
        available_at=BASE,
        payload=candle,
        quality=DataQuality.VALID,
        completeness=DataCompleteness.COMPLETE,
        source_sequence="1",
        market_data_id=market_data_id,
    )


def make_observation(*, observation_id: str = "observation-1"):
    return __import__(
        "bot_obrero.analysis_contracts",
        fromlist=["MarketObservation"],
    ).MarketObservation(
        symbol="BTC/USDT",
        observation_timestamp=BASE,
        available_timestamp=BASE,
        observation_type=CANDLE_DATA_TYPE,
        values={"close": Decimal("105")},
        provenance=OBSERVED,
        venue="SYNTHETIC",
        observation_id=observation_id,
    )


def make_analysis_result():
    return AnalysisResult(
        symbol="BTC/USDT",
        observation_ids=("observation-1",),
        analysis_type="indicator.ema",
        values={"ema": Decimal("104.5")},
        calculated_at=EVALUATION,
        decision_timestamp=BASE,
        provenance=DERIVED,
        analysis_id="analysis-1",
    )


def make_signal(*, signal_id: str = "signal-1"):
    return Signal(
        symbol="BTC/USDT",
        hypothesis_id="hypothesis-1",
        direction="LONG",
        generated_at=EVALUATION,
        decision_timestamp=BASE,
        provenance=DERIVED,
        evidence={"analysis_id": "analysis-1"},
        validity=SignalValidity.VALID,
        signal_id=signal_id,
    )


def make_context(
    *,
    proposal: TradeProposal | None = None,
    evaluation_timestamp: datetime = EVALUATION,
    account: CanonicalAccountState | None = None,
    limits: RiskLimitSet | None = None,
    resolution: RiskLimitResolution | None = None,
    bindings: tuple[AvailabilityBinding, ...] | None = None,
    position: CanonicalPosition | None = None,
    exposure: CanonicalExposure | None = None,
    read_set: ReservationReadSet | None = None,
    effective_capacity: EffectiveCapacity | None = None,
    market_data: MarketData | None = None,
    market_observation=None,
    analysis_result: AnalysisResult | None = None,
    signal: Signal | None = None,
):
    proposal = make_proposal() if proposal is None else proposal
    account = make_account() if account is None else account
    limits = make_limits() if limits is None else limits

    if bindings is None:
        collected = [
            AvailabilityBinding(
                AvailabilitySubjectKind.ACCOUNT_STATE,
                account.account_state_id,
                BASE,
                "test",
                "account-evidence",
            ),
            AvailabilityBinding(
                AvailabilitySubjectKind.RISK_LIMIT_SET,
                limits.risk_limit_set_id,
                BASE,
                "test",
                "limits-evidence",
            ),
        ]
        if read_set is not None:
            collected.append(
                AvailabilityBinding(
                    AvailabilitySubjectKind.RESERVATION_READ_SET,
                    derive_reservation_read_set_id(read_set),
                    BASE,
                    "test",
                    "reservation-evidence",
                )
            )
        if exposure is not None:
            collected.append(
                AvailabilityBinding(
                    AvailabilitySubjectKind.EXPOSURE,
                    exposure.exposure_id,
                    BASE,
                    "test",
                    "exposure-evidence",
                )
            )
        if market_data is not None:
            collected.append(
                AvailabilityBinding(
                    AvailabilitySubjectKind.MARKET_DATA,
                    market_data.market_data_id,
                    BASE,
                    "test",
                    "market-data-evidence",
                )
            )
        if market_observation is not None:
            collected.append(
                AvailabilityBinding(
                    AvailabilitySubjectKind.MARKET_OBSERVATION,
                    market_observation.observation_id,
                    BASE,
                    "test",
                    "observation-evidence",
                )
            )
        bindings = tuple(collected)

    return RiskEvaluationContext(
        trade_proposal=proposal,
        instrument=INSTRUMENT,
        canonical_account_state=account,
        risk_limit_set=limits,
        evaluation_timestamp=evaluation_timestamp,
        availability_bindings=bindings,
        risk_limit_resolution=resolution,
        canonical_position=position,
        canonical_exposure=exposure,
        reservation_read_set=read_set,
        effective_capacity=effective_capacity,
        market_data=market_data,
        market_observation=market_observation,
        analysis_result=analysis_result,
        signal=signal,
    )


def test_context_exports_are_phase_i4_only():
    module = __import__("bot_obrero.risk_evaluation_context", fromlist=["*"])
    assert set(module.__all__) == {
        "RiskEvaluationContext",
        "RiskEvaluationContextError",
        "RiskEvaluationContextStatus",
    }


def test_complete_context_exposes_trade_proposal_identity():
    proposal = make_proposal()
    context = make_context(proposal=proposal, resolution=make_resolution())

    assert context.proposal_id == proposal.proposal_id
    assert context.signal_id == proposal.signal_id
    assert context.correlation_id == proposal.correlation_id
    assert context.decision_timestamp == proposal.decision_timestamp
    assert context.is_complete is True
    assert context.completeness is RiskEvaluationContextStatus.COMPLETE
    assert context.evaluation_context_id.startswith("risk-evaluation-context-v1:")


def test_same_logical_context_has_same_deterministic_identity():
    proposal = make_proposal()
    first = make_context(proposal=proposal, resolution=make_resolution())
    second = make_context(proposal=proposal, resolution=make_resolution())

    assert first.evaluation_context_id == second.evaluation_context_id


def test_material_proposal_change_changes_identity():
    proposal = make_proposal()
    first = make_context(proposal=proposal, resolution=make_resolution())
    changed = replace(proposal, correlation_id="correlation-2")
    second = make_context(proposal=changed, resolution=make_resolution())

    assert first.evaluation_context_id != second.evaluation_context_id


def test_material_snapshot_change_changes_identity():
    proposal = make_proposal()
    first = make_context(proposal=proposal, resolution=make_resolution())
    changed_account = make_account(available="899")
    second = make_context(
        proposal=proposal,
        account=changed_account,
        resolution=make_resolution(),
    )

    assert first.evaluation_context_id != second.evaluation_context_id


def test_evaluation_timestamp_is_material_identity_content():
    proposal = make_proposal()
    first = make_context(proposal=proposal, resolution=make_resolution())
    second = make_context(
        proposal=proposal,
        evaluation_timestamp=EVALUATION + timedelta(seconds=1),
        resolution=make_resolution(),
    )

    assert first.evaluation_context_id != second.evaluation_context_id


def test_reservation_read_set_identity_delegates_to_i3_and_excludes_read_at():
    proposal = make_proposal()
    first_read = make_read_set(read_at=BASE)
    second_read = make_read_set(read_at=EVALUATION)
    first = make_context(
        proposal=proposal,
        read_set=first_read,
        resolution=make_resolution(),
    )
    second = make_context(
        proposal=proposal,
        read_set=second_read,
        resolution=make_resolution(),
    )

    assert derive_reservation_read_set_id(first_read) == derive_reservation_read_set_id(second_read)
    assert first.evaluation_context_id == second.evaluation_context_id


def test_reservation_read_set_material_change_changes_identity():
    proposal = make_proposal()
    first_read = make_read_set()
    changed_read = make_read_set(reservation=make_reservation(reserved_amount="110"))
    first = make_context(
        proposal=proposal,
        read_set=first_read,
        resolution=make_resolution(),
    )
    second = make_context(
        proposal=proposal,
        read_set=changed_read,
        resolution=make_resolution(),
    )

    assert first.evaluation_context_id != second.evaluation_context_id


def test_context_is_immutable():
    context = make_context(resolution=make_resolution())

    with pytest.raises(FrozenInstanceError):
        context.evaluation_timestamp = EVALUATION + timedelta(seconds=1)

    with pytest.raises(FrozenInstanceError):
        context.incompleteness_reasons = ("changed",)

    with pytest.raises(FrozenInstanceError):
        context.canonical_account_state.balances = ()


def test_equal_and_earlier_proposal_timestamps_are_valid():
    equal = make_context(
        proposal=make_proposal(decision_timestamp=EVALUATION),
        resolution=make_resolution(),
    )
    earlier = make_context(
        proposal=make_proposal(decision_timestamp=BASE),
        resolution=make_resolution(),
    )

    assert equal.is_complete
    assert earlier.is_complete


def test_future_proposal_timestamp_is_rejected():
    with pytest.raises(RiskEvaluationContextError, match="decision_timestamp"):
        make_context(
            proposal=make_proposal(decision_timestamp=LATE),
            resolution=make_resolution(),
        )


def test_naive_evaluation_timestamp_is_rejected_without_normalization():
    with pytest.raises(RiskEvaluationContextError, match="timezone-aware"):
        make_context(
            evaluation_timestamp=datetime(2026, 10, 5, 20, 10),
            resolution=make_resolution(),
        )


def test_binding_exact_evaluation_time_is_valid():
    bindings = (
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "account-state-1",
            EVALUATION,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            EVALUATION,
            "test",
            "limits-evidence",
        ),
    )
    context = make_context(bindings=bindings, resolution=make_resolution())

    assert context.is_complete


def test_binding_after_evaluation_time_is_rejected():
    bindings = (
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "account-state-1",
            LATE,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            BASE,
            "test",
            "limits-evidence",
        ),
    )

    with pytest.raises(ValueError, match="not valid"):
        make_context(bindings=bindings, resolution=make_resolution())


@pytest.mark.parametrize(
    "missing_kind",
    [
        AvailabilitySubjectKind.ACCOUNT_STATE,
        AvailabilitySubjectKind.RISK_LIMIT_SET,
    ],
)
def test_missing_required_binding_produces_incomplete_context(missing_kind):
    all_bindings = [
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "account-state-1",
            BASE,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            BASE,
            "test",
            "limits-evidence",
        ),
    ]
    bindings = tuple(item for item in all_bindings if item.subject_kind is not missing_kind)
    context = make_context(bindings=bindings, resolution=make_resolution())

    assert context.completeness is RiskEvaluationContextStatus.INCOMPLETE
    assert any(missing_kind.value in reason for reason in context.incompleteness_reasons)

    with pytest.raises(RiskEvaluationContextError, match="incomplete"):
        context.require_complete()


def test_missing_risk_limit_resolution_is_incomplete_not_approved():
    context = make_context(resolution=None)

    assert context.completeness is RiskEvaluationContextStatus.INCOMPLETE
    assert "RISK_LIMIT_RESOLUTION_MISSING" in context.incompleteness_reasons
    with pytest.raises(RiskEvaluationContextError, match="incomplete"):
        context.require_complete()


@pytest.mark.parametrize(
    ("status", "expected_complete"),
    [
        (RiskLimitResolutionStatus.AVAILABLE, True),
        (RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT, True),
        (RiskLimitResolutionStatus.LIMITS_UNAVAILABLE, False),
        (RiskLimitResolutionStatus.LIMITS_INCOMPLETE, False),
    ],
)
def test_risk_limit_resolution_semantics_are_preserved(status, expected_complete):
    if status is RiskLimitResolutionStatus.LIMITS_UNAVAILABLE:
        resolution = make_resolution(
            status=status,
            risk_limit_set_id=None,
            applicable_limit_ids=(),
        )
    elif status is RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT:
        resolution = make_resolution(
            status=status,
            risk_limit_set_id="risk-limit-set-1",
            applicable_limit_ids=(),
        )
    else:
        resolution = make_resolution(
            status=status,
            risk_limit_set_id="risk-limit-set-1",
            applicable_limit_ids=() if status is RiskLimitResolutionStatus.LIMITS_INCOMPLETE else ("limit-1",),
        )

    context = make_context(resolution=resolution)

    assert context.is_complete is expected_complete


def test_resolution_must_match_the_risk_limit_snapshot():
    resolution = make_resolution(risk_limit_set_id="risk-limit-set-other")

    with pytest.raises(RiskEvaluationContextError, match="must match risk_limit_set"):
        make_context(resolution=resolution)


def test_resolution_cannot_claim_unknown_limit_ids():
    resolution = make_resolution(applicable_limit_ids=("limit-unknown",))

    with pytest.raises(RiskEvaluationContextError, match="unknown applicable"):
        make_context(resolution=resolution)


def test_binding_subject_id_mismatch_is_rejected():
    bindings = (
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "wrong-account-state",
            BASE,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            BASE,
            "test",
            "limits-evidence",
        ),
    )

    with pytest.raises(RiskEvaluationContextError, match="does not match"):
        make_context(bindings=bindings, resolution=make_resolution())


def test_binding_without_matching_snapshot_is_rejected():
    binding = AvailabilityBinding(
        AvailabilitySubjectKind.MARKET_DATA,
        "market-data-1",
        BASE,
        "test",
        "market-data-evidence",
    )
    with pytest.raises(RiskEvaluationContextError, match="no matching snapshot"):
        make_context(
            bindings=(
                AvailabilityBinding(
                    AvailabilitySubjectKind.ACCOUNT_STATE,
                    "account-state-1",
                    BASE,
                    "test",
                    "account-evidence",
                ),
                AvailabilityBinding(
                    AvailabilitySubjectKind.RISK_LIMIT_SET,
                    "risk-limit-set-1",
                    BASE,
                    "test",
                    "limits-evidence",
                ),
                binding,
            ),
            resolution=make_resolution(),
        )


def test_optional_inputs_are_preserved_without_new_contracts():
    read_set = make_read_set()
    market_data = make_market_data()
    market_observation = make_observation()
    context = make_context(
        resolution=make_resolution(),
        position=make_position(),
        exposure=make_exposure(),
        read_set=read_set,
        effective_capacity=make_effective_capacity(),
        market_data=market_data,
        market_observation=market_observation,
        analysis_result=make_analysis_result(),
        signal=make_signal(),
    )

    assert context.is_complete
    assert context.canonical_position is not None
    assert context.canonical_exposure is not None
    assert context.reservation_read_set == read_set
    assert context.reservation_read_set is not read_set
    assert context.effective_capacity is not None
    assert context.market_data == market_data
    assert context.market_data is not market_data
    assert context.market_observation == market_observation
    assert context.market_observation is not market_observation
    assert context.analysis_result is not None
    assert context.signal is not None


def test_optional_position_does_not_invent_an_i3_binding_kind():
    context = make_context(
        resolution=make_resolution(),
        position=make_position(),
    )

    assert context.is_complete
    assert all(
        binding.subject_kind is not AvailabilitySubjectKind.ACCOUNT_STATE
        or binding.subject_id == "account-state-1"
        for binding in context.availability_bindings
    )


@pytest.mark.parametrize(
    ("kind", "builder"),
    [
        (AvailabilitySubjectKind.EXPOSURE, make_exposure),
        (AvailabilitySubjectKind.RESERVATION_READ_SET, make_read_set),
        (AvailabilitySubjectKind.MARKET_DATA, make_market_data),
        (AvailabilitySubjectKind.MARKET_OBSERVATION, make_observation),
    ],
)
def test_optional_supported_snapshots_require_their_availability_binding(kind, builder):
    if kind is AvailabilitySubjectKind.RESERVATION_READ_SET:
        read_set = builder()
        context = make_context(
            read_set=read_set,
            bindings=(
                AvailabilityBinding(
                    AvailabilitySubjectKind.ACCOUNT_STATE,
                    "account-state-1",
                    BASE,
                    "test",
                    "account-evidence",
                ),
                AvailabilityBinding(
                    AvailabilitySubjectKind.RISK_LIMIT_SET,
                    "risk-limit-set-1",
                    BASE,
                    "test",
                    "limits-evidence",
                ),
            ),
            resolution=make_resolution(),
        )
    else:
        snapshot = builder()
        kwargs = {
            "exposure": snapshot if kind is AvailabilitySubjectKind.EXPOSURE else None,
            "market_data": snapshot if kind is AvailabilitySubjectKind.MARKET_DATA else None,
            "market_observation": snapshot if kind is AvailabilitySubjectKind.MARKET_OBSERVATION else None,
        }
        context = make_context(
            **kwargs,
            bindings=(
                AvailabilityBinding(
                    AvailabilitySubjectKind.ACCOUNT_STATE,
                    "account-state-1",
                    BASE,
                    "test",
                    "account-evidence",
                ),
                AvailabilityBinding(
                    AvailabilitySubjectKind.RISK_LIMIT_SET,
                    "risk-limit-set-1",
                    BASE,
                    "test",
                    "limits-evidence",
                ),
            ),
            resolution=make_resolution(),
        )

    assert context.completeness is RiskEvaluationContextStatus.INCOMPLETE
    assert any(kind.value in reason for reason in context.incompleteness_reasons)


def test_partial_required_snapshots_are_incomplete():
    context = make_context(
        account=make_account(completeness=Completeness.PARTIAL),
        resolution=make_resolution(),
    )

    assert "ACCOUNT_STATE_INCOMPLETE" in context.incompleteness_reasons

    context = make_context(
        read_set=make_read_set(completeness=Completeness.PARTIAL),
        resolution=make_resolution(),
    )
    assert "RESERVATION_READ_SET_INCOMPLETE" in context.incompleteness_reasons


def test_effective_capacity_and_optional_snapshots_retain_fail_closed_completeness():
    context = make_context(
        effective_capacity=make_effective_capacity(completeness=Completeness.PARTIAL),
        resolution=make_resolution(),
    )

    assert "EFFECTIVE_CAPACITY_INCOMPLETE" in context.incompleteness_reasons

    context = make_context(
        market_data=replace(make_market_data(), completeness=DataCompleteness.PARTIAL),
        resolution=make_resolution(),
    )
    assert "MARKET_DATA_INCOMPLETE" in context.incompleteness_reasons

    context = make_context(
        position=make_position(completeness=Completeness.PARTIAL),
        resolution=make_resolution(),
    )
    assert "POSITION_INCOMPLETE" in context.incompleteness_reasons



def test_market_data_unknown_availability_is_incomplete_even_with_valid_binding():
    data = replace(make_market_data(), available_at=None)
    bindings = (
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "account-state-1",
            BASE,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            BASE,
            "test",
            "limits-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.MARKET_DATA,
            data.market_data_id,
            BASE,
            "external-binding",
            "market-data-evidence",
        ),
    )

    context = make_context(
        market_data=data,
        bindings=bindings,
        resolution=make_resolution(),
    )

    assert context.completeness is RiskEvaluationContextStatus.INCOMPLETE
    assert "MARKET_DATA_AVAILABILITY_UNKNOWN" in context.incompleteness_reasons
    with pytest.raises(RiskEvaluationContextError, match="incomplete"):
        context.require_complete()


def test_market_data_unknown_availability_is_incomplete_without_binding():
    data = replace(make_market_data(), available_at=None)
    context = make_context(
        market_data=data,
        bindings=(
            AvailabilityBinding(
                AvailabilitySubjectKind.ACCOUNT_STATE,
                "account-state-1",
                BASE,
                "test",
                "account-evidence",
            ),
            AvailabilityBinding(
                AvailabilitySubjectKind.RISK_LIMIT_SET,
                "risk-limit-set-1",
                BASE,
                "test",
                "limits-evidence",
            ),
        ),
        resolution=make_resolution(),
    )

    assert context.completeness is RiskEvaluationContextStatus.INCOMPLETE
    assert "MARKET_DATA_AVAILABILITY_UNKNOWN" in context.incompleteness_reasons
    assert "MARKET_DATA_AVAILABILITY_BINDING_MISSING" in context.incompleteness_reasons


def test_market_data_matching_explicit_availability_binding_remains_complete():
    data = make_market_data()
    context = make_context(
        market_data=data,
        bindings=(
            AvailabilityBinding(
                AvailabilitySubjectKind.ACCOUNT_STATE,
                "account-state-1",
                BASE,
                "test",
                "account-evidence",
            ),
            AvailabilityBinding(
                AvailabilitySubjectKind.RISK_LIMIT_SET,
                "risk-limit-set-1",
                BASE,
                "test",
                "limits-evidence",
            ),
            AvailabilityBinding(
                AvailabilitySubjectKind.MARKET_DATA,
                data.market_data_id,
                BASE,
                "test",
                "market-data-evidence",
            ),
        ),
        resolution=make_resolution(),
    )

    assert context.is_complete


def test_market_data_availability_binding_mismatch_still_fails_closed():
    data = make_market_data()
    with pytest.raises(RiskEvaluationContextError, match="MARKET_DATA availability"):
        make_context(
            market_data=data,
            bindings=(
                AvailabilityBinding(
                    AvailabilitySubjectKind.ACCOUNT_STATE,
                    "account-state-1",
                    BASE,
                    "test",
                    "account-evidence",
                ),
                AvailabilityBinding(
                    AvailabilitySubjectKind.RISK_LIMIT_SET,
                    "risk-limit-set-1",
                    BASE,
                    "test",
                    "limits-evidence",
                ),
                AvailabilityBinding(
                    AvailabilitySubjectKind.MARKET_DATA,
                    data.market_data_id,
                    EVALUATION,
                    "test",
                    "market-data-evidence",
                ),
            ),
            resolution=make_resolution(),
        )


def test_provenance_metadata_is_defensively_snapshotted():
    source_provenance = Provenance(
        "mutable-source",
        ArtifactNature.OBSERVED,
        metadata={"nested": {"value": 1}},
    )
    account = replace(make_account(), provenance=source_provenance)
    context = make_context(account=account, resolution=make_resolution())
    original_id = context.evaluation_context_id

    source_provenance.metadata["nested"]["value"] = 2

    assert context.evaluation_context_id == original_id
    assert context.canonical_account_state.provenance.metadata["nested"]["value"] == 1


def test_market_observation_values_are_defensively_snapshotted():
    observation = replace(
        make_observation(),
        values={"payload": {"close": Decimal("105")}},
    )
    context = make_context(
        market_observation=observation,
        resolution=make_resolution(),
    )
    original_id = context.evaluation_context_id

    observation.values["payload"]["close"] = Decimal("999")

    assert context.evaluation_context_id == original_id
    assert context.market_observation.values["payload"]["close"] == Decimal("105")


def test_signal_evidence_is_defensively_snapshotted():
    signal = replace(
        make_signal(),
        evidence={"nested": {"values": [1, 2, 3]}},
    )
    context = make_context(
        signal=signal,
        resolution=make_resolution(),
    )
    original_id = context.evaluation_context_id

    signal.evidence["nested"]["values"].append(4)

    assert context.evaluation_context_id == original_id
    assert context.signal.evidence["nested"]["values"] == (1, 2, 3)


def test_nested_lists_are_frozen_inside_the_context():
    signal = replace(
        make_signal(),
        evidence={"series": [{"value": 1}, {"value": 2}]},
    )
    context = make_context(
        signal=signal,
        resolution=make_resolution(),
    )

    assert isinstance(context.signal.evidence, MappingProxyType)
    assert context.signal.evidence["series"] == (
        {"value": 1},
        {"value": 2},
    )
    with pytest.raises(TypeError):
        context.signal.evidence["series"] = ()
    with pytest.raises(AttributeError):
        context.signal.evidence["series"].append({"value": 3})
    with pytest.raises(TypeError):
        context.signal.evidence["series"][0]["value"] = 99


def test_context_snapshot_isolated_from_source_and_context_mutation():
    observation = make_observation()
    source_values = observation.values
    context = make_context(
        market_observation=observation,
        resolution=make_resolution(),
    )

    with pytest.raises(TypeError):
        context.market_observation.values["close"] = Decimal("999")

    assert source_values["close"] == Decimal("105")
    assert context.market_observation.values["close"] == Decimal("105")


def test_identity_tracks_snapshot_content_across_external_mutation():
    observation = replace(
        make_observation(),
        values={"payload": {"close": Decimal("105")}},
    )
    context_a = make_context(
        market_observation=observation,
        resolution=make_resolution(),
    )
    identity_a = context_a.evaluation_context_id

    observation.values["payload"]["close"] = Decimal("106")
    context_b = make_context(
        market_observation=observation,
        resolution=make_resolution(),
    )

    assert context_a.evaluation_context_id == identity_a
    assert context_a.market_observation.values["payload"]["close"] == Decimal("105")
    assert context_b.market_observation.values["payload"]["close"] == Decimal("106")
    assert context_b.evaluation_context_id != identity_a

def test_market_data_binding_must_agree_with_snapshot_availability():
    data = make_market_data()
    bindings = (
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "account-state-1",
            BASE,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            BASE,
            "test",
            "limits-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.MARKET_DATA,
            data.market_data_id,
            EVALUATION,
            "test",
            "market-data-evidence",
        ),
    )

    with pytest.raises(RiskEvaluationContextError, match="MARKET_DATA availability"):
        make_context(
            market_data=data,
            bindings=bindings,
            resolution=make_resolution(),
        )


def test_market_observation_binding_must_agree_with_snapshot_availability():
    observation = make_observation()
    bindings = (
        AvailabilityBinding(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            "account-state-1",
            BASE,
            "test",
            "account-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "risk-limit-set-1",
            BASE,
            "test",
            "limits-evidence",
        ),
        AvailabilityBinding(
            AvailabilitySubjectKind.MARKET_OBSERVATION,
            observation.observation_id,
            EVALUATION,
            "test",
            "observation-evidence",
        ),
    )

    with pytest.raises(RiskEvaluationContextError, match="MARKET_OBSERVATION availability"):
        make_context(
            market_observation=observation,
            bindings=bindings,
            resolution=make_resolution(),
        )


def test_signal_identity_and_symbol_must_match_proposal_and_instrument():
    with pytest.raises(RiskEvaluationContextError, match="signal_id"):
        make_context(
            signal=make_signal(signal_id="signal-other"),
            resolution=make_resolution(),
        )

    mismatch = replace(
        make_signal(),
        symbol="ETH/USDT",
    )
    with pytest.raises(RiskEvaluationContextError, match="signal.symbol"):
        make_context(signal=mismatch, resolution=make_resolution())


def test_analysis_and_market_data_symbol_identity_are_checked():
    mismatch_analysis = replace(
        make_analysis_result(),
        symbol="ETH/USDT",
    )
    with pytest.raises(RiskEvaluationContextError, match="analysis_result.symbol"):
        make_context(
            analysis_result=mismatch_analysis,
            resolution=make_resolution(),
        )

    mismatch_instrument = InstrumentIdentity(
        instrument_id="other",
        symbol="ETH/USDT",
        market="BINANCE",
        base_asset="ETH",
        quote_asset="USDT",
    )
    with pytest.raises(RiskEvaluationContextError, match="symbol"):
        RiskEvaluationContext(
            trade_proposal=make_proposal(),
            instrument=mismatch_instrument,
            canonical_account_state=make_account(),
            risk_limit_set=make_limits(),
            evaluation_timestamp=EVALUATION,
            availability_bindings=(),
            risk_limit_resolution=make_resolution(),
        )


def test_deterministic_identity_has_no_random_or_wall_clock_generation():
    path = Path("bot_obrero/risk_evaluation_context.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    forbidden_calls = {
        "uuid4",
        "uuid",
        "now",
        "utcnow",
        "randint",
        "urandom",
        "token_bytes",
    }
    observed_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not forbidden_calls.intersection(observed_calls)


def test_provider_neutrality_and_no_io_static_guard():
    path = Path("bot_obrero/risk_evaluation_context.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    forbidden_modules = {
        "bot_obrero.binance_spot",
        "bot_obrero.binance_websocket",
        "bot_obrero.execution",
        "bot_obrero.account",
        "sqlite3",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
    }
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not any(
        module == imported or module.startswith(imported + ".")
        for imported in forbidden_modules
        for module in imports
    )

    source = path.read_text(encoding="utf-8").lower()
    assert "sqlite" not in source
    assert "binance" not in source
    assert "requests" not in source
    assert "httpx" not in source

    forbidden_symbols = {
        "RiskEngine",
        "RiskDecision",
        "RiskAuthorization",
        "FinancialAdmissionBoundary",
        "ExecutionBoundary",
    }
    observed_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    observed_names.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    )
    assert not forbidden_symbols.intersection(observed_names)


def test_static_identity_namespace_and_hash_shape():
    context = make_context(resolution=make_resolution())

    prefix, digest = context.evaluation_context_id.split(":", 1)
    assert prefix == "risk-evaluation-context-v1"
    assert len(digest) == 64
    int(digest, 16)
