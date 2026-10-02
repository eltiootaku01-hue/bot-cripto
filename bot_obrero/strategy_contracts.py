"""Minimal provider-neutral Strategy contract.

This module defines only the contractual frontier between an immutable
AnalysisSnapshot and a future strategy implementation. It does not implement
trading logic, signals, orders, risk, execution, registries, or factories.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .analysis_snapshot import AnalysisSnapshot


class StrategyContractError(ValueError):
    """Raised when a Strategy contract boundary is invalid."""


_ALLOWED_SCALARS = (str, int, bool, Decimal, datetime, date, type(None))


def _require_nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StrategyContractError(f"{field_name} must be a non-empty string")
    return value


def _freeze_value(value: Any, field_name: str) -> Any:
    """Recursively convert supported configuration/result values to immutable forms."""
    if isinstance(value, Enum):
        value = value.value

    if type(value) is float:
        raise StrategyContractError(f"{field_name} must not use float")
    if isinstance(value, _ALLOWED_SCALARS):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise StrategyContractError(
                    f"{field_name} datetime values must be timezone-aware"
                )
        return value

    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise StrategyContractError(
                    f"{field_name} mapping keys must be non-empty strings"
                )
            frozen[key] = _freeze_value(item, f"{field_name}[{key!r}]")
        return MappingProxyType(frozen)

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )

    raise StrategyContractError(
        f"{field_name} contains an unsupported or mutable value type: "
        f"{type(value).__name__}"
    )


def _freeze_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StrategyContractError(f"{field_name} must be a mapping")
    frozen = _freeze_value(value, field_name)
    if not isinstance(frozen, Mapping):
        raise StrategyContractError(f"{field_name} must be a mapping")
    return frozen


@dataclass(frozen=True)
class StrategyCalculationConfig:
    """Immutable, explicit effective Strategy calculation configuration."""

    strategy_type: str
    strategy_identity: str
    strategy_version: str
    effective_configuration: Mapping[str, Any]
    effective_parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_nonempty_string(self.strategy_type, "strategy_type")
        _require_nonempty_string(self.strategy_identity, "strategy_identity")
        _require_nonempty_string(self.strategy_version, "strategy_version")
        object.__setattr__(
            self,
            "effective_configuration",
            _freeze_mapping(
                self.effective_configuration,
                "effective_configuration",
            ),
        )
        object.__setattr__(
            self,
            "effective_parameters",
            _freeze_mapping(
                self.effective_parameters,
                "effective_parameters",
            ),
        )


@dataclass(frozen=True)
class StrategicArtifact:
    """Neutral strategy output carrying the complete calculation evidence."""

    snapshot_id: str
    strategic_artifact_id: str
    symbol: str
    decision_timestamp: datetime
    observation_ids: tuple[str, ...]
    strategy_type: str
    strategy_identity: str
    strategy_version: str
    effective_configuration: Mapping[str, Any]
    effective_parameters: Mapping[str, Any]
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_nonempty_string(self.snapshot_id, "snapshot_id")
        _require_nonempty_string(self.strategic_artifact_id, "strategic_artifact_id")
        if self.strategic_artifact_id == self.snapshot_id:
            raise StrategyContractError(
                "strategic_artifact_id must not reuse snapshot_id"
            )
        _require_nonempty_string(self.symbol, "symbol")
        _require_nonempty_string(self.strategy_type, "strategy_type")
        _require_nonempty_string(self.strategy_identity, "strategy_identity")
        _require_nonempty_string(self.strategy_version, "strategy_version")

        if not isinstance(self.decision_timestamp, datetime):
            raise StrategyContractError("decision_timestamp must be a datetime")
        if (
            self.decision_timestamp.tzinfo is None
            or self.decision_timestamp.utcoffset() is None
        ):
            raise StrategyContractError(
                "decision_timestamp must be timezone-aware"
            )

        observation_ids = tuple(self.observation_ids)
        if not observation_ids:
            raise StrategyContractError(
                "observation_ids must be a non-empty sequence"
            )
        if any(
            not isinstance(item, str) or not item.strip()
            for item in observation_ids
        ):
            raise StrategyContractError(
                "observation_ids must contain non-empty strings"
            )

        object.__setattr__(self, "observation_ids", observation_ids)
        object.__setattr__(
            self,
            "effective_configuration",
            _freeze_mapping(
                self.effective_configuration,
                "effective_configuration",
            ),
        )
        object.__setattr__(
            self,
            "effective_parameters",
            _freeze_mapping(
                self.effective_parameters,
                "effective_parameters",
            ),
        )
        object.__setattr__(
            self,
            "values",
            _freeze_mapping(self.values, "values"),
        )


class Strategy(Protocol):
    """Minimal Strategy boundary: AnalysisSnapshot + StrategyCalculationConfig."""

    strategy_identity: str
    strategy_version: str

    def compute(
        self,
        snapshot: AnalysisSnapshot,
        configuration: StrategyCalculationConfig,
    ) -> StrategicArtifact:
        """Produce a neutral strategic artifact with deterministic logical fields."""
        ...


def build_strategic_artifact(
    snapshot: AnalysisSnapshot,
    configuration: StrategyCalculationConfig,
    values: Mapping[str, Any],
) -> StrategicArtifact:
    """Construct a neutral strategic artifact from existing immutable evidence."""

    if not isinstance(snapshot, AnalysisSnapshot):
        raise StrategyContractError("snapshot must be an AnalysisSnapshot")
    if not isinstance(configuration, StrategyCalculationConfig):
        raise StrategyContractError(
            "configuration must be a StrategyCalculationConfig"
        )

    if snapshot.symbol != snapshot.results[0].symbol:
        raise StrategyContractError("snapshot symbol is inconsistent")
    if tuple(snapshot.observation_ids) != tuple(
        observation_id for observation_id in snapshot.results[0].observation_ids
    ):
        raise StrategyContractError("snapshot observation_ids are inconsistent")

    return StrategicArtifact(
        snapshot_id=snapshot.snapshot_id,
        strategic_artifact_id=uuid4().hex,
        symbol=snapshot.symbol,
        decision_timestamp=snapshot.decision_timestamp,
        observation_ids=tuple(snapshot.observation_ids),
        strategy_type=configuration.strategy_type,
        strategy_identity=configuration.strategy_identity,
        strategy_version=configuration.strategy_version,
        effective_configuration=configuration.effective_configuration,
        effective_parameters=configuration.effective_parameters,
        values=values,
    )


__all__ = [
    "Strategy",
    "StrategyCalculationConfig",
    "StrategyContractError",
    "StrategicArtifact",
    "build_strategic_artifact",
]
