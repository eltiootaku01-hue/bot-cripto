import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from bot_obrero.binance_spot import (
    BinanceSpotRestAdapter,
    BinanceSpotRestConfig,
    BinancePayloadError,
    DuplicateKline,
    HistoricalGapDetected,
    InvalidHistoricalRange,
    MaximumRequestsExceeded,
    PaginationStalled,
    UnexpectedPageOrder,
)
from bot_obrero.acquisition import InstrumentMapper, InstrumentMappingRule
from bot_obrero.market_data import DataCompleteness, InstrumentIdentity


OPEN_1 = 1_790_942_400_000
STEP = 60_000
CLOSE_DELTA = 59_999
OPEN_2 = OPEN_1 + STEP
OPEN_3 = OPEN_2 + STEP
OPEN_4 = OPEN_3 + STEP
R1 = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
R2 = datetime(2026, 10, 2, 12, 1, 1, tzinfo=timezone.utc)
INSTRUMENT = InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT", base_asset="BTC", quote_asset="USDT")


def row(open_time, *, close_value="123.7500", volume="0.0100", open_value="123.4500"):
    return [
        open_time,
        open_value,
        "124.0000",
        "123.0000",
        close_value,
        volume,
        open_time + CLOSE_DELTA,
        "1.2345000",
        42,
        "0.0050",
        "0.6172500",
        "0",
    ]


def mapper():
    return InstrumentMapper([
        InstrumentMappingRule(
            provider="binance",
            provider_symbol="BTCUSDT",
            provider_market="SPOT",
            provider_venue="BINANCE",
            instrument=INSTRUMENT,
        )
    ])


def adapter(getter, *, clock=lambda: R1):
    return BinanceSpotRestAdapter(
        instrument_mapper=mapper(),
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=getter,
        clock=clock,
    )


def sequential_getter(pages, captures=None):
    calls = {"n": 0}

    def getter(url, headers, timeout):
        if captures is not None:
            captures.append(url)
        index = calls["n"]
        calls["n"] += 1
        return 200, {}, json.dumps(pages[min(index, len(pages) - 1)]).encode()

    return getter, calls


def test_one_page_returns_canonical_market_data():
    getter, calls = sequential_getter([[row(OPEN_1), row(OPEN_2)]])
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=2,
    )
    assert len(result) == 2
    assert result[0].payload.start < result[1].payload.start
    assert calls["n"] == 1
    assert all(item.completeness is DataCompleteness.UNKNOWN for item in result)


def test_two_pages_advance_from_last_close_plus_one_millisecond():
    captures = []
    getter, calls = sequential_getter(
        [[row(OPEN_1), row(OPEN_2)], [row(OPEN_3)]],
        captures=captures,
    )
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_3,
        page_limit=2,
    )
    assert [item.payload.start for item in result] == [
        datetime.fromtimestamp(OPEN_1 / 1000, tz=timezone.utc),
        datetime.fromtimestamp(OPEN_2 / 1000, tz=timezone.utc),
        datetime.fromtimestamp(OPEN_3 / 1000, tz=timezone.utc),
    ]
    assert calls["n"] == 2
    assert parse_qs(urlparse(captures[0]).query)["startTime"] == [str(OPEN_1)]
    assert parse_qs(urlparse(captures[1]).query)["startTime"] == [str(OPEN_2 + CLOSE_DELTA + 1)]


def test_multiple_pages_preserve_each_page_received_at():
    times = iter([R1, R2, R2])
    getter, _ = sequential_getter([[row(OPEN_1)], [row(OPEN_2)], [row(OPEN_3)]])
    result = adapter(getter, clock=lambda: next(times)).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_3,
        page_limit=1,
        max_requests=3,
    )
    assert result[0].received_at == R1
    assert result[1].received_at == R2
    assert result[2].received_at == R2


def test_duplicate_between_pages_is_rejected():
    getter, _ = sequential_getter(
        [[row(OPEN_1), row(OPEN_2)], [row(OPEN_2), row(OPEN_3)]]
    )
    with pytest.raises(DuplicateKline, match="duplicate historical kline"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_3,
            page_limit=2,
        )


def test_wrong_order_inside_page_is_rejected():
    getter, _ = sequential_getter([[row(OPEN_2), row(OPEN_1)]])
    with pytest.raises(UnexpectedPageOrder, match="not strictly chronological"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_2,
            page_limit=2,
        )


def test_gap_between_returned_klines_is_rejected_without_synthetic_fill():
    getter, _ = sequential_getter([[row(OPEN_1)], [row(OPEN_3)]])
    with pytest.raises(HistoricalGapDetected, match="kline gap detected"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_3,
            page_limit=1,
        )


def test_pagination_stalled_when_provider_returns_an_older_unseen_page():
    getter, _ = sequential_getter([[row(OPEN_2)], [row(OPEN_1)]])
    with pytest.raises(PaginationStalled, match="did not advance"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_3,
            page_limit=1,
        )


def test_maximum_requests_prevents_unbounded_pagination():
    getter, calls = sequential_getter([[row(OPEN_1)], [row(OPEN_2)], [row(OPEN_3)]])
    with pytest.raises(MaximumRequestsExceeded, match="max_requests=2"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_3,
            page_limit=1,
            max_requests=2,
        )
    assert calls["n"] == 2


def test_invalid_range_is_rejected_before_http():
    getter, calls = sequential_getter([[row(OPEN_1)]])
    with pytest.raises(InvalidHistoricalRange, match="start_time"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_2,
            end_time=OPEN_1,
        )
    assert calls["n"] == 0


def test_equal_range_returns_matching_kline_or_empty_without_special_case_error():
    getter, calls = sequential_getter([[row(OPEN_2)]])
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_2,
        end_time=OPEN_2,
        page_limit=1,
    )
    assert len(result) == 1
    assert calls["n"] == 1

    empty_getter, empty_calls = sequential_getter([[]])
    empty = adapter(empty_getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1 + 1,
        end_time=OPEN_1 + 1,
        page_limit=1,
    )
    assert empty == []
    assert empty_calls["n"] == 1


def test_valid_range_with_no_data_returns_empty_result():
    getter, calls = sequential_getter([[]])
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=1000,
    )
    assert result == []
    assert calls["n"] == 1


def test_available_at_remains_unknown_without_explicit_handoff_configuration():
    getter, _ = sequential_getter([[row(OPEN_1)], [row(OPEN_2)]])
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=1,
    )
    assert all(item.available_at is None for item in result)


def test_decimal_precision_is_preserved_across_pages():
    getter, _ = sequential_getter([
        [row(OPEN_1, open_value="123.4500", volume="0.0100")],
        [row(OPEN_2, open_value="123.45000000", volume="0.01000000")],
    ])
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=1,
    )
    assert result[0].payload.open.as_tuple().exponent == -4
    assert result[1].payload.open.as_tuple().exponent == -8
    assert result[0].payload.volume.as_tuple().exponent == -4
    assert result[1].payload.volume.as_tuple().exponent == -8


def test_float_payload_is_still_rejected_by_existing_binance_parser():
    bad = row(OPEN_1)
    bad[1] = 123.45
    getter, _ = sequential_getter([[bad]])
    with pytest.raises(BinancePayloadError, match="decimal string"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_1,
            page_limit=1,
        )


def test_range_order_is_not_based_on_received_at():
    times = iter([R2, R1])
    getter, _ = sequential_getter([[row(OPEN_1)], [row(OPEN_2)]])
    result = adapter(getter, clock=lambda: next(times)).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=1,
    )
    assert result[0].received_at > result[1].received_at
    assert result[0].payload.start < result[1].payload.start


def test_pagination_uses_canonical_candle_only():
    from bot_obrero import data as legacy
    from bot_obrero import market_data as canonical

    getter, _ = sequential_getter([[row(OPEN_1)], [row(OPEN_2)]])
    result = adapter(getter).fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=1,
    )
    assert all(isinstance(item.payload, canonical.Candle) for item in result)
    assert all(not isinstance(item.payload, legacy.Candle) for item in result)


def test_max_page_limit_matches_current_official_binance_limit():
    getter, calls = sequential_getter([[row(OPEN_1)]])
    with pytest.raises(ValueError, match="limit"):
        adapter(getter).fetch_historical_market_data(
            symbol="BTCUSDT",
            interval="1m",
            start_time=OPEN_1,
            end_time=OPEN_1,
            page_limit=1001,
        )
    assert calls["n"] == 0
