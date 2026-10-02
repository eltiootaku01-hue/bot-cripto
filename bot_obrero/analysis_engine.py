"""Provider-neutral Analysis Engine contractual core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from .analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    MarketObservation,
    Provenance,
)


class AnalysisEngineError(ValueError):
    """Base error for provider-neutral Analysis Engine contract failures."""


class InputContractError(AnalysisEngineError):
    """Input observations violate the Engine contract."""


class TemporalContractError(AnalysisEngineError):
    """Decision timestamp or availability is invalid."""


class WindowContractError(AnalysisEngineError):
    """Analysis window is invalid."""


class AlgorithmConfigurationError(AnalysisEngineError):
    """Algorithm/configuration boundary is invalid."""


class AlgorithmExecutionError(AnalysisEngineError):
    """Algorithm failed to produce derived values."""


class WindowKind(str, Enum):
    ALL = "ALL"
    LAST_N = "LAST_N"
    TIME_RANGE = "TIME_RANGE"
    EXPLICIT = "EXPLICIT"


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise TemporalContractError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TemporalContractError(f"{field_name} must be timezone-aware")


def _freeze_mapping(value: Mapping[str, Any] | None, field_name: str) -> Mapping[str, Any]:
    if value is None:
        raise AlgorithmConfigurationError(
            f"{field_name} is required; use an explicit empty mapping when intentionally empty"
        )
    if not isinstance(value, Mapping):
        raise AlgorithmConfigurationError(f"{field_name} must be a mapping")
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class WindowSpecification:
    """Immutable window selector."""

    kind: WindowKind
    count: int | None = None
    start: datetime | None = None
    end: datetime | None = None
    observation_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        try:
            kind = WindowKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise WindowContractError("window.kind is invalid") from exc
        object.__setattr__(self, "kind", kind)

        if kind is WindowKind.ALL:
            if any(
                value is not None
                for value in (self.count, self.start, self.end, self.observation_ids)
            ):
                raise WindowContractError("ALL window accepts no additional selectors")
            return

        if kind is WindowKind.LAST_N:
            if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count <= 0:
                raise WindowContractError("LAST_N requires count > 0")
            if any(
                value is not None
                for value in (self.start, self.end, self.observation_ids)
            ):
                raise WindowContractError("LAST_N accepts only count")
            return

        if kind is WindowKind.TIME_RANGE:
            if self.count is not None or self.observation_ids is not None:
                raise WindowContractError("TIME_RANGE accepts only start and end")
            if self.start is None or self.end is None:
                raise WindowContractError("TIME_RANGE requires start and end")
            _require_aware(self.start, "window.start")
            _require_aware(self.end, "window.end")
            if self.start >= self.end:
                raise WindowContractError("TIME_RANGE requires start < end")
            return

        if self.count is not None or self.start is not None or self.end is not None:
            raise WindowContractError("EXPLICIT accepts only observation_ids")
        if self.observation_ids is None:
            raise WindowContractError("EXPLICIT requires observation_ids")
        ids = tuple(self.observation_ids)
        if not ids:
            raise WindowContractError("EXPLICIT requires at least one observation_id")
        if any(not isinstance(item, str) or not item.strip() for item in ids):
            raise WindowContractError("EXPLICIT observation_ids must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise WindowContractError("EXPLICIT observation_ids must be unique")
        object.__setattr__(self, "observation_ids", ids)

    @classmethod
    def all(cls) -> "WindowSpecification":
        return cls(kind=WindowKind.ALL)

    @classmethod
    def last_n(cls, count: int) -> "WindowSpecification":
        return cls(kind=WindowKind.LAST_N, count=count)

    @classmethod
    def time_range(cls, start: datetime, end: datetime) -> "WindowSpecification":
        return cls(kind=WindowKind.TIME_RANGE, start=start, end=end)

    @classmethod
    def explicit(cls, observation_ids: Sequence[str]) -> "WindowSpecification":
        return cls(kind=WindowKind.EXPLICIT, observation_ids=tuple(observation_ids))


@dataclass(frozen=True)
class AnalysisCalculationConfig:
    """Immutable calculation identity plus effective configuration."""

    analysis_type: str
    algorithm_identity: str
    algorithm_version: str
    effective_configuration: Mapping[str, Any]
    effective_parameters: Mapping[str, Any]
    window: WindowSpecification

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.analysis_type, "analysis_type"),
            (self.algorithm_identity, "algorithm_identity"),
            (self.algorithm_version, "algorithm_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise AlgorithmConfigurationError(f"{field_name} must be non-empty")

        object.__setattr__(
            self,
            "effective_configuration",
            _freeze_mapping(self.effective_configuration, "effective_configuration"),
        )
        object.__setattr__(
            self,
            "effective_parameters",
            _freeze_mapping(self.effective_parameters, "effective_parameters"),
        )
        if not isinstance(self.window, WindowSpecification):
            raise WindowContractError("window must be WindowSpecification")


class AnalysisAlgorithm(Protocol):
    """Minimal provider-neutral algorithm boundary."""

    def compute(
        self,
        observations: Sequence[MarketObservation],
        configuration: AnalysisCalculationConfig,
    ) -> Mapping[str, Any]:
        """Return derived values only."""


def _validate_observations(
    observations: Sequence[MarketObservation],
) -> tuple[MarketObservation, ...]:
    if not isinstance(observations, Sequence):
        raise InputContractError("observations must be a Sequence")
    if not observations:
        raise InputContractError("observations must not be empty")

    validated = tuple(observations)
    seen: set[str] = set()

    for observation in validated:
        if not isinstance(observation, MarketObservation):
            raise InputContractError("every observation must be a MarketObservation")
        if not isinstance(observation.observation_id, str) or not observation.observation_id.strip():
            raise InputContractError("observation_id must be non-empty")
        if observation.observation_id in seen:
            raise InputContractError("duplicate observation_id")
        seen.add(observation.observation_id)

        if observation.provenance.nature is not ArtifactNature.OBSERVED:
            raise InputContractError("MarketObservation provenance must be OBSERVED")

        _require_aware(observation.observation_timestamp, "observation_timestamp")
        _require_aware(observation.available_timestamp, "available_timestamp")

    symbol = validated[0].symbol
    venue = validated[0].venue
    if not isinstance(symbol, str) or not symbol.strip():
        raise InputContractError("symbol must be non-empty")

    for observation in validated[1:]:
        if observation.symbol != symbol:
            raise InputContractError("mixed symbols are not allowed")
        if observation.venue != venue:
            raise InputContractError("mixed venues are not allowed")

    return validated


def _validate_order(observations: Sequence[MarketObservation]) -> None:
    previous: tuple[datetime, datetime, str] | None = None
    for observation in observations:
        current = (
            observation.observation_timestamp,
            observation.available_timestamp,
            observation.observation_id,
        )
        if previous is not None and current <= previous:
            raise InputContractError(
                "observations must be strictly ascending by "
                "(observation_timestamp, available_timestamp, observation_id)"
            )
        previous = current


def _validate_temporal_availability(
    observations: Sequence[MarketObservation],
    decision_timestamp: datetime,
) -> None:
    _require_aware(decision_timestamp, "decision_timestamp")
    for observation in observations:
        try:
            observation.evidence_at(decision_timestamp)
        except (ValueError, TypeError) as exc:
            raise TemporalContractError(str(exc)) from exc


def _select_window(
    observations: tuple[MarketObservation, ...],
    window: WindowSpecification,
) -> tuple[MarketObservation, ...]:
    if window.kind is WindowKind.ALL:
        return observations

    if window.kind is WindowKind.LAST_N:
        assert window.count is not None
        if window.count > len(observations):
            raise WindowContractError(
                f"LAST_N window too large: requested {window.count}, available {len(observations)}"
            )
        return observations[-window.count :]

    if window.kind is WindowKind.TIME_RANGE:
        assert window.start is not None and window.end is not None
        selected = tuple(
            observation
            for observation in observations
            if window.start <= observation.observation_timestamp < window.end
        )
        if not selected:
            raise WindowContractError("TIME_RANGE selected no observations")
        return selected

    assert window.observation_ids is not None
    by_id = {observation.observation_id: observation for observation in observations}
    missing = [item for item in window.observation_ids if item not in by_id]
    if missing:
        raise WindowContractError(
            "EXPLICIT selection references unknown observation_id(s): "
            + ", ".join(missing)
        )
    selected = tuple(by_id[item] for item in window.observation_ids)
    _validate_order(selected)
    return selected


def _build_provenance(config: AnalysisCalculationConfig) -> Provenance:
    return Provenance(
        source="analysis-engine",
        nature=ArtifactNature.DERIVED,
        metadata={
            "algorithm_identity": config.algorithm_identity,
            "algorithm_version": config.algorithm_version,
            "effective_configuration": dict(config.effective_configuration),
            "effective_parameters": dict(config.effective_parameters),
            "analysis_type": config.analysis_type,
        },
    )


class AnalysisEngine:
    """Provider-neutral orchestration boundary."""

    def execute(
        self,
        observations: Sequence[MarketObservation],
        *,
        decision_timestamp: datetime,
        configuration: AnalysisCalculationConfig,
        algorithm: AnalysisAlgorithm,
    ) -> AnalysisResult:
        if not isinstance(configuration, AnalysisCalculationConfig):
            raise AlgorithmConfigurationError(
                "configuration must be AnalysisCalculationConfig"
            )

        validated = _validate_observations(observations)
        _validate_order(validated)
        _validate_temporal_availability(validated, decision_timestamp)

        selected = _select_window(validated, configuration.window)
        if not selected:
            raise WindowContractError("analysis window selected no observations")

        try:
            compute = algorithm.compute
        except AttributeError as exc:
            raise AlgorithmConfigurationError(
                "algorithm must expose compute(observations, configuration)"
            ) from exc
        if not callable(compute):
            raise AlgorithmConfigurationError("algorithm.compute must be callable")

        selected_tuple = tuple(selected)
        try:
            derived_values = compute(selected_tuple, configuration)
        except AnalysisEngineError:
            raise
        except Exception as exc:
            raise AlgorithmExecutionError("algorithm execution failed") from exc

        if isinstance(derived_values, AnalysisResult):
            raise AlgorithmExecutionError(
                "algorithm must return derived values, not AnalysisResult"
            )
        if not isinstance(derived_values, Mapping):
            raise AlgorithmExecutionError(
                "algorithm must return a mapping of derived values"
            )

        calculated_at = max(datetime.now(timezone.utc), decision_timestamp)
        provenance = _build_provenance(configuration)

        return AnalysisResult.from_observations(
            symbol=selected_tuple[0].symbol,
            observations=selected_tuple,
            analysis_type=configuration.analysis_type,
            values=dict(derived_values),
            calculated_at=calculated_at,
            decision_timestamp=decision_timestamp,
            provenance=provenance,
        )


__all__ = [
    "AnalysisAlgorithm",
    "AnalysisCalculationConfig",
    "AnalysisEngine",
    "AnalysisEngineError",
    "AlgorithmConfigurationError",
    "AlgorithmExecutionError",
    "InputContractError",
    "TemporalContractError",
    "WindowContractError",
    "WindowKind",
    "WindowSpecification",
]
