"""Provider-neutral immutable container for coherent analysis results.

Canonical Analysis Snapshot v1.0 groups AnalysisResult artifacts that share the
same analytical decision and exact observation base. It validates coherence
without recalculating, mutating, or discovering any indicator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence
from uuid import uuid4

from .analysis_contracts import AnalysisResult, ArtifactNature, ContractError


class AnalysisSnapshotError(ContractError):
    """Raised when AnalysisSnapshot coherence rules are violated."""


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise AnalysisSnapshotError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AnalysisSnapshotError(f"{field_name} must be timezone-aware")


def _require_nonempty_string(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisSnapshotError(f"{field_name} must be a non-empty string")
    return value


def _analysis_key(result: AnalysisResult) -> tuple[str, str, str]:
    metadata = result.provenance.metadata
    identity = metadata.get("algorithm_identity")
    version = metadata.get("algorithm_version")
    analysis_type = metadata.get("analysis_type")

    _require_nonempty_string(analysis_type, "provenance.metadata['analysis_type']")
    _require_nonempty_string(
        identity,
        "provenance.metadata['algorithm_identity']",
    )
    _require_nonempty_string(
        version,
        "provenance.metadata['algorithm_version']",
    )

    if analysis_type != result.analysis_type:
        raise AnalysisSnapshotError(
            "provenance.metadata['analysis_type'] must match result.analysis_type"
        )

    return (analysis_type, identity, version)


def _validate_results(
    results: Sequence[AnalysisResult],
) -> tuple[AnalysisResult, ...]:
    if isinstance(results, (str, bytes)) or not isinstance(results, Sequence):
        raise AnalysisSnapshotError(
            "results must be a Sequence[AnalysisResult]"
        )
    if not results:
        raise AnalysisSnapshotError(
            "AnalysisSnapshot requires at least one result"
        )

    normalized = tuple(results)
    for result in normalized:
        if not isinstance(result, AnalysisResult):
            raise AnalysisSnapshotError(
                "results must contain only AnalysisResult instances"
            )

    first = normalized[0]
    symbol = first.symbol
    decision_timestamp = first.decision_timestamp
    observation_ids = tuple(first.observation_ids)

    _require_nonempty_string(symbol, "symbol")
    _require_aware(decision_timestamp, "decision_timestamp")
    if not observation_ids or any(
        not isinstance(item, str) or not item.strip() for item in observation_ids
    ):
        raise AnalysisSnapshotError(
            "observation_ids must be a non-empty sequence of non-empty strings"
        )

    seen_keys: set[tuple[str, str, str]] = set()

    for result in normalized:
        if result.symbol != symbol:
            raise AnalysisSnapshotError(
                "all AnalysisResult symbols must match snapshot.symbol"
            )
        if result.decision_timestamp != decision_timestamp:
            raise AnalysisSnapshotError(
                "all AnalysisResult decision_timestamp values must match"
            )
        if tuple(result.observation_ids) != observation_ids:
            raise AnalysisSnapshotError(
                "all AnalysisResult observation_ids must exactly match the snapshot"
            )
        _require_aware(result.calculated_at, "result.calculated_at")
        if result.calculated_at < decision_timestamp:
            raise AnalysisSnapshotError(
                "AnalysisResult calculated_at cannot precede snapshot.decision_timestamp"
            )
        if result.provenance.nature is not ArtifactNature.DERIVED:
            raise AnalysisSnapshotError(
                "AnalysisSnapshot accepts only DERIVED AnalysisResult artifacts"
            )

        key = _analysis_key(result)
        if key in seen_keys:
            raise AnalysisSnapshotError(
                "duplicate analytical operation: "
                + " / ".join(key)
            )
        seen_keys.add(key)

    return tuple(
        sorted(
            normalized,
            key=_analysis_key,
        )
    )


@dataclass(frozen=True)
class AnalysisSnapshot:
    """Immutable canonical grouping of coherent AnalysisResult artifacts."""

    symbol: str
    decision_timestamp: datetime
    observation_ids: tuple[str, ...]
    results: tuple[AnalysisResult, ...]
    snapshot_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _require_nonempty_string(self.symbol, "symbol")
        _require_aware(self.decision_timestamp, "decision_timestamp")
        _require_nonempty_string(self.snapshot_id, "snapshot_id")

        observation_ids = tuple(self.observation_ids)
        if not observation_ids or any(
            not isinstance(item, str) or not item.strip()
            for item in observation_ids
        ):
            raise AnalysisSnapshotError(
                "observation_ids must be a non-empty sequence of non-empty strings"
            )

        normalized_results = _validate_results(self.results)
        if any(result.symbol != self.symbol for result in normalized_results):
            raise AnalysisSnapshotError(
                "all AnalysisResult symbols must match snapshot.symbol"
            )
        if any(
            result.decision_timestamp != self.decision_timestamp
            for result in normalized_results
        ):
            raise AnalysisSnapshotError(
                "all AnalysisResult decision_timestamp values must match"
            )
        if any(
            tuple(result.observation_ids) != observation_ids
            for result in normalized_results
        ):
            raise AnalysisSnapshotError(
                "all AnalysisResult observation_ids must exactly match the snapshot"
            )

        object.__setattr__(self, "observation_ids", observation_ids)
        object.__setattr__(self, "results", normalized_results)

    @classmethod
    def from_results(
        cls,
        results: Sequence[AnalysisResult],
        *,
        snapshot_id: str | None = None,
    ) -> "AnalysisSnapshot":
        normalized = _validate_results(results)
        first = normalized[0]
        return cls(
            symbol=first.symbol,
            decision_timestamp=first.decision_timestamp,
            observation_ids=tuple(first.observation_ids),
            results=normalized,
            snapshot_id=uuid4().hex if snapshot_id is None else snapshot_id,
        )


__all__ = [
    "AnalysisSnapshot",
    "AnalysisSnapshotError",
]
