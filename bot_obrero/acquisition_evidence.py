"""Provider-neutral operational evidence for acquisition operations.

The canonical MarketData contract describes market data itself. This module
describes how an acquisition operation was requested, executed, retried, and
completed without embedding operational metadata into MarketData.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class AcquisitionEvidenceError(ValueError):
    """Base validation error for acquisition operation evidence."""


class AcquisitionMode(str, Enum):
    LIVE = "LIVE"
    HISTORICAL = "HISTORICAL"
    REPLAY = "REPLAY"


class AcquisitionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


def _nonempty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionEvidenceError(f"{field_name} must be a non-empty string")
    return value


def _aware_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise AcquisitionEvidenceError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AcquisitionEvidenceError(f"{field_name} must be timezone-aware")
    return value


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcquisitionEvidenceError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class AcquisitionErrorEvidence:
    """Structured final failure information without storing network bodies."""

    category: str
    message: str
    provider_error_code: int | str | None = None
    http_status: int | None = None
    outcome_unknown: bool | None = None

    def __post_init__(self) -> None:
        _nonempty_text(self.category, "error.category")
        _nonempty_text(self.message, "error.message")
        if self.http_status is not None:
            if isinstance(self.http_status, bool) or not isinstance(self.http_status, int):
                raise AcquisitionEvidenceError("error.http_status must be an integer or None")
            if self.http_status < 100 or self.http_status > 599:
                raise AcquisitionEvidenceError("error.http_status must be between 100 and 599")
        if self.provider_error_code is not None and isinstance(self.provider_error_code, bool):
            raise AcquisitionEvidenceError("error.provider_error_code must not be bool")
        if self.outcome_unknown is not None and not isinstance(self.outcome_unknown, bool):
            raise AcquisitionEvidenceError("error.outcome_unknown must be bool or None")


def error_evidence_from_exception(
    exc: Exception,
    *,
    category: str | None = None,
) -> AcquisitionErrorEvidence:
    """Extract only bounded operational error metadata from an exception."""

    provider_error_code = getattr(exc, "code", None)
    http_status = getattr(exc, "status_code", None)
    if http_status is None:
        http_status = getattr(exc, "http_status", None)
    outcome_unknown = getattr(exc, "outcome_unknown", None)

    return AcquisitionErrorEvidence(
        category=category or type(exc).__name__,
        message=str(exc) or type(exc).__name__,
        provider_error_code=provider_error_code,
        http_status=http_status,
        outcome_unknown=outcome_unknown,
    )


@dataclass(frozen=True)
class AcquisitionOperationEvidence:
    """Immutable evidence describing one logical acquisition operation."""

    operation_id: str
    mode: AcquisitionMode
    provider: str
    source_id: str
    venue: str
    market: str
    symbol: str
    interval: str | None
    requested_start: datetime | None
    requested_end: datetime | None
    limit: int | None
    request_count: int
    retry_count: int
    page_count: int
    status: AcquisitionStatus
    started_at: datetime
    finished_at: datetime
    last_cursor: int | None = None
    next_cursor: int | None = None
    error: AcquisitionErrorEvidence | None = None

    def __post_init__(self) -> None:
        _nonempty_text(self.operation_id, "operation_id")
        if not isinstance(self.mode, AcquisitionMode):
            raise AcquisitionEvidenceError("mode must be AcquisitionMode")
        _nonempty_text(self.provider, "provider")
        _nonempty_text(self.source_id, "source_id")
        _nonempty_text(self.venue, "venue")
        _nonempty_text(self.market, "market")
        _nonempty_text(self.symbol, "symbol")

        if self.interval is not None:
            _nonempty_text(self.interval, "interval")
        if self.requested_start is not None:
            _aware_datetime(self.requested_start, "requested_start")
        if self.requested_end is not None:
            _aware_datetime(self.requested_end, "requested_end")
        if self.requested_start is not None and self.requested_end is not None:
            if self.requested_start > self.requested_end:
                raise AcquisitionEvidenceError("requested_start must be <= requested_end")

        if self.limit is not None:
            if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
                raise AcquisitionEvidenceError("limit must be a positive integer or None")

        _nonnegative_int(self.request_count, "request_count")
        _nonnegative_int(self.retry_count, "retry_count")
        _nonnegative_int(self.page_count, "page_count")
        if self.retry_count > self.request_count:
            raise AcquisitionEvidenceError("retry_count cannot exceed request_count")
        if self.page_count > self.request_count:
            raise AcquisitionEvidenceError("page_count cannot exceed request_count")

        if not isinstance(self.status, AcquisitionStatus):
            raise AcquisitionEvidenceError("status must be AcquisitionStatus")
        _aware_datetime(self.started_at, "started_at")
        _aware_datetime(self.finished_at, "finished_at")
        if self.finished_at < self.started_at:
            raise AcquisitionEvidenceError("finished_at must be >= started_at")

        for field_name, value in (("last_cursor", self.last_cursor), ("next_cursor", self.next_cursor)):
            if value is not None:
                _nonnegative_int(value, field_name)

        if self.status is AcquisitionStatus.SUCCESS and self.error is not None:
            raise AcquisitionEvidenceError("SUCCESS evidence cannot contain an error")
        if self.status is AcquisitionStatus.FAILED and self.error is None:
            raise AcquisitionEvidenceError("FAILED evidence requires an error")
        if self.error is not None and not isinstance(self.error, AcquisitionErrorEvidence):
            raise AcquisitionEvidenceError("error must be AcquisitionErrorEvidence or None")

    @property
    def duration(self) -> timedelta:
        """Derive operation duration from the authoritative timestamps."""
        return self.finished_at - self.started_at


class AcquisitionOperationRecorder:
    """Short-lived mutable recorder used only while one operation is executing."""

    def __init__(
        self,
        *,
        mode: AcquisitionMode,
        provider: str,
        source_id: str,
        venue: str,
        market: str,
        symbol: str,
        interval: str | None,
        requested_start: datetime | None,
        requested_end: datetime | None,
        limit: int | None,
        started_at: datetime,
        operation_id: str | None = None,
    ) -> None:
        self.operation_id = operation_id or uuid4().hex
        self.mode = mode
        self.provider = provider
        self.source_id = source_id
        self.venue = venue
        self.market = market
        self.symbol = symbol
        self.interval = interval
        self.requested_start = requested_start
        self.requested_end = requested_end
        self.limit = limit
        self.started_at = started_at
        self.request_count = 0
        self.retry_count = 0
        self.page_count = 0
        self.last_cursor: int | None = None
        self.next_cursor: int | None = None
        self._current_logical_attempts = 0

        _aware_datetime(self.started_at, "started_at")

    def begin_logical_request(self) -> None:
        if self._current_logical_attempts != 0:
            raise AcquisitionEvidenceError("a logical request is already active")
        self._current_logical_attempts = 0

    def record_physical_request(self) -> None:
        self._current_logical_attempts += 1
        self.request_count += 1
        if self._current_logical_attempts > 1:
            self.retry_count += 1

    def end_logical_request(self) -> None:
        if self._current_logical_attempts < 0:
            raise AcquisitionEvidenceError("logical request state is invalid")
        self._current_logical_attempts = 0

    def record_successful_page(
        self,
        *,
        cursor: int | None = None,
        next_cursor: int | None = None,
    ) -> None:
        self.page_count += 1
        if cursor is not None:
            self.last_cursor = cursor
        if next_cursor is not None:
            self.next_cursor = next_cursor

    def finish(
        self,
        *,
        status: AcquisitionStatus,
        finished_at: datetime,
        error: AcquisitionErrorEvidence | None = None,
    ) -> AcquisitionOperationEvidence:
        if self._current_logical_attempts != 0:
            raise AcquisitionEvidenceError("cannot finalize evidence while a logical request is active")
        return AcquisitionOperationEvidence(
            operation_id=self.operation_id,
            mode=self.mode,
            provider=self.provider,
            source_id=self.source_id,
            venue=self.venue,
            market=self.market,
            symbol=self.symbol,
            interval=self.interval,
            requested_start=self.requested_start,
            requested_end=self.requested_end,
            limit=self.limit,
            request_count=self.request_count,
            retry_count=self.retry_count,
            page_count=self.page_count,
            status=status,
            started_at=self.started_at,
            finished_at=_aware_datetime(finished_at, "finished_at"),
            last_cursor=self.last_cursor,
            next_cursor=self.next_cursor,
            error=error,
        )


def new_operation_recorder(**kwargs: Any) -> AcquisitionOperationRecorder:
    """Create a recorder with a fresh operation identity."""
    return AcquisitionOperationRecorder(**kwargs)


__all__ = [
    "AcquisitionErrorEvidence",
    "AcquisitionEvidenceError",
    "AcquisitionMode",
    "AcquisitionOperationEvidence",
    "AcquisitionOperationRecorder",
    "AcquisitionStatus",
    "error_evidence_from_exception",
    "new_operation_recorder",
]
