"""Explicit availability evidence for the canonical acquisition boundary.

This module determines whether a normalized provider representation has enough
temporal evidence to populate MarketData.available_at. It does not implement
decision-time validation; EvidenceTimestamp remains the only look-ahead guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AvailabilityEvidenceError(ValueError):
    """Raised when availability evidence is missing, inconsistent, or ambiguous."""


class AvailabilityEvidenceKind(str, Enum):
    UNKNOWN = "UNKNOWN"
    RECEIVED_AND_AVAILABLE = "RECEIVED_AND_AVAILABLE"
    CONSUMER_HANDOFF = "CONSUMER_HANDOFF"
    EXPLICIT_EXTERNAL_EVIDENCE = "EXPLICIT_EXTERNAL_EVIDENCE"


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AvailabilityEvidenceError(f"{field_name} must be timezone-aware")


def _reference(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AvailabilityEvidenceError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class AvailabilityEvidence:
    """An explicit statement about the evidence supporting system availability."""

    kind: AvailabilityEvidenceKind
    available_at: datetime | None = None
    evidence_reference: str | None = None
    consumer_scope: str | None = None

    def __post_init__(self) -> None:
        try:
            kind = AvailabilityEvidenceKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise AvailabilityEvidenceError(
                f"kind is invalid: {self.kind!r}"
            ) from exc
        object.__setattr__(self, "kind", kind)

        if self.available_at is not None:
            _aware(self.available_at, "available_at")

        if self.evidence_reference is not None:
            _reference(self.evidence_reference, "evidence_reference")

        if self.consumer_scope is not None:
            _reference(self.consumer_scope, "consumer_scope")

        if kind is AvailabilityEvidenceKind.UNKNOWN:
            if self.available_at is not None:
                raise AvailabilityEvidenceError(
                    "UNKNOWN availability evidence cannot contain available_at"
                )
            return

        if self.available_at is None:
            raise AvailabilityEvidenceError(
                f"{kind.value} availability evidence requires available_at"
            )

        if kind is AvailabilityEvidenceKind.EXPLICIT_EXTERNAL_EVIDENCE:
            _reference(self.evidence_reference, "evidence_reference")
            _reference(self.consumer_scope, "consumer_scope")

    @classmethod
    def unknown(cls) -> "AvailabilityEvidence":
        return cls(kind=AvailabilityEvidenceKind.UNKNOWN)

    @classmethod
    def received_and_available(
        cls,
        received_at: datetime,
        *,
        evidence_reference: str | None = None,
        consumer_scope: str | None = None,
    ) -> "AvailabilityEvidence":
        _aware(received_at, "received_at")
        return cls(
            kind=AvailabilityEvidenceKind.RECEIVED_AND_AVAILABLE,
            available_at=received_at,
            evidence_reference=evidence_reference,
            consumer_scope=consumer_scope,
        )

    @classmethod
    def consumer_handoff(
        cls,
        *,
        received_at: datetime,
        available_at: datetime,
        evidence_reference: str | None = None,
        consumer_scope: str | None = None,
    ) -> "AvailabilityEvidence":
        _aware(received_at, "received_at")
        _aware(available_at, "available_at")
        if available_at < received_at:
            raise AvailabilityEvidenceError(
                "CONSUMER_HANDOFF availability cannot precede received_at"
            )
        return cls(
            kind=AvailabilityEvidenceKind.CONSUMER_HANDOFF,
            available_at=available_at,
            evidence_reference=evidence_reference,
            consumer_scope=consumer_scope,
        )

    @classmethod
    def explicit_external_evidence(
        cls,
        *,
        available_at: datetime,
        evidence_reference: str,
        consumer_scope: str,
    ) -> "AvailabilityEvidence":
        _aware(available_at, "available_at")
        return cls(
            kind=AvailabilityEvidenceKind.EXPLICIT_EXTERNAL_EVIDENCE,
            available_at=available_at,
            evidence_reference=evidence_reference,
            consumer_scope=consumer_scope,
        )

    def resolve(self, *, received_at: datetime) -> datetime | None:
        """Resolve only the explicitly declared evidence; never fall back to other timestamps."""
        _aware(received_at, "received_at")

        if self.kind is AvailabilityEvidenceKind.UNKNOWN:
            return None

        if self.kind is AvailabilityEvidenceKind.RECEIVED_AND_AVAILABLE:
            if self.available_at != received_at:
                raise AvailabilityEvidenceError(
                    "RECEIVED_AND_AVAILABLE requires available_at == received_at"
                )
            return received_at

        if self.kind is AvailabilityEvidenceKind.CONSUMER_HANDOFF:
            assert self.available_at is not None
            if self.available_at < received_at:
                raise AvailabilityEvidenceError(
                    "CONSUMER_HANDOFF availability cannot precede received_at"
                )
            return self.available_at

        if self.kind is AvailabilityEvidenceKind.EXPLICIT_EXTERNAL_EVIDENCE:
            assert self.available_at is not None
            return self.available_at

        raise AvailabilityEvidenceError(
            f"unsupported availability evidence kind: {self.kind!r}"
        )


def resolve_availability(
    evidence: AvailabilityEvidence,
    *,
    received_at: datetime,
) -> datetime | None:
    """Resolve explicit availability evidence without consulting observed_at or fallbacks."""
    if not isinstance(evidence, AvailabilityEvidence):
        raise TypeError("evidence must be AvailabilityEvidence")
    return evidence.resolve(received_at=received_at)


__all__ = [
    "AvailabilityEvidence",
    "AvailabilityEvidenceError",
    "AvailabilityEvidenceKind",
    "resolve_availability",
]
