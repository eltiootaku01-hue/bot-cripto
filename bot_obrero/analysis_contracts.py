"""Phase 1.6 contracts for traceable market observation and analysis artifacts.

These contracts intentionally stop at Signal. They have no execution or trading
integration and do not define an indicator or strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .temporal import EvidenceTimestamp


class ArtifactNature(str, Enum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"


class SignalValidity(str, Enum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class ContractError(ValueError):
    """Raised when a Phase 1.6 artifact violates its structural contract."""


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ContractError(f"{field_name} must be timezone-aware")


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    return MappingProxyType(dict(value))


def _nonempty(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ContractError(f"{field_name} must not be empty")
    return value


@dataclass(frozen=True)
class Provenance:
    source: str
    nature: ArtifactNature
    reference: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.source, "source")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@dataclass(frozen=True)
class MarketObservation:
    symbol: str
    observation_timestamp: datetime
    available_timestamp: datetime
    observation_type: str
    values: Mapping[str, Any]
    provenance: Provenance
    venue: str | None = None
    observation_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _nonempty(self.symbol, "symbol")
        _nonempty(self.observation_type, "observation_type")
        _aware(self.observation_timestamp, "observation_timestamp")
        _aware(self.available_timestamp, "available_timestamp")
        if self.provenance.nature is not ArtifactNature.OBSERVED:
            raise ContractError("MarketObservation must be OBSERVED")
        object.__setattr__(self, "values", _mapping(self.values))

    def evidence_at(self, decision_timestamp: datetime) -> EvidenceTimestamp:
        """Validate availability at a decision time using the existing temporal primitive."""
        _aware(decision_timestamp, "decision_timestamp")
        return EvidenceTimestamp(
            event_timestamp=self.observation_timestamp,
            available_timestamp=self.available_timestamp,
            decision_timestamp=decision_timestamp,
        )


@dataclass(frozen=True)
class AnalysisResult:
    symbol: str
    observation_ids: tuple[str, ...]
    analysis_type: str
    values: Mapping[str, Any]
    calculated_at: datetime
    decision_timestamp: datetime
    provenance: Provenance
    analysis_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _nonempty(self.symbol, "symbol")
        _nonempty(self.analysis_type, "analysis_type")
        _aware(self.calculated_at, "calculated_at")
        _aware(self.decision_timestamp, "decision_timestamp")
        if self.calculated_at < self.decision_timestamp:
            raise ContractError("calculated_at cannot precede decision_timestamp")
        if not self.observation_ids:
            raise ContractError("AnalysisResult requires observation_ids")
        if any(not item for item in self.observation_ids):
            raise ContractError("observation_ids must be non-empty")
        if self.provenance.nature is not ArtifactNature.DERIVED:
            raise ContractError("AnalysisResult must be DERIVED")
        object.__setattr__(self, "observation_ids", tuple(self.observation_ids))
        object.__setattr__(self, "values", _mapping(self.values))

    @classmethod
    def from_observations(
        cls,
        *,
        symbol: str,
        observations: Sequence[MarketObservation],
        analysis_type: str,
        values: Mapping[str, Any],
        calculated_at: datetime,
        decision_timestamp: datetime,
        provenance: Provenance,
    ) -> "AnalysisResult":
        if not observations:
            raise ContractError("AnalysisResult requires observations")
        if any(observation.symbol != symbol for observation in observations):
            raise ContractError("all observations must match symbol")
        for observation in observations:
            observation.evidence_at(decision_timestamp)
        return cls(
            symbol=symbol,
            observation_ids=tuple(observation.observation_id for observation in observations),
            analysis_type=analysis_type,
            values=values,
            calculated_at=calculated_at,
            decision_timestamp=decision_timestamp,
            provenance=provenance,
        )


@dataclass(frozen=True)
class Hypothesis:
    symbol: str
    supporting_analysis_ids: tuple[str, ...]
    expected_direction: str
    expected_horizon: str
    invalidation_conditions: tuple[str, ...]
    created_at: datetime
    decision_timestamp: datetime
    provenance: Provenance
    hypothesis_id: str = field(default_factory=lambda: uuid4().hex)
    expires_at: datetime | None = None
    status: str = "UNRESOLVED"

    def __post_init__(self) -> None:
        _nonempty(self.symbol, "symbol")
        _nonempty(self.expected_direction, "expected_direction")
        _nonempty(self.expected_horizon, "expected_horizon")
        _aware(self.created_at, "created_at")
        _aware(self.decision_timestamp, "decision_timestamp")
        if self.created_at > self.decision_timestamp:
            raise ContractError("created_at cannot follow decision_timestamp")
        if not self.supporting_analysis_ids:
            raise ContractError("Hypothesis requires supporting_analysis_ids")
        if any(not item for item in self.supporting_analysis_ids):
            raise ContractError("supporting_analysis_ids must be non-empty")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.decision_timestamp:
                raise ContractError("expires_at must follow decision_timestamp")
        if self.provenance.nature is not ArtifactNature.DERIVED:
            raise ContractError("Hypothesis must be DERIVED")
        object.__setattr__(self, "supporting_analysis_ids", tuple(self.supporting_analysis_ids))
        object.__setattr__(self, "invalidation_conditions", tuple(self.invalidation_conditions))


@dataclass(frozen=True)
class Signal:
    symbol: str
    hypothesis_id: str
    direction: str
    generated_at: datetime
    decision_timestamp: datetime
    provenance: Provenance
    evidence: Mapping[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    validity: SignalValidity = SignalValidity.UNKNOWN
    signal_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _nonempty(self.symbol, "symbol")
        _nonempty(self.hypothesis_id, "hypothesis_id")
        _nonempty(self.direction, "direction")
        _aware(self.generated_at, "generated_at")
        _aware(self.decision_timestamp, "decision_timestamp")
        if self.generated_at < self.decision_timestamp:
            raise ContractError("generated_at cannot precede decision_timestamp")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.decision_timestamp:
                raise ContractError("expires_at must follow decision_timestamp")
        if self.provenance.nature is not ArtifactNature.DERIVED:
            raise ContractError("Signal must be DERIVED")
        object.__setattr__(self, "evidence", _mapping(self.evidence))

    def is_valid_at(self, timestamp: datetime) -> bool:
        _aware(timestamp, "timestamp")
        if self.validity is not SignalValidity.VALID:
            return False
        if timestamp < self.decision_timestamp:
            return False
        return self.expires_at is None or timestamp < self.expires_at
