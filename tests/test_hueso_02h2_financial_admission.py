from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.evidence_binding import AvailabilityBinding, AvailabilitySubjectKind
from bot_obrero.financial_admission import (
    FinancialAdmissionBoundary,
    FinancialAdmissionContractError,
    FinancialAdmissionRequest,
    FinancialAdmissionStatus,
    build_admission_idempotency_key,
)
from bot_obrero.market_data import InstrumentIdentity
from bot_obrero.reservation import (
    Reservation,
    ReservationAdmissionBusy,
    ReservationResourceKind,
    SQLiteReservationStore,
    ReservationTransitionEvidence,
)
from bot_obrero.risk_authorization import (
    RiskAuthorizationStatus,
    authorize_risk_decision,
    risk_authorization_semantic_fingerprint,
)
from bot_obrero.risk_contracts import (
    BalanceSnapshot,
    CanonicalAccountState,
    Completeness,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidenceRef,
    RiskLimit,
    RiskLimitSet,
)
from bot_obrero.risk_evaluation_context import RiskEvaluationContext
from bot_obrero.risk_evaluation_policy import RiskEvaluationPolicy
from bot_obrero.risk_limit_applicability import RiskLimitApplicabilityResolver
from bot_obrero.trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
)
from dataclasses import replace

UTC = timezone.utc
BASE = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
ACCOUNT = "account-h2"
ASSET = "USDT"
RESOURCE = ReservationResourceKind.QUOTE


def make_proposal(
    *,
    proposal_id: str = "proposal-h2",
    signal_id: str = "signal-h2",
    correlation_id: str = "correlation-h2",
    max_quote_spend: Decimal = Decimal("25"),
) -> TradeProposal:
    proposal = TradeProposal(
        signal_id=signal_id,
        symbol="BTC/USDT",
        side=TradeSide.BUY,
        requested_quantity=Decimal("1"),
        requested_price=None,
        max_quote_spend=max_quote_spend,
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
        decision_timestamp=BASE,
        correlation_id=correlation_id,
    )
    object.__setattr__(proposal, "proposal_id", proposal_id)
    return proposal


def make_state(
    *,
    account_id: str = ACCOUNT,
    completeness: Completeness = Completeness.COMPLETE,
    available: str = "100",
) -> CanonicalAccountState:
    amount = Decimal(available)
    return CanonicalAccountState(
        account_state_id=f"state-{account_id}-{available}",
        account_id=account_id,
        as_of=BASE,
        balances=(
            BalanceSnapshot(
                asset=ASSET,
                total=amount,
                available=amount,
                locked=Decimal("0"),
            ),
        ),
        completeness=completeness,
        provenance=Provenance("h2-account", ArtifactNature.OBSERVED),
    )


def make_policy() -> RiskEvaluationPolicy:
    return RiskEvaluationPolicy(
        policy_id="risk-policy-h2",
        policy_version="1.0.0",
        required_availability_subjects=(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            AvailabilitySubjectKind.RISK_LIMIT_SET,
        ),
        require_context_complete=True,
        require_risk_evidence_for_approval=True,
        max_valuation_age=None,
    )


def make_context(
    proposal: TradeProposal,
    account_state: CanonicalAccountState | None = None,
) -> RiskEvaluationContext:
    account_state = make_state() if account_state is None else account_state
    limit_set = RiskLimitSet(
        risk_limit_set_id="h2-limits",
        limits=(
            RiskLimit(
                risk_limit_id="h2-limit",
                scope="ACCOUNT",
                metric="MAX_NOTIONAL",
                threshold=Decimal("1000"),
                unit="USDT",
                effective_from=BASE - timedelta(minutes=1),
                effective_until=None,
                provenance=Provenance("h2-limit", ArtifactNature.OBSERVED),
            ),
        ),
        as_of=BASE,
        provenance=Provenance("h2-limit-set", ArtifactNature.OBSERVED),
    )
    resolution = RiskLimitApplicabilityResolver().resolve(
        limit_set,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE.replace(second=30),
    )
    bindings = (
        AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.ACCOUNT_STATE,
            subject_id=account_state.account_state_id,
            available_at=BASE,
            source="synthetic",
            evidence_reference="account",
        ),
        AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.RISK_LIMIT_SET,
            subject_id=limit_set.risk_limit_set_id,
            available_at=BASE,
            source="synthetic",
            evidence_reference="limits",
        ),
    )
    return RiskEvaluationContext(
        trade_proposal=proposal,
        instrument=InstrumentIdentity(
            instrument_id="h2-instrument-btcusdt",
            symbol="BTC/USDT",
            market="TEST",
            base_asset="BTC",
            quote_asset="USDT",
        ),
        canonical_account_state=account_state,
        risk_limit_set=limit_set,
        evaluation_timestamp=BASE.replace(second=30),
        availability_bindings=bindings,
        risk_limit_resolution=resolution,
    )


def make_decision(
    proposal: TradeProposal,
    *,
    outcome: RiskDecisionOutcome = RiskDecisionOutcome.APPROVED,
    risk_decision_id: str = "risk-h2",
    proposal_id: str | None = None,
    signal_id: str | None = None,
    correlation_id: str | None = None,
    evidence: tuple[RiskEvidenceRef, ...] | None = None,
    context: RiskEvaluationContext | None = None,
    policy: RiskEvaluationPolicy | None = None,
) -> RiskDecision:
    policy = make_policy() if policy is None else policy
    context = make_context(proposal) if context is None else context
    resolved_evidence = (
        (
            RiskEvidenceRef(
                kind="RISK_DECISION",
                reference_id=risk_decision_id,
                as_of=BASE,
            ),
        )
        if evidence is None
        else evidence
    )
    decision = RiskDecision.from_risk_evaluation(
        proposal=proposal,
        context=context,
        policy=policy,
        risk_decision_id=risk_decision_id,
        outcome=outcome,
        reason="HUESO 02-H2 test",
        risk_evidence=resolved_evidence,
    )
    changes = {}
    if proposal_id is not None:
        changes["proposal_id"] = proposal_id
    if signal_id is not None:
        changes["signal_id"] = signal_id
    if correlation_id is not None:
        changes["correlation_id"] = correlation_id
    return replace(decision, **changes) if changes else decision


def make_request(
    *,
    proposal: TradeProposal | None = None,
    decision: RiskDecision | None = None,
    account_id: str = ACCOUNT,
    resource_kind: ReservationResourceKind = RESOURCE,
    asset: str = ASSET,
    amount: Decimal = Decimal("25"),
    state: CanonicalAccountState | None = None,
    created_at: datetime = BASE,
    key: str | None = None,
    evidence: tuple[RiskEvidenceRef, ...] | None = None,
) -> FinancialAdmissionRequest:
    if proposal is None:
        default_limit = (
            amount
            if type(amount) is Decimal and amount.is_finite() and amount > 0
            else Decimal("25")
        )
        proposal = make_proposal(max_quote_spend=default_limit)
    state = make_state(account_id=account_id) if state is None else state
    context = make_context(proposal, state)
    policy = make_policy()
    decision = (
        make_decision(proposal, context=context, policy=policy)
        if decision is None
        else decision
    )
    authorization_result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        context=context,
        policy=policy,
    )
    authorization = authorization_result.authorization
    fingerprint = (
        risk_authorization_semantic_fingerprint(authorization)
        if authorization is not None
        else "risk-authorization-semantic-v1:" + ("0" * 64)
    )
    final_key = (
        build_admission_idempotency_key(
            risk_decision_id=decision.risk_decision_id,
            proposal_id=proposal.proposal_id,
            account_id=account_id,
            resource_kind=resource_kind,
            asset=asset,
            approved_reserved_amount=amount,
            correlation_id=proposal.correlation_id,
            authorization_fingerprint=fingerprint,
        )
        if key is None
        else key
    )
    return FinancialAdmissionRequest(
        proposal=proposal,
        risk_decision=decision,
        context=context,
        policy=policy,
        authorization=authorization,
        account_id=account_id,
        resource_kind=resource_kind,
        asset=asset,
        approved_reserved_amount=amount,
        canonical_account_state=state,
        created_at=created_at,
        admission_idempotency_key=final_key,
        evidence=decision.risk_evidence if evidence is None else evidence,
    )


def make_store(tmp_path: Path) -> SQLiteReservationStore:
    return SQLiteReservationStore(tmp_path / "reservations.sqlite3")


def release(store: SQLiteReservationStore, reservation: Reservation) -> None:
    store.release(
        reservation.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="H2_RELEASE",
            reference_id=reservation.reservation_id,
            occurred_at=BASE.replace(second=1),
        ),
    )


def test_request_is_immutable_and_identity_bound():
    request = make_request()

    with pytest.raises(FrozenInstanceError):
        request.account_id = "other"

    assert request.proposal.proposal_id == request.risk_decision.proposal_id
    assert request.proposal.signal_id == request.risk_decision.signal_id
    assert request.proposal.correlation_id == request.risk_decision.correlation_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposal_id", "different-proposal"),
        ("signal_id", "different-signal"),
        ("correlation_id", "different-correlation"),
    ],
)
def test_identity_mismatch_is_rejected(field, value):
    proposal = make_proposal()
    decision = make_decision(proposal, **{field: value})

    with pytest.raises(FinancialAdmissionContractError, match="mismatch"):
        make_request(proposal=proposal, decision=decision)


@pytest.mark.parametrize(
    "outcome",
    [RiskDecisionOutcome.REJECTED, RiskDecisionOutcome.UNKNOWN],
)
def test_only_approved_risk_decisions_can_cross_boundary(outcome):
    proposal = make_proposal()
    decision = make_decision(proposal, outcome=outcome)

    with pytest.raises(FinancialAdmissionContractError, match="APPROVED"):
        make_request(proposal=proposal, decision=decision)


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        1,
        1.0,
    ],
)
def test_approved_reserved_amount_is_strict_decimal_and_positive(amount):
    with pytest.raises(FinancialAdmissionContractError, match="approved_reserved_amount"):
        make_request(amount=amount)


def test_account_binding_and_completeness_are_fail_closed():
    proposal = make_proposal()
    mismatched_state = make_state(account_id="other-account")
    with pytest.raises(FinancialAdmissionContractError, match="account_id"):
        make_request(proposal=proposal, state=mismatched_state)

    for completeness in (Completeness.PARTIAL, Completeness.UNKNOWN):
        with pytest.raises(FinancialAdmissionContractError, match="COMPLETE"):
            make_request(
                proposal=proposal,
                state=make_state(completeness=completeness),
            )


@pytest.mark.parametrize(
    ("resource_kind", "asset"),
    [
        ("QUOTE", ASSET),
        (RESOURCE, ""),
    ],
)
def test_resource_binding_structural_validation(resource_kind, asset):
    with pytest.raises(FinancialAdmissionContractError):
        make_request(resource_kind=resource_kind, asset=asset)


def test_resource_side_semantics_are_not_fabricated():
    request = make_request(resource_kind=ReservationResourceKind.QUOTE, asset="USDT")
    assert request.proposal.side is TradeSide.BUY
    assert request.resource_kind is ReservationResourceKind.QUOTE


def test_evidence_must_be_explicit_and_match_risk_decision():
    request = make_request()
    assert request.evidence == request.risk_decision.risk_evidence

    proposal = make_proposal()
    with pytest.raises(FinancialAdmissionContractError, match="explicit risk evidence"):
        make_request(
            proposal=proposal,
            decision=make_decision(proposal, evidence=()),
        )

    wrong = (
        RiskEvidenceRef(
            kind="OTHER",
            reference_id="wrong",
            as_of=BASE,
        ),
    )
    with pytest.raises(FinancialAdmissionContractError, match="match"):
        make_request(evidence=wrong)


def test_idempotency_key_binds_full_context():
    request = make_request()

    expected = build_admission_idempotency_key(
        risk_decision_id=request.risk_decision.risk_decision_id,
        proposal_id=request.proposal.proposal_id,
        account_id=request.account_id,
        resource_kind=request.resource_kind,
        asset=request.asset,
        approved_reserved_amount=request.approved_reserved_amount,
        correlation_id=request.proposal.correlation_id,
        authorization_fingerprint=request.authorization_fingerprint,
    )
    assert request.admission_idempotency_key == expected

    with pytest.raises(FinancialAdmissionContractError, match="bind"):
        make_request(key="financial-admission-v1:tampered")


def test_same_key_with_different_amount_is_rejected():
    first = make_request(amount=Decimal("25"))
    with pytest.raises(FinancialAdmissionContractError, match="bind|economic terms"):
        make_request(
            proposal=first.proposal,
            decision=first.risk_decision,
            amount=Decimal("26"),
            key=first.admission_idempotency_key,
        )


def test_same_key_with_different_proposal_is_rejected():
    first = make_request()
    second_proposal = make_proposal(proposal_id="proposal-other")
    second_decision = make_decision(second_proposal)
    with pytest.raises(FinancialAdmissionContractError, match="bind"):
        make_request(
            proposal=second_proposal,
            decision=second_decision,
            key=first.admission_idempotency_key,
        )


def test_same_key_with_different_account_is_rejected():
    first = make_request(account_id="account-a")
    with pytest.raises(FinancialAdmissionContractError, match="bind"):
        make_request(
            proposal=first.proposal,
            decision=first.risk_decision,
            account_id="account-b",
            state=make_state(account_id="account-b"),
            key=first.admission_idempotency_key,
        )


def test_same_key_with_different_correlation_is_rejected():
    first = make_request()
    different_proposal = make_proposal(correlation_id="correlation-other")
    different_decision = make_decision(different_proposal)
    with pytest.raises(FinancialAdmissionContractError, match="bind"):
        make_request(
            proposal=different_proposal,
            decision=different_decision,
            key=first.admission_idempotency_key,
        )


def test_request_created_at_must_be_timezone_aware():
    with pytest.raises(FinancialAdmissionContractError, match="timezone-aware"):
        make_request(created_at=datetime(2026, 10, 5, 12, 0))


def test_happy_path_admits_with_exact_approved_amount(tmp_path):
    store = make_store(tmp_path)
    request = make_request(amount=Decimal("25"))

    result = FinancialAdmissionBoundary(store).admit(request)

    assert result.status is FinancialAdmissionStatus.ADMITTED
    assert result.reservation is not None
    assert result.reservation.reserved_amount == Decimal("25")
    assert result.reservation.proposal_id == request.proposal.proposal_id
    assert result.reservation.risk_decision_id == request.risk_decision.risk_decision_id
    assert result.reservation.account_id == request.account_id
    assert result.reservation.resource_kind is request.resource_kind
    assert result.reservation.asset == request.asset
    store.close()


def test_second_identical_attempt_is_already_admitted(tmp_path):
    store = make_store(tmp_path)
    request = make_request(amount=Decimal("25"))
    boundary = FinancialAdmissionBoundary(store)

    first = boundary.admit(request)
    second = boundary.admit(request)

    assert first.status is FinancialAdmissionStatus.ADMITTED
    assert second.status is FinancialAdmissionStatus.ALREADY_ADMITTED
    assert second.reservation == first.reservation
    assert len(store.list_for_proposal(request.proposal.proposal_id)) == 1
    store.close()


def test_same_key_context_conflict_cannot_create_second_reservation(tmp_path):
    store = make_store(tmp_path)
    request = make_request(amount=Decimal("25"))
    boundary = FinancialAdmissionBoundary(store)

    first = boundary.admit(request)
    assert first.status is FinancialAdmissionStatus.ADMITTED

    with pytest.raises(FinancialAdmissionContractError):
        make_request(
            proposal=request.proposal,
            decision=request.risk_decision,
            amount=Decimal("30"),
            key=request.admission_idempotency_key,
        )

    assert len(store.list_for_proposal(request.proposal.proposal_id)) == 1
    store.close()


def test_different_risk_decision_after_terminal_reuse_is_allowed(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    first_request = make_request(proposal=proposal)
    boundary = FinancialAdmissionBoundary(store)

    first = boundary.admit(first_request)
    assert first.status is FinancialAdmissionStatus.ADMITTED
    assert first.reservation is not None
    release(store, first.reservation)

    second_decision = make_decision(
        proposal,
        risk_decision_id="risk-h2-second",
    )
    second_request = make_request(
        proposal=proposal,
        decision=second_decision,
        amount=Decimal("25"),
    )

    second = boundary.admit(second_request)

    assert second.status is FinancialAdmissionStatus.ADMITTED
    assert second.reservation is not None
    assert second.reservation.risk_decision_id == "risk-h2-second"
    assert second.reservation.reserved_amount == Decimal("25")
    store.close()


def test_same_risk_decision_after_terminal_state_is_already_admitted(tmp_path):
    store = make_store(tmp_path)
    request = make_request()
    boundary = FinancialAdmissionBoundary(store)

    first = boundary.admit(request)
    assert first.reservation is not None
    release(store, first.reservation)

    second = boundary.admit(request)

    assert second.status is FinancialAdmissionStatus.ALREADY_ADMITTED
    assert second.reservation is not None
    assert second.reservation.reservation_id == first.reservation.reservation_id
    assert len(store.list_for_proposal(request.proposal.proposal_id)) == 1
    store.close()


def test_store_rejection_is_fail_closed(tmp_path):
    store = make_store(tmp_path)
    request = make_request(amount=Decimal("101"))

    result = FinancialAdmissionBoundary(store).admit(request)

    assert result.status is FinancialAdmissionStatus.REJECTED
    assert result.reservation is None
    assert "exceeds" in result.reason
    assert store.read_set_for_account(ACCOUNT).reservations == ()
    store.close()


def test_sqlite_busy_is_fail_closed():
    class BusyStore:
        def list_for_proposal(self, proposal_id):
            return ()

        def admit(self, **kwargs):
            raise ReservationAdmissionBusy("busy")

        def create(self, *args, **kwargs):
            raise AssertionError("create() must never be called")

    request = make_request()
    result = FinancialAdmissionBoundary(BusyStore()).admit(request)

    assert result.status is FinancialAdmissionStatus.BUSY
    assert result.reservation is None


def test_unexpected_exception_is_fail_closed():
    class BrokenStore:
        def list_for_proposal(self, proposal_id):
            return ()

        def admit(self, **kwargs):
            raise RuntimeError("unexpected")

        def create(self, *args, **kwargs):
            raise AssertionError("create() must never be called")

    request = make_request()
    result = FinancialAdmissionBoundary(BrokenStore()).admit(request)

    assert result.status is FinancialAdmissionStatus.REJECTED
    assert result.reservation is None
    assert result.reason.startswith("ADMISSION_FAIL_CLOSED:")


def test_delegates_to_admit_not_create_and_preserves_exact_amount():
    calls = []

    class RecordingStore:
        def list_for_proposal(self, proposal_id):
            return ()

        def admit(self, *, request):
            calls.append(("admit", {"request": request}))
            return Reservation.from_trade_proposal_and_risk_decision(
                proposal=request.proposal,
                risk_decision=request.risk_decision,
                account_id=request.account_id,
                resource_kind=request.resource_kind,
                asset=request.asset,
                reserved_amount=request.approved_reserved_amount,
                created_at=request.created_at,
                reservation_id="recording-reservation",
            )

        def create(self, *args, **kwargs):
            raise AssertionError("create() must never be called")

    request = make_request(amount=Decimal("37.50"))
    result = FinancialAdmissionBoundary(RecordingStore()).admit(request)

    assert result.status is FinancialAdmissionStatus.ADMITTED
    assert len(calls) == 1
    assert calls[0][0] == "admit"
    assert calls[0][1]["request"] is request
    assert calls[0][1]["request"].approved_reserved_amount == request.approved_reserved_amount


def test_fail_closed_when_existing_same_decision_context_differs():
    proposal = make_proposal()
    request = make_request(proposal=proposal)

    existing = Reservation.from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=request.risk_decision,
        account_id=ACCOUNT,
        resource_kind=RESOURCE,
        asset=ASSET,
        reserved_amount=Decimal("24"),
        created_at=BASE,
        reservation_id="existing-conflict",
    )

    class ExistingStore:
        def list_for_proposal(self, proposal_id):
            return (existing,)

        def admit(self, **kwargs):
            raise AssertionError("admit() must not be called on idempotency conflict")

        def create(self, *args, **kwargs):
            raise AssertionError("create() must never be called")

    result = FinancialAdmissionBoundary(ExistingStore()).admit(request)

    assert result.status is FinancialAdmissionStatus.REJECTED
    assert result.reservation is None
    assert result.reason == "EXISTING_RESERVATION_AUTHORIZATION_UNKNOWN"


def test_provider_neutrality_and_architecture_guards():
    path = Path("bot_obrero/financial_admission.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    forbidden_imports = {
        "bot_obrero.binance_spot",
        "bot_obrero.binance_websocket",
        "bot_obrero.execution",
        "bot_obrero.account",
        "numpy",
        "pandas",
        "talib",
    }

    imports: list[str] = []
    float_calls: list[int] = []
    float_literals: list[int] = []
    create_calls: list[int] = []
    forbidden_names = {
        "RiskEngine",
        "FinalAdmission",
        "OrderIntent",
        "StrategyRuntime",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                float_calls.append(node.lineno)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "create":
                create_calls.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            float_literals.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in forbidden_names:
            raise AssertionError(f"forbidden architecture symbol: {node.id}")

    assert all(item not in forbidden_imports for item in imports)
    assert float_calls == []
    assert float_literals == []
    assert create_calls == []

    source = path.read_text(encoding="utf-8")
    assert "SQLiteReservationStore.admit()" in source
    assert "persistent_ledger" not in source
    assert "ReservationReadSet" not in source


def test_existing_indicator_modules_remain_present():
    assert Path("bot_obrero/analysis_sma.py").exists()
    assert Path("bot_obrero/analysis_ema.py").exists()
    assert Path("bot_obrero/analysis_rsi.py").exists()


def test_admission_idempotency_survives_store_restart(tmp_path):
    path = tmp_path / "reservations.sqlite3"
    request = make_request(amount=Decimal("25"))

    store_a = SQLiteReservationStore(path)
    boundary_a = FinancialAdmissionBoundary(store_a)

    first = boundary_a.admit(request)

    assert first.status is FinancialAdmissionStatus.ADMITTED
    assert first.reservation is not None
    persisted_reservation = first.reservation
    store_a.close()

    store_b = SQLiteReservationStore(path)
    boundary_b = FinancialAdmissionBoundary(store_b)

    try:
        second = boundary_b.admit(request)

        assert second.status is FinancialAdmissionStatus.ALREADY_ADMITTED
        assert second.reservation == persisted_reservation
        assert len(store_b.list_for_proposal(request.proposal.proposal_id)) == 1
    finally:
        store_b.close()
