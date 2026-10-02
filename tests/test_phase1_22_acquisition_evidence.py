from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.acquisition import InstrumentMapper, InstrumentMappingRule
from bot_obrero.acquisition_evidence import (
    AcquisitionErrorEvidence,
    AcquisitionEvidenceError,
    AcquisitionMode,
    AcquisitionOperationEvidence,
    AcquisitionOperationRecorder,
    AcquisitionStatus,
    new_operation_recorder,
)
from bot_obrero.binance_instruments import (
    BinanceSpotInstrumentMetadata,
)
from bot_obrero.binance_resilience import BinanceRetryPolicy
from bot_obrero.binance_spot import (
    BinanceAPIError,
    BinanceHTTPError,
    BinanceRateLimitError,
    BinanceSpotRestAdapter,
)
from bot_obrero.market_data import InstrumentIdentity


STARTED = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
FINISHED = STARTED + timedelta(seconds=5)
RECEIVED = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)


def kline_row(open_time: int, close_time: int) -> list[object]:
    return [
        open_time,
        "10.0",
        "11.0",
        "9.0",
        "10.5",
        "2.0",
        close_time,
        "21.0",
        4,
        "1.0",
        "10.5",
        "0",
    ]


def mapper() -> InstrumentMapper:
    return InstrumentMapper(
        [
            InstrumentMappingRule(
                provider="binance",
                provider_symbol="BTCUSDT",
                provider_market="SPOT",
                provider_venue="BINANCE",
                instrument=InstrumentIdentity(
                    instrument_id="binance:SPOT:BTCUSDT",
                    symbol="BTC/USDT",
                    market="SPOT",
                ),
            )
        ]
    )


def fixed_operation_clock() -> callable:
    values = iter([STARTED, FINISHED])
    return lambda: next(values)


def adapter(http_get, *, sleeper=lambda _: None, max_attempts=3):
    return BinanceSpotRestAdapter(
        instrument_mapper=mapper(),
        http_get=http_get,
        clock=lambda: RECEIVED,
        operation_clock=fixed_operation_clock(),
        retry_policy=BinanceRetryPolicy(
            max_attempts=max_attempts,
            backoff_base_seconds=0.5,
            max_backoff_seconds=2.0,
            sleeper=sleeper,
        ),
    )


def test_evidence_contract_is_immutable_and_derives_duration():
    evidence = AcquisitionOperationEvidence(
        operation_id="op-1",
        mode=AcquisitionMode.LIVE,
        provider="binance",
        source_id="binance-spot-rest",
        venue="BINANCE",
        market="SPOT",
        symbol="BTCUSDT",
        interval="1m",
        requested_start=STARTED,
        requested_end=FINISHED,
        limit=1,
        request_count=1,
        retry_count=0,
        page_count=1,
        status=AcquisitionStatus.SUCCESS,
        started_at=STARTED,
        finished_at=FINISHED,
    )
    assert evidence.duration == timedelta(seconds=5)
    with pytest.raises(FrozenInstanceError):
        evidence.request_count = 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation_id": ""},
        {"provider": ""},
        {"source_id": ""},
        {"symbol": ""},
    ],
)
def test_evidence_rejects_empty_identity_fields(kwargs):
    values = dict(
        operation_id="op-1",
        mode=AcquisitionMode.LIVE,
        provider="binance",
        source_id="binance-spot-rest",
        venue="BINANCE",
        market="SPOT",
        symbol="BTCUSDT",
        interval="1m",
        requested_start=None,
        requested_end=None,
        limit=1,
        request_count=0,
        retry_count=0,
        page_count=0,
        status=AcquisitionStatus.FAILED,
        started_at=STARTED,
        finished_at=FINISHED,
        error=AcquisitionErrorEvidence("TEST", "failed"),
    )
    values.update(kwargs)
    with pytest.raises(AcquisitionEvidenceError):
        AcquisitionOperationEvidence(**values)


def test_evidence_requires_timezone_aware_operation_times():
    with pytest.raises(AcquisitionEvidenceError, match="started_at"):
        AcquisitionOperationEvidence(
            operation_id="op-1",
            mode=AcquisitionMode.LIVE,
            provider="binance",
            source_id="binance-spot-rest",
            venue="BINANCE",
            market="SPOT",
            symbol="BTCUSDT",
            interval="1m",
            requested_start=None,
            requested_end=None,
            limit=1,
            request_count=1,
            retry_count=0,
            page_count=1,
            status=AcquisitionStatus.SUCCESS,
            started_at=datetime(2026, 10, 2, 12, 0),
            finished_at=FINISHED,
        )


def test_partial_status_is_distinct_from_candle_partial_state():
    recorder = new_operation_recorder(
        mode=AcquisitionMode.HISTORICAL,
        provider="binance",
        source_id="binance-spot-rest",
        venue="BINANCE",
        market="SPOT",
        symbol="BTCUSDT",
        interval="1m",
        requested_start=STARTED,
        requested_end=FINISHED,
        limit=1,
        started_at=STARTED,
    )
    evidence = recorder.finish(
        status=AcquisitionStatus.PARTIAL,
        finished_at=FINISHED,
    )
    assert evidence.status is AcquisitionStatus.PARTIAL


def test_operation_id_remains_stable_across_multiple_physical_attempts():
    recorder = AcquisitionOperationRecorder(
        mode=AcquisitionMode.LIVE,
        provider="binance",
        source_id="binance-spot-rest",
        venue="BINANCE",
        market="SPOT",
        symbol="BTCUSDT",
        interval="1m",
        requested_start=None,
        requested_end=None,
        limit=1,
        started_at=STARTED,
    )
    operation_id = recorder.operation_id
    recorder.begin_logical_request()
    recorder.record_physical_request()
    recorder.record_physical_request()
    recorder.end_logical_request()
    evidence = recorder.finish(
        status=AcquisitionStatus.SUCCESS,
        finished_at=FINISHED,
    )
    assert evidence.operation_id == operation_id
    assert evidence.request_count == 2
    assert evidence.retry_count == 1


def test_live_500_then_200_records_success_two_requests_one_retry():
    calls = []
    responses = [
        (500, {}, b"{}"),
        (200, {}, json.dumps([kline_row(0, 59999)]).encode()),
    ]

    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)

    items, evidence = adapter(http_get).fetch_market_data_with_evidence(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )

    assert len(items) == 1
    assert evidence.mode is AcquisitionMode.LIVE
    assert evidence.provider == "binance"
    assert evidence.source_id == "binance-spot-rest"
    assert evidence.venue == "BINANCE"
    assert evidence.market == "SPOT"
    assert evidence.request_count == 2
    assert evidence.retry_count == 1
    assert evidence.page_count == 1
    assert evidence.status is AcquisitionStatus.SUCCESS
    assert evidence.error is None
    assert evidence.started_at == STARTED
    assert evidence.finished_at == FINISHED
    assert len(calls) == 2


def test_live_429_then_success_records_retry_without_real_sleep():
    calls = []
    delays = []
    responses = [
        (429, {"Retry-After": "7"}, b'{"code":-1003,"msg":"Too many requests"}'),
        (200, {}, json.dumps([kline_row(0, 59999)]).encode()),
    ]

    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)

    _, evidence = adapter(http_get, sleeper=delays.append).fetch_market_data_with_evidence(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )

    assert len(calls) == 2
    assert delays == [7.0]
    assert evidence.request_count == 2
    assert evidence.retry_count == 1
    assert evidence.page_count == 1
    assert evidence.status is AcquisitionStatus.SUCCESS


def test_live_418_with_retry_after_then_success_is_evidenced():
    calls = []
    delays = []
    responses = [
        (418, {"Retry-After": "4"}, b"{}"),
        (200, {}, json.dumps([kline_row(0, 59999)]).encode()),
    ]

    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)

    _, evidence = adapter(http_get, sleeper=delays.append).fetch_market_data_with_evidence(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )

    assert len(calls) == 2
    assert delays == [4.0]
    assert evidence.request_count == 2
    assert evidence.retry_count == 1
    assert evidence.page_count == 1
    assert evidence.status is AcquisitionStatus.SUCCESS


def test_live_retry_exhaustion_attaches_failed_evidence_to_original_error():
    calls = []
    delays = []

    def http_get(url, headers, timeout):
        calls.append(url)
        return 500, {}, b"upstream failure"

    with pytest.raises(BinanceHTTPError) as exc_info:
        adapter(
            http_get,
            sleeper=delays.append,
            max_attempts=3,
        ).fetch_market_data_with_evidence(
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
        )

    evidence = exc_info.value.acquisition_evidence
    assert len(calls) == 3
    assert delays == [0.5, 1.0]
    assert evidence.status is AcquisitionStatus.FAILED
    assert evidence.request_count == 3
    assert evidence.retry_count == 2
    assert evidence.page_count == 0
    assert evidence.error is not None
    assert evidence.error.http_status == 500
    assert evidence.error.outcome_unknown is True


def test_historical_two_pages_three_requests_one_retry_and_cursor_progress():
    calls = []
    delays = []
    responses = [
        (500, {}, b"{}"),
        (200, {}, json.dumps([kline_row(0, 59999)]).encode()),
        (200, {}, json.dumps([kline_row(60000, 119999)]).encode()),
    ]

    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)

    items, evidence = adapter(
        http_get,
        sleeper=delays.append,
        max_attempts=3,
    ).fetch_historical_market_data_with_evidence(
        symbol="BTCUSDT",
        interval="1m",
        start_time=0,
        end_time=119999,
        page_limit=1,
        max_requests=10,
    )

    assert len(items) == 2
    assert evidence.mode is AcquisitionMode.HISTORICAL
    assert evidence.requested_start == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert evidence.requested_end == datetime(1970, 1, 1, 0, 1, 59, 999000, tzinfo=timezone.utc)
    assert evidence.page_count == 2
    assert evidence.request_count == 3
    assert evidence.retry_count == 1
    assert evidence.last_cursor == 60000
    assert evidence.next_cursor == 120000
    assert evidence.status is AcquisitionStatus.SUCCESS
    assert delays == [0.5]


def test_historical_failure_preserves_successful_progress_as_failed_evidence():
    calls = []
    responses = [
        (200, {}, json.dumps([kline_row(0, 59999)]).encode()),
        (500, {}, b"{}"),
        (500, {}, b"{}"),
        (500, {}, b"{}"),
    ]

    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)

    with pytest.raises(BinanceHTTPError) as exc_info:
        adapter(http_get, sleeper=lambda _: None).fetch_historical_market_data_with_evidence(
            symbol="BTCUSDT",
            interval="1m",
            start_time=0,
            end_time=119999,
            page_limit=1,
            max_requests=10,
        )

    evidence = exc_info.value.acquisition_evidence
    assert len(calls) == 4
    assert evidence.status is AcquisitionStatus.FAILED
    assert evidence.page_count == 1
    assert evidence.request_count == 4
    assert evidence.retry_count == 2
    assert evidence.last_cursor == 0
    assert evidence.next_cursor == 60000
    assert evidence.error is not None
    assert evidence.error.http_status == 500
    assert evidence.error.outcome_unknown is True


def test_metadata_retry_is_evidenced_without_capturing_response_body():
    calls = []
    delays = []
    metadata_payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "isSpotTradingAllowed": True,
            }
        ]
    }
    responses = [
        (500, {"Retry-After": "2"}, b"temporary failure"),
        (200, {}, json.dumps(metadata_payload).encode()),
    ]

    def http_get(url, headers, timeout):
        calls.append(url)
        return responses.pop(0)

    metadata = BinanceSpotInstrumentMetadata(
        http_get=http_get,
        retry_policy=BinanceRetryPolicy(
            max_attempts=3,
            sleeper=delays.append,
        ),
        operation_clock=fixed_operation_clock(),
    )
    record, evidence = metadata.fetch_with_evidence("BTCUSDT")

    assert record.base_asset == "BTC"
    assert record.quote_asset == "USDT"
    assert len(calls) == 2
    assert delays == [2.0]
    assert evidence.request_count == 2
    assert evidence.retry_count == 1
    assert evidence.page_count == 0
    assert evidence.status is AcquisitionStatus.SUCCESS


def test_failed_metadata_exposes_structured_error_without_http_body():
    calls = []

    def http_get(url, headers, timeout):
        calls.append(url)
        return 400, {}, b'{"code":-1121,"msg":"Invalid symbol."}'

    metadata = BinanceSpotInstrumentMetadata(
        http_get=http_get,
        retry_policy=BinanceRetryPolicy(max_attempts=1, sleeper=lambda _: None),
        operation_clock=fixed_operation_clock(),
    )

    with pytest.raises(BinanceAPIError) as exc_info:
        metadata.fetch_with_evidence("BTCUSDT")

    evidence = exc_info.value.acquisition_evidence
    assert len(calls) == 1
    assert evidence.status is AcquisitionStatus.FAILED
    assert evidence.request_count == 1
    assert evidence.retry_count == 0
    assert evidence.error is not None
    assert evidence.error.provider_error_code == -1121
    assert evidence.error.http_status == 400


def test_error_evidence_does_not_store_http_response_body():
    error = AcquisitionErrorEvidence(
        category="HTTP",
        message="request failed",
        http_status=500,
        outcome_unknown=True,
    )
    assert not hasattr(error, "response_body")
