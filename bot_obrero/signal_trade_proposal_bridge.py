"""Explicit, fail-closed bridge from canonical Signal to TradeProposal.

This module does not produce signals, size positions, query market data, or execute.
All economic terms and direction-to-side mappings must be supplied by PAPER policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from .analysis_contracts import Signal, SignalValidity
from .trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
    build_trade_proposal,
)


class SignalTradeProposalBridgeError(ValueError):
    """An explicit, auditable rejection at the Signal → TradeProposal boundary."""


@dataclass(frozen=True)
class PaperProposalPolicy:
    """Explicit PAPER proposal terms; no field is inferred from market state."""

    direction_to_side: Mapping[str, TradeSide]
    requested_quantity: Decimal
    order_type: TradeOrderType
    price_policy: PricePolicy
    strategy_identity: str
    strategy_version: str
    requested_price: Decimal | None = None
    max_quote_spend: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.direction_to_side, Mapping) or not self.direction_to_side:
            raise SignalTradeProposalBridgeError("PAPER_POLICY_DIRECTION_MAPPING_REQUIRED")
        mapping = dict(self.direction_to_side)
        if any(type(key) is not str or not key.strip() for key in mapping):
            raise SignalTradeProposalBridgeError("PAPER_POLICY_DIRECTION_KEYS_INVALID")
        if any(not isinstance(value, TradeSide) for value in mapping.values()):
            raise SignalTradeProposalBridgeError("PAPER_POLICY_DIRECTION_SIDE_INVALID")
        object.__setattr__(self, "direction_to_side", MappingProxyType(mapping))
        if type(self.requested_quantity) is not Decimal or not self.requested_quantity.is_finite() or self.requested_quantity <= 0:
            raise SignalTradeProposalBridgeError("PAPER_POLICY_QUANTITY_MUST_BE_EXPLICIT_POSITIVE_DECIMAL")
        if not isinstance(self.order_type, TradeOrderType):
            raise SignalTradeProposalBridgeError("PAPER_POLICY_ORDER_TYPE_REQUIRED")
        if not isinstance(self.price_policy, PricePolicy):
            raise SignalTradeProposalBridgeError("PAPER_POLICY_PRICE_POLICY_REQUIRED")
        if type(self.strategy_identity) is not str or not self.strategy_identity.strip():
            raise SignalTradeProposalBridgeError("PAPER_POLICY_STRATEGY_IDENTITY_REQUIRED")
        if type(self.strategy_version) is not str or not self.strategy_version.strip():
            raise SignalTradeProposalBridgeError("PAPER_POLICY_STRATEGY_VERSION_REQUIRED")
        if self.requested_price is not None and (
            type(self.requested_price) is not Decimal
            or not self.requested_price.is_finite()
            or self.requested_price <= 0
        ):
            raise SignalTradeProposalBridgeError("PAPER_POLICY_REQUESTED_PRICE_INVALID")
        if self.max_quote_spend is not None and (
            type(self.max_quote_spend) is not Decimal
            or not self.max_quote_spend.is_finite()
            or self.max_quote_spend <= 0
        ):
            raise SignalTradeProposalBridgeError("PAPER_POLICY_MAX_QUOTE_SPEND_INVALID")


def build_paper_trade_proposal(
    *,
    signal: Signal,
    policy: PaperProposalPolicy | None,
    decision_timestamp: datetime,
) -> TradeProposal:
    """Build a canonical proposal only from a valid Signal and explicit PAPER terms."""
    if not isinstance(signal, Signal):
        raise SignalTradeProposalBridgeError("CANONICAL_SIGNAL_REQUIRED")
    if policy is None or not isinstance(policy, PaperProposalPolicy):
        raise SignalTradeProposalBridgeError("EXPLICIT_PAPER_POLICY_REQUIRED")
    if not isinstance(decision_timestamp, datetime) or decision_timestamp.tzinfo is None or decision_timestamp.utcoffset() is None:
        raise SignalTradeProposalBridgeError("DECISION_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    if decision_timestamp < signal.decision_timestamp:
        raise SignalTradeProposalBridgeError("DECISION_TIMESTAMP_PRECEDES_SIGNAL_DECISION")
    if decision_timestamp < signal.generated_at:
        raise SignalTradeProposalBridgeError("DECISION_TIMESTAMP_PRECEDES_SIGNAL_GENERATION")
    if signal.validity is not SignalValidity.VALID or not signal.is_valid_at(decision_timestamp):
        raise SignalTradeProposalBridgeError("SIGNAL_INVALID_EXPIRED_OR_VALIDITY_UNKNOWN")
    provenance_identity = signal.provenance.metadata.get("strategy_identity")
    provenance_version = signal.provenance.metadata.get("strategy_version")
    if provenance_identity != policy.strategy_identity:
        raise SignalTradeProposalBridgeError("SIGNAL_STRATEGY_IDENTITY_MISSING_OR_MISMATCHED")
    if provenance_version != policy.strategy_version:
        raise SignalTradeProposalBridgeError("SIGNAL_STRATEGY_VERSION_MISSING_OR_MISMATCHED")
    if signal.direction not in policy.direction_to_side:
        raise SignalTradeProposalBridgeError("SIGNAL_DIRECTION_HAS_NO_EXPLICIT_SIDE_MAPPING")

    side = policy.direction_to_side[signal.direction]
    if policy.order_type is TradeOrderType.MARKET:
        if policy.price_policy is not PricePolicy.MARKET_REFERENCE or policy.requested_price is not None:
            raise SignalTradeProposalBridgeError("MARKET_ORDER_PRICE_TERMS_INCOMPATIBLE")
    elif policy.order_type is TradeOrderType.LIMIT:
        if policy.price_policy is not PricePolicy.FIXED or policy.requested_price is None:
            raise SignalTradeProposalBridgeError("LIMIT_ORDER_REQUIRES_FIXED_EXPLICIT_PRICE")
    else:
        raise SignalTradeProposalBridgeError("UNSUPPORTED_ORDER_TYPE")

    if side is TradeSide.BUY and policy.order_type is TradeOrderType.MARKET:
        if policy.max_quote_spend is None:
            raise SignalTradeProposalBridgeError("BUY_MARKET_REQUIRES_MAX_QUOTE_SPEND")
    elif policy.max_quote_spend is not None:
        raise SignalTradeProposalBridgeError("MAX_QUOTE_SPEND_ONLY_ALLOWED_FOR_BUY_MARKET")

    try:
        return build_trade_proposal(
            signal=signal,
            side=side,
            requested_quantity=policy.requested_quantity,
            requested_price=policy.requested_price,
            max_quote_spend=policy.max_quote_spend,
            price_policy=policy.price_policy,
            order_type=policy.order_type,
            strategy_identity=policy.strategy_identity,
            strategy_version=policy.strategy_version,
            decision_timestamp=decision_timestamp,
        )
    except (TypeError, ValueError) as exc:
        raise SignalTradeProposalBridgeError(f"CANONICAL_TRADE_PROPOSAL_REJECTED: {exc}") from exc


__all__ = [
    "PaperProposalPolicy",
    "SignalTradeProposalBridgeError",
    "build_paper_trade_proposal",
]
