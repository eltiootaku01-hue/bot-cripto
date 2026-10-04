from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
)
from bot_obrero.risk_contracts import (
    BalanceSnapshot,
    CanonicalAccountState,
    CanonicalExposure,
    CanonicalPosition,
    Completeness,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidenceRef,
    RiskLimit,
    RiskLimitSet,
)

UTC = timezone.utc
BASE = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
OBSERVED = Provenance("synthetic-test", ArtifactNature.OBSERVED)
DERIVED = Provenance("synthetic-test", ArtifactNature.DERIVED)


def _financial_input(value):
    return Decimal(value) if isinstance(value, str) else value


def balance(asset="USDT", total="100", available="80", locked="20"):
    return BalanceSnapshot(
        asset=asset,
        total=_financial_input(total),
        available=_financial_input(available),
        locked=_financial_input(locked),
    )


def account_state(*balances):
    return CanonicalAccountState(
        account_state_id="state-1",
        account_id="account-1",
        as_of=BASE,
        balances=balances or (balance(),),
        completeness=Completeness.COMPLETE,
        provenance=OBSERVED,
    )


def position():
    return CanonicalPosition(
        position_id="position-1",
        account_id="account-1",
        symbol="BTC/USDT",
        quantity=Decimal("0.5"),
        as_of=BASE,
        completeness=Completeness.COMPLETE,
        provenance=OBSERVED,
    )


def exposure(*, notional=Decimal("5000"), valuation_price=Decimal("10000"), valuation_as_of=BASE):
    return CanonicalExposure(
        exposure_id="exposure-1",
        account_id="account-1",
        symbol="BTC/USDT",
        quantity=Decimal("0.5"),
        valuation_price=valuation_price,
        notional=notional,
        as_of=BASE,
        valuation_as_of=valuation_as_of,
        completeness=Completeness.COMPLETE,
        provenance=DERIVED,
    )


def risk_limit(risk_limit_id="limit-1"):
    return RiskLimit(
        risk_limit_id=risk_limit_id,
        scope="account",
        metric="max_notional",
        threshold=Decimal("10000"),
        unit="USDT",
        effective_from=BASE,
        effective_until=BASE + timedelta(hours=1),
        provenance=OBSERVED,
    )


def make_trade_proposal() -> TradeProposal:
    return TradeProposal(
        signal_id="signal-1",
        symbol="BTC/USDT",
        side=TradeSide.BUY,
        requested_quantity=Decimal("0.001"),
        requested_price=None,
        max_quote_spend=Decimal("100"),
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
        decision_timestamp=BASE,
    )

def risk_decision(
    *,
    outcome=RiskDecisionOutcome.UNKNOWN,
    proposal_id="proposal-1",
    risk_decision_id="decision-1",
    correlation_id="correlation-1",
):
    return RiskDecision(
        risk_decision_id=risk_decision_id,
        proposal_id=proposal_id,
        signal_id="signal-1",
        outcome=outcome,
        reason="Insufficient evidence",
        decision_timestamp=BASE,
        risk_evidence=(
            RiskEvidenceRef(kind="ACCOUNT_STATE", reference_id="state-1", as_of=BASE),
        ),
        correlation_id=correlation_id,
    )


def test_balance_accepts_decimal_financial_values_and_is_immutable():
    item = balance()
    assert isinstance(item.total, Decimal)
    assert isinstance(item.available, Decimal)
    assert isinstance(item.locked, Decimal)
    with pytest.raises(FrozenInstanceError):
        item.total = Decimal("1")


def test_balance_rejects_float_and_int_and_invalid_values():
    with pytest.raises(ValueError, match="Decimal"):
        balance(total=100.0)
    with pytest.raises(ValueError, match="Decimal"):
        balance(total=100)
    with pytest.raises(ValueError, match="non-negative"):
        balance(total="-1")
    with pytest.raises(ValueError, match="available cannot exceed"):
        balance(available="101")
    with pytest.raises(ValueError, match="locked cannot exceed"):
        balance(locked="101")
    with pytest.raises(ValueError, match="plus locked"):
        balance(available="90", locked="20")


def test_balance_rejects_empty_asset():
    with pytest.raises(ValueError, match="asset"):
        balance(asset=" ")


def test_account_state_validates_identity_completeness_and_provenance():
    state = account_state()
    assert state.account_state_id == "state-1"
    assert state.account_id == "account-1"
    assert state.as_of == BASE
    assert state.completeness is Completeness.COMPLETE
    assert state.provenance is OBSERVED
    assert state.balances == (balance(),)


def test_account_state_rejects_duplicate_assets_id_reuse_and_naive_time():
    with pytest.raises(ValueError, match="duplicate assets"):
        account_state(balance(), balance())
    with pytest.raises(ValueError, match="differ from account_id"):
        CanonicalAccountState(
            account_state_id="account-1",
            account_id="account-1",
            as_of=BASE,
            balances=(balance(),),
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CanonicalAccountState(
            account_state_id="state-2",
            account_id="account-1",
            as_of=datetime(2026, 10, 3, 12, 0),
            balances=(balance(),),
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )


def test_account_state_balances_are_immutable_tuple():
    balances = [balance()]
    state = account_state(*balances)
    assert isinstance(state.balances, tuple)
    balances.append(balance(asset="BTC"))
    assert len(state.balances) == 1


def test_position_is_account_level_and_decimal():
    item = position()
    assert item.account_id == "account-1"
    assert item.symbol == "BTC/USDT"
    assert isinstance(item.quantity, Decimal)
    with pytest.raises(ValueError, match="Decimal"):
        CanonicalPosition(
            position_id="position-2",
            account_id="account-1",
            symbol="BTC/USDT",
            quantity=1,
            as_of=BASE,
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )


def test_position_rejects_id_reuse_and_naive_time():
    with pytest.raises(ValueError, match="differ from account_id"):
        CanonicalPosition(
            position_id="account-1",
            account_id="account-1",
            symbol="BTC/USDT",
            quantity=Decimal("1"),
            as_of=BASE,
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CanonicalPosition(
            position_id="position-2",
            account_id="account-1",
            symbol="BTC/USDT",
            quantity=Decimal("1"),
            as_of=datetime(2026, 10, 3, 12, 0),
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )


def test_exposure_supports_unknown_valuation_without_inventing_notional():
    item = exposure(notional=None, valuation_price=None, valuation_as_of=None)
    assert item.notional is None
    assert item.valuation_price is None
    assert item.valuation_as_of is None


def test_exposure_requires_valuation_evidence_when_notional_exists():
    with pytest.raises(ValueError, match="notional requires"):
        exposure(notional=Decimal("5000"), valuation_price=None, valuation_as_of=None)
    with pytest.raises(ValueError, match="valuation_as_of requires"):
        exposure(notional=None, valuation_price=None, valuation_as_of=BASE)


def test_exposure_never_calculates_notional_automatically():
    item = exposure(notional=None, valuation_price=Decimal("12345"), valuation_as_of=BASE)
    assert item.notional is None
    assert item.valuation_price == Decimal("12345")


def test_exposure_rejects_float_and_int_financial_values():
    with pytest.raises(ValueError, match="Decimal"):
        CanonicalExposure(
            exposure_id="exposure-2",
            account_id="account-1",
            symbol="BTC/USDT",
            quantity=0.5,
            valuation_price=None,
            notional=None,
            as_of=BASE,
            valuation_as_of=None,
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )
    with pytest.raises(ValueError, match="Decimal"):
        CanonicalExposure(
            exposure_id="exposure-2",
            account_id="account-1",
            symbol="BTC/USDT",
            quantity=Decimal("0.5"),
            valuation_price=10000,
            notional=None,
            as_of=BASE,
            valuation_as_of=BASE,
            completeness=Completeness.COMPLETE,
            provenance=OBSERVED,
        )


def test_risk_limit_is_explicit_and_temporal():
    item = risk_limit()
    assert item.threshold == Decimal("10000")
    assert item.effective_until > item.effective_from
    assert item.unit == "USDT"
    with pytest.raises(ValueError, match="effective_until"):
        RiskLimit(
            risk_limit_id="limit-2",
            scope="account",
            metric="max_notional",
            threshold=Decimal("100"),
            unit="USDT",
            effective_from=BASE,
            effective_until=BASE,
            provenance=OBSERVED,
        )
    with pytest.raises(ValueError, match="Decimal"):
        RiskLimit(
            risk_limit_id="limit-2",
            scope="account",
            metric="max_notional",
            threshold=1.0,
            unit="USDT",
            effective_from=BASE,
            effective_until=None,
            provenance=OBSERVED,
        )


def test_risk_limit_set_has_immutable_unique_limits():
    first = risk_limit()
    second = risk_limit("limit-2")
    second = RiskLimit(
        risk_limit_id=second.risk_limit_id,
        scope="account",
        metric="max_position",
        threshold=Decimal("1"),
        unit="BTC",
        effective_from=BASE,
        effective_until=None,
        provenance=OBSERVED,
    )
    limits = [first, second]
    item = RiskLimitSet(
        risk_limit_set_id="limit-set-1",
        limits=limits,
        as_of=BASE,
        provenance=OBSERVED,
    )
    assert isinstance(item.limits, tuple)
    limits.append(first)
    assert len(item.limits) == 2
    with pytest.raises(ValueError, match="duplicate"):
        RiskLimitSet(
            risk_limit_set_id="limit-set-2",
            limits=(first, first),
            as_of=BASE,
            provenance=OBSERVED,
        )


def test_risk_evidence_ref_is_explicit_and_immutable():
    ref = RiskEvidenceRef(kind="EXPOSURE", reference_id="exposure-1", as_of=BASE)
    assert ref.kind == "EXPOSURE"
    assert ref.reference_id == "exposure-1"
    assert ref.as_of == BASE
    with pytest.raises(FrozenInstanceError):
        ref.kind = "POSITION"


def test_risk_decision_contract_and_immutability():
    decision = risk_decision(outcome=RiskDecisionOutcome.REJECTED)
    assert decision.proposal_id == "proposal-1"
    assert decision.signal_id == "signal-1"
    assert decision.outcome is RiskDecisionOutcome.REJECTED
    assert decision.reason == "Insufficient evidence"
    assert decision.correlation_id == "correlation-1"
    assert isinstance(decision.risk_evidence, tuple)
    with pytest.raises(FrozenInstanceError):
        decision.reason = "changed"
    with pytest.raises(FrozenInstanceError):
        decision.proposal_id = "proposal-2"


def test_risk_decision_from_trade_proposal_propagates_proposal_identity():
    proposal = make_trade_proposal()

    decision = RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id="decision-bound-1",
        outcome=RiskDecisionOutcome.UNKNOWN,
        reason="Insufficient evidence",
        decision_timestamp=BASE,
        risk_evidence=(),
    )

    assert decision.proposal_id == proposal.proposal_id
    assert decision.signal_id == proposal.signal_id
    assert decision.correlation_id == proposal.correlation_id
    assert decision.risk_decision_id != proposal.proposal_id
    assert decision.risk_decision_id != proposal.signal_id
    assert decision.risk_decision_id != proposal.correlation_id


def test_risk_decision_from_same_trade_proposal_never_regenerates_correlation_id():
    proposal = make_trade_proposal()

    first = RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id="decision-bound-1",
        outcome=RiskDecisionOutcome.UNKNOWN,
        reason="first",
        decision_timestamp=BASE,
        risk_evidence=(),
    )
    second = RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id="decision-bound-2",
        outcome=RiskDecisionOutcome.UNKNOWN,
        reason="second",
        decision_timestamp=BASE,
        risk_evidence=(),
    )

    assert first.correlation_id == proposal.correlation_id
    assert second.correlation_id == proposal.correlation_id
    assert first.risk_decision_id != second.risk_decision_id


def test_risk_decision_from_trade_proposal_preserves_frozen_source_identity():
    proposal = make_trade_proposal()

    RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id="decision-bound-3",
        outcome=RiskDecisionOutcome.UNKNOWN,
        reason="Insufficient evidence",
        decision_timestamp=BASE,
        risk_evidence=(),
    )

    with pytest.raises(FrozenInstanceError):
        proposal.correlation_id = "changed"


def test_risk_decision_from_trade_proposal_rejects_non_proposal_source():
    with pytest.raises(TypeError, match="TradeProposal"):
        RiskDecision.from_trade_proposal(
            proposal=object(),
            risk_decision_id="decision-bound-4",
            outcome=RiskDecisionOutcome.UNKNOWN,
            reason="Insufficient evidence",
            decision_timestamp=BASE,
            risk_evidence=(),
        )

def test_risk_decision_requires_explicit_nonempty_proposal_id():
    with pytest.raises(TypeError):
        RiskDecision(
            risk_decision_id="decision-missing-proposal",
            signal_id="signal-1",
            outcome=RiskDecisionOutcome.UNKNOWN,
            reason="reason",
            decision_timestamp=BASE,
            risk_evidence=(),
            correlation_id="correlation-2",
        )
    with pytest.raises(ValueError, match="proposal_id"):
        risk_decision(proposal_id=None)
    with pytest.raises(ValueError, match="proposal_id"):
        risk_decision(proposal_id="")


def test_two_risk_decisions_keep_evaluation_identity_separate_from_proposal_identity():
    first = risk_decision(
        risk_decision_id="decision-1",
        proposal_id="proposal-1",
        correlation_id="correlation-1",
    )
    second = risk_decision(
        risk_decision_id="decision-2",
        proposal_id="proposal-1",
        correlation_id="correlation-2",
    )

    assert first.proposal_id == second.proposal_id == "proposal-1"
    assert first.risk_decision_id != second.risk_decision_id
    assert first.correlation_id != second.correlation_id


def test_risk_decision_rejects_invalid_outcome_and_id_reuse():
    with pytest.raises(ValueError, match="RiskDecisionOutcome"):
        RiskDecision(
            risk_decision_id="decision-2",
            proposal_id="proposal-2",
            signal_id="signal-1",
            outcome="APPROVED",
            reason="reason",
            decision_timestamp=BASE,
            risk_evidence=(),
            correlation_id="correlation-2",
        )
    with pytest.raises(ValueError, match="correlation_id"):
        RiskDecision(
            risk_decision_id="decision-2",
            proposal_id="proposal-2",
            signal_id="signal-1",
            outcome=RiskDecisionOutcome.UNKNOWN,
            reason="reason",
            decision_timestamp=BASE,
            risk_evidence=(),
            correlation_id="decision-2",
        )
    with pytest.raises(ValueError, match="correlation_id"):
        RiskDecision(
            risk_decision_id="decision-2",
            proposal_id="proposal-2",
            signal_id="signal-1",
            outcome=RiskDecisionOutcome.UNKNOWN,
            reason="reason",
            decision_timestamp=BASE,
            risk_evidence=(),
            correlation_id="signal-1",
        )


def test_unknown_is_distinct_from_approved_and_is_fail_closed_by_semantics():
    assert RiskDecisionOutcome.UNKNOWN is not RiskDecisionOutcome.APPROVED
    assert RiskDecisionOutcome.UNKNOWN.value == "UNKNOWN"
    assert set(RiskDecisionOutcome) == {
        RiskDecisionOutcome.APPROVED,
        RiskDecisionOutcome.REJECTED,
        RiskDecisionOutcome.UNKNOWN,
    }


def test_all_identifiers_are_distinct_in_contract_fixture():
    identifiers = {
        account_state().account_state_id,
        account_state().account_id,
        position().position_id,
        exposure().exposure_id,
        risk_limit().risk_limit_id,
        "limit-set-1",
        risk_decision().risk_decision_id,
        risk_decision().proposal_id,
        risk_decision().signal_id,
        risk_decision().correlation_id,
    }
    assert len(identifiers) == 10


def test_every_contract_uses_timezone_aware_temporal_fields():
    assert account_state().as_of.tzinfo is not None
    assert position().as_of.tzinfo is not None
    assert exposure().as_of.tzinfo is not None
    assert exposure().valuation_as_of is not None
    assert risk_limit().effective_from.tzinfo is not None
    assert risk_limit().effective_until.tzinfo is not None
    limit_set = RiskLimitSet(
        risk_limit_set_id="limit-set-2",
        limits=(risk_limit(),),
        as_of=BASE,
        provenance=OBSERVED,
    )
    assert limit_set.as_of.tzinfo is not None
    assert risk_decision().decision_timestamp.tzinfo is not None


def test_only_authorized_contract_names_are_exported():
    module = __import__("bot_obrero.risk_contracts", fromlist=["*"])
    assert set(module.__all__) == {
        "BalanceSnapshot",
        "CanonicalAccountState",
        "CanonicalPosition",
        "CanonicalExposure",
        "Completeness",
        "RiskLimit",
        "RiskLimitSet",
        "RiskDecision",
        "RiskDecisionOutcome",
        "RiskEvidenceRef",
    }


def test_provider_and_execution_neutrality_static_guard():
    path = Path("bot_obrero/risk_contracts.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = (
        "binance",
        "websocket",
        "requests",
        "http",
        "execution",
        "ownership",
        "reconciliation",
    )
    imported = []
    float_calls = []
    float_literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                float_calls.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            float_literals.append(node.lineno)
    assert all(
        not any(fragment in name.lower() for fragment in forbidden)
        for name in imported
    )
    assert float_calls == []
    assert float_literals == []


def test_provenance_is_observed_or_derived_only():
    assert account_state().provenance.nature is ArtifactNature.OBSERVED
    assert exposure().provenance.nature is ArtifactNature.DERIVED
