"""Provider-neutral acquisition boundary for canonical market data.

This module stops at canonical MarketData. It does not connect to providers,
persist data, create MarketObservation instances, or infer provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .availability import AvailabilityEvidence, resolve_availability
from .market_data import (
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    MarketData,
    MarketDataError,
    CANDLE_DATA_TYPE,
    SourceIdentity,
)


class AcquisitionBoundaryError(ValueError):
    """Base error for the provider-neutral acquisition boundary."""


class ProviderPayloadError(AcquisitionBoundaryError):
    """Raised when a provider representation is malformed."""


class NormalizationError(AcquisitionBoundaryError):
    """Raised when a provider value cannot be normalized without inference."""


class InstrumentMappingError(AcquisitionBoundaryError):
    """Base class for explicit instrument mapping failures."""


class InstrumentMappingNotFound(InstrumentMappingError):
    """No explicit mapping matches the provider identity."""


class InstrumentMappingAmbiguous(InstrumentMappingError):
    """More than one explicit mapping matches the provider identity."""


class CanonicalValidationError(AcquisitionBoundaryError):
    """Canonical MarketData/Candle validation rejected normalized input."""


def _nonempty_text(value: Any, field_name: str, *, error_type: type[Exception] = ProviderPayloadError) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field_name} must be a non-empty string")
    return value


def _mapping_copy(value: Mapping[str, Any] | None, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ProviderPayloadError(f"{field_name} must be a mapping")
    return MappingProxyType(dict(value))


def _optional_enum(value: Any, enum_type: type, field_name: str) -> Any:
    if value is None:
        return enum_type.UNKNOWN
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise NormalizationError(f"{field_name} is invalid: {value!r}") from exc


def _normalize_timestamp(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise NormalizationError(f"{field_name} must be an ISO-8601 timestamp") from exc
    else:
        raise NormalizationError(f"{field_name} must be a datetime or ISO-8601 string")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NormalizationError(f"{field_name} must be timezone-aware")
    return parsed


def _normalize_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise NormalizationError(f"{field_name} must not be supplied as float or bool")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise NormalizationError(f"{field_name} is not a valid exact decimal") from exc
    if not result.is_finite():
        raise NormalizationError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True)
class ProviderRecord:
    """Raw provider representation before canonical-domain normalization."""

    provider_symbol: str
    provider: str
    provider_market: str | None
    provider_venue: str | None
    observed_at: Any
    available_at: Any | None
    candle_start: Any
    candle_end: Any
    timeframe: str
    open: Any
    high: Any
    low: Any
    close: Any
    volume: Any
    quality: Any = None
    candle_completeness: Any = None
    market_data_completeness: Any = None
    candle_state: Any = None
    finality: Any = None
    source_sequence: str | int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty_text(self.provider_symbol, "provider_symbol")
        _nonempty_text(self.provider, "provider")
        if self.provider_market is not None:
            _nonempty_text(self.provider_market, "provider_market")
        if self.provider_venue is not None:
            _nonempty_text(self.provider_venue, "provider_venue")
        _nonempty_text(self.timeframe, "timeframe")
        if self.source_sequence is not None and (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, (str, int))
            or (isinstance(self.source_sequence, str) and not self.source_sequence.strip())
        ):
            raise ProviderPayloadError("source_sequence must be a non-empty string, integer, or None")
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))


@dataclass(frozen=True)
class ParseResult:
    """Successful interpretation of a controlled provider payload."""

    record: ProviderRecord


@dataclass(frozen=True)
class NormalizedMarketDataInput:
    """Canonical-ready values; still not a MarketData instance."""

    instrument: InstrumentIdentity
    source: SourceIdentity
    observed_at: datetime
    received_at: datetime
    available_at: datetime | None
    candle_start: datetime
    candle_end: datetime
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quality: DataQuality
    candle_completeness: DataCompleteness
    market_data_completeness: DataCompleteness
    candle_state: CandleState
    finality: CandleFinality
    source_sequence: str | int | None


@dataclass(frozen=True)
class NormalizationResult:
    """Successful non-canonical normalization result."""

    value: NormalizedMarketDataInput


@dataclass(frozen=True)
class InstrumentMappingRule:
    """Exact provider identity -> canonical InstrumentIdentity mapping."""

    provider: str
    provider_symbol: str
    provider_market: str | None
    provider_venue: str | None
    instrument: InstrumentIdentity

    def __post_init__(self) -> None:
        _nonempty_text(self.provider, "mapping.provider")
        _nonempty_text(self.provider_symbol, "mapping.provider_symbol")
        if self.provider_market is not None:
            _nonempty_text(self.provider_market, "mapping.provider_market")
        if self.provider_venue is not None:
            _nonempty_text(self.provider_venue, "mapping.provider_venue")
        if not isinstance(self.instrument, InstrumentIdentity):
            raise ProviderPayloadError("mapping.instrument must be InstrumentIdentity")


class InstrumentMapper:
    """Resolves only exact, explicitly configured mappings."""

    def __init__(self, rules: Sequence[InstrumentMappingRule]) -> None:
        self._rules = tuple(rules)

    def resolve(self, record: ProviderRecord) -> InstrumentIdentity:
        matches = tuple(
            rule
            for rule in self._rules
            if (
                rule.provider,
                rule.provider_symbol,
                rule.provider_market,
                rule.provider_venue,
            )
            == (
                record.provider,
                record.provider_symbol,
                record.provider_market,
                record.provider_venue,
            )
        )
        if not matches:
            raise InstrumentMappingNotFound(
                "no explicit instrument mapping matches provider identity"
            )
        if len(matches) > 1:
            raise InstrumentMappingAmbiguous(
                "more than one explicit instrument mapping matches provider identity"
            )
        return matches[0].instrument


def build_source_identity(
    *,
    source_id: str,
    provider: str,
    venue: str | None,
) -> SourceIdentity:
    """Require an explicit source_id; provider is never used as a fallback."""
    return SourceIdentity(source_id=source_id, provider=provider, venue=venue)


def parse_provider_payload(payload: Mapping[str, Any]) -> ParseResult:
    """Interpret a controlled provider payload without canonicalizing it."""
    if not isinstance(payload, Mapping):
        raise ProviderPayloadError("provider payload must be a mapping")

    required = (
        "provider_symbol",
        "provider",
        "observed_at",
        "candle_start",
        "candle_end",
        "timeframe",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ProviderPayloadError(f"provider payload missing required fields: {', '.join(missing)}")

    record = ProviderRecord(
        provider_symbol=payload["provider_symbol"],
        provider=payload["provider"],
        provider_market=payload.get("provider_market"),
        provider_venue=payload.get("provider_venue"),
        observed_at=payload["observed_at"],
        available_at=payload.get("available_at"),
        candle_start=payload["candle_start"],
        candle_end=payload["candle_end"],
        timeframe=payload["timeframe"],
        open=payload["open"],
        high=payload["high"],
        low=payload["low"],
        close=payload["close"],
        volume=payload["volume"],
        quality=payload.get("quality"),
        candle_completeness=payload.get("candle_completeness"),
        market_data_completeness=payload.get("market_data_completeness"),
        candle_state=payload.get("candle_state"),
        finality=payload.get("finality"),
        source_sequence=payload.get("source_sequence"),
        metadata=payload.get("metadata"),
    )
    return ParseResult(record=record)


def normalize_provider_record(
    record: ProviderRecord,
    *,
    received_at: datetime,
    source_id: str,
    instrument_mapper: InstrumentMapper,
) -> NormalizationResult:
    """Normalize without inventing timestamps, mappings, source IDs, or quality."""
    if not isinstance(record, ProviderRecord):
        raise NormalizationError("record must be ProviderRecord")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise NormalizationError("received_at must be timezone-aware")

    instrument = instrument_mapper.resolve(record)
    try:
        source = build_source_identity(
            source_id=source_id,
            provider=record.provider,
            venue=record.provider_venue,
        )
    except (TypeError, ValueError) as exc:
        raise NormalizationError(str(exc)) from exc

    observed_at = _normalize_timestamp(record.observed_at, "observed_at")
    candle_start = _normalize_timestamp(record.candle_start, "candle_start")
    candle_end = _normalize_timestamp(record.candle_end, "candle_end")
    available_at = (
        None
        if record.available_at is None
        else _normalize_timestamp(record.available_at, "available_at")
    )

    value = NormalizedMarketDataInput(
        instrument=instrument,
        source=source,
        observed_at=observed_at,
        received_at=received_at,
        available_at=available_at,
        candle_start=candle_start,
        candle_end=candle_end,
        timeframe=_nonempty_text(record.timeframe, "timeframe"),
        open=_normalize_decimal(record.open, "open"),
        high=_normalize_decimal(record.high, "high"),
        low=_normalize_decimal(record.low, "low"),
        close=_normalize_decimal(record.close, "close"),
        volume=_normalize_decimal(record.volume, "volume"),
        quality=_optional_enum(record.quality, DataQuality, "quality"),
        candle_completeness=_optional_enum(
            record.candle_completeness, DataCompleteness, "candle_completeness"
        ),
        market_data_completeness=_optional_enum(
            record.market_data_completeness,
            DataCompleteness,
            "market_data_completeness",
        ),
        candle_state=_optional_enum(record.candle_state, CandleState, "candle_state"),
        finality=_optional_enum(record.finality, CandleFinality, "finality"),
        source_sequence=record.source_sequence,
    )
    return NormalizationResult(value=value)


def apply_availability_evidence(
    normalized: NormalizedMarketDataInput,
    evidence: AvailabilityEvidence,
) -> NormalizationResult:
    """Apply an explicit availability decision before crossing the canonical boundary."""
    if not isinstance(normalized, NormalizedMarketDataInput):
        raise TypeError("normalized must be NormalizedMarketDataInput")
    if not isinstance(evidence, AvailabilityEvidence):
        raise TypeError("evidence must be AvailabilityEvidence")
    available_at = resolve_availability(
        evidence,
        received_at=normalized.received_at,
    )
    return NormalizationResult(
        value=replace(normalized, available_at=available_at)
    )


def canonicalize_market_data(value: NormalizedMarketDataInput) -> MarketData:
    """Cross the canonical boundary exactly once; canonical module owns validation."""
    if not isinstance(value, NormalizedMarketDataInput):
        raise CanonicalValidationError("value must be NormalizedMarketDataInput")
    try:
        candle = Candle(
            start=value.candle_start,
            end=value.candle_end,
            timeframe=value.timeframe,
            open=value.open,
            high=value.high,
            low=value.low,
            close=value.close,
            volume=value.volume,
            candle_state=value.candle_state,
            completeness=value.candle_completeness,
            finality=value.finality,
        )
        return MarketData(
            instrument=value.instrument,
            source=value.source,
            data_type=CANDLE_DATA_TYPE,
            observed_at=value.observed_at,
            received_at=value.received_at,
            available_at=value.available_at,
            payload=candle,
            quality=value.quality,
            completeness=value.market_data_completeness,
            source_sequence=value.source_sequence,
        )
    except MarketDataError as exc:
        raise CanonicalValidationError(
            "canonical validation rejected provider data: " + str(exc)
        ) from exc


def provider_payload_to_market_data_with_availability(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source_id: str,
    instrument_mapper: InstrumentMapper,
    availability_evidence: AvailabilityEvidence,
) -> MarketData:
    """Run parsing, normalization, explicit availability, and canonicalization."""
    parsed = parse_provider_payload(payload)
    normalized = normalize_provider_record(
        parsed.record,
        received_at=received_at,
        source_id=source_id,
        instrument_mapper=instrument_mapper,
    )
    available = apply_availability_evidence(
        normalized.value,
        availability_evidence,
    )
    return canonicalize_market_data(available.value)


def provider_payload_to_market_data(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source_id: str,
    instrument_mapper: InstrumentMapper,
) -> MarketData:
    """Run the complete controlled provider-neutral boundary."""
    parsed = parse_provider_payload(payload)
    normalized = normalize_provider_record(
        parsed.record,
        received_at=received_at,
        source_id=source_id,
        instrument_mapper=instrument_mapper,
    )
    return canonicalize_market_data(normalized.value)
