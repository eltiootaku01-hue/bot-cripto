from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance, Signal, SignalValidity
from bot_obrero.signal_trade_proposal_bridge import (
    PaperProposalPolicy,
    SignalTradeProposalBridgeError,
    build_paper_trade_proposal,
)
from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeSide

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def make_signal(*, direction="UP", validity=SignalValidity.VALID, expires_at=None,
                decision_timestamp=None, generated_at=None):
    decision_timestamp = decision_timestamp or NOW - timedelta(seconds=10)
    generated_at = generated_at or NOW - timedelta(seconds=5)
    return Signal(
        symbol="BTC/USDT",
        hypothesis_id="hypothesis-1",
        direction=direction,
        generated_at=generated_at,
        decision_timestamp=decision_timestamp,
        provenance=Provenance(
            source="deterministic-test",
            nature=ArtifactNature.DERIVED,
            reference="artifact-1",
            metadata={"strategy_identity": "strategy.test", "strategy_version": "1.2.3"},
        ),
        evidence={"test": "deterministic"},
        expires_at=expires_at,
        validity=validity,
        signal_id="signal-canonical-1",
    )


def policy(**overrides):
    values = dict(
        direction_to_side={"UP": TradeSide.BUY, "DOWN": TradeSide.SELL},
        requested_quantity=Decimal("0.2"),
        order_type=TradeOrderType.MARKET,
        price_policy=PricePolicy.MARKET_REFERENCE,
        strategy_identity="strategy.test",
        strategy_version="1.2.3",
        requested_price=None,
        max_quote_spend=Decimal("25"),
    )
    values.update(overrides)
    return PaperProposalPolicy(**values)


def test_valid_signal_and_explicit_policy_build_canonical_proposal():
    signal = make_signal()
    proposal = build_paper_trade_proposal(signal=signal, policy=policy(), decision_timestamp=NOW)
    assert proposal.signal_id == signal.signal_id
    assert proposal.symbol == signal.symbol
    assert proposal.side is TradeSide.BUY
    assert proposal.requested_quantity == Decimal("0.2")
    assert proposal.max_quote_spend == Decimal("25")
    assert proposal.strategy_identity == "strategy.test"
    assert proposal.strategy_version == "1.2.3"
    assert proposal.decision_timestamp == NOW


@pytest.mark.parametrize("validity", [SignalValidity.EXPIRED, SignalValidity.UNKNOWN])
def test_expired_or_unknown_validity_rejected(validity):
    with pytest.raises(SignalTradeProposalBridgeError, match="SIGNAL_INVALID"):
        build_paper_trade_proposal(signal=make_signal(validity=validity), policy=policy(), decision_timestamp=NOW)


def test_expired_by_timestamp_rejected():
    signal = make_signal(expires_at=NOW)
    with pytest.raises(SignalTradeProposalBridgeError, match="SIGNAL_INVALID"):
        build_paper_trade_proposal(signal=signal, policy=policy(), decision_timestamp=NOW)


def test_strategy_identity_and_version_must_match_signal_provenance():
    with pytest.raises(SignalTradeProposalBridgeError, match="STRATEGY_IDENTITY"):
        build_paper_trade_proposal(signal=make_signal(), policy=policy(strategy_identity="other"), decision_timestamp=NOW)
    with pytest.raises(SignalTradeProposalBridgeError, match="STRATEGY_VERSION"):
        build_paper_trade_proposal(signal=make_signal(), policy=policy(strategy_version="9.9"), decision_timestamp=NOW)


def test_missing_policy_rejected():
    with pytest.raises(SignalTradeProposalBridgeError, match="EXPLICIT_PAPER_POLICY_REQUIRED"):
        build_paper_trade_proposal(signal=make_signal(), policy=None, decision_timestamp=NOW)


def test_unmapped_direction_rejected_without_buy_sell_guess():
    with pytest.raises(SignalTradeProposalBridgeError, match="NO_EXPLICIT_SIDE_MAPPING"):
        build_paper_trade_proposal(signal=make_signal(direction="BULLISH"), policy=policy(), decision_timestamp=NOW)


@pytest.mark.parametrize("quantity", [None, Decimal("0"), Decimal("-1")])
def test_missing_or_invalid_quantity_rejected(quantity):
    with pytest.raises(SignalTradeProposalBridgeError, match="QUANTITY"):
        policy(requested_quantity=quantity)


def test_buy_market_without_max_quote_spend_rejected():
    with pytest.raises(SignalTradeProposalBridgeError, match="BUY_MARKET_REQUIRES_MAX_QUOTE_SPEND"):
        build_paper_trade_proposal(signal=make_signal(), policy=policy(max_quote_spend=None), decision_timestamp=NOW)


def test_incompatible_order_and_price_policy_rejected():
    with pytest.raises(SignalTradeProposalBridgeError, match="MARKET_ORDER_PRICE_TERMS_INCOMPATIBLE"):
        build_paper_trade_proposal(
            signal=make_signal(), policy=policy(price_policy=PricePolicy.FIXED), decision_timestamp=NOW
        )


def test_limit_requires_explicit_fixed_price():
    with pytest.raises(SignalTradeProposalBridgeError, match="LIMIT_ORDER_REQUIRES_FIXED_EXPLICIT_PRICE"):
        build_paper_trade_proposal(
            signal=make_signal(),
            policy=policy(order_type=TradeOrderType.LIMIT, price_policy=PricePolicy.FIXED,
                          requested_price=None, max_quote_spend=None),
            decision_timestamp=NOW,
        )


def test_decision_timestamp_before_signal_decision_or_generation_rejected():
    signal = make_signal()
    with pytest.raises(SignalTradeProposalBridgeError, match="PRECEDES_SIGNAL_DECISION"):
        build_paper_trade_proposal(signal=signal, policy=policy(), decision_timestamp=signal.decision_timestamp - timedelta(seconds=1))
    with pytest.raises(SignalTradeProposalBridgeError, match="PRECEDES_SIGNAL_GENERATION"):
        build_paper_trade_proposal(signal=signal, policy=policy(), decision_timestamp=signal.generated_at - timedelta(seconds=1))


def test_non_signal_input_including_product_no_trade_result_rejected():
    # Product V2 NO_TRADE is not a canonical Signal and cannot enter this bridge.
    with pytest.raises(SignalTradeProposalBridgeError, match="CANONICAL_SIGNAL_REQUIRED"):
        build_paper_trade_proposal(signal={"final_decision": "NO_TRADE"}, policy=policy(), decision_timestamp=NOW)


def test_bridge_has_no_private_exchange_or_execution_dependencies():
    source = Path("bot_obrero/signal_trade_proposal_bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.lower() for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported.update((node.module or "").lower() for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    assert not any("binance" in name or "api_key" in name or "paper_adapter" in name or "execution" in name for name in imported)
