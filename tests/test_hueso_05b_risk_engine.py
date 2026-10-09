from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.evidence_binding import (
    AvailabilityBinding,
    AvailabilitySubjectKind,
    RiskLimitResolution,
    RiskLimitResolutionStatus,
)
from bot_obrero.market_data import InstrumentIdentity
from bot_obrero.risk_contracts import (
    BalanceSnapshot,
    CanonicalAccountState,
    CanonicalExposure,
    Completeness,
    RiskDecisionOutcome,
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
from bot_obrero.risk_engine import (
    APPROVED_REASON,
    MAX_NOTIONAL_METRIC,
    MAX_NOTIONAL_SCOPE,
    REJECTED_LIMIT_REASON,
    REJECTED_PROPOSAL_REASON,
    REJECTED_VALUATION_REASON,
    UNKNOWN_UNIT_MISMATCH_REASON,
    RiskEngine,
)

BASE = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
EVALUATION = BASE + timedelta(seconds=30)
PROVENANCE = Provenance(
    source="synthetic-risk-fixture",
    nature=ArtifactNature.OBSERVED,
)


def make_proposal(
    *,
    order_type=TradeOrderType.LIMIT,
    side=TradeSide.BUY,
    quantity="2",
    price="100",
    max_quote_spend=None,
    decision_timestamp=BASE,
):
    return TradeProposal(
        signal_id="signal-1",
        symbol="BTC/USDT",
        side=side,
        requested_quantity=Decimal(quantity),
        requested_price=(
            None
            if order_type is TradeOrderType.MARKET
            else Decimal(price)
        ),
        max_quote_spend=(
            None
            if max_quote_spend is None
            else Decimal(max_quote_spend)
        ),
        price_policy=(
            PricePolicy.MARKET_REFERENCE
            if order_type is TradeOrderType.MARKET
            else PricePolicy.FIXED
        ),
        order_type=order_type,
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
        decision_timestamp=decision_timestamp,
        correlation_id="corr-1",
    )


def make_limit(
    risk_limit_id="limit-1",
    *,
    threshold="250",
    unit="USDT",
):
    return RiskLimit(
        risk_limit_id=risk_limit_id,
        scope=MAX_NOTIONAL_SCOPE,
        metric=MAX_NOTIONAL_METRIC,
        threshold=Decimal(threshold),
        unit=unit,
        effective_from=BASE - timedelta(minutes=1),
        effective_until=None,
        provenance=PROVENANCE,
    )


def make_context(
    proposal,
    *,
    risk_limits=None,
    resolution=None,
    availability_subjects=(
        AvailabilitySubjectKind.ACCOUNT_STATE,
        AvailabilitySubjectKind.RISK_LIMIT_SET,
    ),
    account_completeness=Completeness.COMPLETE,
    exposure=None,
):
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

    resolver = RiskLimitApplicabilityResolver()
    resolved = resolver.resolve(
        risk_limit_set,
        scope=MAX_NOTIONAL_SCOPE,
        metric=MAX_NOTIONAL_METRIC,
        evaluation_timestamp=EVALUATION,
    )
    if resolution is None:
        resolution = resolved

    bindings = []
    if AvailabilitySubjectKind.ACCOUNT_STATE in availability_subjects:
        bindings.append(
            AvailabilityBinding(
                subject_kind=AvailabilitySubjectKind.ACCOUNT_STATE,
                subject_id="account-state-1",
                available_at=BASE,
                source="synthetic",
                evidence_reference="account-evidence",
            )
        )
    if AvailabilitySubjectKind.RISK_LIMIT_SET in availability_subjects:
        bindings.append(
            AvailabilityBinding(
                subject_kind=AvailabilitySubjectKind.RISK_LIMIT_SET,
                subject_id=risk_limit_set.risk_limit_set_id,
                available_at=BASE,
                source="synthetic",
                evidence_reference="limit-evidence",
            )
        )
    if (
        exposure is not None
        and AvailabilitySubjectKind.EXPOSURE in availability_subjects
    ) or exposure is not None:
        bindings.append(
            AvailabilityBinding(
                subject_kind=AvailabilitySubjectKind.EXPOSURE,
                subject_id=exposure.exposure_id,
                available_at=BASE,
                source="synthetic",
                evidence_reference="exposure-evidence",
            )
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
        availability_bindings=tuple(bindings),
        risk_limit_resolution=resolution,
        canonical_exposure=exposure,
    )


def make_policy(**overrides):
    values = {
        "policy_id": "risk-policy-v1",
        "policy_version": "1.0.0",
        "required_availability_subjects": (
            AvailabilitySubjectKind.ACCOUNT_STATE,
            AvailabilitySubjectKind.RISK_LIMIT_SET,
        ),
        "require_context_complete": True,
        "require_risk_evidence_for_approval": True,
        "max_valuation_age": None,
    }
    values.update(overrides)
    return RiskEvaluationPolicy(**values)


def evaluate(
    proposal,
    context,
    *,
    policy=None,
    resolver=None,
):
    return RiskEngine().evaluate(
        proposal=proposal,
        context=context,
        policy=make_policy() if policy is None else policy,
        risk_limit_resolver=(
            RiskLimitApplicabilityResolver()
            if resolver is None
            else resolver
        ),
    )


def test_happy_path_is_approved():
    proposal = make_proposal()
    context = make_context(proposal)
    policy = make_policy()
    result = evaluate(proposal, context, policy=policy)

    assert result.outcome is RiskDecisionOutcome.APPROVED
    assert result.reason == APPROVED_REASON
    assert result.decision_timestamp == proposal.decision_timestamp
    assert result.proposal_id == proposal.proposal_id
    assert result.signal_id == proposal.signal_id
    assert result.correlation_id == proposal.correlation_id
    assert result.evaluation_context_id == context.evaluation_context_id
    assert result.policy_id == policy.policy_id
    assert result.policy_version == policy.policy_version
    assert result.risk_evidence


def test_max_notional_is_approved_at_exact_threshold():
    proposal = make_proposal(quantity="2.5", price="100")
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.APPROVED
    assert result.reason == APPROVED_REASON


def test_max_notional_over_limit_is_rejected():
    proposal = make_proposal(quantity="3", price="100")
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.REJECTED
    assert result.reason == REJECTED_LIMIT_REASON


def test_max_notional_limit_unit_matches_instrument_quote_asset():
    proposal = make_proposal(quantity="2", price="100")
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert context.instrument.quote_asset == "USDT"
    assert context.risk_limit_set.limits[0].unit == "USDT"
    assert result.outcome is RiskDecisionOutcome.APPROVED


def test_max_notional_limit_unit_mismatch_is_unknown():
    proposal = make_proposal(quantity="2", price="100")
    risk_limit_set = RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=(make_limit(unit="BTC"),),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    context = make_context(proposal, risk_limits=risk_limit_set)
    result = evaluate(proposal, context)

    assert context.instrument.quote_asset == "USDT"
    assert context.risk_limit_set.limits[0].unit == "BTC"
    assert result.outcome is RiskDecisionOutcome.UNKNOWN
    assert result.reason == UNKNOWN_UNIT_MISMATCH_REASON


def test_buy_market_max_quote_spend_requires_quote_asset_limit_unit():
    proposal = make_proposal(
        order_type=TradeOrderType.MARKET,
        side=TradeSide.BUY,
        quantity="10",
        max_quote_spend="200",
    )
    compatible = make_context(proposal)
    compatible_result = evaluate(proposal, compatible)

    incompatible_limits = RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=(make_limit(unit="BTC"),),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    incompatible = make_context(proposal, risk_limits=incompatible_limits)
    incompatible_result = evaluate(proposal, incompatible)

    assert compatible_result.outcome is RiskDecisionOutcome.APPROVED
    assert incompatible_result.outcome is RiskDecisionOutcome.UNKNOWN
    assert incompatible_result.reason == UNKNOWN_UNIT_MISMATCH_REASON


def test_max_notional_limit_unit_mismatch_does_not_convert_currency():
    proposal = make_proposal(quantity="2", price="100")
    risk_limit_set = RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=(make_limit(threshold="1000", unit="BTC"),),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    context = make_context(proposal, risk_limits=risk_limit_set)

    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.UNKNOWN
    assert result.reason == UNKNOWN_UNIT_MISMATCH_REASON


def test_max_notional_unit_mismatch_is_deterministic():
    proposal = make_proposal(quantity="2", price="100")
    risk_limit_set = RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=(make_limit(unit="BTC"),),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    context = make_context(proposal, risk_limits=risk_limit_set)

    first = evaluate(proposal, context)
    second = evaluate(proposal, context)

    assert first.risk_decision_id != second.risk_decision_id
    assert first.outcome is RiskDecisionOutcome.UNKNOWN
    assert second.outcome is RiskDecisionOutcome.UNKNOWN
    assert first.reason == second.reason == UNKNOWN_UNIT_MISMATCH_REASON
    assert first.risk_evidence == second.risk_evidence
    assert first.decision_timestamp == second.decision_timestamp
    assert first.proposal_id == second.proposal_id
    assert first.signal_id == second.signal_id
    assert first.correlation_id == second.correlation_id


def test_buy_market_uses_explicit_max_quote_spend():
    proposal = make_proposal(
        order_type=TradeOrderType.MARKET,
        side=TradeSide.BUY,
        quantity="10",
        price="999",
        max_quote_spend="200",
    )
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.APPROVED
    assert result.reason == APPROVED_REASON


def test_market_sell_without_explicit_valuation_is_unknown():
    proposal = make_proposal(
        order_type=TradeOrderType.MARKET,
        side=TradeSide.SELL,
        quantity="1",
    )
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_incomplete_context_is_unknown_even_when_policy_allows_incompleteness():
    proposal = make_proposal()
    context = make_context(
        proposal,
        account_completeness=Completeness.PARTIAL,
    )
    policy = make_policy(require_context_complete=False)

    result = evaluate(proposal, context, policy=policy)

    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_missing_required_availability_is_unknown():
    proposal = make_proposal()
    context = make_context(
        proposal,
        availability_subjects=(AvailabilitySubjectKind.ACCOUNT_STATE,),
    )
    result = evaluate(proposal, context)
    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_no_applicable_limit_is_unknown():
    proposal = make_proposal()
    risk_limit_set = RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=(
            RiskLimit(
                risk_limit_id="other-metric",
                scope=MAX_NOTIONAL_SCOPE,
                metric="MAX_DRAWDOWN",
                threshold=Decimal("1"),
                unit="USDT",
                effective_from=BASE - timedelta(minutes=1),
                effective_until=None,
                provenance=PROVENANCE,
            ),
        ),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    context = make_context(proposal, risk_limits=risk_limit_set)
    result = evaluate(proposal, context)
    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_overlapping_limits_are_unknown():
    risk_limit_set = RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=(make_limit("limit-a"), make_limit("limit-b")),
        as_of=BASE,
        provenance=PROVENANCE,
    )
    proposal = make_proposal()
    context = make_context(proposal, risk_limits=risk_limit_set)
    result = evaluate(proposal, context)
    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_resolution_mismatch_is_unknown():
    proposal = make_proposal()
    context = make_context(
        proposal,
        resolution=RiskLimitResolution(
            status=RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
            risk_limit_set_id="limits-1",
        ),
    )
    result = evaluate(proposal, context)
    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_valuation_stale_is_rejected_when_policy_requires_freshness():
    exposure = CanonicalExposure(
        exposure_id="exposure-1",
        account_id="account-1",
        symbol="BTC/USDT",
        quantity=Decimal("2"),
        valuation_price=Decimal("100"),
        notional=Decimal("200"),
        as_of=BASE,
        valuation_as_of=BASE - timedelta(minutes=10),
        completeness=Completeness.COMPLETE,
        provenance=PROVENANCE,
    )
    context = make_context(proposal=make_proposal(), exposure=exposure)
    policy = make_policy(max_valuation_age=timedelta(minutes=5))
    result = evaluate(context.trade_proposal, context, policy=policy)

    assert result.outcome is RiskDecisionOutcome.REJECTED
    assert result.reason == REJECTED_VALUATION_REASON


def test_valuation_unknown_is_unknown_when_policy_requires_freshness():
    exposure = CanonicalExposure(
        exposure_id="exposure-1",
        account_id="account-1",
        symbol="BTC/USDT",
        quantity=Decimal("2"),
        valuation_price=None,
        notional=None,
        as_of=BASE,
        valuation_as_of=None,
        completeness=Completeness.COMPLETE,
        provenance=PROVENANCE,
    )
    context = make_context(proposal=make_proposal(), exposure=exposure)
    policy = make_policy(max_valuation_age=timedelta(minutes=5))
    result = evaluate(context.trade_proposal, context, policy=policy)

    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_required_approval_evidence_is_fail_closed():
    class NoEvidenceRiskEngine(RiskEngine):
        @classmethod
        def _build_risk_evidence(cls, *, proposal, context, applicable_limit):
            return ()

    proposal = make_proposal()
    context = make_context(proposal)
    result = NoEvidenceRiskEngine().evaluate(
        proposal=proposal,
        context=context,
        policy=make_policy(),
        risk_limit_resolver=RiskLimitApplicabilityResolver(),
    )

    assert result.outcome is RiskDecisionOutcome.UNKNOWN


def test_invalid_trade_proposal_is_rejected_fail_closed():
    proposal = make_proposal()
    object.__setattr__(proposal, "requested_quantity", Decimal("0"))
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.REJECTED
    assert result.reason == REJECTED_PROPOSAL_REASON


def test_proposal_context_identity_mismatch_is_rejected():
    proposal = make_proposal()
    context = make_context(make_proposal())
    result = evaluate(proposal, context)

    assert result.outcome is RiskDecisionOutcome.REJECTED


def test_provider_and_runtime_static_safety():
    source = Path("bot_obrero/risk_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_text = (
        "binance",
        "requests",
        "httpx",
        "websocket",
        "ExchangeAdapter",
        "ExecutionBoundary",
        "ReservationStore",
    )
    lowered = source.lower()
    assert all(item.lower() not in lowered for item in forbidden_text)

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")

        if isinstance(node, ast.Call):
            assert not (
                isinstance(node.func, ast.Name)
                and node.func.id == "float"
            )
            if isinstance(node.func, ast.Attribute):
                assert not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "datetime"
                    and node.func.attr == "now"
                )

    assert all(
        forbidden not in module
        for module in imports
        for forbidden in (
            "binance",
            "requests",
            "httpx",
            "websocket",
        )
    )


def test_engine_has_no_future_authorization_surface():
    source = Path("bot_obrero/risk_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }
    assert "RiskAuthorization" not in names
    assert "FinalAdmission" not in names


def test_engine_never_uses_unapproved_clock_surfaces():
    source = Path("bot_obrero/risk_engine.py").read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utcnow" not in source


def test_engine_preserves_context_and_proposal():
    proposal = make_proposal()
    context = make_context(proposal)
    before = (
        proposal,
        context.evaluation_context_id,
        context.trade_proposal,
        context.canonical_account_state,
        context.risk_limit_set,
        context.availability_bindings,
    )

    evaluate(proposal, context)

    after = (
        proposal,
        context.evaluation_context_id,
        context.trade_proposal,
        context.canonical_account_state,
        context.risk_limit_set,
        context.availability_bindings,
    )
    assert after == before


def test_deterministic_logical_result():
    proposal = make_proposal()
    context = make_context(proposal)
    first = evaluate(proposal, context)
    second = evaluate(proposal, context)

    assert first.risk_decision_id != second.risk_decision_id
    assert first.proposal_id == second.proposal_id
    assert first.signal_id == second.signal_id
    assert first.correlation_id == second.correlation_id
    assert first.evaluation_context_id == second.evaluation_context_id == context.evaluation_context_id
    assert first.policy_id == second.policy_id == make_policy().policy_id
    assert first.policy_version == second.policy_version == make_policy().policy_version
    assert first.outcome is second.outcome
    assert first.reason == second.reason
    assert first.decision_timestamp == second.decision_timestamp
    assert first.risk_evidence == second.risk_evidence


def test_constants_define_the_only_metric_and_scope():
    assert MAX_NOTIONAL_SCOPE == "ACCOUNT"
    assert MAX_NOTIONAL_METRIC == "MAX_NOTIONAL"


def test_required_identity_and_correlation_are_proposal_derived():
    proposal = make_proposal()
    context = make_context(proposal)
    result = evaluate(proposal, context)

    assert result.proposal_id == proposal.proposal_id
    assert result.signal_id == proposal.signal_id
    assert result.correlation_id == proposal.correlation_id
