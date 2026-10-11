from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot_obrero.financial_admission import (
    FinancialAdmissionBoundary,
    FinancialAdmissionContractError,
    FinancialAdmissionRequest,
    FinancialAdmissionStatus,
    build_admission_idempotency_key,
    build_financial_admission_request,
)
from bot_obrero.reservation import ReservationResourceKind, SQLiteReservationStore
from bot_obrero.risk_authorization import (
    authorize_risk_decision,
    risk_authorization_semantic_fingerprint,
)
from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeSide
from test_hueso_02h2_financial_admission import (
    ACCOUNT,
    BASE,
    make_context,
    make_decision,
    make_policy,
    make_proposal,
    make_state,
)

def build(proposal=None, *, created_at=BASE, account_id=ACCOUNT, state=None):
    proposal = make_proposal() if proposal is None else proposal
    state = make_state(account_id=account_id) if state is None else state
    context = make_context(proposal, state)
    policy = make_policy()
    decision = make_decision(proposal, context=context, policy=policy)
    auth_result = authorize_risk_decision(
        proposal=proposal, risk_decision=decision, context=context, policy=policy
    )
    assert auth_result.authorization is not None
    return build_financial_admission_request(
        proposal=proposal,
        risk_decision=decision,
        context=context,
        policy=policy,
        authorization=auth_result.authorization,
        account_id=account_id,
        canonical_account_state=state,
        created_at=created_at,
    )

def test_factory_builds_canonical_valid_request_and_key():
    request = build()
    assert isinstance(request, FinancialAdmissionRequest)
    validated = request.validate()
    assert validated.resource_kind is request.resource_kind
    assert request.resource_kind is ReservationResourceKind.QUOTE
    assert request.asset == "USDT"
    assert request.approved_reserved_amount == Decimal("25")
    assert request.evidence is request.risk_decision.risk_evidence or request.evidence == request.risk_decision.risk_evidence
    assert request.authorization_fingerprint == risk_authorization_semantic_fingerprint(request.authorization)
    assert request.admission_idempotency_key == build_admission_idempotency_key(
        risk_decision_id=request.risk_decision.risk_decision_id,
        proposal_id=request.proposal.proposal_id,
        account_id=request.account_id,
        resource_kind=request.resource_kind,
        asset=request.asset,
        approved_reserved_amount=request.approved_reserved_amount,
        correlation_id=request.proposal.correlation_id,
        authorization_fingerprint=request.authorization_fingerprint,
    )

@pytest.mark.parametrize(
    ("side", "order_type", "price", "max_spend", "expected_kind", "expected_asset", "expected_amount"),
    [
        (TradeSide.BUY, TradeOrderType.LIMIT, Decimal("12"), None, ReservationResourceKind.QUOTE, "USDT", Decimal("12")),
        (TradeSide.BUY, TradeOrderType.MARKET, None, Decimal("25"), ReservationResourceKind.QUOTE, "USDT", Decimal("25")),
        (TradeSide.SELL, TradeOrderType.LIMIT, Decimal("12"), None, ReservationResourceKind.BASE, "BTC", Decimal("1")),
        (TradeSide.SELL, TradeOrderType.MARKET, None, None, ReservationResourceKind.BASE, "BTC", Decimal("1")),
    ],
)
def test_factory_uses_canonical_terms_for_all_order_shapes(
    side, order_type, price, max_spend, expected_kind, expected_asset, expected_amount
):
    proposal = make_proposal()
    proposal = replace(
        proposal,
        side=side,
        order_type=order_type,
        requested_price=price,
        max_quote_spend=max_spend,
        price_policy=PricePolicy.FIXED if order_type is TradeOrderType.LIMIT else PricePolicy.MARKET_REFERENCE,
    )
    request = build(proposal)
    assert (request.resource_kind, request.asset, request.approved_reserved_amount) == (
        expected_kind, expected_asset, expected_amount
    )

def test_factory_fails_closed_on_mismatched_authorization():
    request = build()
    other_auth = replace(
        request.authorization,
        risk_decision_id="different-risk-decision",
    )
    with pytest.raises(FinancialAdmissionContractError):
        build_financial_admission_request(
            proposal=request.proposal, risk_decision=request.risk_decision,
            context=request.context, policy=request.policy, authorization=other_auth,
            account_id=request.account_id, canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )

def test_factory_rejects_account_mismatch_and_naive_timestamp():
    request = build()
    with pytest.raises(FinancialAdmissionContractError):
        build_financial_admission_request(
            proposal=request.proposal, risk_decision=request.risk_decision,
            context=request.context, policy=request.policy, authorization=request.authorization,
            account_id="other", canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )
    with pytest.raises(FinancialAdmissionContractError, match="timezone-aware"):
        build(created_at=datetime(2026, 10, 5, 12, 0))

def test_boundary_accepts_factory_request_without_contract_changes(tmp_path):
    request = build()
    store = SQLiteReservationStore(tmp_path / "reservations.sqlite3")
    try:
        result = FinancialAdmissionBoundary(store).admit(request)
        assert result.status is FinancialAdmissionStatus.ADMITTED
        assert result.reservation is not None
        assert result.reservation.proposal_id == request.proposal.proposal_id
    finally:
        store.close()

def test_factory_does_not_accept_noncanonical_or_empty_evidence():
    request = build()
    empty_decision = replace(request.risk_decision, risk_evidence=())
    with pytest.raises((FinancialAdmissionContractError, ValueError)):
        build_financial_admission_request(
            proposal=request.proposal, risk_decision=empty_decision,
            context=request.context, policy=request.policy, authorization=request.authorization,
            account_id=request.account_id, canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )

def test_factory_rejects_proposal_context_mismatch():
    request = build()
    other_proposal = replace(
        request.proposal,
        correlation_id="different-correlation-context",
    )

    with pytest.raises(
        FinancialAdmissionContractError,
        match="context.trade_proposal must structurally match proposal",
    ):
        build_financial_admission_request(
            proposal=other_proposal,
            risk_decision=request.risk_decision,
            context=request.context,
            policy=request.policy,
            authorization=request.authorization,
            account_id=request.account_id,
            canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )


def test_factory_rejects_policy_mismatch_with_risk_decision():
    request = build()
    incompatible_policy = replace(
        request.policy,
        policy_id="different-risk-policy",
    )

    with pytest.raises(
        FinancialAdmissionContractError,
        match="policy_id/policy_version mismatch",
    ):
        build_financial_admission_request(
            proposal=request.proposal,
            risk_decision=request.risk_decision,
            context=request.context,
            policy=incompatible_policy,
            authorization=request.authorization,
            account_id=request.account_id,
            canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )


def test_factory_rejects_decision_identity_incompatible_with_authorization():
    request = build()
    different_decision = replace(
        request.risk_decision,
        risk_decision_id="different-risk-decision-identity",
    )

    with pytest.raises(
        FinancialAdmissionContractError,
        match="authorization risk_decision_id mismatch",
    ):
        build_financial_admission_request(
            proposal=request.proposal,
            risk_decision=different_decision,
            context=request.context,
            policy=request.policy,
            authorization=request.authorization,
            account_id=request.account_id,
            canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )


def test_factory_rejects_nonempty_evidence_contradicting_authorization():
    request = build()
    assert request.risk_decision.risk_evidence
    contradictory_evidence = replace(
        request.risk_decision.risk_evidence[0],
        reference_id="different-evidence-reference",
    )
    decision_with_different_evidence = replace(
        request.risk_decision,
        risk_evidence=(contradictory_evidence,),
    )
    assert decision_with_different_evidence.risk_evidence
    assert (
        decision_with_different_evidence.risk_evidence
        != request.authorization.risk_evidence
    )

    with pytest.raises(
        FinancialAdmissionContractError,
        match="authorization evidence must match risk_decision evidence exactly",
    ):
        build_financial_admission_request(
            proposal=request.proposal,
            risk_decision=decision_with_different_evidence,
            context=request.context,
            policy=request.policy,
            authorization=request.authorization,
            account_id=request.account_id,
            canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )


def test_factory_rejects_economic_terms_incompatible_with_canonical_instrument():
    request = build()
    incompatible_proposal = replace(
        request.proposal,
        symbol="ETH/USDT",
    )

    with pytest.raises(
        FinancialAdmissionContractError,
        match="proposal symbol and canonical instrument are incompatible",
    ):
        build_financial_admission_request(
            proposal=incompatible_proposal,
            risk_decision=request.risk_decision,
            context=request.context,
            policy=request.policy,
            authorization=request.authorization,
            account_id=request.account_id,
            canonical_account_state=request.canonical_account_state,
            created_at=BASE,
        )

