"""Binance Spot public REST market-data adapter.

This module is intentionally limited to one unauthenticated Spot REST endpoint:
GET /api/v3/klines.

The adapter translates Binance's provider representation into the provider-neutral
FASE 1.10 acquisition boundary and does not implement trading, private endpoints,
WebSockets, persistence, retries, or background processing.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .acquisition import (
    InstrumentMapper,
    NormalizationError,
    ProviderPayloadError,
    canonicalize_market_data,
    normalize_provider_record,
    parse_provider_payload,
)
from .availability import AvailabilityEvidence, resolve_availability
from .binance_intervals import validate_binance_spot_interval
from .market_data import MarketData
from .binance_resilience import BinanceRequestBudget, BinanceRetryPolicy


class BinanceAdapterError(RuntimeError):
    """Base error for the Binance Spot adapter."""


class BinanceTransportError(BinanceAdapterError):
    """Network or timeout failure before a usable HTTP response was received."""


class BinanceHTTPError(BinanceAdapterError):
    """HTTP-level failure returned by Binance or an intermediary."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.outcome_unknown = outcome_unknown


class BinanceRateLimitError(BinanceHTTPError):
    """429/418 response requiring the caller to honor Binance backoff guidance."""


class BinanceAPIError(BinanceAdapterError):
    """A Binance API error object containing provider error code/message."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        http_status: int,
    ) -> None:
        super().__init__(f"Binance API error {code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


class BinancePayloadError(BinanceAdapterError):
    """The HTTP response did not match Binance's documented kline shape."""


class InvalidHistoricalRange(BinanceAdapterError):
    """The requested historical time range is invalid."""


class PaginationStalled(BinanceAdapterError):
    """Historical pagination failed to advance beyond the current cursor."""


class UnexpectedPageOrder(BinanceAdapterError):
    """A historical page violated chronological order."""


class DuplicateKline(BinanceAdapterError):
    """The historical acquisition returned the same logical kline more than once."""


class HistoricalGapDetected(BinanceAdapterError):
    """A gap was detected between consecutive returned klines."""


class MaximumRequestsExceeded(BinanceAdapterError):
    """The historical acquisition would exceed its configured request bound."""


@dataclass(frozen=True)
class BinanceSpotRestConfig:
    """Explicit, non-sensitive configuration for the public Binance adapter."""

    base_url: str = "https://data-api.binance.vision"
    endpoint: str = "/api/v3/klines"
    timeout_seconds: float = 10.0
    source_id: str = "binance-spot-rest"
    provider: str = "binance"
    market: str = "SPOT"
    venue: str = "BINANCE"

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("base_url must be an HTTPS origin")
        if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a path, query, or fragment")
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith("/"):
            raise ValueError("endpoint must be an absolute path")
        if "?" in self.endpoint or "#" in self.endpoint:
            raise ValueError("endpoint must not contain query parameters")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        for name, value in (
            ("source_id", self.source_id),
            ("provider", self.provider),
            ("market", self.market),
            ("venue", self.venue),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


HTTPGetter = Callable[
    [str, Mapping[str, str], float],
    tuple[int, Mapping[str, str], bytes],
]


def _default_http_get(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(
        url,
        headers=dict(headers),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        return int(exc.code), response_headers, body
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise BinanceTransportError(f"Binance HTTP transport failure: {exc}") from exc


def _decode_json(body: bytes, *, http_status: int) -> Any:
    try:
        text = body.decode("utf-8")
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BinancePayloadError(
            f"Binance response body is not valid UTF-8 JSON (HTTP {http_status})"
        ) from exc


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _ms_to_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BinancePayloadError(f"{field_name} must be an integer millisecond timestamp")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise BinancePayloadError(f"{field_name} is outside supported datetime range") from exc


def _decimal_string(value: Any, field_name: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise BinancePayloadError(f"{field_name} must be a non-empty decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise BinancePayloadError(f"{field_name} is not a valid decimal string") from exc
    if not result.is_finite():
        raise BinancePayloadError(f"{field_name} must be finite")
    return result


def _datetime_to_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    value_utc = value.astimezone(timezone.utc)
    return (
        (value_utc.toordinal() - datetime(1970, 1, 1, tzinfo=timezone.utc).toordinal())
        * 86_400_000
        + value_utc.hour * 3_600_000
        + value_utc.minute * 60_000
        + value_utc.second * 1_000
        + value_utc.microsecond // 1_000
    )


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BinancePayloadError(f"{field_name} must be a non-negative integer")
    return value


Clock = Callable[[], datetime]


class BinanceSpotRestAdapter:
    """One-shot public Binance Spot kline adapter.

    Availability is conservative by default. A consumer handoff is only
    recorded when an explicit, injected handoff clock is configured. The
    configured clock records the adapter's pre-return availability boundary:
    the instant after canonical validation and immediately before the adapter
    returns the completed MarketData result to its caller. It is not an
    observation of the later instant at which the caller receives the return
    value.
    """

    CONSUMER_SCOPE = "BinanceSpotRestAdapter.fetch_market_data pre-return availability boundary"
    AVAILABILITY_EVIDENCE_REFERENCE = "binance-spot-rest.fetch_market_data:pre-return-boundary"


    def __init__(
        self,
        *,
        instrument_mapper: InstrumentMapper,
        config: BinanceSpotRestConfig | None = None,
        http_get: HTTPGetter = _default_http_get,
        clock: Clock | None = None,
        consumer_handoff_clock: Clock | None = None,
    ) -> None:
        self.config = config or BinanceSpotRestConfig()
        self.instrument_mapper = instrument_mapper
        self._http_get = http_get
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._consumer_handoff_clock = consumer_handoff_clock

    @property
    def endpoint_url(self) -> str:
        return self.config.base_url.rstrip("/") + self.config.endpoint

    def build_query(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> dict[str, str]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        validated_interval = validate_binance_spot_interval(interval)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        for name, value in (("startTime", start_time), ("endTime", end_time)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer millisecond timestamp")
        query = {
            "symbol": symbol,
            "interval": validated_interval.value,
            "limit": str(limit),
        }
        if start_time is not None:
            query["startTime"] = str(start_time)
        if end_time is not None:
            query["endTime"] = str(end_time)
        return query

    def _request(self, query: Mapping[str, str]) -> Any:
        encoded = urllib.parse.urlencode(query)
        url = f"{self.endpoint_url}?{encoded}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "bot-cripto-binance-spot-adapter/1.0",
        }
        context = (
            f"endpoint={self.endpoint_url} "
            f"symbol={query.get('symbol')} interval={query.get('interval')}"
        )
        try:
            http_status, response_headers, body = self._http_get(
                url,
                headers,
                self.config.timeout_seconds,
            )
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise BinanceTransportError(
                f"Binance HTTP transport failure: {exc}; {context}"
            ) from exc

        if http_status < 200 or http_status >= 300:
            try:
                payload = _decode_json(body, http_status=http_status)
            except BinancePayloadError:
                payload = None

            if isinstance(payload, Mapping) and "code" in payload and "msg" in payload:
                code = payload.get("code")
                message = payload.get("msg")
                if isinstance(code, int) and isinstance(message, str):
                    if http_status in (429, 418):
                        raise BinanceRateLimitError(
                            http_status,
                            f"Binance rate limit response: {code}: {message}; {context}",
                            retry_after_seconds=_retry_after_seconds(response_headers),
                            outcome_unknown=False,
                        )
                    raise BinanceAPIError(
                        code,
                        f"{message}; {context}",
                        http_status=http_status,
                    )

            retry_after = _retry_after_seconds(response_headers)
            if http_status in (429, 418):
                raise BinanceRateLimitError(
                    http_status,
                    f"Binance HTTP rate limit failure: {http_status}; {context}",
                    retry_after_seconds=retry_after,
                )
            raise BinanceHTTPError(
                http_status,
                f"Binance HTTP failure: {http_status}; {context}",
                retry_after_seconds=retry_after,
                outcome_unknown=500 <= http_status <= 599,
            )

        payload = _decode_json(body, http_status=http_status)
        if isinstance(payload, Mapping) and "code" in payload and "msg" in payload:
            code = payload.get("code")
            message = payload.get("msg")
            if isinstance(code, int) and isinstance(message, str):
                raise BinanceAPIError(
                    code,
                    f"{message}; {context}",
                    http_status=http_status,
                )

        return payload

    def _provider_records(
        self,
        payload: Any,
        *,
        symbol: str,
        interval: str,
        received_at: datetime,
        allow_empty: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            raise BinancePayloadError("Binance kline response root must be a JSON array")
        if not payload:
            if allow_empty:
                return []
            raise BinancePayloadError("Binance returned an empty kline response")

        records: list[dict[str, Any]] = []
        for index, row in enumerate(payload):
            prefix = f"kline[{index}]"
            if not isinstance(row, list) or len(row) != 12:
                raise BinancePayloadError(
                    f"{prefix} must contain exactly 12 elements"
                )

            open_time_ms = _nonnegative_int(row[0], f"{prefix}[0]")
            close_time_ms = _nonnegative_int(row[6], f"{prefix}[6]")
            if close_time_ms < open_time_ms:
                raise BinancePayloadError(
                    f"{prefix} close time must be >= open time"
                )

            open_price = _decimal_string(row[1], f"{prefix}[1]")
            high_price = _decimal_string(row[2], f"{prefix}[2]")
            low_price = _decimal_string(row[3], f"{prefix}[3]")
            close_price = _decimal_string(row[4], f"{prefix}[4]")
            volume = _decimal_string(row[5], f"{prefix}[5]")
            quote_volume = _decimal_string(row[7], f"{prefix}[7]")
            trade_count = _nonnegative_int(row[8], f"{prefix}[8]")
            taker_buy_base_volume = _decimal_string(row[9], f"{prefix}[9]")
            taker_buy_quote_volume = _decimal_string(row[10], f"{prefix}[10]")
            if not isinstance(row[11], str):
                raise BinancePayloadError(f"{prefix}[11] must be a string")

            candle_start = _ms_to_datetime(open_time_ms, f"{prefix}[0]")
            candle_end = _ms_to_datetime(close_time_ms, f"{prefix}[6]")
            if candle_end <= candle_start:
                raise BinancePayloadError(
                    f"{prefix} must span a positive time interval"
                )

            is_closed = received_at >= candle_end
            records.append(
                {
                    "provider_symbol": symbol,
                    "provider": self.config.provider,
                    "provider_market": self.config.market,
                    "provider_venue": self.config.venue,
                    "observed_at": candle_start,
                    "available_at": None,
                    "candle_start": candle_start,
                    "candle_end": candle_end,
                    "timeframe": interval,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "quality": "VALID",
                    "candle_completeness": "COMPLETE" if is_closed else "PARTIAL",
                    "market_data_completeness": "UNKNOWN",
                    "candle_state": "CLOSED" if is_closed else "OPEN",
                    "finality": "UNKNOWN",
                    "source_sequence": None,
                    "metadata": {
                        "binance_quote_asset_volume": str(quote_volume),
                        "binance_number_of_trades": trade_count,
                        "binance_taker_buy_base_asset_volume": str(taker_buy_base_volume),
                        "binance_taker_buy_quote_asset_volume": str(taker_buy_quote_volume),
                    },
                }
            )
        return records

    def fetch_market_data(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
        allow_empty: bool = False,
    ) -> list[MarketData]:
        """Perform one public REST call and return canonical MarketData objects."""
        query = self.build_query(
            symbol=symbol,
            interval=interval,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
        payload = self._request(query)
        received_at = self._clock()
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        provider_records = self._provider_records(
            payload,
            symbol=symbol,
            interval=interval,
            received_at=received_at,
            allow_empty=allow_empty,
        )

        canonical_items: list[MarketData] = []
        for raw in provider_records:
            parsed = parse_provider_payload(raw).record
            try:
                normalized = normalize_provider_record(
                    parsed,
                    received_at=received_at,
                    source_id=self.config.source_id,
                    instrument_mapper=self.instrument_mapper,
                )
            except NormalizationError:
                raise
            # Canonical validation completes before the explicit consumer
            # availability boundary is crossed.
            canonical_items.append(canonicalize_market_data(normalized.value))

        # FASE 1.14: absence of an explicitly configured pre-return boundary
        # remains UNKNOWN. No received_at/observed_at/close_time fallback exists.
        if self._consumer_handoff_clock is None:
            evidence = AvailabilityEvidence.unknown()
        else:
            # This callback is evaluated inside the adapter, before return.
            # It measures the explicit pre-return availability boundary, not
            # the later instant observed by the caller after return.
            handoff_at = self._consumer_handoff_clock()
            evidence = AvailabilityEvidence.consumer_handoff(
                received_at=received_at,
                available_at=handoff_at,
                evidence_reference=self.AVAILABILITY_EVIDENCE_REFERENCE,
                consumer_scope=self.CONSUMER_SCOPE,
            )

        available_at = resolve_availability(
            evidence,
            received_at=received_at,
        )
        if available_at is None:
            return canonical_items

        # MarketData is frozen; dataclasses.replace re-runs the canonical
        # constructor validation while changing only the explicitly resolved
        # availability timestamp.
        return [replace(item, available_at=available_at) for item in canonical_items]

    def fetch_historical_market_data(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
        page_limit: int = 1000,
        max_requests: int = 1000,
    ) -> list[MarketData]:
        """Fetch a bounded historical range using deterministic temporal pagination.

        Binance documents that kline requests are chronological, use open time as
        the logical identity, and accept at most 1000 records per request. The
        next request advances from the last returned candle's close time + 1 ms,
        avoiding inclusive-range overlap without inventing a cursor.
        """
        self.build_query(
            symbol=symbol,
            interval=interval,
            limit=page_limit,
            start_time=start_time,
            end_time=end_time,
        )
        if start_time > end_time:
            raise InvalidHistoricalRange("start_time must be <= end_time")
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("max_requests must be an integer >= 1")

        cursor = start_time
        requests_made = 0
        collected: list[MarketData] = []
        seen_identity: set[tuple[str, str, datetime]] = set()

        while cursor <= end_time:
            if requests_made >= max_requests:
                raise MaximumRequestsExceeded(
                    f"historical acquisition exceeded max_requests={max_requests}"
                )

            page = self.fetch_market_data(
                symbol=symbol,
                interval=interval,
                limit=page_limit,
                start_time=cursor,
                end_time=end_time,
                allow_empty=True,
            )
            requests_made += 1

            if not page:
                break

            previous_item = collected[-1] if collected else None
            for position, item in enumerate(page):
                item_start_ms = _datetime_to_ms(item.payload.start)
                if item_start_ms < start_time or item_start_ms > end_time:
                    raise UnexpectedPageOrder(
                        "historical page returned a kline outside the requested range"
                    )

                identity = item.candle_identity
                if identity in seen_identity:
                    raise DuplicateKline(
                        "duplicate historical kline: "
                        f"instrument_id={identity[0]} "
                        f"timeframe={identity[1]} "
                        f"start={identity[2].isoformat()}"
                    )

                if previous_item is not None:
                    expected_start = previous_item.payload.end + timedelta(milliseconds=1)
                    actual_start = item.payload.start
                    if position == 0 and actual_start < expected_start:
                        raise PaginationStalled(
                            "historical pagination did not advance: "
                            f"cursor={cursor}, page_start={item_start_ms}"
                        )
                    if actual_start < expected_start:
                        raise UnexpectedPageOrder(
                            "historical klines are not strictly chronological"
                        )
                    if actual_start > expected_start:
                        raise HistoricalGapDetected(
                            "historical kline gap detected between "
                            f"{previous_item.payload.start.isoformat()} and "
                            f"{actual_start.isoformat()}"
                        )

                seen_identity.add(identity)
                collected.append(item)
                previous_item = item

            last_item = page[-1]
            new_cursor = _datetime_to_ms(last_item.payload.end) + 1

            if new_cursor <= cursor:
                raise PaginationStalled(
                    f"historical pagination did not advance: cursor={cursor}, "
                    f"next_cursor={new_cursor}"
                )

            cursor = new_cursor
            if cursor > end_time or len(page) < page_limit:
                break

        return collected


    def fetch_one(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 1,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> MarketData:
        """Fetch exactly one kline and return one canonical MarketData item."""
        items = self.fetch_market_data(
            symbol=symbol,
            interval=interval,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
        if len(items) != 1:
            raise BinancePayloadError(
                f"expected exactly one kline but received {len(items)}"
            )
        return items[0]


__all__ = [
    "BinanceAdapterError",
    "BinanceAPIError",
    "BinanceHTTPError",
    "BinancePayloadError",
    "BinanceRateLimitError",
    "BinanceSpotRestAdapter",
    "BinanceSpotRestConfig",
    "BinanceTransportError",
    "DuplicateKline",
    "HistoricalGapDetected",
    "InvalidHistoricalRange",
    "MaximumRequestsExceeded",
    "PaginationStalled",
    "UnexpectedPageOrder",
]
