from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero import prospective_btc_eth_v2 as v2


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def canonical(value):
    return v2._sha256_bytes(v2._canonical_json(value))


def make_candle(symbol: str, index: int, close: Decimal | None = None) -> v2.Candle:
    start = START + timedelta(hours=index)
    end = start + timedelta(hours=1)
    value = close if close is not None else Decimal("100") + Decimal(index) / Decimal("10")
    return v2.Candle(
        symbol=symbol, start=start, end=end, available_at=end + timedelta(milliseconds=200),
        open=value, high=value * Decimal("1.01"), low=value * Decimal("0.99"),
        close=value, volume=Decimal("10"),
    )


def make_kline_line(symbol: str = "BTCUSDT") -> str:
    start = START
    end = start + timedelta(hours=1)
    market_data = {
        "instrument": {
            "instrument_id": symbol, "symbol": symbol[:3] + "/" + symbol[3:],
            "market": "SPOT", "instrument_type": "CRYPTO_SPOT",
            "base_asset": symbol[:-4], "quote_asset": "USDT",
        },
        "source": {"source_id": "binance-spot-websocket", "provider": "binance", "venue": "BINANCE"},
        "data_type": "CANDLE",
        "observed_at": end.isoformat(),
        "received_at": (end + timedelta(milliseconds=100)).isoformat(),
        "available_at": (end + timedelta(milliseconds=200)).isoformat(),
        "quality": "VALID", "completeness": "COMPLETE",
        "candle": {
            "start": start.isoformat(), "end": end.isoformat(), "timeframe": "1h",
            "open": "100", "high": "102", "low": "99", "close": "101", "volume": "5",
            "candle_state": "CLOSED", "completeness": "COMPLETE", "finality": "FINAL",
        },
    }
    envelope = {
        "schema_version": 1, "run_id": "fixture-run", "symbol": symbol, "sequence": 1,
        "record_sha256": canonical(market_data), "duplicate_exact": False, "market_data": market_data,
    }
    return json.dumps(envelope, sort_keys=True)


def make_quote_line(symbol: str = "BTCUSDT", update_id: int = 123, *, bid: str = "100", ask: str = "101") -> str:
    received = START + timedelta(hours=1, seconds=1)
    payload = {
        "schema": "prospective-book-ticker-v1", "symbol": symbol, "update_id": update_id,
        "bid": bid, "bid_quantity": "2", "ask": ask, "ask_quantity": "3",
        "observed_at": received.isoformat(), "received_at": received.isoformat(),
        "available_at": (received + timedelta(milliseconds=20)).isoformat(),
        "provider_event_time": None, "source": "binance-public:spot-websocket-bookTicker",
        "observation_type": "BINANCE_SPOT_BOOK_TICKER", "venue": "BINANCE", "market": "SPOT",
    }
    envelope = {
        "schema_version": 1, "run_id": "fixture-run", "symbol": symbol, "sequence": 1,
        "record_sha256": canonical(payload), "quote": payload,
    }
    return json.dumps(envelope, sort_keys=True)


def test_parsers_accept_hash_bound_closed_candles_and_spot_quotes():
    candle = v2._parse_candle_line(make_kline_line(), "BTCUSDT")
    quote = v2._parse_quote_line(make_quote_line(), "BTCUSDT")

    assert candle.symbol == "BTCUSDT"
    assert candle.close == Decimal("101")
    assert candle.end == candle.start + timedelta(hours=1)
    assert quote.bid == Decimal("100")
    assert quote.ask == Decimal("101")
    assert quote.update_id == 123


def test_parsers_reject_modified_payload_hashes():
    raw = json.loads(make_quote_line())
    raw["quote"]["bid"] = "99"
    with pytest.raises(v2.ProspectiveV2Error, match="HASH_MISMATCH"):
        v2._parse_quote_line(json.dumps(raw), "BTCUSDT")


def test_parsers_reject_open_or_nonfinal_candles():
    raw = json.loads(make_kline_line())
    raw["market_data"]["candle"]["finality"] = "NOT_FINAL"
    raw["record_sha256"] = canonical(raw["market_data"])
    with pytest.raises(v2.ProspectiveV2Error, match="NOT_FINAL"):
        v2._parse_candle_line(json.dumps(raw), "BTCUSDT")


def test_quote_store_uses_first_fresh_quote_after_decision():
    store = v2.QuoteStore()
    try:
        t0 = START + timedelta(days=1)
        store.add(v2.Quote("BTCUSDT", 1, t0 + timedelta(seconds=1), t0 + timedelta(seconds=1, milliseconds=10), Decimal("100"), Decimal("101")))
        store.commit()
        quote = store.first_at_or_after("BTCUSDT", t0)
        assert quote.update_id == 1
        assert quote.available_at >= t0
        assert (quote.available_at - t0).total_seconds() <= 2
    finally:
        store.close()


def test_quote_store_fails_closed_when_first_available_quote_is_stale():
    store = v2.QuoteStore()
    try:
        t0 = START + timedelta(days=1)
        stale = t0 + timedelta(seconds=3)
        store.add(v2.Quote("ETHUSDT", 1, stale, stale, Decimal("100"), Decimal("101")))
        store.commit()
        with pytest.raises(v2.ProspectiveV2Error, match="NOT_WITHIN_2_SECONDS"):
            store.first_at_or_after("ETHUSDT", t0)
    finally:
        store.close()


def test_quote_store_rejects_same_update_id_with_conflicting_prices():
    store = v2.QuoteStore()
    try:
        t0 = START + timedelta(days=1)
        store.add(v2.Quote("BTCUSDT", 1, t0, t0, Decimal("100"), Decimal("101")))
        with pytest.raises(v2.ProspectiveV2Error, match="CONFLICTING_PRICES"):
            store.add(v2.Quote("BTCUSDT", 1, t0 + timedelta(milliseconds=1), t0 + timedelta(milliseconds=1), Decimal("99"), Decimal("101")))
    finally:
        store.close()


def test_features_and_all_strategy_conditions_are_suffix_invariant():
    candles = {
        symbol: [make_candle(symbol, i, Decimal("100") + Decimal(i) / Decimal("10") + Decimal(str(math.sin(i / 7)))) for i in range(260)]
        for symbol in v2.SYMBOLS
    }
    altered = {symbol: list(rows) for symbol, rows in candles.items()}
    cut = 220
    for symbol in v2.SYMBOLS:
        for i in range(cut, len(altered[symbol])):
            original = altered[symbol][i]
            changed = original.close * Decimal("1.7") + Decimal("11")
            altered[symbol][i] = v2.Candle(
                symbol, original.start, original.end, original.available_at,
                changed, changed * Decimal("1.01"), changed * Decimal("0.99"), changed,
                original.volume + Decimal("100"),
            )
    original_features = {symbol: v2._features(rows) for symbol, rows in candles.items()}
    altered_features = {symbol: v2._features(rows) for symbol, rows in altered.items()}

    for symbol in v2.SYMBOLS:
        for feature_name in original_features[symbol]:
            assert original_features[symbol][feature_name][:cut] == altered_features[symbol][feature_name][:cut]
    for index in range(2, cut):
        for strategy in v2.STRATEGIES:
            assert v2._strategy_conditions(strategy, index, candles, original_features) == v2._strategy_conditions(strategy, index, altered, altered_features)


def test_block_bootstrap_is_deterministic_and_returns_ordered_interval():
    values = [math.sin(index / 15.0) / 100.0 + 0.001 for index in range(365)]
    first = v2._bootstrap_difference(values, seed=20261019, replicates=500)
    second = v2._bootstrap_difference(values, seed=20261019, replicates=500)

    assert first == second
    assert first["daily_observations"] == 365
    assert first["block_length_days"] == 7
    assert first["replicates"] == 500
    assert first["ci95_lower"] <= first["ci95_upper"]


def test_holm_correction_is_monotonic_and_bounded():
    adjusted = v2._holm({"a": 0.001, "b": 0.02, "c": 0.5, "d": 0.03})
    assert all(0 <= value <= 1 for value in adjusted.values())
    ordered = sorted(({"a": 0.001, "b": 0.02, "c": 0.5, "d": 0.03}).items(), key=lambda item: item[1])
    adjusted_ordered = [adjusted[name] for name, _ in ordered]
    assert adjusted_ordered == sorted(adjusted_ordered)
    assert adjusted["a"] >= 0.001


def test_filter_snapshot_is_hash_verified_and_must_precede_t0(tmp_path):
    t0 = START + timedelta(days=30)
    payload = {
        "schema_version": 1,
        "capture_kind": "PUBLIC_BINANCE_SPOT_EXCHANGE_INFO_FILTER_SNAPSHOT",
        "captured_at": (t0 - timedelta(hours=1)).isoformat(),
        "source_url": "https://api.binance.com/api/v3/exchangeInfo",
        "source_response": {"symbols": []},
        "source_response_sha256_canonical": canonical({"symbols": []}),
        "symbols": {
            "BTCUSDT": {"min_qty": "0.00001", "step_size": "0.00001", "max_qty": "9000", "min_notional": "5"},
            "ETHUSDT": {"min_qty": "0.0001", "step_size": "0.0001", "max_qty": "900000", "min_notional": "5"},
        },
    }
    payload["snapshot_sha256"] = canonical(payload)
    path = tmp_path / "filters.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    filters, digest = v2.load_filter_snapshot(path, t0)
    assert set(filters) == set(v2.SYMBOLS)
    assert filters["BTCUSDT"].step_size == Decimal("0.00001")
    assert len(digest) == 64

    payload["symbols"]["BTCUSDT"]["step_size"] = "0.0001"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(v2.ProspectiveV2Error, match="HASH_MISMATCH"):
        v2.load_filter_snapshot(path, t0)


def test_capture_window_rejects_missing_year_instead_of_filling_gaps():
    empty = {symbol: {} for symbol in v2.SYMBOLS}
    t0 = datetime(2027, 1, 1, tzinfo=UTC)
    with pytest.raises(v2.ProspectiveV2Error, match="INCOMPLETE_KLINE_COVERAGE"):
        v2._capture_window(empty, t0, t0 + timedelta(days=365))


def test_load_warmup_snapshot_verifies_hash_and_uses_only_pre_t0_closed_bars(tmp_path):
    t0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    warm_start = t0 - timedelta(hours=v2.WARMUP_HOURS)
    captured_at = warm_start + timedelta(hours=1)
    row = {
        "start": warm_start.isoformat(),
        "end": (warm_start + timedelta(hours=1)).isoformat(),
        "open": "100", "high": "102", "low": "99", "close": "101", "volume": "10",
    }
    payload = {
        "schema_version": 1,
        "capture_kind": "PUBLIC_BINANCE_SPOT_KLINE_WARMUP_ONLY",
        "captured_at": captured_at.isoformat(),
        "t0": t0.isoformat(),
        "warmup_hours_required": v2.WARMUP_HOURS,
        "warmup_start": warm_start.isoformat(),
        "warmup_end_exclusive_expected": t0.isoformat(),
        "symbols": {"BTCUSDT": [row], "ETHUSDT": [row]},
        "source_page_sha256_canonical": {"BTCUSDT": [], "ETHUSDT": []},
        "limitations": "Warm-up only.",
    }
    payload["snapshot_sha256"] = canonical(payload)
    path = tmp_path / "warmup.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    rows, digest = v2.load_warmup_snapshot(path, t0)
    assert set(rows) == set(v2.SYMBOLS)
    assert set(rows["BTCUSDT"]) == {warm_start}
    assert rows["ETHUSDT"][warm_start].available_at == captured_at
    assert len(digest) == 64

    payload["symbols"]["BTCUSDT"][0]["close"] = "999"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(v2.ProspectiveV2Error, match="HASH_MISMATCH"):
        v2.load_warmup_snapshot(path, t0)


def test_preregistered_v2_cli_help_exposes_capture_warmup_period_and_replay():
    parser = v2._parser()
    commands = parser._subparsers._group_actions[0].choices
    assert {"capture-filters", "capture-warmup", "preregister", "capture-period", "replay"} <= set(commands)


def test_quote_parser_rejects_observed_timestamp_after_receive():
    raw = json.loads(make_quote_line())
    raw["quote"]["observed_at"] = (START + timedelta(hours=1, seconds=3)).isoformat()
    raw["quote"]["received_at"] = (START + timedelta(hours=1, seconds=1)).isoformat()
    raw["quote"]["available_at"] = (START + timedelta(hours=1, seconds=4)).isoformat()
    raw["record_sha256"] = canonical(raw["quote"])
    with pytest.raises(v2.ProspectiveV2Error, match="AVAILABILITY_ORDER_INVALID"):
        v2._parse_quote_line(json.dumps(raw), "BTCUSDT")
