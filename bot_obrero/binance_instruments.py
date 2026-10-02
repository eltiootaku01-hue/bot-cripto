"""Binance Spot ExchangeInfo instrument metadata and explicit resolution.

This module is provider-specific. It parses only the Binance Spot metadata needed
to resolve an instrument and delegates canonical market-data acquisition to the
existing BinanceSpotRestAdapter. It does not modify the canonical market-data
contract and has no persistent/background cache.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from .acquisition import InstrumentMapper, InstrumentMappingRule
from .binance_spot import (
    BinanceAPIError,
    BinanceHTTPError,
    BinancePayloadError,
    BinanceRateLimitError,
    BinanceSpotRestAdapter,
    BinanceSpotRestConfig,
    BinanceTransportError,
)
from .market_data import InstrumentIdentity, MarketData


class ExchangeInfoTransportError(RuntimeError):
    """Network/transport failure while requesting ExchangeInfo."""


class ExchangeInfoHTTPError(RuntimeError):
    """HTTP failure while requesting ExchangeInfo."""


class ExchangeInfoPayloadError(RuntimeError):
    """Malformed ExchangeInfo response."""


class InstrumentNotFound(RuntimeError):
    """The requested Binance symbol is absent from ExchangeInfo."""


class InstrumentInactive(RuntimeError):
    """The requested Binance instrument exists but is not active for acquisition."""


class InstrumentMetadataInvalid(RuntimeError):
    """A Binance symbol record lacks required or valid metadata."""


class InstrumentMappingAmbiguous(RuntimeError):
    """More than one Binance symbol record matches the requested provider symbol."""


class InstrumentResolution(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


@dataclass(frozen=True)
class BinanceInstrumentRecord:
    """Provider-specific subset of Binance Spot symbol metadata."""

    provider_symbol: str
    base_asset: str
    quote_asset: str
    market: str
    venue: str
    status: str
    is_spot_trading_allowed: bool

    def __post_init__(self) -> None:
        for name in (
            "provider_symbol",
            "base_asset",
            "quote_asset",
            "market",
            "venue",
            "status",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InstrumentMetadataInvalid(f"{name} must be a non-empty string")
        if self.market != "SPOT":
            raise InstrumentMetadataInvalid("Binance instrument market must be SPOT")
        if self.venue != "BINANCE":
            raise InstrumentMetadataInvalid("Binance instrument venue must be BINANCE")
        if not isinstance(self.is_spot_trading_allowed, bool):
            raise InstrumentMetadataInvalid(
                "is_spot_trading_allowed must be boolean"
            )


@dataclass(frozen=True)
class BinanceInstrumentResolution:
    outcome: InstrumentResolution
    instrument: InstrumentIdentity | None = None
    metadata: BinanceInstrumentRecord | None = None


HTTPGetter = Callable[
    [str, Mapping[str, str], float],
    tuple[int, Mapping[str, str], bytes],
]


def _default_exchange_info_get(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return (
            int(exc.code),
            dict(exc.headers.items()) if exc.headers else {},
            exc.read(),
        )
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise ExchangeInfoTransportError(
            f"Binance ExchangeInfo transport failure: {exc}"
        ) from exc


class BinanceSpotInstrumentMetadata:
    """Fetch and resolve Binance Spot instrument metadata with an in-memory cache."""

    def __init__(
        self,
        *,
        config: BinanceSpotRestConfig | None = None,
        http_get: HTTPGetter = _default_exchange_info_get,
    ) -> None:
        self.config = config or BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
        if self.config.endpoint != "/api/v3/exchangeInfo":
            raise ValueError("ExchangeInfo config endpoint must be /api/v3/exchangeInfo")
        self._http_get = http_get
        self._cache: dict[str, BinanceInstrumentRecord] = {}

    @property
    def endpoint_url(self) -> str:
        return self.config.base_url.rstrip("/") + "/api/v3/exchangeInfo"

    def _request(self, symbol: str) -> Any:
        encoded = urllib.parse.urlencode({"symbol": symbol})
        url = f"{self.endpoint_url}?{encoded}"
        try:
            status, headers, body = self._http_get(
                url,
                {"Accept": "application/json", "User-Agent": "bot-cripto-binance-spot-metadata/1.0"},
                self.config.timeout_seconds,
            )
        except ExchangeInfoTransportError:
            raise
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise ExchangeInfoTransportError(
                f"Binance ExchangeInfo transport failure: {exc}"
            ) from exc

        if status < 200 or status >= 300:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, Mapping) and isinstance(payload.get("code"), int) and isinstance(payload.get("msg"), str):
                if status in (429, 418):
                    raise BinanceRateLimitError(
                        status,
                        f"Binance ExchangeInfo rate limit: {payload['code']}: {payload['msg']}",
                    )
                raise BinanceAPIError(payload["code"], payload["msg"], http_status=status)
            raise ExchangeInfoHTTPError(f"Binance ExchangeInfo HTTP failure: {status}")

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExchangeInfoPayloadError("ExchangeInfo response is not valid UTF-8 JSON") from exc
        if isinstance(payload, Mapping) and "code" in payload and "msg" in payload:
            if isinstance(payload.get("code"), int) and isinstance(payload.get("msg"), str):
                raise BinanceAPIError(payload["code"], payload["msg"], http_status=status)
        return payload

    @staticmethod
    def _parse_symbol_record(raw: Any) -> BinanceInstrumentRecord:
        if not isinstance(raw, Mapping):
            raise ExchangeInfoPayloadError("ExchangeInfo symbol entry must be an object")
        required = (
            "symbol",
            "status",
            "baseAsset",
            "quoteAsset",
            "isSpotTradingAllowed",
        )
        missing = [name for name in required if name not in raw]
        if missing:
            raise ExchangeInfoPayloadError(
                "ExchangeInfo symbol entry missing required fields: " + ", ".join(missing)
            )
        symbol = raw["symbol"]
        status = raw["status"]
        base = raw["baseAsset"]
        quote = raw["quoteAsset"]
        spot_allowed = raw["isSpotTradingAllowed"]
        if any(not isinstance(value, str) or not value.strip() for value in (symbol, status, base, quote)):
            raise ExchangeInfoPayloadError("ExchangeInfo symbol identity fields must be non-empty strings")
        if not isinstance(spot_allowed, bool):
            raise ExchangeInfoPayloadError("isSpotTradingAllowed must be boolean")
        return BinanceInstrumentRecord(
            provider_symbol=symbol,
            base_asset=base,
            quote_asset=quote,
            market="SPOT",
            venue="BINANCE",
            status=status,
            is_spot_trading_allowed=spot_allowed,
        )

    def fetch(self, symbol: str, *, refresh: bool = False) -> BinanceInstrumentRecord:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InstrumentMetadataInvalid("symbol must be a non-empty string")
        key = symbol.strip()
        if not refresh and key in self._cache:
            return self._cache[key]

        payload = self._request(key)
        if not isinstance(payload, Mapping):
            raise ExchangeInfoPayloadError("ExchangeInfo root must be an object")
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise ExchangeInfoPayloadError("ExchangeInfo symbols must be an array")

        matches = [self._parse_symbol_record(item) for item in symbols]
        exact = [item for item in matches if item.provider_symbol == key]
        if not exact:
            raise InstrumentNotFound(f"Binance Spot symbol not found: {key}")
        if len(exact) > 1:
            raise InstrumentMappingAmbiguous(
                f"multiple Binance metadata records match symbol {key}"
            )
        record = exact[0]
        self._cache[key] = record
        return record

    def resolve(self, symbol: str, *, refresh: bool = False) -> BinanceInstrumentResolution:
        try:
            metadata = self.fetch(symbol, refresh=refresh)
        except InstrumentNotFound:
            return BinanceInstrumentResolution(InstrumentResolution.NOT_FOUND)
        except InstrumentMappingAmbiguous:
            return BinanceInstrumentResolution(InstrumentResolution.AMBIGUOUS)
        except (ExchangeInfoPayloadError, InstrumentMetadataInvalid):
            return BinanceInstrumentResolution(InstrumentResolution.INVALID)

        if metadata.market != "SPOT" or metadata.venue != "BINANCE":
            return BinanceInstrumentResolution(
                InstrumentResolution.INVALID, metadata=metadata
            )
        if not metadata.base_asset or not metadata.quote_asset:
            return BinanceInstrumentResolution(
                InstrumentResolution.INVALID, metadata=metadata
            )

        # Canonical symbol is derived from the provider's explicit base/quote
        # fields, not by parsing provider_symbol. The instrument id is a
        # namespaced provider identity, not a universal cross-provider mapping.
        instrument = InstrumentIdentity(
            instrument_id=f"binance:SPOT:{metadata.provider_symbol}",
            symbol=f"{metadata.base_asset}/{metadata.quote_asset}",
            market="SPOT",
        )
        return BinanceInstrumentResolution(
            InstrumentResolution.FOUND,
            instrument=instrument,
            metadata=metadata,
        )

    def resolve_active(self, symbol: str, *, refresh: bool = False) -> InstrumentIdentity:
        result = self.resolve(symbol, refresh=refresh)
        if result.outcome is InstrumentResolution.NOT_FOUND:
            raise InstrumentNotFound(f"Binance Spot symbol not found: {symbol}")
        if result.outcome is InstrumentResolution.AMBIGUOUS:
            raise InstrumentMappingAmbiguous(f"multiple Binance records match: {symbol}")
        if result.outcome is InstrumentResolution.INVALID or result.instrument is None:
            raise InstrumentMetadataInvalid(f"invalid Binance metadata for: {symbol}")
        assert result.metadata is not None
        if result.metadata.status != "TRADING" or not result.metadata.is_spot_trading_allowed:
            raise InstrumentInactive(
                f"Binance Spot instrument is inactive: {symbol} "
                f"(status={result.metadata.status}, "
                f"isSpotTradingAllowed={result.metadata.is_spot_trading_allowed})"
            )
        return result.instrument


class BinanceMetadataBackedAdapter:
    """Metadata-backed facade reusing the existing Binance market-data adapter."""

    def __init__(
        self,
        *,
        metadata: BinanceSpotInstrumentMetadata,
        config: BinanceSpotRestConfig | None = None,
        http_get: HTTPGetter | None = None,
        clock: Callable[..., Any] | None = None,
        consumer_handoff_clock: Callable[..., Any] | None = None,
    ) -> None:
        self.metadata = metadata
        self.config = config or BinanceSpotRestConfig()
        self._http_get = http_get
        self._clock = clock
        self._consumer_handoff_clock = consumer_handoff_clock

    def resolve_instrument(self, symbol: str, *, refresh: bool = false) -> InstrumentIdentity:
        """Resolve one active Binance Spot symbol from ExchangeInfo for acquisition."""
        return self.metadata.resolve_active(symbol, refresh=refresh)

    def _mapper_for(self, symbol: str) -> InstrumentMapper:
        instrument = self.resolve_instrument(symbol)
        metadata = self.metadata.fetch(symbol)
        return InstrumentMapper(
            [
                InstrumentMappingRule(
                    provider="binance",
                    provider_symbol=metadata.provider_symbol,
                    provider_market="SPOT",
                    provider_venue="BINANCE",
                    instrument=instrument,
                )
            ]
        )

    def _adapter(self, symbol: str) -> BinanceSpotRestAdapter:
        kwargs: dict[str, Any] = {
            "instrument_mapper": self._mapper_for(symbol),
            "config": self.config,
        }
        if self._http_get is not None:
            kwargs["http_get"] = self._http_get
        if self._clock is not None:
            kwargs["clock"] = self._clock
        if self._consumer_handoff_clock is not None:
            kwargs["consumer_handoff_clock"] = self._consumer_handoff_clock
        return BinanceSpotRestAdapter(**kwargs)

    def fetch_market_data(self, **kwargs: Any) -> list[MarketData]:
        symbol = kwargs["symbol"]
        return self._adapter(symbol).fetch_market_data(**kwargs)

    def fetch_historical_market_data(self, **kwargs: Any) -> list[MarketData]:
        symbol = kwargs["symbol"]
        return self._adapter(symbol).fetch_historical_market_data(**kwargs)


__all__ = [
    "BinanceInstrumentRecord",
    "BinanceInstrumentResolution",
    "BinanceMetadataBackedAdapter",
    "BinanceSpotInstrumentMetadata",
    "ExchangeInfoHTTPError",
    "ExchangeInfoPayloadError",
    "ExchangeInfoTransportError",
    "InstrumentInactive",
    "InstrumentMappingAmbiguous",
    "InstrumentMetadataInvalid",
    "InstrumentNotFound",
]
