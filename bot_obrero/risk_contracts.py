"""Canonical provider-neutral financial state and risk decision contracts for Phase 1.45.

This module defines immutable transport contracts only. It does not evaluate risk,
calculate exposure/equity, produce OrderIntent values, or access providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable

from .analysis_contracts import ArtifactNature, Provenance
from .trade_proposal import TradeProposal


class Completeness(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class RiskDecisionOutcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _decimal(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _provenance(value: Provenance, field_name: str = "provenance") -> Provenance:
    if not isinstance(value, Provenance):
        raise ValueError(f"{field_name} must be Provenance")
    if value.nature not in (ArtifactNature.OBSERVED, ArtifactNature.DERIVED):
        raise ValueError(f"{field_name} has unsupported nature")
    return value


@dataclass(frozen=True)
class BalanceSnapshot:
    asset: str
    total: Decimal
    available: Decimal
    locked: Decimal

    def __post_init__(self) -> None:
        _nonempty(self.asset, "asset")
        for field_name in ("total", "available", "locked"):
            _decimal(getattr(self, field_name), field_name)
        if self.total < 0 or self.available < 0 or self.locked < 0:
            raise ValueError("balance quantities must be non-negative")
        if self.available > self.total:
            raise ValueError("available cannot exceed total")
        if self.locked > self.total:
            raise ValueError("locked cannot exceed total")
        if self.available + self.locked > self.total:
            raise ValueError("available plus locked cannot exceed total")


@dataclass(frozen=True)
class CanonicalAccountState:
    account_state_id: str
    account_id: str
    as_of: datetime
    balances: tuple[BalanceSnapshot, ...]
    completeness: Completeness
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty(self.account_state_id, "account_state_id")
        _nonempty(self.account_id, "account_id")
        if self.account_state_id == self.account_id:
            raise ValueError("account_state_id must differ from account_id")
        _aware(self.as_of, "as_of")
        if not isinstance(self.completeness, Completeness):
            raise ValueError("completeness must be Completeness")
        _provenance(self.provenance)
        balances = tuple(self.balances)
        if not all(isinstance(item, BalanceSnapshot) for item in balances):
            raise ValueError("balances must contain only BalanceSnapshot values")
        assets = [item.asset for item in balances]
        if len(assets) != len(set(assets)):
            raise ValueError("balances must not contain duplicate assets")
        object.__setattr__(self, "balances", balances)


@dataclass(frozen=True)
class CanonicalPosition:
    position_id: str
    account_id: str
    symbol: str
    quantity: Decimal
    as_of: datetime
    completeness: Completeness
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty(self.position_id, "position_id")
        _nonempty(self.account_id, "account_id")
        _nonempty(self.symbol, "symbol")
        if self.position_id == self.account_id:
            raise ValueError("position_id must differ from account_id")
        _decimal(self.quantity, "quantity")
        _aware(self.as_of, "as_of")
        if not isinstance(self.completeness, Completeness):
            raise ValueError("completeness must be Completeness")
        _provenance(self.provenance)


@dataclass(frozen=True)
class CanonicalExposure:
    exposure_id: str
    account_id: str
    symbol: str
    quantity: Decimal
    valuation_price: Decimal | None
    notional: Decimal | None
    as_of: datetime
    valuation_as_of: datetime | None
    completeness: Completeness
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty(self.exposure_id, "exposure_id")
        _nonempty(self.account_id, "account_id")
        _nonempty(self.symbol, "symbol")
        if self.exposure_id == self.account_id:
            raise ValueError("exposure_id must differ from account_id")
        _decimal(self.quantity, "quantity")
        if self.valuation_price is not None:
            _decimal(self.valuation_price, "valuation_price")
        if self.notional is not None:
            _decimal(self.notional, "notional")
        _aware(self.as_of, "as_of")
        if self.valuation_as_of is not None:
            _aware(self.valuation_as_of, "valuation_as_of")
        if self.notional is not None and (
            self.valuation_price is None or self.valuation_as_of is None
        ):
            raise ValueError(
                "notional requires valuation_price and valuation_as_of evidence"
            )
        if self.valuation_as_of is not None and self.valuation_price is None:
            raise ValueError("valuation_as_of requires valuation_price evidence")
        if not isinstance(self.completeness, Completeness):
            raise ValueError("completeness must be Completeness")
        _provenance(self.provenance)


@dataclass(frozen=True)
class RiskLimit:
    risk_limit_id: str
    scope: str
    metric: str
    threshold: Decimal
    unit: str
    effective_from: datetime
    effective_until: datetime | None
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty(self.risk_limit_id, "risk_limit_id")
        _nonempty(self.scope, "scope")
        _nonempty(self.metric, "metric")
        _decimal(self.threshold, "threshold")
        _nonempty(self.unit, "unit")
        _aware(self.effective_from, "effective_from")
        if self.effective_until is not None:
            _aware(self.effective_until, "effective_until")
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must follow effective_from")
        _provenance(self.provenance)


@dataclass(frozen=True)
class RiskLimitSet:
    risk_limit_set_id: str
    limits: tuple[RiskLimit, ...]
    as_of: datetime
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty(self.risk_limit_set_id, "risk_limit_set_id")
        _aware(self.as_of, "as_of")
        _provenance(self.provenance)
        limits = tuple(self.limits)
        if not all(isinstance(item, RiskLimit) for item in limits):
            raise ValueError("limits must contain only RiskLimit values")
        limit_ids = [item.risk_limit_id for item in limits]
        if len(limit_ids) != len(set(limit_ids)):
            raise ValueError("limits must not contain duplicate risk_limit_id values")
        object.__setattr__(self, "limits", limits)


@dataclass(frozen=True)
class RiskEvidenceRef:
    kind: str
    reference_id: str
    as_of: datetime

    def __post_init__(self) -> None:
        _nonempty(self.kind, "kind")
        _nonempty(self.reference_id, "reference_id")
        _aware(self.as_of, "as_of")


@dataclass(frozen=True)
class RiskDecision:
    """Immutable risk evaluation decision with a proposal-bound construction path.

    The primitive dataclass constructor remains available for the existing
    transport contract. The from_trade_proposal constructor is the
    identity-safe path that derives proposal_id, signal_id, and correlation_id
    directly from one concrete TradeProposal.
    """

    risk_decision_id: str
    proposal_id: str
    signal_id: str
    outcome: RiskDecisionOutcome
    reason: str
    decision_timestamp: datetime
    risk_evidence: tuple[RiskEvidenceRef, ...]
    correlation_id: str

    @classmethod
    def from_trade_proposal(
        cls,
        *,
        proposal: TradeProposal,
        risk_decision_id: str,
        outcome: RiskDecisionOutcome,
        reason: str,
        decision_timestamp: datetime,
        risk_evidence: tuple[RiskEvidenceRef, ...],
    ) -> "RiskDecision":
        """Build a RiskDecision while deriving identity from one TradeProposal."""
        if not isinstance(proposal, TradeProposal):
            raise TypeError("proposal must be TradeProposal")
        return cls(
            risk_decision_id=risk_decision_id,
            proposal_id=proposal.proposal_id,
            signal_id=proposal.signal_id,
            outcome=outcome,
            reason=reason,
            decision_timestamp=decision_timestamp,
            risk_evidence=risk_evidence,
            correlation_id=proposal.correlation_id,
        )

    def __post_init__(self) -> None:
        _nonempty(self.risk_decision_id, "risk_decision_id")
        _nonempty(self.proposal_id, "proposal_id")
        _nonempty(self.signal_id, "signal_id")
        if self.proposal_id in (self.risk_decision_id, self.signal_id):
            raise ValueError(
                "proposal_id must differ from risk_decision_id and signal_id"
            )
        if not isinstance(self.outcome, RiskDecisionOutcome):
            raise ValueError("outcome must be RiskDecisionOutcome")
        _nonempty(self.reason, "reason")
        _aware(self.decision_timestamp, "decision_timestamp")
        _nonempty(self.correlation_id, "correlation_id")
        if self.correlation_id in (
            self.risk_decision_id,
            self.proposal_id,
            self.signal_id,
        ):
            raise ValueError(
                "correlation_id must differ from risk_decision_id, proposal_id, and signal_id"
            )
        evidence = tuple(self.risk_evidence)
        if not all(isinstance(item, RiskEvidenceRef) for item in evidence):
            raise ValueError("risk_evidence must contain only RiskEvidenceRef values")
        object.__setattr__(self, "risk_evidence", evidence)


__all__ = [
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
]
