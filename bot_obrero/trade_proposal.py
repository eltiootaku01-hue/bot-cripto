"""Immutable, provider-neutral trade proposal contract.

This module defines the economic proposal handed from Signal toward the
future Risk layer. It performs structural validation only: it does not
authorize execution, evaluate risk, calculate sizing, consult providers, or
construct OrderIntent values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from .analysis_contracts import ContractError, Signal


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class PricePolicy(str, Enum):
    FIXED = "FIXED"
    MARKET_REFERENCE = "MARKET_REFERENCE"


def _nonempty_str(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty str")


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ContractError(f"{field_name} must be timezone-aware")


def _decimal_positive(value: Decimal, field_name: str) -> None:
    if type(value) is not Decimal:
        raise ContractError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ContractError(f"{field_name} must be finite")
    if value <= 0:
        raise ContractError(f"{field_name} must be greater than zero")


@dataclass(frozen=True)
class TradeProposal:
    proposal_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    signal_id: str
    symbol: str
    side: TradeSide
    requested_quantity: Decimal
    requested_price: Decimal | None
    price_policy: PricePolicy
    order_type: TradeOrderType
    strategy_identity: str
    strategy_version: str
    decision_timestamp: datetime
    correlation_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _nonempty_str(self.proposal_id, "proposal_id")
        _nonempty_str(self.signal_id, "signal_id")
        _nonempty_str(self.symbol, "symbol")
        if self.proposal_id == self.signal_id:
            raise ContractError("proposal_id must differ from signal_id")
        if not isinstance(self.side, TradeSide):
            raise ContractError("side must be TradeSide")
        _decimal_positive(self.requested_quantity, "requested_quantity")

        if self.requested_price is not None:
            _decimal_positive(self.requested_price, "requested_price")

        if not isinstance(self.price_policy, PricePolicy):
            raise ContractError("price_policy must be PricePolicy")
        if not isinstance(self.order_type, TradeOrderType):
            raise ContractError("order_type must be TradeOrderType")

        if self.order_type is TradeOrderType.MARKET:
            if self.requested_price is not None:
                raise ContractError(
                    "MARKET order must not specify requested_price"
                )
            if self.price_policy is not PricePolicy.MARKET_REFERENCE:
                raise ContractError(
                    "MARKET order requires MARKET_REFERENCE price_policy"
                )
        elif self.order_type is TradeOrderType.LIMIT:
            if self.requested_price is None:
                raise ContractError(
                    "LIMIT order requires requested_price"
                )
            if self.price_policy is not PricePolicy.FIXED:
                raise ContractError(
                    "LIMIT order requires FIXED price_policy"
                )

        _nonempty_str(self.strategy_identity, "strategy_identity")
        _nonempty_str(self.strategy_version, "strategy_version")
        _aware(self.decision_timestamp, "decision_timestamp")

        _nonempty_str(self.correlation_id, "correlation_id")
        if self.correlation_id in (self.proposal_id, self.signal_id):
            raise ContractError(
                "correlation_id must differ from proposal_id and signal_id"
            )


def build_trade_proposal(
    *,
    signal: Signal,
    side: TradeSide,
    requested_quantity: Decimal,
    requested_price: Decimal | None,
    price_policy: PricePolicy,
    order_type: TradeOrderType,
    strategy_identity: str,
    strategy_version: str,
    decision_timestamp: datetime | None = None,
) -> TradeProposal:
    """Build a TradeProposal from one Signal without interpreting its direction."""
    if not isinstance(signal, Signal):
        raise ContractError("signal must be Signal")

    proposal_timestamp = (
        signal.decision_timestamp
        if decision_timestamp is None
        else decision_timestamp
    )
    _aware(proposal_timestamp, "decision_timestamp")
    if proposal_timestamp < signal.decision_timestamp:
        raise ContractError(
            "proposal decision_timestamp cannot precede signal decision_timestamp"
        )

    return TradeProposal(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        side=side,
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        price_policy=price_policy,
        order_type=order_type,
        strategy_identity=strategy_identity,
        strategy_version=strategy_version,
        decision_timestamp=proposal_timestamp,
    )


__all__ = [
    "PricePolicy",
    "TradeOrderType",
    "TradeProposal",
    "TradeSide",
    "build_trade_proposal",
]
