from __future__ import annotations

import ast
import json
from dataclasses import replace
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
    PersistedReservationAuthorizationBinding,
    Reservation,
    ReservationAuthorizationBindingInput,
    ReservationResourceKind,
    ReservationTransitionEvidence,
    SQLiteReservationStore,
)
from bot_obrero.risk_authorization import (
    RiskAuthorization,
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

UTC = timezone.utc
BASE = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
EVALUATION = BASE + timedelta(seconds=30)
ACCOUNT = "account-05d"
_PROVENANCE = Provenance("hueso-05d-test", ArtifactNature.OBSERVED)
_UNSET = object()


def make_proposal(
    *,
    side: TradeSide = TradeSide.BUY,
    order_type: TradeOrderType = TradeOrderType.MARKET,
    requested_quantity: Decimal = Decimal("2"),
    requested_price: Decimal | None = None,
    max_quote_spend: Decimal | None = Decimal("7.25"),
) -> TradeProposal:
    if order_type is TradeOrderType.LIMIT:
        requested_price = Decimal("3.25")
        max_quote_spend = None
        price_policy = PricePolicy.FIXED
    else:
        requested_price = None
        max_quote_spend = Decimal("7.25") if side is TradeSide.BUY else None
        price_policy = PricePolicy.MARKET_REFERENCE
    return TradeProposal(
        signal_id="signal-05d",
        symbol="BTC/USDT",
        side=side,
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        max_quote_spend=max_quote_spend,
        price_policy=price_policy,
        order_type=order_type,
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
        decision_timestamp=BASE,
        correlation_id="correlation-05d",
    )


def make_account_state(*, account_id: str = ACCOUNT) -> CanonicalAccountState:
    return CanonicalAccountState(
        account_state_id=f"account-state-{account_id}",
        account_id=account_id,
        as_of=BASE,
        balances=(
            BalanceSnapshot(
                asset="USDT",
                total=Decimal("10000"),
                available=Decimal("10000"),
                locked=Decimal("0"),
            ),
            BalanceSnapshot(
                asset="BTC",
                total=Decimal("100"),
                available=Decimal("100"),
                locked=Decimal("0"),
            ),
        ),
        completeness=Completeness.COMPLETE,
        provenance=_PROVENANCE,
    )


def make_policy() -> RiskEvaluationPolicy:
    return RiskEvaluationPolicy(
        policy_id="policy-05d",
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
    state: CanonicalAccountState | None = None,
) -> RiskEvaluationContext:
    state = make_account_state() if state is None else state
    limit_set = RiskLimitSet(
        risk_limit_set_id=f"limits-{proposal.proposal_id}",
        limits=(
            RiskLimit(
                risk_limit_id=f"limit-{proposal.proposal_id}",
                scope="ACCOUNT",
                metric="MAX_NOTIONAL",
                threshold=Decimal("10000"),
                unit="USDT",
                effective_from=BASE - timedelta(minutes=1),
                effective_until=None,
                provenance=_PROVENANCE,
            ),
        ),
        as_of=BASE,
        provenance=_PROVENANCE,
    )
    resolution = RiskLimitApplicabilityResolver().resolve(
        limit_set,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=EVALUATION,
    )
    bindings = (
        AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.ACCOUNT_STATE,
            subject_id=state.account_state_id,
            available_at=BASE,
            source="synthetic",
            evidence_reference="canonical-account-state",
        ),
        AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.RISK_LIMIT_SET,
            subject_id=limit_set.risk_limit_set_id,
            available_at=BASE,
            source="synthetic",
            evidence_reference="risk-limit-set",
        ),
    )
    return RiskEvaluationContext(
        trade_proposal=proposal,
        instrument=InstrumentIdentity(
            instrument_id="instrument-btcusdt-05d",
            symbol="BTC/USDT",
            market="TEST",
            base_asset="BTC",
            quote_asset="USDT",
        ),
        canonical_account_state=state,
        risk_limit_set=limit_set,
        evaluation_timestamp=EVALUATION,
        availability_bindings=bindings,
        risk_limit_resolution=resolution,
    )


def make_bundle(
    *,
    side: TradeSide = TradeSide.BUY,
    order_type: TradeOrderType = TradeOrderType.MARKET,
    proposal: TradeProposal | None = None,
    state: CanonicalAccountState | None = None,
    decision: RiskDecision | None = None,
    context: RiskEvaluationContext | None = None,
    policy: RiskEvaluationPolicy | None = None,
    evidence: tuple[RiskEvidenceRef, ...] | None = None,
):
    proposal = make_proposal(side=side, order_type=order_type) if proposal is None else proposal
    state = make_account_state() if state is None else state
    context = make_context(proposal, state) if context is None else context
    policy = make_policy() if policy is None else policy
    resolved_evidence = evidence or (
        RiskEvidenceRef(
            kind="RISK_DECISION",
            reference_id=f"decision-{proposal.proposal_id}",
            as_of=BASE,
        ),
        RiskEvidenceRef(
            kind="RISK_LIMIT",
            reference_id=context.risk_limit_set.risk_limit_set_id,
            as_of=BASE,
        ),
    )
    decision = (
        RiskDecision.from_risk_evaluation(
            proposal=proposal,
            context=context,
            policy=policy,
            risk_decision_id=f"decision-{proposal.proposal_id}",
            outcome=RiskDecisionOutcome.APPROVED,
            reason="HUESO 05-D test decision",
            risk_evidence=resolved_evidence,
        )
        if decision is None
        else decision
    )
    auth_result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        context=context,
        policy=policy,
    )
    return proposal, state, context, policy, decision, auth_result.authorization


def expected_terms(proposal: TradeProposal, context: RiskEvaluationContext):
    if proposal.side is TradeSide.BUY and proposal.order_type is TradeOrderType.LIMIT:
        return ReservationResourceKind.QUOTE, context.instrument.quote_asset, (
            proposal.requested_quantity * proposal.requested_price
        )
    if proposal.side is TradeSide.BUY and proposal.order_type is TradeOrderType.MARKET:
        return ReservationResourceKind.QUOTE, context.instrument.quote_asset, proposal.max_quote_spend
    if proposal.side is TradeSide.SELL:
        return ReservationResourceKind.BASE, context.instrument.base_asset, proposal.requested_quantity
    raise AssertionError("unsupported test proposal")


def make_request(
    *,
    side: TradeSide = TradeSide.BUY,
    order_type: TradeOrderType = TradeOrderType.MARKET,
    proposal: TradeProposal | None = None,
    state: CanonicalAccountState | None = None,
    context: RiskEvaluationContext | None = None,
    policy: RiskEvaluationPolicy | None = None,
    decision: RiskDecision | None = None,
    authorization=_UNSET,
    evidence: tuple[RiskEvidenceRef, ...] | None = None,
    resource_kind: ReservationResourceKind | None = None,
    asset: str | None = None,
    amount: Decimal | None = None,
    idempotency_key: str | None = None,
) -> FinancialAdmissionRequest:
    bundle = make_bundle(
        side=side,
        order_type=order_type,
        proposal=proposal,
        state=state,
        context=context,
        policy=policy,
        decision=decision,
        evidence=evidence,
    )
    proposal, state, context, policy, decision, generated_auth = bundle
    auth = generated_auth if authorization is _UNSET else authorization
    expected_resource, expected_asset, expected_amount = expected_terms(proposal, context)
    resource_kind = expected_resource if resource_kind is None else resource_kind
    asset = expected_asset if asset is None else asset
    amount = expected_amount if amount is None else amount
    key_auth = auth if isinstance(auth, RiskAuthorization) else generated_auth
    fingerprint = (
        risk_authorization_semantic_fingerprint(key_auth)
        if isinstance(key_auth, RiskAuthorization)
        else "risk-authorization-semantic-v1:" + ("0" * 64)
    )
    key = (
        build_admission_idempotency_key(
            risk_decision_id=decision.risk_decision_id,
            proposal_id=proposal.proposal_id,
            account_id=state.account_id,
            resource_kind=resource_kind,
            asset=asset,
            approved_reserved_amount=amount,
            correlation_id=proposal.correlation_id,
            authorization_fingerprint=fingerprint,
        )
        if idempotency_key is None
        else idempotency_key
    )
    return FinancialAdmissionRequest(
        proposal=proposal,
        risk_decision=decision,
        context=context,
        policy=policy,
        authorization=auth,
        account_id=state.account_id,
        resource_kind=resource_kind,
        asset=asset,
        approved_reserved_amount=amount,
        canonical_account_state=state,
        created_at=BASE + timedelta(seconds=1),
        admission_idempotency_key=key,
        evidence=decision.risk_evidence if evidence is None else evidence,
    )


def make_store(path) -> SQLiteReservationStore:
    return SQLiteReservationStore(path)


def test_unbound_reservation_writers_are_internal_fixture_only():
    source_path = Path("bot_obrero/reservation.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    store_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SQLiteReservationStore"
    )
    method_nodes = [
        node
        for node in store_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    method_names = {node.name for node in method_nodes}

    assert "admit" in method_names
    assert not any(name.startswith("create") for name in method_names)
    assert "_insert_unbound_fixture" in method_names
    assert "_create_unbound_fixture_from_trade_proposal_and_risk_decision" in method_names
    assert callable(FinancialAdmissionBoundary.admit)

    store = SQLiteReservationStore(":memory:")
    try:
        assert not hasattr(store, "create")
        assert not hasattr(store, "create_from_trade_proposal_and_risk_decision")
    finally:
        store.close()

    fixture_only_methods = {
        "_insert_unbound_fixture",
        "_create_unbound_fixture_from_trade_proposal_and_risk_decision",
    }
    for method in method_nodes:
        if method.name.startswith("_"):
            continue
        called_attributes = {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert called_attributes.isdisjoint(fixture_only_methods), (
            f"public ReservationStore method {method.name} invokes a fixture-only writer"
        )
        string_constants = [
            node.value
            for node in ast.walk(method)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not any("INSERT INTO reservations" in value.upper() for value in string_constants), (
            f"public ReservationStore method {method.name} inserts reservations directly"
        )

    for module_path in Path("bot_obrero").glob("*.py"):
        if module_path.name == "reservation.py":
            continue
        module_tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(module_tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in fixture_only_methods, (
                    f"business module {module_path} called fixture-only "
                    f"ReservationStore API {node.func.attr}"
                )


def test_authorization_fingerprint_is_semantic_and_excludes_random_id():
    request = make_request()
    first = request.authorization
    equivalent = replace(first, authorization_id="a-different-random-id")

    assert risk_authorization_semantic_fingerprint(first) == risk_authorization_semantic_fingerprint(equivalent)
    assert request.admission_idempotency_key.startswith("financial-admission-v2:")

    changed_evidence = replace(
        first,
        risk_evidence=(
            RiskEvidenceRef(kind="OTHER", reference_id="different", as_of=BASE),
        ),
    )
    assert risk_authorization_semantic_fingerprint(changed_evidence) != risk_authorization_semantic_fingerprint(first)


@pytest.mark.parametrize(
    ("side", "order_type", "resource", "asset", "amount"),
    [
        (TradeSide.BUY, TradeOrderType.LIMIT, ReservationResourceKind.QUOTE, "USDT", Decimal("6.50")),
        (TradeSide.BUY, TradeOrderType.MARKET, ReservationResourceKind.QUOTE, "USDT", Decimal("7.25")),
        (TradeSide.SELL, TradeOrderType.LIMIT, ReservationResourceKind.BASE, "BTC", Decimal("2")),
        (TradeSide.SELL, TradeOrderType.MARKET, ReservationResourceKind.BASE, "BTC", Decimal("2")),
    ],
)
def test_exact_economic_terms_are_derived_from_proposal_and_instrument(side, order_type, resource, asset, amount):
    request = make_request(side=side, order_type=order_type)
    assert request.resource_kind is resource
    assert request.asset == asset
    assert request.approved_reserved_amount == amount


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resource_kind", ReservationResourceKind.BASE),
        ("asset", "BTC"),
        ("amount", Decimal("8")),
    ],
)
def test_economic_term_mismatch_is_rejected(field, value):
    values = {field: value}
    with pytest.raises(FinancialAdmissionContractError, match="economic terms"):
        make_request(**values)


def test_missing_or_wrong_type_authorization_is_rejected():
    with pytest.raises(FinancialAdmissionContractError, match="authorization"):
        make_request(authorization=None)
    with pytest.raises(FinancialAdmissionContractError, match="authorization"):
        make_request(authorization=object())


def test_legacy_decision_without_complete_provenance_binding_is_rejected():
    proposal = make_proposal()
    legacy = RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id="legacy-risk-decision",
        outcome=RiskDecisionOutcome.APPROVED,
        reason="legacy decision",
        decision_timestamp=BASE,
        risk_evidence=(
            RiskEvidenceRef(kind="LEGACY", reference_id="legacy", as_of=BASE),
        ),
    )
    with pytest.raises(FinancialAdmissionContractError, match="evaluation_context_id"):
        make_request(proposal=proposal, decision=legacy)


def test_semantic_idempotency_survives_restart_and_preserves_original_authorization_id(tmp_path):
    path = tmp_path / "reservations.sqlite3"
    request = make_request()
    store_a = make_store(path)
    first = FinancialAdmissionBoundary(store_a).admit(request)

    assert first.status is FinancialAdmissionStatus.ADMITTED
    assert first.reservation is not None
    binding_a = store_a.get_authorization_binding(first.reservation.reservation_id)
    assert isinstance(binding_a, PersistedReservationAuthorizationBinding)
    assert binding_a.authorization_id == request.authorization.authorization_id
    assert binding_a.semantic_fingerprint == request.authorization_fingerprint
    assert binding_a.risk_decision_id == request.risk_decision.risk_decision_id
    assert binding_a.risk_evidence == request.evidence
    assert len(store_a.transitions(first.reservation.reservation_id)) == 1
    store_a.close()

    store_b = make_store(path)
    try:
        equivalent_auth = replace(
            request.authorization,
            authorization_id="new-random-authorization-id",
        )
        equivalent_request = replace(request, authorization=equivalent_auth)
        second = FinancialAdmissionBoundary(store_b).admit(equivalent_request)

        assert second.status is FinancialAdmissionStatus.ALREADY_ADMITTED
        assert second.reservation == first.reservation
        binding_b = store_b.get_authorization_binding(first.reservation.reservation_id)
        assert binding_b == binding_a
        assert len(store_b.list_for_proposal(request.proposal.proposal_id)) == 1
    finally:
        store_b.close()


def test_same_decision_with_changed_semantic_binding_is_conflict(tmp_path):
    request = make_request()
    different_evidence = (
        RiskEvidenceRef(kind="RISK_DECISION", reference_id="changed-evidence", as_of=BASE),
        request.evidence[1],
    )
    changed_decision = replace(request.risk_decision, risk_evidence=different_evidence)
    changed_auth = replace(request.authorization, risk_evidence=different_evidence)
    changed_request = make_request(
        proposal=request.proposal,
        state=request.canonical_account_state,
        context=request.context,
        policy=request.policy,
        decision=changed_decision,
        authorization=changed_auth,
        evidence=different_evidence,
    )
    store = make_store(tmp_path / "reservations.sqlite3")
    boundary = FinancialAdmissionBoundary(store)

    assert boundary.admit(request).status is FinancialAdmissionStatus.ADMITTED
    conflict = boundary.admit(changed_request)
    assert conflict.status is FinancialAdmissionStatus.REJECTED
    assert conflict.reason == "IDEMPOTENCY_CONTEXT_CONFLICT"
    assert len(store.list_for_proposal(request.proposal.proposal_id)) == 1
    store.close()


def test_historical_reservation_without_binding_is_not_treated_as_authorized(tmp_path):
    request = make_request()
    store = make_store(tmp_path / "historical.sqlite3")
    legacy_reservation = Reservation.from_trade_proposal_and_risk_decision(
        proposal=request.proposal,
        risk_decision=request.risk_decision,
        account_id=request.account_id,
        resource_kind=request.resource_kind,
        asset=request.asset,
        reserved_amount=request.approved_reserved_amount,
        created_at=request.created_at,
        reservation_id="historical-no-binding",
    )
    store._insert_unbound_fixture(
        legacy_reservation,
        evidence=ReservationTransitionEvidence(
            kind="RESERVATION_CREATED",
            reference_id=request.proposal.proposal_id,
            occurred_at=request.created_at,
        ),
    )

    result = FinancialAdmissionBoundary(store).admit(request)
    assert result.status is FinancialAdmissionStatus.REJECTED
    assert result.reason == "EXISTING_RESERVATION_AUTHORIZATION_UNKNOWN"
    assert store.list_for_proposal(request.proposal.proposal_id) == (legacy_reservation,)
    assert store.get_authorization_binding(legacy_reservation.reservation_id) is None
    store.close()


def test_binding_write_failure_rolls_back_reservation_and_transition(tmp_path, monkeypatch):
    request = make_request()
    store = make_store(tmp_path / "rollback.sqlite3")

    def fail_write(**kwargs):
        raise RuntimeError("binding write failure")

    monkeypatch.setattr(store, "_insert_authorization_binding", fail_write)
    result = FinancialAdmissionBoundary(store).admit(request)

    assert result.status is FinancialAdmissionStatus.REJECTED
    assert store.list_for_proposal(request.proposal.proposal_id) == ()
    assert store._connection.execute("SELECT COUNT(*) FROM reservations").fetchone() == (0,)
    assert store._connection.execute("SELECT COUNT(*) FROM reservation_transitions").fetchone() == (0,)
    assert store._connection.execute("SELECT COUNT(*) FROM reservation_authorization_bindings").fetchone() == (0,)
    store.close()


def test_boundary_revalidates_tampered_request_before_store_access():
    request = make_request()
    object.__setattr__(
        request,
        "authorization",
        replace(request.authorization, policy_version="9.9.9"),
    )

    class RecordingStore:
        def __init__(self):
            self.lookups = 0
            self.admissions = 0

        def list_for_proposal(self, proposal_id):
            self.lookups += 1
            return ()

        def admit(self, **kwargs):
            self.admissions += 1
            raise AssertionError("invalid request must never reach store.admit")

    store = RecordingStore()
    result = FinancialAdmissionBoundary(store).admit(request)
    assert result.status is FinancialAdmissionStatus.REJECTED
    assert result.reason == "ADMISSION_PRECONDITION_REJECTED"
    assert store.lookups == 0
    assert store.admissions == 0


def test_direct_store_requires_binding_dto(tmp_path):
    request = make_request()
    store = make_store(tmp_path / "requires-binding.sqlite3")
    with pytest.raises(TypeError, match="authorization_binding"):
        store.admit(
            proposal=request.proposal,
            risk_decision=request.risk_decision,
            account_id=request.account_id,
            resource_kind=request.resource_kind,
            asset=request.asset,
            reserved_amount=request.approved_reserved_amount,
            canonical_account_state=request.canonical_account_state,
            created_at=request.created_at,
        )
    assert store.list_for_proposal(request.proposal.proposal_id) == ()
    store.close()


def test_binding_dto_rejects_proposal_or_evidence_mismatch(tmp_path):
    request = make_request()
    store = make_store(tmp_path / "dto-mismatch.sqlite3")
    auth = request.authorization
    bad_binding = ReservationAuthorizationBindingInput(
        authorization_id=auth.authorization_id,
        semantic_fingerprint=request.authorization_fingerprint,
        risk_decision_id=auth.risk_decision_id,
        proposal_id="unrelated-proposal",
        signal_id=auth.signal_id,
        correlation_id=auth.correlation_id,
        evaluation_context_id=auth.evaluation_context_id,
        policy_id=auth.policy_id,
        policy_version=auth.policy_version,
        decision_timestamp=auth.decision_timestamp,
        risk_evidence=request.evidence,
    )
    with pytest.raises(Exception, match="authorization binding"):
        store.admit(
            proposal=request.proposal,
            risk_decision=request.risk_decision,
            account_id=request.account_id,
            resource_kind=request.resource_kind,
            asset=request.asset,
            reserved_amount=request.approved_reserved_amount,
            canonical_account_state=request.canonical_account_state,
            created_at=request.created_at,
            authorization_binding=bad_binding,
        )
    assert store.list_for_proposal(request.proposal.proposal_id) == ()
    store.close()


def test_legacy_tables_are_migrated_without_backfilling_binding(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE reservations (
            reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            resource_kind TEXT NOT NULL, asset TEXT NOT NULL,
            reserved_amount TEXT NOT NULL, consumed_amount TEXT NOT NULL,
            remaining_amount TEXT NOT NULL, state TEXT NOT NULL,
            proposal_id TEXT NOT NULL, risk_decision_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL, client_order_id TEXT,
            exchange_order_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE reservation_transitions (
            transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL,
            occurred_at TEXT NOT NULL, evidence_kind TEXT NOT NULL,
            evidence_reference_id TEXT NOT NULL, consumed_delta TEXT NOT NULL,
            FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id)
        )
        """
    )
    connection.commit()
    connection.close()

    store = make_store(path)
    names = {
        row[0]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "reservations" in names
    assert "reservation_transitions" in names
    assert "reservation_authorization_bindings" in names
    assert store._connection.execute(
        "SELECT COUNT(*) FROM reservation_authorization_bindings"
    ).fetchone() == (0,)
    store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_decision_id", "changed-decision"),
        ("proposal_id", "changed-proposal"),
        ("signal_id", "changed-signal"),
        ("correlation_id", "changed-correlation"),
        ("evaluation_context_id", "changed-context"),
        ("policy_id", "changed-policy"),
        ("policy_version", "9.9.9"),
        ("decision_timestamp", BASE + timedelta(seconds=1)),
        (
            "risk_evidence",
            (RiskEvidenceRef(kind="OTHER", reference_id="changed", as_of=BASE),),
        ),
    ],
)
def test_semantic_fingerprint_changes_for_each_logical_binding_field(field, value):
    request = make_request()
    changed = replace(request.authorization, **{field: value})

    assert risk_authorization_semantic_fingerprint(changed) != request.authorization_fingerprint


def test_v2_idempotency_key_changes_with_semantic_fingerprint_and_financial_terms():
    request = make_request()
    changed_auth = replace(
        request.authorization,
        policy_version="9.9.9",
    )
    changed_fingerprint = risk_authorization_semantic_fingerprint(changed_auth)

    key_with_changed_authorization = build_admission_idempotency_key(
        risk_decision_id=request.risk_decision.risk_decision_id,
        proposal_id=request.proposal.proposal_id,
        account_id=request.account_id,
        resource_kind=request.resource_kind,
        asset=request.asset,
        approved_reserved_amount=request.approved_reserved_amount,
        correlation_id=request.proposal.correlation_id,
        authorization_fingerprint=changed_fingerprint,
    )
    key_with_changed_amount = build_admission_idempotency_key(
        risk_decision_id=request.risk_decision.risk_decision_id,
        proposal_id=request.proposal.proposal_id,
        account_id=request.account_id,
        resource_kind=request.resource_kind,
        asset=request.asset,
        approved_reserved_amount=request.approved_reserved_amount + Decimal("1"),
        correlation_id=request.proposal.correlation_id,
        authorization_fingerprint=request.authorization_fingerprint,
    )

    assert key_with_changed_authorization != request.admission_idempotency_key
    assert key_with_changed_amount != request.admission_idempotency_key


def test_request_rejects_each_cross_object_provenance_mismatch():
    request = make_request()

    with pytest.raises(FinancialAdmissionContractError, match="correlation_id mismatch"):
        replace(
            request,
            risk_decision=replace(
                request.risk_decision,
                correlation_id="different-correlation",
            ),
        )

    changed_instrument = replace(
        request.context.instrument,
        instrument_id="different-canonical-instrument",
    )
    changed_context = replace(request.context, instrument=changed_instrument)
    with pytest.raises(FinancialAdmissionContractError, match="evaluation_context_id mismatch"):
        replace(request, context=changed_context)

    changed_policy = replace(request.policy, policy_version="9.9.9")
    with pytest.raises(FinancialAdmissionContractError, match="policy_id/policy_version mismatch"):
        replace(request, policy=changed_policy)

    changed_timestamp_auth = replace(
        request.authorization,
        decision_timestamp=BASE + timedelta(seconds=1),
    )
    with pytest.raises(FinancialAdmissionContractError, match="decision_timestamp mismatch"):
        replace(request, authorization=changed_timestamp_auth)

    changed_evidence = (
        RiskEvidenceRef(kind="OTHER", reference_id="not-bound", as_of=BASE),
    )
    changed_evidence_auth = replace(
        request.authorization,
        risk_evidence=changed_evidence,
    )
    with pytest.raises(FinancialAdmissionContractError, match="authorization evidence"):
        replace(request, authorization=changed_evidence_auth)




@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("risk_decision_id", "different-decision", "authorization risk_decision_id mismatch"),
        ("proposal_id", "different-proposal", "authorization proposal/signal/correlation binding mismatch"),
        ("signal_id", "different-signal", "authorization proposal/signal/correlation binding mismatch"),
        ("correlation_id", "different-correlation", "authorization proposal/signal/correlation binding mismatch"),
        ("evaluation_context_id", "different-context", "authorization evaluation_context_id mismatch"),
        ("policy_id", "different-policy", "authorization policy binding mismatch"),
        ("policy_version", "9.9.9", "authorization policy binding mismatch"),
        ("decision_timestamp", BASE + timedelta(seconds=1), "decision_timestamp mismatch"),
        (
            "risk_evidence",
            (RiskEvidenceRef(kind="OTHER", reference_id="discordant", as_of=BASE),),
            "authorization evidence",
        ),
    ],
)
def test_request_rejects_each_authorization_binding_mismatch(field, value, message):
    request = make_request()
    mismatched_authorization = replace(request.authorization, **{field: value})

    with pytest.raises(FinancialAdmissionContractError, match=message):
        replace(request, authorization=mismatched_authorization)


def test_incomplete_risk_evaluation_context_cannot_enter_admission():
    request = make_request()
    incomplete_context = replace(request.context, availability_bindings=())

    with pytest.raises(FinancialAdmissionContractError, match="context must be COMPLETE"):
        replace(request, context=incomplete_context)

