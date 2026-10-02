"""Manual real Binance Spot REST E2E verification for FASE 1.20.

This script deliberately exercises the production Binance metadata and market-data
adapters. It is not a pytest test and is never invoked by the normal offline CI.

Network instrumentation delegates to the production HTTP implementations; it does
not create a second HTTP client or alter the request semantics.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from bot_obrero import binance_instruments as metadata_module
from bot_obrero import binance_spot as spot_module
from bot_obrero.binance_instruments import (
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
)
from bot_obrero.binance_intervals import validate_binance_spot_interval
from bot_obrero.binance_spot import BinanceSpotRestConfig, _datetime_to_ms
from bot_obrero.market_data import DataQuality


MAX_TOTAL_REQUESTS = 8
HISTORICAL_PAGE_LIMIT = 2
HISTORICAL_MAX_REQUESTS = 2


class RequestCounter:
    def __init__(self) -> None:
        self.exchange_info = 0
        self.klines = 0
        self.urls: list[str] = []
        self.live_raw_timestamps: dict[str, tuple[int, int]] = {}

    @property
    def total(self) -> int:
        return self.exchange_info + self.klines


counter = RequestCounter()


def real_exchange_info_get(url, headers, timeout):
    counter.exchange_info += 1
    counter.urls.append(url)
    if counter.total > MAX_TOTAL_REQUESTS:
        raise RuntimeError(f"E2E request budget exceeded: max={MAX_TOTAL_REQUESTS}")
    return metadata_module._default_exchange_info_get(url, headers, timeout)


def real_klines_get(url, headers, timeout):
    counter.klines += 1
    counter.urls.append(url)
    if counter.total > MAX_TOTAL_REQUESTS:
        raise RuntimeError(f"E2E request budget exceeded: max={MAX_TOTAL_REQUESTS}")

    status, response_headers, body = spot_module._default_http_get(
        url, headers, timeout
    )

    # Evidence-only extraction from the exact response consumed by the production
    # adapter. All provider parsing remains inside BinanceSpotRestAdapter.
    if status >= 200 and status < 300:
        payload = json.loads(body.decode("utf-8"))
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            row = payload[0]
            if len(row) == 12 and isinstance(row[0], int) and isinstance(row[6], int):
                counter.live_raw_timestamps[url] = (row[0], row[6])

    return status, response_headers, body


def assert_utc(value: datetime, name: str) -> None:
    assert value.tzinfo is timezone.utc, f"{name} is not timezone.utc: {value!r}"
    assert value.utcoffset() == timedelta(0), f"{name} is not UTC: {value!r}"


def run_live(
    *,
    metadata: BinanceSpotInstrumentMetadata,
    adapter: BinanceMetadataBackedAdapter,
    symbol: str,
    interval: str,
) -> None:
    interval_value = validate_binance_spot_interval(interval).value
    assert interval_value == interval

    instrument = metadata.resolve_active(symbol)
    assert instrument.instrument_id == f"binance:SPOT:{symbol}"
    expected_symbol = "BTC/USDT" if symbol == "BTCUSDT" else "ETH/USDT"
    assert instrument.symbol == expected_symbol
    assert instrument.market == "SPOT"

    before = len(counter.urls)
    items = adapter.fetch_market_data(symbol=symbol, interval=interval, limit=1)
    assert len(items) == 1, f"{symbol}/{interval}: expected one MarketData item"
    assert len(counter.urls) == before + 1, (
        f"{symbol}/{interval}: expected one klines request"
    )

    item = items[0]
    assert item.instrument.instrument_id == instrument.instrument_id
    assert item.instrument.symbol == expected_symbol
    assert item.instrument.market == "SPOT"
    assert item.source.source_id == "binance-spot-rest"
    assert item.source.provider == "binance"
    assert item.source.venue == "BINANCE"
    assert item.available_at is None
    assert item.observed_at == item.payload.start
    assert_utc(item.observed_at, "observed_at")
    assert_utc(item.received_at, "received_at")
    assert_utc(item.payload.start, "payload.start")
    assert_utc(item.payload.end, "payload.end")
    assert item.payload.timeframe == interval
    assert item.payload.end > item.payload.start
    assert item.quality is DataQuality.VALID

    url = counter.urls[-1]
    parsed = parse_qs(urlparse(url).query)
    assert parsed["symbol"] == [symbol]
    assert parsed["interval"] == [interval]
    assert "timeZone" not in parsed
    assert "timezone" not in parsed

    raw_open, raw_close = counter.live_raw_timestamps[url]
    assert _datetime_to_ms(item.payload.start) == raw_open
    assert _datetime_to_ms(item.payload.end) == raw_close

    print(
        f"LIVE {symbol}/{interval}: "
        f"instrument={item.instrument.instrument_id} "
        f"source={item.source.source_id} "
        f"start={item.payload.start.isoformat()} "
        f"end={item.payload.end.isoformat()} "
        f"observed_at={item.observed_at.isoformat()} "
        f"received_at={item.received_at.isoformat()} "
        f"available_at=None "
        f"quality={item.quality.value} "
        f"completeness={item.completeness.value} "
        f"candle_completeness={item.payload.completeness.value} "
        f"candle_state={item.payload.candle_state.value} "
        f"finality={item.payload.finality.value}"
    )


def main() -> int:
    print("FASE 1.20.1 — REAL BINANCE SPOT REST E2E")
    print(f"max_requests={MAX_TOTAL_REQUESTS}")

    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"),
        http_get=real_exchange_info_get,
    )
    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        config=BinanceSpotRestConfig(),
        http_get=real_klines_get,
        consumer_handoff_clock=None,
    )

    print("EXCHANGEINFO + INSTRUMENT RESOLUTION")
    for symbol, expected in (
        ("BTCUSDT", ("BTC", "USDT")),
        ("ETHUSDT", ("ETH", "USDT")),
    ):
        record = metadata.fetch(symbol)
        assert record.provider_symbol == symbol
        assert (record.base_asset, record.quote_asset) == expected
        assert record.market == "SPOT"
        assert record.venue == "BINANCE"
        assert record.status == "TRADING"
        assert record.is_spot_trading_allowed is True
        instrument = metadata.resolve_active(symbol)
        print(
            f"METADATA {symbol}: "
            f"instrument_id={instrument.instrument_id} "
            f"symbol={instrument.symbol} market={instrument.market}"
        )

    run_live(metadata=metadata, adapter=adapter, symbol="BTCUSDT", interval="1m")
    run_live(metadata=metadata, adapter=adapter, symbol="ETHUSDT", interval="5m")

    print("HISTORICAL BTCUSDT/1m")
    now = datetime.now(timezone.utc)
    current_minute = now.replace(second=0, microsecond=0)
    end_time = _datetime_to_ms(current_minute - timedelta(minutes=2))
    start_time = _datetime_to_ms(current_minute - timedelta(minutes=4))

    historical_urls_before = len(counter.urls)
    historical = adapter.fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=start_time,
        end_time=end_time,
        page_limit=HISTORICAL_PAGE_LIMIT,
        max_requests=HISTORICAL_MAX_REQUESTS,
    )
    historical_urls = counter.urls[historical_urls_before:]

    assert historical, "Historical acquisition returned no MarketData"
    starts = [item.payload.start for item in historical]
    assert starts == sorted(starts), "Historical candles are not chronological"
    identities = [item.candle_identity for item in historical]
    assert len(identities) == len(set(identities)), "Historical duplicate candle identity"
    assert all(
        start_time <= _datetime_to_ms(item.payload.start) <= end_time
        for item in historical
    )
    assert all(item.source.source_id == "binance-spot-rest" for item in historical)
    assert all(item.source.provider == "binance" for item in historical)
    assert all(item.source.venue == "BINANCE" for item in historical)
    assert all(item.available_at is None for item in historical)
    assert all(item.payload.timeframe == "1m" for item in historical)
    assert all(item.payload.start.tzinfo is timezone.utc for item in historical)
    assert all(item.payload.end.tzinfo is timezone.utc for item in historical)
    assert all(
        "timeZone=" not in url and "timezone=" not in url for url in historical_urls
    )

    if len(historical_urls) >= 2:
        first_page_last = historical[HISTORICAL_PAGE_LIMIT - 1]
        first_page_end = _datetime_to_ms(first_page_last.payload.end)
        second_query = parse_qs(urlparse(historical_urls[1]).query)
        observed_cursor = int(second_query["startTime"][0])
        expected_cursor = first_page_end + 1
        assert observed_cursor == expected_cursor
        print(
            f"HISTORICAL pages>=2: requests={len(historical_urls)} "
            f"cursor={observed_cursor} expected={expected_cursor}"
        )
    else:
        print("HISTORICAL request completed with one page")

    print(
        f"HISTORICAL BTCUSDT/1m: requests={len(historical_urls)} "
        f"candles={len(historical)} "
        f"start={historical[0].payload.start.isoformat()} "
        f"end={historical[-1].payload.end.isoformat()}"
    )
    print(
        f"REQUESTS total={counter.total} "
        f"exchangeInfo={counter.exchange_info} klines={counter.klines}"
    )
    assert counter.total <= MAX_TOTAL_REQUESTS
    print("RESULT: REAL E2E PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        metadata_module.ExchangeInfoTransportError,
        metadata_module.ExchangeInfoHTTPError,
        spot_module.BinanceTransportError,
        spot_module.BinanceHTTPError,
        spot_module.BinanceRateLimitError,
    ) as exc:
        print(
            f"NETWORK_OR_BINANCE_ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print("RESULT: REQUIRES CEREBRO REVIEW", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:
        print(f"E2E_FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("RESULT: REQUIRES CEREBRO REVIEW", file=sys.stderr)
        raise SystemExit(1) from exc
