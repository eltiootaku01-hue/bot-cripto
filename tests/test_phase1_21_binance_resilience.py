from __future__ import annotations

import json
import socket
import urllib.error

import pytest

from bot_obrero.acquisition import InstrumentMapper, InstrumentMappingRule
from bot_obrero.binance_instruments import (
    BinanceSpotInstrumentMetadata,
    ExchangeInfoHTTPError,
    ExchangeInfoTransportError,
)
from bot_obrero.binance_resilience import BinanceRetryPolicy
from bot_obrero.binance_spot import (
    BinanceAPIError,
    BinanceHTTPError,
    BinancePayloadError,
    BinanceRateLimitError,
    BinanceSpotRestAdapter,
    BinanceSpotRestConfig,
    BinanceTransportError,
    MaximumRequestsExceeded,
)
from bot_obrero.market_data import InstrumentIdentity


def kline_row(open_time: int, close_time: int) -> list[object]:
    return [open_time, "10.0", "11.0", "9.0", "10.5", "2.0", close_time, "21.0", 4, "1.0", "10.5", "0"]


def mapper() -> InstrumentMapper:
    return InstrumentMapper([
        InstrumentMappingRule(
            provider="binance",
            provider_symbol="BTCUSDT",
            provider_market="SPOT",
            provider_venue="BINANCE",
            instrument=InstrumentIdentity(
                instrument_id="binance:SPOT:BTCUSDT",
                symbol="BTC/USDT",
                market="SPOT",
                base_asset="BTC",
                quote_asset="USDT",
            ),
        )
    ])


def adapter(http_get, *, sleeper=lambda seconds: None, max_attempts=3):
    return BinanceSpotRestAdapter(
        instrument_mapper=mapper(),
        config=BinanceSpotRestConfig(timeout_seconds=1),
        http_get=http_get,
        retry_policy=BinanceRetryPolicy(
            max_attempts=max_attempts,
            backoff_base_seconds=0.5,
            max_backoff_seconds=2.0,
            sleeper=sleeper,
        ),
    )


def test_timeout_is_retried_bounded_and_final_outcome_unknown():
    calls = []
    delays = []
    def http_get(url, headers, timeout):
        calls.append(1)
        raise socket.timeout("timed out")
    with pytest.raises(BinanceTransportError) as exc:
        adapter(http_get, sleeper=delays.append, max_attempts=3)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert len(calls) == 3
    assert delays == [0.5, 1.0]
    assert exc.value.outcome_unknown is True


def test_connection_error_is_retried():
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        raise urllib.error.URLError("connection refused")
    with pytest.raises(BinanceTransportError):
        adapter(http_get, sleeper=lambda _: None, max_attempts=2)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 404])
def test_client_http_errors_are_not_retried(status):
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return status, {}, b"{}"
    with pytest.raises(BinanceHTTPError) as exc:
        adapter(http_get)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert exc.value.status_code == status
    assert len(calls) == 1


def test_429_respects_retry_after_and_then_succeeds():
    calls = []
    delays = []
    body = json.dumps([kline_row(0, 59999)]).encode()
    def http_get(url, headers, timeout):
        calls.append(1)
        if len(calls) == 1:
            return 429, {"Retry-After": "3.25"}, b"{\"code\":-1003,\"msg\":\"Too many requests\"}"
        return 200, {}, body
    result = adapter(http_get, sleeper=delays.append)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert result == [kline_row(0, 59999)]
    assert len(calls) == 2
    assert delays == [3.25]


@pytest.mark.parametrize("headers", [{}, {"Retry-After": "invalid"}])
def test_rate_limit_without_valid_retry_after_uses_bounded_backoff(headers):
    delays = []
    calls = []
    def http_get(url, request_headers, timeout):
        calls.append(1)
        return 429, headers, b"{}"
    with pytest.raises(BinanceRateLimitError):
        adapter(http_get, sleeper=delays.append, max_attempts=2)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert calls == [1, 1]
    assert delays == [0.5]


@pytest.mark.parametrize("status", [500])
def test_server_errors_are_bounded(status):
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return status, {}, b"{}"
    with pytest.raises(BinanceHTTPError):
        adapter(http_get, sleeper=lambda _: None, max_attempts=3)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert len(calls) == 3


def test_418_retries_only_with_explicit_retry_after():
    calls = []
    delays = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return 418, {"Retry-After": "4"}, b"{}"
    with pytest.raises(BinanceRateLimitError):
        adapter(http_get, sleeper=delays.append, max_attempts=3)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert len(calls) == 3
    assert delays == [4.0, 4.0]


def test_418_without_retry_after_is_not_retried():
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return 418, {}, b"{}"
    with pytest.raises(BinanceRateLimitError):
        adapter(http_get, sleeper=lambda _: None, max_attempts=3)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert len(calls) == 1

def test_http_error_preserves_status_headers_and_body():
    def http_get(url, headers, timeout):
        return 500, {"X-Test": "value"}, b"server failure"
    with pytest.raises(BinanceHTTPError) as exc:
        adapter(http_get, sleeper=lambda _: None, max_attempts=1)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert exc.value.status_code == 500
    assert exc.value.response_headers["X-Test"] == "value"
    assert exc.value.response_body == b"server failure"

def test_500_marks_unknown_outcome():
    def http_get(url, headers, timeout):
        return 500, {}, b"{}"
    with pytest.raises(BinanceHTTPError) as exc:
        adapter(http_get, sleeper=lambda _: None, max_attempts=1)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert exc.value.outcome_unknown is True


def test_api_error_remains_distinct_and_non_retryable_for_400():
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return 400, {}, b"{\"code\":-1121,\"msg\":\"Invalid symbol.\"}"
    with pytest.raises(BinanceAPIError) as exc:
        adapter(http_get)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert exc.value.code == -1121
    assert exc.value.http_status == 400
    assert len(calls) == 1


def test_invalid_json_is_not_retried():
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return 200, {}, b"not-json"
    with pytest.raises(BinancePayloadError):
        adapter(http_get)._request({"symbol": "BTCUSDT", "interval": "1m", "limit": "1"})
    assert len(calls) == 1


def test_valid_json_wrong_kline_shape_is_not_accepted():
    def http_get(url, headers, timeout):
        return 200, {}, b"[{\"wrong\": true}]"
    with pytest.raises(BinancePayloadError):
        adapter(http_get).fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)


def test_historical_retry_does_not_advance_cursor_and_max_requests_counts_physical_attempts():
    calls = []
    delays = []
    body = json.dumps([kline_row(0, 59999)]).encode()
    def http_get(url, headers, timeout):
        calls.append(url)
        if len(calls) == 1:
            return 500, {}, b"{}"
        return 200, {}, body
    with pytest.raises(MaximumRequestsExceeded):
        adapter(http_get, sleeper=delays.append, max_attempts=3).fetch_historical_market_data(
            symbol="BTCUSDT", interval="1m", start_time=0, end_time=59999, page_limit=1, max_requests=1
        )
    assert len(calls) == 1


def test_historical_second_page_after_retry_preserves_cursor_and_order():
    calls = []
    delays = []
    first = json.dumps([kline_row(0, 59999)]).encode()
    second = json.dumps([kline_row(60000, 119999)]).encode()
    responses = [(500, {}, b"{}"), (200, {}, first), (200, {}, second)]
    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)
    items = adapter(http_get, sleeper=delays.append).fetch_historical_market_data(
        symbol="BTCUSDT", interval="1m", start_time=0, end_time=119999, page_limit=1, max_requests=3
    )
    assert len(items) == 2
    assert [item.payload.start.timestamp() for item in items] == [0, 60]
    assert "startTime=60000" in calls[2]
    assert delays == [0.5]


def test_exchange_info_transport_is_retryable():
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        raise urllib.error.URLError("dns failure")
    metadata = BinanceSpotInstrumentMetadata(http_get=http_get, retry_policy=BinanceRetryPolicy(max_attempts=2, sleeper=lambda _: None))
    with pytest.raises(ExchangeInfoTransportError) as exc:
        metadata.fetch("BTCUSDT")
    assert len(calls) == 2
    assert exc.value.outcome_unknown is True


def test_exchange_info_http_status_and_retry_after_are_preserved():
    calls = []
    def http_get(url, headers, timeout):
        calls.append(1)
        return 500, {"Retry-After": "2"}, b"not-json"
    metadata = BinanceSpotInstrumentMetadata(http_get=http_get, retry_policy=BinanceRetryPolicy(max_attempts=1, sleeper=lambda _: None))
    with pytest.raises(ExchangeInfoHTTPError) as exc:
        metadata.fetch("BTCUSDT")
    assert exc.value.status_code == 500
    assert exc.value.retry_after_seconds == 2.0
    assert exc.value.outcome_unknown is True


def test_retry_policy_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        BinanceRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        BinanceRetryPolicy(backoff_base_seconds=2, max_backoff_seconds=1)