from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.evidence_binding import AvailabilityBinding, AvailabilitySubjectKind
from bot_obrero.market_data import InstrumentIdentity
from bot_obrero.risk_authorization import (
    RiskAuthorization,
    RiskAuthorizationContractError,
    RiskAuthorizationStatus,
    authorize_risk_decision,
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
from bot_obrero.risk_engine import RiskEngine
from bot_obrero.risk_evaluation_context import RiskEvaluationContext
from bot_obrero.risk_evaluation_policy import RiskEvaluationPolicy
from bot_obrero.risk_limit_applicability import RiskLimitApplicabilityResolver
from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeProposal, TradeSide

UTC = timezone.utc
BASE = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
EVALUATION = BASE + timedelta(seconds=30)
PROVENANCE = Provenance(
    source="synthetic-risk-authorization",
    nature=ArtifactNature.OBSERVED,
)


def make_proposal(*, quantity="2"):
    return TradeProposal(
        signal_id="signal-1",
        symbol="BTC/USDT",
        side=TradeSide.BUY,
        requested_quantity=Decimal(quantity),
        requested_price=Decimal("100"),
        max_quote_spend=None,
        price_policy=PricePolicy.FIXED,
        order_type=TradeOrderType.LIMIT,
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
        decision_timestamp=BASE,
        correlation_id="corr-1",
    )


def make_limit(*, limit_id="limit-1", threshold="250"):
    return RiskLimit(
        risk_limit_id=limit_id,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        threshold=Decimal(threshold),
        unit="USDT",
        effective_from=BASE - timedelta(minutes=1),
        effective_until=None,
        provenance=PROVENANCE,
    )


def make_context(proposal, *, risk_limits=None, account_completeness=Completeness.COMPLETE):
    risk_limit_set = (
        RiskLimitSet(
            risk_limit_set_id="limits-1",
            limits=(make_limit(),),
            as_of=BASE,
            provenance=PROVENANCE,
        )
        if risk_limits is None
        else risk_limits
    )
    resolution = RiskLimitApplicabilityResolver().resolve(
        risk_limit_set,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=EVALUATION,
    )
    bindings = (
        AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.ACCOUNT_STATE,
            subject_id="account-state-1",
            available_at=BASE,
            source="synthetic",
            evidence_reference="account",
        ),
        AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.RISK_LIMIT_SET,
            subject_id=risk_limit_set.risk_limit_set_id,
            available_at=BASE,
            source="synthetic",
            evidence_reference="limits",
        ),
    )
    account = CanonicalAccountState(
        account_state_id="account-state-1",
        account_id="account-1",
        as_of=BASE,
        balances=(
            BalanceSnapshot(
                asset="USDT",
                total=Decimal("10000"),
                available=Decimal("9000"),
                locked=Decimal("1000"),
            ),
        ),
        completeness=account_completeness,
        provenance=PROVENANCE,
    )
    instrument = InstrumentIdentity(
        instrument_id="instrument-btcusdt",
        symbol="BTC/USDT",
        market="TEST",
        base_asset="BTC",
        quote_asset="USDT",
    )
    return RiskEvaluationContext(
        trade_proposal=proposal,
        instrument=instrument,
        canonical_account_state=account,
        risk_limit_set=risk_limit_set,
        evaluation_timestamp=EVALUATION,
        availability_bindings=bindings,
        risk_limit_resolution=resolution,
    )


def make_policy(*, policy_id="risk-policy-v1", policy_version="1.0.0"):
    return RiskEvaluationPolicy(
        policy_id=policy_id,
        policy_version=policy_version,
        required_availability_subjects=(
            AvailabilitySubjectKind.ACCOUNT_STATE,
            AvailabilitySubjectKind.RISK_LIMIT_SET,
        ),
        require_context_complete=True,
        require_risk_evidence_for_approval=True,
        max_valuation_age=None,
    )


def evaluate(proposal, context, policy=None):
    policy = make_policy() if policy is None else policy
    return RiskEngine().evaluate(
        proposal=proposal,
        context=context,
        policy=policy,
        risk_limit_resolver=RiskLimitApplicabilityResolver(),
    )


def approved_bundle():
    proposal = make_proposal()
    context = make_context(proposal)
    policy = make_policy()
    decision = evaluate(proposal, context, policy)
    assert decision.outcome is RiskDecisionOutcome.APPROVED
    return proposal, context, policy, decision


def authorize(bundle):
    proposal, context, policy, decision = bundle
    return authorize_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        context=context,
        policy=policy,
    )


def test_approved_decision_produces_authorized_artifact():
    proposal, context, policy, decision = approved_bundle()
    result = authorize((proposal, context, policy, decision))

    assert result.status is RiskAuthorizationStatus.AUTHORIZED
    assert isinstance(result.authorization, RiskAuthorization)
    authorization = result.authorization
    assert authorization.status is RiskAuthorizationStatus.AUTHORIZED
    assert authorization.risk_decision_id == decision.risk_decision_id
    assert authorization.proposal_id == proposal.proposal_id
    assert authorization.signal_id == proposal.signal_id
    assert authorization.correlation_id == proposal.correlation_id
    assert authorization.evaluation_context_id == context.evaluation_context_id
    assert authorization.policy_id == policy.policy_id
    assert authorization.policy_version == policy.policy_version
    assert authorization.decision_timestamp == proposal.decision_timestamp
    assert authorization.risk_evidence is decision.risk_evidence
    assert result.reason == "RISK_AUTHORIZATION_CREATED"


def test_rejected_decision_cannot_authorize():
    proposal = make_proposal(quantity="3")
    context = make_context(proposal)
    policy = make_policy()
    decision = evaluate(proposal, context, policy)

    assert decision.outcome is RiskDecisionOutcome.REJECTED
    result = authorize((proposal, context, policy, decision))

    assert result.status is RiskAuthorizationStatus.REJECTED
    assert result.authorization is None
    assert result.reason == decision.reason


def test_unknown_decision_cannot_authorize():
    proposal = make_proposal()
    context = make_context(proposal, account_completeness=Completeness.PARTIAL)
    policy = make_policy()
    decision = evaluate(proposal, context, policy)

    assert decision.outcome is RiskDecisionOutcome.UNKNOWN
    result = authorize((proposal, context, policy, decision))

    assert result.status is RiskAuthorizationStatus.UNKNOWN
    assert result.authorization is None
    assert result.reason == decision.reason


@pytest.mark.parametrize(
    ("mutator", "expected_reason"),
    [
        (lambda d: replace(d, proposal_id="other-proposal"), "PROPOSAL_ID_MISMATCH"),
        (lambda d: replace(d, signal_id="other-signal"), "SIGNAL_ID_MISMATCH"),
        (lambda d: replace(d, correlation_id="other-correlation"), "CORRELATION_ID_MISMATCH"),
        (lambda d: replace(d, evaluation_context_id="other-context"), "EVALUATION_CONTEXT_ID_MISMATCH"),
        (lambda d: replace(d, policy_id="other-policy"), "POLICY_ID_MISMATCH"),
        (lambda d: replace(d, policy_version="9.9.9"), "POLICY_VERSION_MISMATCH"),
        (lambda d: replace(d, decision_timestamp=BASE + timedelta(seconds=1)), "DECISION_TIMESTAMP_MISMATCH"),
        (lambda d: replace(d, risk_evidence=()), "RISK_EVIDENCE_EMPTY"),
    ],
)
def test_approved_binding_tampering_is_fail_closed(mutator, expected_reason):
    proposal, context, policy, decision = approved_bundle()
    tampered = mutator(decision)
    result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=tampered,
        context=context,
        policy=policy,
    )

    assert result.status is RiskAuthorizationStatus.UNKNOWN
    assert result.authorization is None
    assert result.reason == expected_reason


def test_context_mismatch_is_fail_closed():
    proposal, context, policy, decision = approved_bundle()
    other_limits = RiskLimitSet(
        risk_limit_set_id="limits-2",
        limits=(make_limit(limit_id="limit-2"),),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    other_context = make_context(proposal, risk_limits=other_limits)

    result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        context=other_context,
        policy=policy,
    )

    assert result.status is RiskAuthorizationStatus.UNKNOWN
    assert result.authorization is None
    assert result.reason == "EVALUATION_CONTEXT_ID_MISMATCH"


def test_policy_mismatch_by_object_is_fail_closed():
    proposal, context, policy, decision = approved_bundle()

    for mismatched in (
        make_policy(policy_id="other-policy"),
        make_policy(policy_version="9.9.9"),
    ):
        result = authorize_risk_decision(
            proposal=proposal,
            risk_decision=decision,
            context=context,
            policy=mismatched,
        )
        assert result.status is RiskAuthorizationStatus.UNKNOWN
        assert result.authorization is None


def test_proposal_signal_correlation_mismatch_is_fail_closed():
    proposal, context, policy, decision = approved_bundle()

    for field_name in ("proposal_id", "signal_id", "correlation_id"):
        forged = deepcopy(proposal)
        object.__setattr__(forged, field_name, f"other-{field_name}")
        result = authorize_risk_decision(
            proposal=forged,
            risk_decision=decision,
            context=context,
            policy=policy,
        )
        assert result.status is RiskAuthorizationStatus.UNKNOWN
        assert result.authorization is None


def test_incomplete_context_cannot_authorize_even_with_matching_context_binding():
    proposal, context, policy, decision = approved_bundle()
    incomplete = make_context(proposal, account_completeness=Completeness.PARTIAL)
    rebound = replace(decision, evaluation_context_id=incomplete.evaluation_context_id)

    result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=rebound,
        context=incomplete,
        policy=policy,
    )

    assert result.status is RiskAuthorizationStatus.UNKNOWN
    assert result.authorization is None
    assert result.reason == "RISK_CONTEXT_INCOMPLETE"


def test_unbound_legacy_decision_cannot_authorize():
    proposal, context, policy, _ = approved_bundle()
    unbound = RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id="legacy",
        outcome=RiskDecisionOutcome.APPROVED,
        reason="legacy-approved",
        decision_timestamp=proposal.decision_timestamp,
        risk_evidence=(
            RiskEvidenceRef(kind="TEST", reference_id="evidence", as_of=BASE),
        ),
    )
    result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=unbound,
        context=context,
        policy=policy,
    )
    assert result.status is RiskAuthorizationStatus.UNKNOWN
    assert result.authorization is None
    assert result.reason == "EVALUATION_CONTEXT_ID_MISMATCH"


def test_empty_evidence_is_fail_closed():
    proposal, context, policy, decision = approved_bundle()
    result = authorize_risk_decision(
        proposal=proposal,
        risk_decision=replace(decision, risk_evidence=()),
        context=context,
        policy=policy,
    )
    assert result.status is RiskAuthorizationStatus.UNKNOWN
    assert result.authorization is None
    assert result.reason == "RISK_EVIDENCE_EMPTY"


def test_risk_evidence_is_preserved_without_reconstruction():
    proposal, context, policy, decision = approved_bundle()
    result = authorize((proposal, context, policy, decision))

    assert result.authorization is not None
    assert result.authorization.risk_evidence == decision.risk_evidence
    assert result.authorization.risk_evidence is decision.risk_evidence


def test_authorization_and_inputs_are_immutable_and_not_mutated():
    proposal, context, policy, decision = approved_bundle()
    before = (proposal, context, policy, decision)

    result = authorize((proposal, context, policy, decision))
    authorization = result.authorization
    assert authorization is not None

    assert (proposal, context, policy, decision) == before

    with pytest.raises(FrozenInstanceError):
        authorization.policy_id = "other"

    with pytest.raises(FrozenInstanceError):
        decision.policy_id = "other"


def test_logical_determinism_ignores_instance_ids():
    first = approved_bundle()
    second = approved_bundle()

    first_result = authorize(first)
    second_result = authorize(second)

    assert first[3].risk_decision_id != second[3].risk_decision_id
    assert first_result.authorization is not None
    assert second_result.authorization is not None

    left = first_result.authorization
    right = second_result.authorization

    assert left.authorization_id != right.authorization_id
    assert (
        left.status,
        left.proposal_id,
        left.signal_id,
        left.correlation_id,
        left.evaluation_context_id,
        left.policy_id,
        left.policy_version,
        left.decision_timestamp,
        left.risk_evidence,
    ) == (
        right.status,
        right.proposal_id,
        right.signal_id,
        right.correlation_id,
        right.evaluation_context_id,
        right.policy_id,
        right.policy_version,
        right.decision_timestamp,
        right.risk_evidence,
    )


def test_provenance_binding_is_derived_from_context_and_policy():
    _, context, policy, decision = approved_bundle()
    assert decision.evaluation_context_id == context.evaluation_context_id
    assert decision.policy_id == policy.policy_id
    assert decision.policy_version == policy.policy_version


def test_partial_risk_decision_binding_is_rejected():
    with pytest.raises(ValueError, match="complete"):
        RiskDecision(
            risk_decision_id="risk-partial",
            proposal_id="proposal",
            signal_id="signal",
            outcome=RiskDecisionOutcome.APPROVED,
            reason="test",
            decision_timestamp=BASE,
            risk_evidence=(
                RiskEvidenceRef(kind="TEST", reference_id="e", as_of=BASE),
            ),
            correlation_id="corr",
            evaluation_context_id="context-only",
        )


def test_authorization_artifact_rejects_non_authorized_status():
    with pytest.raises(RiskAuthorizationContractError, match="AUTHORIZED"):
        RiskAuthorization(
            authorization_id="auth",
            risk_decision_id="risk",
            proposal_id="proposal",
            signal_id="signal",
            correlation_id="corr",
            evaluation_context_id="context",
            policy_id="policy",
            policy_version="1.0.0",
            decision_timestamp=BASE,
            risk_evidence=(
                RiskEvidenceRef(kind="TEST", reference_id="e", as_of=BASE),
            ),
            status=RiskAuthorizationStatus.UNKNOWN,
        )


def test_static_safety_and_no_future_runtime_surfaces():
    source = Path("bot_obrero/risk_authorization.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lowered = source.lower()

    for forbidden in (
        "binance",
        "requests",
        "httpx",
        "websocket",
        "exchangeadapter",
        "reservationstore",
        "executionboundary",
        "datetime.now",
        "utcnow",
        "float(",
    ):
        assert forbidden not in lowered

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.module or "").lower())
        elif isinstance(node, ast.Call):
            assert not (
                isinstance(node.func, ast.Name) and node.func.id == "float"
            )

    assert not any("binance" in item for item in imports)
    assert not any("requests" in item for item in imports)
    assert not any("httpx" in item for item in imports)
    assert not any("websocket" in item for item in imports)


def test_no_expiration_or_clock_fields():
    source = Path("bot_obrero/risk_authorization.py").read_text(encoding="utf-8").lower()
    assert "expires_at" not in source
    assert "valid_until" not in source
    assert "clock" not in source
    assert "datetime.now" not in source
    assert "utcnow" not in source


def test_no_future_boundary_names_in_module():
    source = Path("bot_obrero/risk_authorization.py").read_text(encoding="utf-8")
    assert "FinancialAdmission" not in source
    assert "FinalAdmission" not in source
    assert "OrderIntent" not in source
