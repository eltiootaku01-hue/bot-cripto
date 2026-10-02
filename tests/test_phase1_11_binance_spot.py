import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot_obrero.acquisition import (
    CanonicalValidationError,
    InstrumentMapper,
    InstrumentMappingAmbiguous,
    InstrumentMappingNotFound,
    InstrumentMappingRule,
)
from bot_obrero.binance_spot import (
    BinanceAPIError,
    BinanceHTTPError,
    BinancePayloadError,
    BinanceRateLimitError,
    BinanceSpotRestAdapter,
    BinanceSpotRestConfig,
    BinanceTransportError,
)
from bot_obrero.market_data import (
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    MarketData,
)


OPEN_TIME_MS = 1790942400000
CLOSE_TIME_MS = OPEN_TIME_MS + 59_999
RECEIVED_CLOSED = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
RECEIVED_OPEN = datetime(2026, 10, 2, 12, 0, 30, tzinfo=timezone.utc)
INSTRUMENT = InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT")


def valid_row(
    *,
    open_value="123.4500",
    high_value="124.0000",
    low_value="123.0000",
    close_value="123.7500",
    volume="0.0100",
    open_time=OPEN_TIME_MS,
    close_time=CLOSE_TIME_MS,
):
    return [
        open_time,
        open_value,
        high_value,
        low_value,
        close_value,
        volume,
        close_time,
        "1.2345000",
        42,
        "0.0050",
        "0.6172500",
        "0",
    ]


def mapping(*rules):
    return InstrumentMapper(
        list(
            rules
            or [
                InstrumentMappingRule(
                    provider="binance",
                    provider_symbol="BTCUSDT",
                    provider_market="SPOT",
                    provider_venue="BINANCE",
                    instrument=INSTRUMENT,
                )
            ]
        )
    )


def adapter(
    getter,
    *,
    clock=lambda: RECEIVED_CLOSED,
    mapper=None,
):
    return BinanceSpotRestAdapter(
        instrument_mapper=mapper or mapping(),
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=getter,
        clock=clock,
    )


def ok_getter(payload, *, capture=None):
    body = json.dumps(payload).encode()
    def getter(url, headers, timeout):
        if capture is not None:
            capture.update(url=url, headers=headers, timeout=timeout)
        return 200, {"Content-Type": "application/json"}, body
    return getter


def test_http_success_calls_the_configured_public_endpoint_and_query_params():
    capture = {}
    item = adapter(ok_getter([valid_row()], capture=capture)).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
        start_time=OPEN_TIME_MS,
        end_time=CLOSE_TIME_MS,
    )
    assert isinstance(item, MarketData)
    assert capture["url"].startswith("https://data-api.binance.vision/api/v3/klines?")
    assert "symbol=BTCUSDT" in capture["url"]
    assert "interval=1m" in capture["url"]
    assert "limit=1" in capture["url"]
    assert f"startTime={OPEN_TIME_MS}" in capture["url"]
    assert f"endTime={CLOSE_TIME_MS}" in capture["url"]
    assert capture["headers"]["Accept"] == "application/json"


def test_binance_kline_is_translated_to_canonical_market_data():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.instrument == INSTRUMENT
    assert item.source.source_id == "binance-spot-rest"
    assert item.source.provider == "binance"
    assert item.source.venue == "BINANCE"
    assert item.payload.start.isoformat().startswith("2026-10-02T")
    assert item.payload.open == Decimal("123.4500")
    assert item.payload.volume == Decimal("0.0100")


def test_response_timestamps_are_milliseconds_and_timezone_aware():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.payload.start.tzinfo is not None
    assert item.payload.end.tzinfo is not None
    assert item.observed_at == item.payload.start


def test_decimal_scale_is_preserved_without_float_conversion():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.payload.open == Decimal("123.4500")
    assert item.payload.open.as_tuple().exponent == -4
    assert item.payload.volume == Decimal("0.0100")
    assert item.payload.volume.as_tuple().exponent == -4


def test_numeric_json_float_is_rejected_instead_of_converted():
    row = valid_row()
    row[1] = 123.45
    with pytest.raises(BinancePayloadError, match="decimal string"):
        adapter(ok_getter([row])).fetch_one(
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
        )


def test_available_at_is_none_and_never_inferred_from_observed_or_received():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.available_at is None
    assert item.received_at == RECEIVED_CLOSED
    with pytest.raises(Exception, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(datetime(2026, 10, 2, 12, 0, 30, tzinfo=timezone.utc))


def test_decision_between_observed_and_received_is_blocked_by_unknown_availability():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    decision = item.observed_at + (item.received_at - item.observed_at) / 2
    assert decision < item.received_at
    with pytest.raises(Exception, match="AVAILABLE_AT_UNKNOWN"):
        item.evidence_at(decision)


def test_closed_candle_becomes_closed_complete_but_not_final():
    item = adapter(
        ok_getter([valid_row()]),
        clock=lambda: RECEIVED_CLOSED,
    ).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert item.payload.candle_state is CandleState.CLOSED
    assert item.payload.completeness is DataCompleteness.COMPLETE
    assert item.completeness is DataCompleteness.UNKNOWN
    assert item.payload.finality.value == "UNKNOWN"


def test_open_candle_becomes_open_partial_without_inventing_finality():
    item = adapter(
        ok_getter([valid_row()]),
        clock=lambda: RECEIVED_OPEN,
    ).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert item.payload.candle_state is CandleState.OPEN
    assert item.payload.completeness is DataCompleteness.PARTIAL
    assert item.payload.finality.value == "UNKNOWN"


def test_quality_is_valid_only_after_strict_structure_and_canonical_validation():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.quality is DataQuality.VALID


def test_http_200_does_not_hide_structurally_invalid_ohlcv():
    with pytest.raises(CanonicalValidationError, match="canonical validation rejected"):
        adapter(
            ok_getter([valid_row(high_value="123.5000")])
        ).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a-list"},
        [],
        [valid_row()[:-1]],
        [valid_row()[0:5] + [True] + valid_row()[6:]],
        [[True] + valid_row()[1:]],
        [valid_row(open_value="not-a-decimal")],
        [valid_row(volume="-0.0100")],
    ],
)
def test_malformed_or_invalid_kline_payloads_are_rejected(payload):
    with pytest.raises((BinancePayloadError, CanonicalValidationError)):
        adapter(ok_getter(payload)).fetch_one(
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
        )


def test_unknown_symbol_mapping_is_rejected():
    with pytest.raises(InstrumentMappingNotFound):
        adapter(ok_getter([valid_row()])).fetch_one(
            symbol="ETHUSDT",
            interval="1m",
            limit=1,
        )


def test_ambiguous_mapping_is_rejected():
    second = InstrumentMappingRule(
        provider="binance",
        provider_symbol="BTCUSDT",
        provider_market="SPOT",
        provider_venue="BINANCE",
        instrument=InstrumentIdentity("other", "BTC/USDT", "SPOT"),
    )
    with pytest.raises(InstrumentMappingAmbiguous):
        adapter(ok_getter([valid_row()]), mapper=mapping(
            InstrumentMappingRule(
                provider="binance",
                provider_symbol="BTCUSDT",
                provider_market="SPOT",
                provider_venue="BINANCE",
                instrument=INSTRUMENT,
            ),
            second,
        )).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)


def test_source_identity_fields_remain_distinct():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.source.source_id != item.source.provider
    assert item.source.source_id == "binance-spot-rest"
    assert item.source.provider == "binance"
    assert item.source.venue == "BINANCE"


@pytest.mark.parametrize("limit", [0, 1001, True])
def test_limit_is_explicitly_bounded_to_binance_documented_range(limit):
    with pytest.raises(ValueError, match="limit"):
        adapter(ok_getter([valid_row()])).build_query(
            symbol="BTCUSDT",
            interval="1m",
            limit=limit,
        )


def test_only_authorized_query_parameters_are_emitted():
    query = adapter(ok_getter([valid_row()])).build_query(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert set(query) == {"symbol", "interval", "limit"}


@pytest.mark.parametrize("status", [403, 430])
def test_waf_or_generic_http_4xx_is_explicitly_rejected(status):
    def getter(url, headers, timeout):
        return status, {"Content-Type": "application/json"}, b'{"unexpected":"body"}'
    with pytest.raises(BinanceHTTPError) as exc_info:
        adapter(getter).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert exc_info.value.status_code == status


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_explicit_and_marks_outcome_unknown(status):
    def getter(url, headers, timeout):
        return status, {"Content-Type": "text/plain"}, b"upstream failure"
    with pytest.raises(BinanceHTTPError) as exc_info:
        adapter(getter).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert exc_info.value.status_code == status
    assert exc_info.value.outcome_unknown is True


@pytest.mark.parametrize("status", [429, 418])
def test_rate_limit_errors_expose_retry_after_and_do_not_auto_retry(status):
    calls = 0
    def getter(url, headers, timeout):
        nonlocal calls
        calls += 1
        return status, {"Retry-After": "7"}, b"not-json"
    with pytest.raises(BinanceRateLimitError) as exc_info:
        adapter(getter).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert exc_info.value.status_code == status
    assert exc_info.value.retry_after_seconds == 7.0
    assert calls == 1


def test_binance_api_error_payload_is_distinguished_from_generic_http_failure():
    def getter(url, headers, timeout):
        body = json.dumps({"code": -1121, "msg": "Invalid symbol."}).encode()
        return 400, {"Content-Type": "application/json"}, body
    with pytest.raises(BinanceAPIError) as exc_info:
        adapter(getter).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert exc_info.value.code == -1121
    assert exc_info.value.http_status == 400
    assert "Invalid symbol." in str(exc_info.value)
    assert "BTCUSDT" in str(exc_info.value)
    assert "1m" in str(exc_info.value)


def test_http_timeout_is_distinguished_as_transport_failure():
    def getter(url, headers, timeout):
        raise TimeoutError("timed out")
    with pytest.raises(BinanceTransportError):
        adapter(getter).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)


def test_api_error_object_on_http_200_is_not_accepted_as_market_data():
    def getter(url, headers, timeout):
        body = json.dumps({"code": -1121, "msg": "Invalid symbol."}).encode()
        return 200, {"Content-Type": "application/json"}, body
    with pytest.raises(BinanceAPIError):
        adapter(getter).fetch_one(symbol="BTCUSDT", interval="1m", limit=1)


def test_legacy_candle_is_not_the_canonical_candle_used_by_the_adapter():
    from bot_obrero import data as legacy
    from bot_obrero import market_data as canonical

    assert canonical.Candle is not legacy.Candle
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert isinstance(item.payload, canonical.Candle)
    assert not isinstance(item.payload, legacy.Candle)


def test_multiple_klines_use_one_http_call_and_preserve_each_candle():
    calls = 0

    def getter(url, headers, timeout):
        nonlocal calls
        calls += 1
        second = valid_row(
            open_time=OPEN_TIME_MS + 60_000,
            close_time=CLOSE_TIME_MS + 60_000,
            open_value="123.7500",
            high_value="125.0000",
            low_value="123.5000",
            close_value="124.0000",
        )
        return 200, {}, json.dumps([valid_row(), second]).encode()

    items = adapter(getter).fetch_market_data(
        symbol="BTCUSDT",
        interval="1m",
        limit=2,
    )
    assert calls == 1
    assert len(items) == 2
    assert items[0].payload.start < items[1].payload.start


def test_adapter_remains_provider_specific_only_at_translation_boundary():
    item = adapter(ok_getter([valid_row()])).fetch_one(
        symbol="BTCUSDT",
        interval="1m",
        limit=1,
    )
    assert item.data_type == "CANDLE"
    assert item.source.provider == "binance"
    assert item.instrument.instrument_id == "btc-usdt-spot"
