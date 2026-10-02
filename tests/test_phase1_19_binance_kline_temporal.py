import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.binance_instruments import (
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
)
from bot_obrero.binance_spot import (
    BinanceSpotRestAdapter,
    BinanceSpotRestConfig,
    _datetime_to_ms,
    _ms_to_datetime,
)


OPEN = 1790942400000
CLOSE = OPEN + 59999
RECEIVED = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
HANDOFF = RECEIVED + timedelta(seconds=1)


def record(symbol: str, base: str, quote: str) -> dict:
    return {
        "symbol": symbol,
        "status": "TRADING",
        "baseAsset": base,
        "quoteAsset": quote,
        "isSpotTradingAllowed": True,
    }


def exchange_payload(*records: dict) -> dict:
    return {"timezone": "UTC", "symbols": list(records)}


def metadata_getter(payload: dict):
    body = json.dumps(payload).encode()

    def getter(url, headers, timeout):
        return 200, {}, body

    return getter


def row(open_time: int = OPEN, close_time: int = CLOSE) -> list:
    return [
        open_time,
        "123.4500",
        "124.0000",
        "123.0000",
        "123.7500",
        "0.0100",
        close_time,
        "1.2345000",
        42,
        "0.0050",
        "0.6172500",
        "0",
    ]


def adapter_for(
    payload: dict,
    market_payload: list,
    *,
    handoff=None,
    clock=lambda: RECEIVED,
):
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    body = json.dumps(market_payload).encode()

    def market_getter(url, headers, timeout):
        return 200, {}, body

    return BinanceMetadataBackedAdapter(
        metadata=metadata,
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=market_getter,
        clock=clock,
        consumer_handoff_clock=handoff,
    )


def test_binance_millisecond_timestamps_round_trip_as_explicit_utc():
    timestamp = OPEN
    parsed = _ms_to_datetime(timestamp, "open_time")
    assert parsed.tzinfo == timezone.utc
    assert parsed == datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    assert _datetime_to_ms(parsed) == timestamp


def test_machine_local_timezone_does_not_change_binance_timestamp_conversion(monkeypatch):
    original_tz = os.environ.get("TZ")
    try:
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        first = _ms_to_datetime(OPEN, "open_time")

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        second = _ms_to_datetime(OPEN, "open_time")

        assert first == second
        assert first.tzinfo == timezone.utc
        assert second.tzinfo == timezone.utc
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz


def test_request_contract_uses_millisecond_start_and_end_without_timezone_parameter():
    adapter = BinanceSpotRestAdapter(
        instrument_mapper=None,  # build_query does not require mapping
        config=BinanceSpotRestConfig(),
    )
    query = adapter.build_query(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
        start_time=OPEN,
        end_time=OPEN + 60000,
    )
    assert query == {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "limit": "1",
        "startTime": str(OPEN),
        "endTime": str(OPEN + 60000),
    }
    assert "timeZone" not in query


def test_start_boundary_preserves_kline_open_time_equal_to_start_time():
    captured = {}
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    body = json.dumps([row(open_time=OPEN, close_time=CLOSE)]).encode()

    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))

    def getter(url, headers, timeout):
        captured["url"] = url
        return 200, {}, body

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        http_get=getter,
        clock=lambda: RECEIVED,
    )
    item = adapter.fetch_market_data(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
        start_time=OPEN,
        end_time=CLOSE,
    )[0]

    assert f"startTime={OPEN}" in captured["url"]
    assert item.payload.start == _ms_to_datetime(OPEN, "open_time")
    assert item.observed_at == item.payload.start


def test_end_boundary_preserves_kline_open_time_equal_to_end_time():
    end_time = OPEN
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    body = json.dumps([row(open_time=end_time, close_time=end_time + 59999)]).encode()

    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))

    def getter(url, headers, timeout):
        assert f"endTime={end_time}" in url
        return 200, {}, body

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        http_get=getter,
        clock=lambda: RECEIVED,
    )
    item = adapter.fetch_market_data(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
        start_time=end_time,
        end_time=end_time,
    )[0]

    assert item.payload.start == _ms_to_datetime(end_time, "open_time")


def test_open_and_close_times_are_preserved_as_utc_without_local_shift():
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    item = adapter_for(
        payload,
        [row(open_time=OPEN, close_time=CLOSE)],
    ).fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)[0]

    assert item.payload.start == _ms_to_datetime(OPEN, "open_time")
    assert item.payload.end == _ms_to_datetime(CLOSE, "close_time")
    assert item.payload.start.tzinfo == timezone.utc
    assert item.payload.end.tzinfo == timezone.utc
    assert item.payload.end > item.payload.start


def test_close_time_does_not_drive_availability():
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    item = adapter_for(
        payload,
        [row()],
        handoff=lambda: HANDOFF,
    ).fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)[0]

    assert item.available_at == HANDOFF
    assert item.available_at != item.payload.end
    assert item.received_at == RECEIVED


def test_no_timezone_parameter_is_added_even_when_binance_supports_it():
    captured = {}
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    body = json.dumps([row()]).encode()

    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))

    def getter(url, headers, timeout):
        captured["url"] = url
        return 200, {}, body

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        http_get=getter,
        clock=lambda: RECEIVED,
    )
    adapter.fetch_market_data(symbol="BTCUSDT", interval="1d", limit=1)

    assert "timeZone=" not in captured["url"]
    assert "timezone=" not in captured["url"]


def test_historical_pagination_uses_last_close_plus_one_millisecond():
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    pages = [
        [row(open_time=OPEN, close_time=CLOSE)],
        [row(open_time=OPEN + 60000, close_time=CLOSE + 60000)],
    ]
    calls = []

    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))

    def getter(url, headers, timeout):
        calls.append(url)
        return 200, {}, json.dumps(pages[len(calls) - 1]).encode()

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        http_get=getter,
        clock=lambda: RECEIVED,
    )
    result = adapter.fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN,
        end_time=OPEN + 60000,
        page_limit=1,
        max_requests=2,
    )

    assert len(result) == 2
    assert f"startTime={OPEN}" in calls[0]
    assert f"startTime={CLOSE + 1}" in calls[1]
    assert all("timeZone=" not in url for url in calls)


@pytest.mark.parametrize("interval", ["1m", "1h", "1d", "1w", "1M"])
def test_temporal_contract_is_compatible_with_phase_1_18_intervals(interval):
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))
    item = adapter_for(
        payload,
        [row()],
    ).fetch_market_data(symbol="BTCUSDT", interval=interval, limit=1)[0]

    assert item.payload.timeframe == interval
    assert item.payload.start == _ms_to_datetime(OPEN, "open_time")
    assert item.payload.end == _ms_to_datetime(CLOSE, "close_time")


def test_metadata_backed_btcusdt_and_ethusdt_keep_phase_1_17_identity():
    payload = exchange_payload(
        record("BTCUSDT", "BTC", "USDT"),
        record("ETHUSDT", "ETH", "USDT"),
    )
    adapter = adapter_for(payload, [row()])

    btc = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)[0]
    eth = adapter.fetch_market_data(symbol="ETHUSDT", interval="1h", limit=1)[0]

    assert btc.instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert eth.instrument.instrument_id == "binance:SPOT:ETHUSDT"
    assert btc.instrument.symbol == "BTC/USDT"
    assert eth.instrument.symbol == "ETH/USDT"


def test_availability_is_unchanged_when_query_window_changes():
    payload = exchange_payload(record("BTCUSDT", "BTC", "USDT"))

    first = adapter_for(
        payload,
        [row()],
        handoff=lambda: HANDOFF,
    ).fetch_market_data(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
        start_time=OPEN,
        end_time=CLOSE,
    )[0]

    second = adapter_for(
        payload,
        [row()],
        handoff=lambda: HANDOFF,
    ).fetch_market_data(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
        start_time=OPEN - 60000,
        end_time=CLOSE + 60000,
    )[0]

    assert first.available_at == HANDOFF
    assert second.available_at == HANDOFF
