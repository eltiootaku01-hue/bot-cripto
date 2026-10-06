"""Minimal deterministic Strategy runtime.

This module is the explicit operational boundary from AnalysisSnapshot +
StrategyCalculationConfig to StrategicArtifact -> Hypothesis -> Signal.

It does not calculate indicators, discover market data, perform sizing or risk,
create trade proposals, call providers, persist financial state, or execute
orders. Strategy-specific input sufficiency is owned by the Strategy itself;
contract failures are fail-closed as REJECTED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from .analysis_contracts import (
    ArtifactNature,
    ContractError,
    Hypothesis,
    Provenance,
    Signal,
    SignalValidity,
)
from .analysis_snapshot import AnalysisSnapshot
from .strategy_contracts import (
    StrategicArtifact,
    Strategy,
    StrategyCalculationConfig,
    StrategyContractError,
    build_hypothesis,
    build_signal,
    build_strategic_artifact,
)


class StrategyRuntimeState(str, Enum):
    READY = "READY"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StrategyRuntimeResult:
    """Immutable, auditable result for one explicit runtime invocation."""

    state: StrategyRuntimeState
    lifecycle: tuple[StrategyRuntimeState, ...]
    snapshot_id: str | None
    strategy_identity: str | None
    strategy_version: str | None
    strategic_artifact: StrategicArtifact | None
    hypothesis: Hypothesis | None
    signal: Signal | None
    decision_timestamp: datetime | None
    reason: str
    provenance: Provenance | None


class StrategyRuntime:
    """Invocable Strategy runtime with no persistent market loop or provider access."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = _utc_now if clock is None else clock

    def execute(
        self,
        snapshot: AnalysisSnapshot,
        configuration: StrategyCalculationConfig,
        strategy: Strategy,
    ) -> StrategyRuntimeResult:
        lifecycle: list[StrategyRuntimeState] = [StrategyRuntimeState.READY]

        identity = (
            configuration.strategy_identity
            if isinstance(configuration, StrategyCalculationConfig)
            else None
        )
        version = (
            configuration.strategy_version
            if isinstance(configuration, StrategyCalculationConfig)
            else None
        )
        snapshot_id = (
            snapshot.snapshot_id if isinstance(snapshot, AnalysisSnapshot) else None
        )
        decision_timestamp = (
            snapshot.decision_timestamp
            if isinstance(snapshot, AnalysisSnapshot)
            else None
        )

        try:
            self._validate_inputs(snapshot, configuration, strategy)
        except (StrategyContractError, ContractError, TypeError) as exc:
            lifecycle.append(StrategyRuntimeState.REJECTED)
            return StrategyRuntimeResult(
                state=StrategyRuntimeState.REJECTED,
                lifecycle=tuple(lifecycle),
                snapshot_id=snapshot_id,
                strategy_identity=identity,
                strategy_version=version,
                strategic_artifact=None,
                hypothesis=None,
                signal=None,
                decision_timestamp=decision_timestamp,
                reason=str(exc),
                provenance=None,
            )

        lifecycle.append(StrategyRuntimeState.EXECUTING)

        try:
            strategic_artifact = strategy.compute(snapshot, configuration)
        except (StrategyContractError, ContractError, ValueError) as exc:
            lifecycle.append(StrategyRuntimeState.REJECTED)
            return StrategyRuntimeResult(
                state=StrategyRuntimeState.REJECTED,
                lifecycle=tuple(lifecycle),
                snapshot_id=snapshot.snapshot_id,
                strategy_identity=configuration.strategy_identity,
                strategy_version=configuration.strategy_version,
                strategic_artifact=None,
                hypothesis=None,
                signal=None,
                decision_timestamp=snapshot.decision_timestamp,
                reason=str(exc),
                provenance=None,
            )
        except Exception as exc:
            lifecycle.append(StrategyRuntimeState.UNKNOWN)
            return StrategyRuntimeResult(
                state=StrategyRuntimeState.UNKNOWN,
                lifecycle=tuple(lifecycle),
                snapshot_id=snapshot.snapshot_id,
                strategy_identity=configuration.strategy_identity,
                strategy_version=configuration.strategy_version,
                strategic_artifact=None,
                hypothesis=None,
                signal=None,
                decision_timestamp=snapshot.decision_timestamp,
                reason=(
                    "unexpected strategy execution failure: "
                    f"{type(exc).__name__}: {exc}"
                ),
                provenance=None,
            )

        try:
            self._validate_strategic_artifact(
                snapshot,
                configuration,
                strategic_artifact,
            )
            provenance = _derive_runtime_provenance(snapshot, configuration)
            now = self._clock()
            _require_aware(now, "runtime clock timestamp")
            if now < snapshot.decision_timestamp:
                raise StrategyRuntimeValidationError(
                    "runtime clock timestamp cannot precede snapshot.decision_timestamp"
                )

            payload = _extract_decision_payload(strategic_artifact.values)

            hypothesis = build_hypothesis(
                strategic_artifact,
                expected_direction=payload["expected_direction"],
                expected_horizon=payload["expected_horizon"],
                invalidation_conditions=payload["invalidation_conditions"],
                created_at=now,
                provenance=provenance,
                expires_at=payload["expires_at"],
                status=payload["hypothesis_status"],
            )

            signal = build_signal(
                hypothesis,
                generated_at=now,
                validity=SignalValidity.VALID,
                provenance=provenance,
                evidence=payload["signal_evidence"],
            )
        except (StrategyRuntimeValidationError, StrategyContractError, ContractError, TypeError, ValueError) as exc:
            lifecycle.append(StrategyRuntimeState.REJECTED)
            return StrategyRuntimeResult(
                state=StrategyRuntimeState.REJECTED,
                lifecycle=tuple(lifecycle),
                snapshot_id=snapshot.snapshot_id,
                strategy_identity=configuration.strategy_identity,
                strategy_version=configuration.strategy_version,
                strategic_artifact=(
                    strategic_artifact
                    if isinstance(strategic_artifact, StrategicArtifact)
                    else None
                ),
                hypothesis=None,
                signal=None,
                decision_timestamp=snapshot.decision_timestamp,
                reason=str(exc),
                provenance=None,
            )
        except Exception as exc:
            lifecycle.append(StrategyRuntimeState.UNKNOWN)
            return StrategyRuntimeResult(
                state=StrategyRuntimeState.UNKNOWN,
                lifecycle=tuple(lifecycle),
                snapshot_id=snapshot.snapshot_id,
                strategy_identity=configuration.strategy_identity,
                strategy_version=configuration.strategy_version,
                strategic_artifact=(
                    strategic_artifact
                    if isinstance(strategic_artifact, StrategicArtifact)
                    else None
                ),
                hypothesis=None,
                signal=None,
                decision_timestamp=snapshot.decision_timestamp,
                reason=(
                    "unexpected strategy-output transformation failure: "
                    f"{type(exc).__name__}: {exc}"
                ),
                provenance=None,
            )

        lifecycle.append(StrategyRuntimeState.COMPLETED)
        return StrategyRuntimeResult(
            state=StrategyRuntimeState.COMPLETED,
            lifecycle=tuple(lifecycle),
            snapshot_id=snapshot.snapshot_id,
            strategy_identity=configuration.strategy_identity,
            strategy_version=configuration.strategy_version,
            strategic_artifact=strategic_artifact,
            hypothesis=hypothesis,
            signal=signal,
            decision_timestamp=snapshot.decision_timestamp,
            reason="strategy decision completed successfully",
            provenance=provenance,
        )

    @staticmethod
    def _validate_inputs(
        snapshot: AnalysisSnapshot,
        configuration: StrategyCalculationConfig,
        strategy: Strategy,
    ) -> None:
        if not isinstance(snapshot, AnalysisSnapshot):
            raise StrategyContractError("snapshot must be an AnalysisSnapshot")
        if not isinstance(configuration, StrategyCalculationConfig):
            raise StrategyContractError(
                "configuration must be a StrategyCalculationConfig"
            )

        _require_aware(snapshot.decision_timestamp, "snapshot.decision_timestamp")

        compute = getattr(strategy, "compute", None)
        if not callable(compute):
            raise StrategyContractError(
                "strategy must expose callable compute(snapshot, configuration)"
            )

        strategy_identity = getattr(strategy, "strategy_identity", None)
        strategy_version = getattr(strategy, "strategy_version", None)

        _require_nonempty_string(strategy_identity, "strategy.strategy_identity")
        _require_nonempty_string(strategy_version, "strategy.strategy_version")

        if strategy_identity != configuration.strategy_identity:
            raise StrategyContractError(
                "strategy_identity does not match configuration.strategy_identity"
            )
        if strategy_version != configuration.strategy_version:
            raise StrategyContractError(
                "strategy_version does not match configuration.strategy_version"
            )

    @staticmethod
    def _validate_strategic_artifact(
        snapshot: AnalysisSnapshot,
        configuration: StrategyCalculationConfig,
        strategic_artifact: StrategicArtifact,
    ) -> None:
        if not isinstance(strategic_artifact, StrategicArtifact):
            raise StrategyContractError(
                "strategy.compute() must return a StrategicArtifact"
            )

        if strategic_artifact.snapshot_id != snapshot.snapshot_id:
            raise StrategyContractError(
                "StrategicArtifact.snapshot_id must match snapshot.snapshot_id"
            )
        if strategic_artifact.symbol != snapshot.symbol:
            raise StrategyContractError(
                "StrategicArtifact.symbol must match snapshot.symbol"
            )
        if strategic_artifact.decision_timestamp != snapshot.decision_timestamp:
            raise StrategyContractError(
                "StrategicArtifact.decision_timestamp must match snapshot.decision_timestamp"
            )
        if tuple(strategic_artifact.observation_ids) != tuple(snapshot.observation_ids):
            raise StrategyContractError(
                "StrategicArtifact.observation_ids must exactly match snapshot.observation_ids"
            )

        expected_analysis_ids = tuple(
            result.analysis_id for result in snapshot.results
        )
        if tuple(strategic_artifact.supporting_analysis_ids) != expected_analysis_ids:
            raise StrategyContractError(
                "StrategicArtifact.supporting_analysis_ids must match snapshot results"
            )

        if strategic_artifact.strategy_type != configuration.strategy_type:
            raise StrategyContractError(
                "StrategicArtifact.strategy_type does not match configuration"
            )
        if strategic_artifact.strategy_identity != configuration.strategy_identity:
            raise StrategyContractError(
                "StrategicArtifact.strategy_identity does not match configuration"
            )
        if strategic_artifact.strategy_version != configuration.strategy_version:
            raise StrategyContractError(
                "StrategicArtifact.strategy_version does not match configuration"
            )
        if strategic_artifact.effective_configuration != configuration.effective_configuration:
            raise StrategyContractError(
                "StrategicArtifact.effective_configuration does not match configuration"
            )
        if strategic_artifact.effective_parameters != configuration.effective_parameters:
            raise StrategyContractError(
                "StrategicArtifact.effective_parameters does not match configuration"
            )


class StrategyRuntimeValidationError(StrategyContractError):
    """Raised when an already-created strategy artifact cannot cross the runtime boundary."""


def _extract_decision_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "expected_direction",
        "expected_horizon",
        "invalidation_conditions",
    )
    for key in required:
        if key not in values:
            raise StrategyRuntimeValidationError(
                f"StrategicArtifact.values requires explicit '{key}'"
            )

    expected_direction = values["expected_direction"]
    expected_horizon = values["expected_horizon"]
    invalidation_conditions = values["invalidation_conditions"]

    _require_nonempty_string(expected_direction, "expected_direction")
    _require_nonempty_string(expected_horizon, "expected_horizon")

    if isinstance(invalidation_conditions, (str, bytes)) or not isinstance(
        invalidation_conditions,
        Sequence,
    ):
        raise StrategyRuntimeValidationError(
            "invalidation_conditions must be an explicit sequence of strings"
        )
    if any(not isinstance(item, str) for item in invalidation_conditions):
        raise StrategyRuntimeValidationError(
            "invalidation_conditions must contain only strings"
        )

    expires_at = values.get("expires_at")
    if expires_at is not None:
        _require_aware(expires_at, "expires_at")

    hypothesis_status = values.get("hypothesis_status", "UNRESOLVED")
    _require_nonempty_string(hypothesis_status, "hypothesis_status")

    signal_evidence = values.get("signal_evidence", {})
    if not isinstance(signal_evidence, Mapping):
        raise StrategyRuntimeValidationError(
            "signal_evidence must be a mapping"
        )

    return {
        "expected_direction": expected_direction,
        "expected_horizon": expected_horizon,
        "invalidation_conditions": tuple(invalidation_conditions),
        "expires_at": expires_at,
        "hypothesis_status": hypothesis_status,
        "signal_evidence": signal_evidence,
    }


def _derive_runtime_provenance(
    snapshot: AnalysisSnapshot,
    configuration: StrategyCalculationConfig,
) -> Provenance:
    sources = []
    for result in snapshot.results:
        sources.append(
            {
                "analysis_id": result.analysis_id,
                "source": result.provenance.source,
                "reference": result.provenance.reference,
                "metadata": dict(result.provenance.metadata),
            }
        )

    return Provenance(
        source="strategy_runtime",
        nature=ArtifactNature.DERIVED,
        reference=snapshot.snapshot_id,
        metadata={
            "snapshot_id": snapshot.snapshot_id,
            "strategy_type": configuration.strategy_type,
            "strategy_identity": configuration.strategy_identity,
            "strategy_version": configuration.strategy_version,
            "supporting_analysis_ids": tuple(
                result.analysis_id for result in snapshot.results
            ),
            "upstream_provenance": tuple(sources),
        },
    )


def _require_nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StrategyContractError(f"{field_name} must be a non-empty string")
    return value


def _require_aware(value: Any, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise StrategyContractError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyContractError(f"{field_name} must be timezone-aware")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "StrategyRuntime",
    "StrategyRuntimeResult",
    "StrategyRuntimeState",
    "StrategyRuntimeValidationError",
]
