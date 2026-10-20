"""Prospective BTC/ETH Economic Replay V2.

This is a research-only replay of already captured public Spot evidence. It never
submits orders. Unknown evidence, missing fresh quotes, changed capture provenance,
or incomplete coverage fail closed as INCOMPLETE / NO_TRADE.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .prospective_capture import validate_manifest as validate_kline_manifest
from .prospective_book_ticker_capture import (
    validate_manifest as validate_bookticker_manifest,
    validate_paired_manifests,
)

SCHEMA_VERSION = 1
SYMBOLS = ("BTCUSDT", "ETHUSDT")
STRATEGIES = (
    "EMA12_26_RSI",
    "EMA24_96_CROSS",
    "DONCHIAN_24_12",
    "RSI_MEANREV_EMA200",
    "BTC_GATED_ETH_TREND",
    "ETH_TSMOM_168H",
    "BTC_ETH_RS_ROTATION_168H",
)
SCENARIOS = {
    "BASE": {"fee": Decimal("0.001"), "slippage_bps": Decimal("5")},
    "STRESS_1": {"fee": Decimal("0.001"), "slippage_bps": Decimal("10")},
    "STRESS_2": {"fee": Decimal("0.002"), "slippage_bps": Decimal("20")},
}
INITIAL_USDT = Decimal("31")
POSITION_FRACTION = Decimal("0.95")
MAX_ENTRIES_30D = 5
MAX_QUOTE_AGE_SECONDS = 2
WARMUP_HOURS = 90 * 24
EVALUATION_HOURS = 365 * 24
ZERO = Decimal("0")
ONE = Decimal("1")


class ProspectiveV2Error(ValueError):
    """Invalid or insufficient prospective research evidence."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json(raw: str | bytes, label: str) -> Any:
    try:
        return json.loads(
            raw,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"NONFINITE_JSON:{token}")),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProspectiveV2Error(f"{label}_JSON_INVALID") from exc


def _dt(value: Any, label: str) -> datetime:
    if type(value) is not str or not value:
        raise ProspectiveV2Error(f"{label}_TIMESTAMP_MISSING")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveV2Error(f"{label}_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveV2Error(f"{label}_TIMESTAMP_NOT_AWARE")
    return parsed.astimezone(timezone.utc)


def _dec(value: Any, label: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if type(value) is not str:
        raise ProspectiveV2Error(f"{label}_MUST_BE_DECIMAL_STRING")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ProspectiveV2Error(f"{label}_DECIMAL_INVALID") from exc
    if not result.is_finite():
        raise ProspectiveV2Error(f"{label}_NOT_FINITE")
    if positive and result <= ZERO:
        raise ProspectiveV2Error(f"{label}_NOT_POSITIVE")
    if nonnegative and result < ZERO:
        raise ProspectiveV2Error(f"{label}_NEGATIVE")
    return result


def _safe_child(root: Path, raw: Any, label: str) -> Path:
    if type(raw) is not str or not raw or "\\" in raw:
        raise ProspectiveV2Error(f"{label}_PATH_INVALID")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(p in {"", ".", ".."} for p in relative.parts):
        raise ProspectiveV2Error(f"{label}_PATH_UNSAFE")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ProspectiveV2Error(f"{label}_SYMLINK_BLOCKED")
    try:
        result = current.resolve(strict=True)
        result.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ProspectiveV2Error(f"{label}_PATH_MISSING_OR_OUTSIDE_ROOT") from exc
    if not result.is_file():
        raise ProspectiveV2Error(f"{label}_FILE_REQUIRED")
    return result


@dataclass(frozen=True)
class Candle:
    symbol: str
    start: datetime
    end: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Quote:
    symbol: str
    update_id: int
    received_at: datetime
    available_at: datetime
    bid: Decimal
    ask: Decimal


@dataclass(frozen=True)
class Filters:
    min_qty: Decimal
    step_size: Decimal
    max_qty: Decimal
    min_notional: Decimal


class QuoteStore:
    """Disk-backed quote index; annual bookTicker captures need not fit in RAM."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS quotes (
                symbol TEXT NOT NULL,
                update_id INTEGER NOT NULL,
                available_ts REAL NOT NULL,
                received_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                bid TEXT NOT NULL,
                ask TEXT NOT NULL,
                PRIMARY KEY(symbol, update_id)
            )"""
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_quotes_time ON quotes(symbol, available_ts, update_id)"
        )
        self.connection.commit()

    def add(self, quote: Quote) -> None:
        existing = self.connection.execute(
            "SELECT bid, ask, available_ts FROM quotes WHERE symbol=? AND update_id=?",
            (quote.symbol, quote.update_id),
        ).fetchone()
        ts = quote.available_at.timestamp()
        if existing:
            if Decimal(existing[0]) != quote.bid or Decimal(existing[1]) != quote.ask:
                raise ProspectiveV2Error("QUOTE_UPDATE_ID_CONFLICTING_PRICES")
            if ts < existing[2]:
                self.connection.execute(
                    """UPDATE quotes SET available_ts=?, received_at=?, available_at=?
                       WHERE symbol=? AND update_id=?""",
                    (ts, quote.received_at.isoformat(), quote.available_at.isoformat(), quote.symbol, quote.update_id),
                )
            return
        self.connection.execute(
            "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                quote.symbol, quote.update_id, ts, quote.received_at.isoformat(),
                quote.available_at.isoformat(), str(quote.bid), str(quote.ask),
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def first_at_or_after(self, symbol: str, instant: datetime, *, max_age_seconds: int = MAX_QUOTE_AGE_SECONDS) -> Quote:
        row = self.connection.execute(
            """SELECT symbol, update_id, received_at, available_at, bid, ask
               FROM quotes WHERE symbol=? AND available_ts>=?
               ORDER BY available_ts, update_id LIMIT 1""",
            (symbol, instant.timestamp()),
        ).fetchone()
        if row is None:
            raise ProspectiveV2Error(f"FRESH_QUOTE_MISSING:{symbol}:{instant.isoformat()}")
        quote = Quote(
            symbol=row[0], update_id=int(row[1]), received_at=_dt(row[2], "quote_received_at"),
            available_at=_dt(row[3], "quote_available_at"), bid=Decimal(row[4]), ask=Decimal(row[5]),
        )
        delay = (quote.available_at - instant).total_seconds()
        if delay < 0 or delay > max_age_seconds:
            raise ProspectiveV2Error(f"FRESH_QUOTE_NOT_WITHIN_{max_age_seconds}_SECONDS:{symbol}:{instant.isoformat()}")
        if quote.received_at < instant or quote.available_at < quote.received_at:
            raise ProspectiveV2Error("QUOTE_TEMPORAL_ORDER_INVALID")
        return quote

    def quote_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])

    def close(self) -> None:
        self.connection.close()


def _validate_source_provenance(pair: Mapping[str, Any]) -> str:
    provenance = pair.get("source_provenance")
    if not isinstance(provenance, Mapping):
        raise ProspectiveV2Error("CAPTURE_SOURCE_PROVENANCE_MISSING")
    if provenance.get("status") != "VALID" or provenance.get("branch") != "main":
        raise ProspectiveV2Error("CAPTURE_SOURCE_PROVENANCE_NOT_VALID")
    if provenance.get("source_status_entries") != []:
        raise ProspectiveV2Error("CAPTURE_SOURCE_TREE_NOT_CLEAN")
    shas = [provenance.get(key) for key in ("git_head_sha", "origin_main_sha", "live_main_sha")]
    if any(type(value) is not str or len(value) not in {40, 64} for value in shas):
        raise ProspectiveV2Error("CAPTURE_SOURCE_SHA_INVALID")
    if len(set(shas)) != 1:
        raise ProspectiveV2Error("CAPTURE_SOURCE_SHA_MISMATCH")
    return shas[0]


def _parse_candle_line(raw_line: str, expected_symbol: str) -> Candle:
    envelope = _strict_json(raw_line, "KLINE_RECORD")
    if not isinstance(envelope, Mapping) or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1:
        raise ProspectiveV2Error("KLINE_ENVELOPE_SCHEMA_INVALID")
    data = envelope.get("market_data")
    if not isinstance(data, Mapping) or _sha256_bytes(_canonical_json(data)) != envelope.get("record_sha256"):
        raise ProspectiveV2Error("KLINE_RECORD_HASH_MISMATCH")
    if str(envelope.get("symbol", "")).replace("/", "") != expected_symbol:
        raise ProspectiveV2Error("KLINE_SYMBOL_MISMATCH")
    instrument, source, candle_raw = data.get("instrument"), data.get("source"), data.get("candle")
    if not isinstance(instrument, Mapping) or not isinstance(source, Mapping) or not isinstance(candle_raw, Mapping):
        raise ProspectiveV2Error("KLINE_RECORD_SHAPE_INVALID")
    if str(instrument.get("symbol", "")).replace("/", "") != expected_symbol:
        raise ProspectiveV2Error("KLINE_INSTRUMENT_MISMATCH")
    if instrument.get("market") != "SPOT" or source.get("provider") != "binance" or source.get("venue") != "BINANCE":
        raise ProspectiveV2Error("KLINE_SOURCE_OR_MARKET_INVALID")
    if candle_raw.get("timeframe") != "1h" or candle_raw.get("candle_state") != "CLOSED" or candle_raw.get("completeness") != "COMPLETE" or candle_raw.get("finality") != "FINAL":
        raise ProspectiveV2Error("KLINE_NOT_FINAL_CLOSED_1H")
    start, end = _dt(candle_raw.get("start"), "candle_start"), _dt(candle_raw.get("end"), "candle_end")
    observed, received, available = (
        _dt(data.get("observed_at"), "candle_observed_at"),
        _dt(data.get("received_at"), "candle_received_at"),
        _dt(data.get("available_at"), "candle_available_at"),
    )
    if end - start != timedelta(hours=1) or start.minute or start.second or start.microsecond:
        raise ProspectiveV2Error("KLINE_HOURLY_ALIGNMENT_INVALID")
    if observed > received or received > available or available < end:
        raise ProspectiveV2Error("KLINE_AVAILABILITY_ORDER_INVALID")
    values = {
        key: _dec(candle_raw.get(key), f"candle_{key}", positive=(key in {"open", "high", "low", "close"}), nonnegative=(key == "volume"))
        for key in ("open", "high", "low", "close", "volume")
    }
    if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
        raise ProspectiveV2Error("KLINE_OHLC_INCONSISTENT")
    return Candle(expected_symbol, start, end, available, **values)


def _parse_quote_line(raw_line: str, expected_symbol: str) -> Quote:
    envelope = _strict_json(raw_line, "QUOTE_RECORD")
    if not isinstance(envelope, Mapping) or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1:
        raise ProspectiveV2Error("QUOTE_ENVELOPE_SCHEMA_INVALID")
    payload = envelope.get("quote")
    if not isinstance(payload, Mapping) or _sha256_bytes(_canonical_json(payload)) != envelope.get("record_sha256"):
        raise ProspectiveV2Error("QUOTE_RECORD_HASH_MISMATCH")
    if payload.get("schema") != "prospective-book-ticker-v1" or payload.get("symbol") != expected_symbol:
        raise ProspectiveV2Error("QUOTE_SYMBOL_OR_SCHEMA_INVALID")
    if payload.get("venue") != "BINANCE" or payload.get("market") != "SPOT":
        raise ProspectiveV2Error("QUOTE_SOURCE_OR_MARKET_INVALID")
    if payload.get("source") != "binance-public:spot-websocket-bookTicker" or payload.get("observation_type") != "BINANCE_SPOT_BOOK_TICKER":
        raise ProspectiveV2Error("QUOTE_SOURCE_TYPE_INVALID")
    update_id = payload.get("update_id")
    if type(update_id) is not int or update_id < 0:
        raise ProspectiveV2Error("QUOTE_UPDATE_ID_INVALID")
    received, available = _dt(payload.get("received_at"), "quote_received_at"), _dt(payload.get("available_at"), "quote_available_at")
    observed = _dt(payload.get("observed_at"), "quote_observed_at")
    if available < received:
        raise ProspectiveV2Error("QUOTE_AVAILABILITY_ORDER_INVALID")
    bid, ask = _dec(payload.get("bid"), "quote_bid", positive=True), _dec(payload.get("ask"), "quote_ask", positive=True)
    if bid > ask:
        raise ProspectiveV2Error("QUOTE_MARKET_CROSSED")
    return Quote(expected_symbol, update_id, received, available, bid, ask)


def _load_pair_evidence(root: Path, store: QuoteStore) -> tuple[dict[str, dict[datetime, Candle]], dict[str, Any]]:
    pair_paths = sorted(root.rglob("pair-manifest.json"))
    if not pair_paths:
        raise ProspectiveV2Error("NO_PAIRED_CAPTURE_MANIFESTS_FOUND")
    candles: dict[str, dict[datetime, Candle]] = {symbol: {} for symbol in SYMBOLS}
    provenance_shas: set[str] = set()
    manifest_evidence: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for pair_path in pair_paths:
        pair_dir = pair_path.parent.resolve(strict=True)
        pair = _strict_json(pair_path.read_bytes(), "PAIR_MANIFEST")
        if not isinstance(pair, Mapping) or type(pair.get("schema_version")) is not int or pair.get("schema_version") != 1:
            raise ProspectiveV2Error("PAIR_MANIFEST_SCHEMA_INVALID")
        if pair.get("status") != "VALID":
            raise ProspectiveV2Error("PAIR_CAPTURE_NOT_VALID")
        configuration = pair.get("configuration")
        if not isinstance(configuration, Mapping) or configuration.get("trading_enabled") is not False:
            raise ProspectiveV2Error("PAIR_CONFIGURATION_OR_TRADING_FLAG_INVALID")
        if configuration.get("capture_kind") != "PROSPECTIVE_BINANCE_SPOT_KLINE_AND_BOOK_TICKER_PAIR" or configuration.get("symbols") != list(SYMBOLS):
            raise ProspectiveV2Error("PAIR_CONFIGURATION_CONTRACT_INVALID")
        if _sha256_bytes(_canonical_json(configuration)) != pair.get("configuration_sha256"):
            raise ProspectiveV2Error("PAIR_CONFIGURATION_HASH_MISMATCH")
        sha = _validate_source_provenance(pair)
        provenance_shas.add(sha)
        kline_manifest = _safe_child(pair_dir, pair.get("kline_manifest"), "KLINE_MANIFEST")
        quote_manifest = _safe_child(pair_dir, pair.get("bookticker_manifest"), "BOOKTICKER_MANIFEST")
        kline_validation = validate_kline_manifest(kline_manifest)
        quote_validation = validate_bookticker_manifest(quote_manifest)
        pair_validation = validate_paired_manifests(
            kline_manifest_path=kline_manifest,
            bookticker_manifest_path=quote_manifest,
            minimum_overlap_seconds=int(configuration.get("minimum_overlap_seconds", 86400)),
        )
        if kline_validation.get("status") != "VALID" or quote_validation.get("status") != "VALID" or pair_validation.get("status") != "VALID":
            raise ProspectiveV2Error("INDEPENDENT_CAPTURE_VALIDATION_FAILED")
        manifest_evidence.append({
            "pair_manifest": str(pair_path.relative_to(root)),
            "pair_manifest_sha256": _sha256_bytes(pair_path.read_bytes()),
            "kline_manifest_sha256": _sha256_bytes(kline_manifest.read_bytes()),
            "bookticker_manifest_sha256": _sha256_bytes(quote_manifest.read_bytes()),
            "source_sha": sha,
            "overlap_seconds": pair_validation.get("overlap_seconds"),
        })
        for symbol in SYMBOLS:
            candle_path = kline_manifest.parent / f"{symbol}.jsonl"
            if not candle_path.is_file() or candle_path.is_symlink():
                raise ProspectiveV2Error(f"KLINE_DATA_FILE_MISSING:{symbol}")
            hashes[str(candle_path.relative_to(root))] = _file_sha256(candle_path)
            with candle_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        raise ProspectiveV2Error("KLINE_EMPTY_JSONL_LINE")
                    candle = _parse_candle_line(line, symbol)
                    previous = candles[symbol].get(candle.start)
                    if previous:
                        same = (previous.open, previous.high, previous.low, previous.close, previous.volume, previous.end) == (candle.open, candle.high, candle.low, candle.close, candle.volume, candle.end)
                        if not same:
                            raise ProspectiveV2Error("OVERLAPPING_CANDLE_CONFLICT")
                        if candle.available_at < previous.available_at:
                            candles[symbol][candle.start] = candle
                    else:
                        candles[symbol][candle.start] = candle
            quote_path = quote_manifest.parent / f"{symbol}-bookTicker.jsonl"
            if not quote_path.is_file() or quote_path.is_symlink():
                raise ProspectiveV2Error(f"QUOTE_DATA_FILE_MISSING:{symbol}")
            hashes[str(quote_path.relative_to(root))] = _file_sha256(quote_path)
            with quote_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        raise ProspectiveV2Error("QUOTE_EMPTY_JSONL_LINE")
                    store.add(_parse_quote_line(line, symbol))
        store.commit()
    if len(provenance_shas) != 1:
        raise ProspectiveV2Error("CAPTURE_SOURCE_CHANGED_DURING_EVALUATION")
    return candles, {
        "capture_count": len(pair_paths),
        "capture_manifests": manifest_evidence,
        "source_sha": next(iter(provenance_shas)),
        "input_file_sha256": hashes,
        "raw_file_count": len(hashes),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_filter_snapshot(output_path: Path) -> dict[str, Any]:
    """Fetch public exchangeInfo and persist the exact response plus normalized filters."""
    if output_path.exists():
        raise ProspectiveV2Error("FILTER_SNAPSHOT_REFUSES_OVERWRITE")
    query = urllib.parse.urlencode({"symbols": json.dumps(list(SYMBOLS), separators=(",", ":"))})
    url = "https://api.binance.com/api/v3/exchangeInfo?" + query
    request = urllib.request.Request(url, headers={"User-Agent": "bot-cripto-prospective-v2/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_bytes = response.read()
    except Exception as exc:
        raise ProspectiveV2Error(f"PUBLIC_EXCHANGE_INFO_FETCH_FAILED:{type(exc).__name__}") from exc
    source_response = _strict_json(raw_bytes, "EXCHANGE_INFO")
    if not isinstance(source_response, Mapping) or not isinstance(source_response.get("symbols"), list):
        raise ProspectiveV2Error("EXCHANGE_INFO_RESPONSE_INVALID")
    by_symbol = {item.get("symbol"): item for item in source_response["symbols"] if isinstance(item, Mapping)}
    filters: dict[str, Any] = {}
    for symbol in SYMBOLS:
        item = by_symbol.get(symbol)
        if not isinstance(item, Mapping) or item.get("status") != "TRADING" or item.get("isSpotTradingAllowed") is False:
            raise ProspectiveV2Error(f"SYMBOL_NOT_TRADING_SPOT:{symbol}")
        raw_filters = item.get("filters")
        if not isinstance(raw_filters, list):
            raise ProspectiveV2Error(f"EXCHANGE_FILTERS_MISSING:{symbol}")
        mapping = {row.get("filterType"): row for row in raw_filters if isinstance(row, Mapping)}
        lot = mapping.get("MARKET_LOT_SIZE")
        if not isinstance(lot, Mapping) or _dec(str(lot.get("stepSize", "0")), "market_step", nonnegative=True) == ZERO:
            lot = mapping.get("LOT_SIZE")
        notional = mapping.get("NOTIONAL") or mapping.get("MIN_NOTIONAL")
        if not isinstance(lot, Mapping) or not isinstance(notional, Mapping):
            raise ProspectiveV2Error(f"REQUIRED_SPOT_FILTER_MISSING:{symbol}")
        min_notional_raw = notional.get("minNotional")
        if type(min_notional_raw) is not str:
            raise ProspectiveV2Error(f"MIN_NOTIONAL_INVALID:{symbol}")
        filters[symbol] = {
            "min_qty": str(lot.get("minQty")),
            "step_size": str(lot.get("stepSize")),
            "max_qty": str(lot.get("maxQty")),
            "min_notional": min_notional_raw,
            "lot_filter_type": lot.get("filterType"),
            "notional_filter_type": notional.get("filterType"),
        }
        for field in ("min_qty", "step_size", "max_qty", "min_notional"):
            _dec(filters[symbol][field], f"{symbol}_{field}", positive=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "capture_kind": "PUBLIC_BINANCE_SPOT_EXCHANGE_INFO_FILTER_SNAPSHOT",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "source_response": source_response,
        "source_response_sha256_canonical": _sha256_bytes(_canonical_json(source_response)),
        "symbols": filters,
    }
    payload["snapshot_sha256"] = _sha256_bytes(_canonical_json(payload))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return {"status": "VALID", "path": str(output_path), "snapshot_sha256": payload["snapshot_sha256"], "captured_at": payload["captured_at"]}


def load_filter_snapshot(path: Path, t0: datetime) -> tuple[dict[str, Filters], str]:
    raw = path.read_bytes()
    payload = _strict_json(raw, "FILTER_SNAPSHOT")
    if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != SCHEMA_VERSION:
        raise ProspectiveV2Error("FILTER_SNAPSHOT_SCHEMA_INVALID")
    supplied_sha = payload.get("snapshot_sha256")
    canonical_payload = dict(payload)
    canonical_payload.pop("snapshot_sha256", None)
    if supplied_sha != _sha256_bytes(_canonical_json(canonical_payload)):
        raise ProspectiveV2Error("FILTER_SNAPSHOT_HASH_MISMATCH")
    if payload.get("capture_kind") != "PUBLIC_BINANCE_SPOT_EXCHANGE_INFO_FILTER_SNAPSHOT":
        raise ProspectiveV2Error("FILTER_SNAPSHOT_KIND_INVALID")
    if _dt(payload.get("captured_at"), "filter_snapshot_captured_at") >= t0:
        raise ProspectiveV2Error("FILTER_SNAPSHOT_MUST_BE_FROZEN_BEFORE_T0")
    symbol_values = payload.get("symbols")
    if not isinstance(symbol_values, Mapping) or set(symbol_values) != set(SYMBOLS):
        raise ProspectiveV2Error("FILTER_SNAPSHOT_SYMBOL_SET_INVALID")
    result: dict[str, Filters] = {}
    for symbol in SYMBOLS:
        row = symbol_values[symbol]
        if not isinstance(row, Mapping):
            raise ProspectiveV2Error("FILTER_ROW_INVALID")
        result[symbol] = Filters(
            min_qty=_dec(row.get("min_qty"), f"{symbol}_min_qty", positive=True),
            step_size=_dec(row.get("step_size"), f"{symbol}_step_size", positive=True),
            max_qty=_dec(row.get("max_qty"), f"{symbol}_max_qty", positive=True),
            min_notional=_dec(row.get("min_notional"), f"{symbol}_min_notional", positive=True),
        )
        if result[symbol].min_qty > result[symbol].max_qty:
            raise ProspectiveV2Error("FILTER_QUANTITY_BOUNDS_INVALID")
    return result, _sha256_bytes(raw)




def capture_warmup_snapshot(t0: datetime, output_path: Path) -> dict[str, Any]:
    """Freeze available public hourly candles for indicator warm-up, excluded from scoring."""
    if output_path.exists():
        raise ProspectiveV2Error("WARMUP_SNAPSHOT_REFUSES_OVERWRITE")
    if t0.minute or t0.second or t0.microsecond:
        raise ProspectiveV2Error("T0_MUST_ALIGN_TO_UTC_HOUR")
    captured_at = datetime.now(timezone.utc)
    if t0 <= captured_at:
        raise ProspectiveV2Error("T0_MUST_BE_IN_THE_FUTURE_FOR_WARMUP_CAPTURE")
    warm_start = t0 - timedelta(hours=WARMUP_HOURS)
    # Do not query the currently open hour. The capture-period collector can
    # fill any remaining pre-T0 bars from live observations before scoring starts.
    current_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    end_exclusive = min(t0 - timedelta(hours=1), current_hour)
    end_ms = int(end_exclusive.timestamp() * 1000) - 1
    start_ms = int(warm_start.timestamp() * 1000)
    if end_ms <= start_ms:
        raise ProspectiveV2Error("WARMUP_WINDOW_HAS_NO_CLOSED_CANDLES")

    rows_by_symbol: dict[str, list[dict[str, str]]] = {}
    page_hashes: dict[str, list[str]] = {}
    for symbol in SYMBOLS:
        symbol_rows: list[dict[str, str]] = []
        hashes: list[str] = []
        cursor = start_ms
        while cursor <= end_ms and len(symbol_rows) < WARMUP_HOURS:
            query = urllib.parse.urlencode({
                "symbol": symbol, "interval": "1h", "startTime": cursor,
                "endTime": end_ms, "limit": min(1000, WARMUP_HOURS - len(symbol_rows)),
            })
            url = "https://api.binance.com/api/v3/klines?" + query
            request = urllib.request.Request(url, headers={"User-Agent": "bot-cripto-prospective-v2/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw_bytes = response.read()
            except Exception as exc:
                raise ProspectiveV2Error(f"WARMUP_PUBLIC_KLINES_FETCH_FAILED:{symbol}:{type(exc).__name__}") from exc
            page = _strict_json(raw_bytes, "WARMUP_KLINES")
            if not isinstance(page, list):
                raise ProspectiveV2Error("WARMUP_KLINES_RESPONSE_INVALID")
            hashes.append(_sha256_bytes(_canonical_json(page)))
            if not page:
                break
            last_open: int | None = None
            for item in page:
                if not isinstance(item, list) or len(item) < 6:
                    raise ProspectiveV2Error("WARMUP_KLINE_ROW_INVALID")
                opened = item[0]
                closed = item[6] if len(item) > 6 else opened + 3_599_999
                if type(opened) is not int or type(closed) is not int or opened < start_ms or opened > end_ms:
                    raise ProspectiveV2Error("WARMUP_KLINE_TIMESTAMP_INVALID")
                if closed >= int(captured_at.timestamp() * 1000):
                    continue
                stamp = datetime.fromtimestamp(opened / 1000, timezone.utc)
                if stamp.minute or stamp.second or stamp.microsecond:
                    raise ProspectiveV2Error("WARMUP_KLINE_NOT_HOURLY_ALIGNED")
                if symbol_rows and _dt(symbol_rows[-1]["start"], "warmup_start") + timedelta(hours=1) != stamp:
                    raise ProspectiveV2Error("WARMUP_KLINE_CHRONOLOGY_GAP")
                candle = {
                    "start": stamp.isoformat(),
                    "end": (stamp + timedelta(hours=1)).isoformat(),
                    "open": str(item[1]), "high": str(item[2]), "low": str(item[3]),
                    "close": str(item[4]), "volume": str(item[5]),
                }
                values = {key: _dec(candle[key], f"warmup_{key}", positive=(key in {"open", "high", "low", "close"}), nonnegative=(key == "volume")) for key in ("open", "high", "low", "close", "volume")}
                if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
                    raise ProspectiveV2Error("WARMUP_KLINE_OHLC_INCONSISTENT")
                symbol_rows.append(candle)
                last_open = opened
            if last_open is None:
                break
            next_cursor = last_open + 3_600_000
            if next_cursor <= cursor:
                raise ProspectiveV2Error("WARMUP_PAGINATION_DID_NOT_ADVANCE")
            cursor = next_cursor
            if len(page) < 1000:
                break
        if not symbol_rows:
            raise ProspectiveV2Error(f"WARMUP_HISTORY_EMPTY:{symbol}")
        if len(symbol_rows) > WARMUP_HOURS:
            symbol_rows = symbol_rows[:WARMUP_HOURS]
        if _dt(symbol_rows[0]["start"], "warmup_first_start") != warm_start:
            raise ProspectiveV2Error(f"WARMUP_DOES_NOT_START_AT_T0_MINUS_90_DAYS:{symbol}")
        rows_by_symbol[symbol] = symbol_rows
        page_hashes[symbol] = hashes

    if len(rows_by_symbol["BTCUSDT"]) != len(rows_by_symbol["ETHUSDT"]):
        raise ProspectiveV2Error("WARMUP_PAIR_BAR_COUNTS_DIFFER")
    for btc, eth in zip(rows_by_symbol["BTCUSDT"], rows_by_symbol["ETHUSDT"]):
        if btc["start"] != eth["start"]:
            raise ProspectiveV2Error("WARMUP_PAIR_TIMESTAMPS_NOT_ALIGNED")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "capture_kind": "PUBLIC_BINANCE_SPOT_KLINE_WARMUP_ONLY",
        "captured_at": captured_at.isoformat(),
        "t0": t0.isoformat(),
        "warmup_hours_required": WARMUP_HOURS,
        "warmup_start": warm_start.isoformat(),
        "warmup_end_exclusive_expected": t0.isoformat(),
        "symbols": rows_by_symbol,
        "source_page_sha256_canonical": page_hashes,
        "limitations": "Historical REST candles are warm-up only, excluded from all economic metrics, and are not evidence of historical point-in-time availability.",
    }
    payload["snapshot_sha256"] = _sha256_bytes(_canonical_json(payload))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return {
        "status": "VALID",
        "path": str(output_path),
        "snapshot_sha256": payload["snapshot_sha256"],
        "captured_at": payload["captured_at"],
        "t0": payload["t0"],
        "bars_per_symbol": {symbol: len(rows) for symbol, rows in rows_by_symbol.items()},
    }


def load_warmup_snapshot(path: Path, t0: datetime) -> tuple[dict[str, dict[datetime, Candle]], str]:
    raw = path.read_bytes()
    payload = _strict_json(raw, "WARMUP_SNAPSHOT")
    if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != SCHEMA_VERSION:
        raise ProspectiveV2Error("WARMUP_SNAPSHOT_SCHEMA_INVALID")
    supplied_sha = payload.get("snapshot_sha256")
    canonical_payload = dict(payload)
    canonical_payload.pop("snapshot_sha256", None)
    if supplied_sha != _sha256_bytes(_canonical_json(canonical_payload)):
        raise ProspectiveV2Error("WARMUP_SNAPSHOT_HASH_MISMATCH")
    if payload.get("capture_kind") != "PUBLIC_BINANCE_SPOT_KLINE_WARMUP_ONLY":
        raise ProspectiveV2Error("WARMUP_SNAPSHOT_KIND_INVALID")
    if _dt(payload.get("t0"), "warmup_t0") != t0:
        raise ProspectiveV2Error("WARMUP_T0_MISMATCH")
    captured_at = _dt(payload.get("captured_at"), "warmup_captured_at")
    if captured_at >= t0:
        raise ProspectiveV2Error("WARMUP_MUST_BE_CAPTURED_BEFORE_T0")
    symbol_rows = payload.get("symbols")
    if not isinstance(symbol_rows, Mapping) or set(symbol_rows) != set(SYMBOLS):
        raise ProspectiveV2Error("WARMUP_SYMBOL_SET_INVALID")
    result: dict[str, dict[datetime, Candle]] = {symbol: {} for symbol in SYMBOLS}
    warm_start = t0 - timedelta(hours=WARMUP_HOURS)
    for symbol in SYMBOLS:
        rows = symbol_rows[symbol]
        if not isinstance(rows, list) or not rows or len(rows) > WARMUP_HOURS:
            raise ProspectiveV2Error(f"WARMUP_BAR_COUNT_INVALID:{symbol}")
        previous: datetime | None = None
        for row in rows:
            if not isinstance(row, Mapping):
                raise ProspectiveV2Error("WARMUP_BAR_SHAPE_INVALID")
            start, end = _dt(row.get("start"), "warmup_start"), _dt(row.get("end"), "warmup_end")
            if end - start != timedelta(hours=1) or start.minute or start.second or start.microsecond:
                raise ProspectiveV2Error("WARMUP_BAR_ALIGNMENT_INVALID")
            if start < warm_start or start >= t0 or end > captured_at:
                raise ProspectiveV2Error("WARMUP_BAR_OUTSIDE_PRE_T0_CAPTURE")
            if previous is None and start != warm_start:
                raise ProspectiveV2Error(f"WARMUP_FIRST_BAR_NOT_T0_MINUS_90_DAYS:{symbol}")
            if previous is not None and start != previous + timedelta(hours=1):
                raise ProspectiveV2Error(f"WARMUP_INTERNAL_GAP:{symbol}")
            values = {
                key: _dec(row.get(key), f"warmup_{key}", positive=(key in {"open", "high", "low", "close"}), nonnegative=(key == "volume"))
                for key in ("open", "high", "low", "close", "volume")
            }
            if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
                raise ProspectiveV2Error("WARMUP_OHLC_INCONSISTENT")
            result[symbol][start] = Candle(symbol, start, end, captured_at, **values)
            previous = start
    btc_starts, eth_starts = set(result["BTCUSDT"]), set(result["ETHUSDT"])
    if btc_starts != eth_starts:
        raise ProspectiveV2Error("WARMUP_PAIR_CHRONOLOGY_MISMATCH")
    return result, _sha256_bytes(raw)


def _code_fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = (
        "bot_obrero/prospective_btc_eth_v2.py",
        "bot_obrero/prospective_capture_pair.py",
        "bot_obrero/prospective_capture.py",
        "bot_obrero/prospective_book_ticker_capture.py",
        "docs/research/BTC_ETH_STRATEGIES_V2_PROTOCOL.md",
    )
    return {name: _file_sha256(root / name) for name in paths}


def create_preregistration(
    *,
    t0: datetime,
    filters_path: Path,
    warmup_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ProspectiveV2Error("PREREGISTRATION_REFUSES_OVERWRITE")
    if t0.minute or t0.second or t0.microsecond or t0 <= datetime.now(timezone.utc):
        raise ProspectiveV2Error("PREREGISTRATION_T0_MUST_BE_FUTURE_UTC_HOUR")
    _, filter_sha = load_filter_snapshot(filters_path, t0)
    _, warmup_sha = load_warmup_snapshot(warmup_path, t0)
    root = Path(__file__).resolve().parents[1]
    try:
        relative_output = output_path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ProspectiveV2Error("PREREGISTRATION_FILE_MUST_BE_INSIDE_REPOSITORY") from exc
    if relative_output.startswith("../") or relative_output.startswith(".git/"):
        raise ProspectiveV2Error("PREREGISTRATION_PATH_UNSAFE")
    created_at = datetime.now(timezone.utc)
    if created_at >= t0:
        raise ProspectiveV2Error("PREREGISTRATION_MUST_PRECEDE_T0")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "study": "BTC_ETH_PROSPECTIVE_STRATEGIES_V2",
        "created_at": created_at.isoformat(),
        "t0": t0.isoformat(),
        "t1_exclusive": (t0 + timedelta(days=365)).isoformat(),
        "registration_path": relative_output,
        "filters_snapshot_sha256": filter_sha,
        "warmup_snapshot_sha256": warmup_sha,
        "code_sha256": _code_fingerprints(),
        "strategies": list(STRATEGIES),
        "scenarios": {name: {"fee_rate": str(value["fee"]), "slippage_bps_per_side": str(value["slippage_bps"]), "spread_source": "OBSERVED_BID_ASK"} for name, value in SCENARIOS.items()},
        "bootstrap": {"replicates": 10_000, "block_length_days": 7, "seed_base": 20261019, "adjustment": "HOLM_FAMILYWISE_5_PERCENT"},
        "rules": {
            "capital_usdt": "31",
            "position_fraction": "0.95",
            "maximum_entries_per_rolling_30_days": MAX_ENTRIES_30D,
            "maximum_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
            "spot_only": True,
            "live_execution": False,
            "promotion_decision": "CANDIDATE_FOR_SECOND_INDEPENDENT_REVIEW_ONLY",
        },
    }
    payload["registration_sha256"] = _sha256_bytes(_canonical_json(payload))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return {
        "status": "VALID",
        "path": str(output_path),
        "registration_sha256": payload["registration_sha256"],
        "t0": payload["t0"],
        "t1_exclusive": payload["t1_exclusive"],
        "instruction": "Commit this exact registration file to main before T0; do not start or score the evaluation before that commit.",
    }


def _validate_preregistration(
    path: Path, t0: datetime, filters_sha: str, warmup_sha: str, capture_source_sha: str,
) -> dict[str, Any]:
    payload = _strict_json(path.read_bytes(), "PREREGISTRATION")
    if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != SCHEMA_VERSION:
        raise ProspectiveV2Error("PREREGISTRATION_SCHEMA_INVALID")
    supplied_sha = payload.get("registration_sha256")
    body = dict(payload)
    body.pop("registration_sha256", None)
    if supplied_sha != _sha256_bytes(_canonical_json(body)):
        raise ProspectiveV2Error("PREREGISTRATION_HASH_MISMATCH")
    if payload.get("study") != "BTC_ETH_PROSPECTIVE_STRATEGIES_V2":
        raise ProspectiveV2Error("PREREGISTRATION_STUDY_INVALID")
    if _dt(payload.get("t0"), "registration_t0") != t0 or _dt(payload.get("t1_exclusive"), "registration_t1") != t0 + timedelta(days=365):
        raise ProspectiveV2Error("PREREGISTRATION_WINDOW_MISMATCH")
    if _dt(payload.get("created_at"), "registration_created_at") >= t0:
        raise ProspectiveV2Error("PREREGISTRATION_CREATED_AFTER_T0")
    if payload.get("filters_snapshot_sha256") != filters_sha or payload.get("warmup_snapshot_sha256") != warmup_sha:
        raise ProspectiveV2Error("PREREGISTRATION_INPUT_HASH_MISMATCH")
    if payload.get("code_sha256") != _code_fingerprints():
        raise ProspectiveV2Error("PREREGISTERED_CODE_FINGERPRINT_MISMATCH")
    if payload.get("strategies") != list(STRATEGIES):
        raise ProspectiveV2Error("PREREGISTERED_STRATEGY_SET_MISMATCH")
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=False,
    )
    if current_head.returncode != 0 or current_head.stdout.strip().lower() != capture_source_sha:
        raise ProspectiveV2Error("CAPTURE_SOURCE_SHA_DIFFERS_FROM_CURRENT_CHECKOUT")
    registration_path = payload.get("registration_path")
    if type(registration_path) is not str or Path(registration_path).as_posix() != registration_path:
        raise ProspectiveV2Error("PREREGISTRATION_GIT_PATH_INVALID")
    committed = subprocess.run(
        ["git", "cat-file", "-e", f"{capture_source_sha}:{registration_path}"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
    )
    if committed.returncode != 0:
        raise ProspectiveV2Error("PREREGISTRATION_NOT_IN_CAPTURE_SOURCE_COMMIT")
    commit_time = subprocess.run(
        ["git", "show", "-s", "--format=%ct", capture_source_sha],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
    )
    if commit_time.returncode != 0 or not commit_time.stdout.strip().isdigit() or int(commit_time.stdout.strip()) >= int(t0.timestamp()):
        raise ProspectiveV2Error("CAPTURE_SOURCE_COMMIT_NOT_BEFORE_T0")
    return dict(payload)


def _ema(values: Sequence[Decimal], period: int) -> list[float | None]:
    alpha = 2.0 / (period + 1.0)
    out: list[float | None] = []
    current: float | None = None
    for index, raw in enumerate(values):
        value = float(raw)
        current = value if current is None else alpha * value + (1.0 - alpha) * current
        out.append(current if index >= period - 1 else None)
    return out


def _rsi(values: Sequence[Decimal], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    numbers = [float(value) for value in values]
    deltas = [numbers[i] - numbers[i - 1] for i in range(1, len(numbers))]
    avg_gain = sum(max(x, 0.0) for x in deltas[:period]) / period
    avg_loss = sum(max(-x, 0.0) for x in deltas[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        if gain == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[period] = value(avg_gain, avg_loss)
    for index in range(period + 1, len(numbers)):
        avg_gain = ((period - 1) * avg_gain + max(deltas[index - 1], 0.0)) / period
        avg_loss = ((period - 1) * avg_loss + max(-deltas[index - 1], 0.0)) / period
        out[index] = value(avg_gain, avg_loss)
    return out


def _features(candles: Sequence[Candle]) -> dict[str, list[float | None]]:
    close = [item.close for item in candles]
    high = [item.high for item in candles]
    low = [item.low for item in candles]
    result = {f"ema{period}": _ema(close, period) for period in (12, 24, 26, 50, 96, 100, 200)}
    result["rsi14"] = _rsi(close)
    for period in (24, 72, 168):
        vals: list[float | None] = [None] * len(candles)
        for i in range(period, len(candles)):
            prior = float(close[i - period])
            vals[i] = float(close[i]) / prior - 1.0
        result[f"mom{period}"] = vals
    for period, name, source in ((24, "donchian_high24_prev", high), (12, "donchian_low12_prev", low)):
        vals = [None] * len(candles)
        for i in range(period, len(candles)):
            window = source[i - period:i]
            vals[i] = max(window) if name.startswith("donchian_high") else min(window)
        result[name] = [None if value is None else float(value) for value in vals]
    return result


def _strategy_conditions(
    strategy: str,
    index: int,
    candles: Mapping[str, Sequence[Candle]],
    feats: Mapping[str, Mapping[str, Sequence[float | None]]],
) -> tuple[bool, bool, str | None]:
    eth, btc = candles["ETHUSDT"], candles["BTCUSDT"]
    e, b = feats["ETHUSDT"], feats["BTCUSDT"]
    def v(series: Sequence[float | None], i: int) -> float | None:
        return series[i]
    buy = sell = False
    desired: str | None = None
    close = float(eth[index].close)
    rsi = v(e["rsi14"], index)
    if strategy == "EMA12_26_RSI":
        fast, slow = v(e["ema12"], index), v(e["ema26"], index)
        pf, ps = v(e["ema12"], index - 1), v(e["ema26"], index - 1)
        buy = None not in (fast, slow, pf, ps, rsi) and pf <= ps and fast > slow and rsi >= 50
        sell = (None not in (fast, slow, pf, ps, rsi) and pf >= ps and fast < slow) or (rsi is not None and rsi >= 70)
    elif strategy == "EMA24_96_CROSS":
        fast, slow = v(e["ema24"], index), v(e["ema96"], index)
        pf, ps = v(e["ema24"], index - 1), v(e["ema96"], index - 1)
        buy = None not in (fast, slow, pf, ps) and pf <= ps and fast > slow
        sell = None not in (fast, slow, pf, ps) and pf >= ps and fast < slow
    elif strategy == "DONCHIAN_24_12":
        upper, lower = v(e["donchian_high24_prev"], index), v(e["donchian_low12_prev"], index)
        buy = upper is not None and close > upper
        sell = lower is not None and close < lower
    elif strategy == "RSI_MEANREV_EMA200":
        prior_rsi, ema200 = v(e["rsi14"], index - 1), v(e["ema200"], index)
        buy = prior_rsi is not None and rsi is not None and ema200 is not None and prior_rsi > 30 and rsi <= 30 and close > ema200
        sell = (rsi is not None and rsi >= 55) or (ema200 is not None and close < ema200)
    elif strategy == "BTC_GATED_ETH_TREND":
        e50, b50, mom72 = v(e["ema50"], index), v(b["ema50"], index), v(e["mom72"], index)
        bc = float(btc[index].close)
        buy = e50 is not None and b50 is not None and mom72 is not None and close > e50 and bc > b50 and mom72 > 0
        sell = not buy
    elif strategy == "ETH_TSMOM_168H":
        mom, ema100 = v(e["mom168"], index), v(e["ema100"], index)
        buy = mom is not None and ema100 is not None and mom > 0 and close > ema100
        sell = not buy
    elif strategy == "BTC_ETH_RS_ROTATION_168H":
        bm, em = v(b["mom168"], index), v(e["mom168"], index)
        candidates = [(bm, "BTCUSDT"), (em, "ETHUSDT")]
        candidates = [(momentum, symbol) for momentum, symbol in candidates if momentum is not None and momentum > 0]
        desired = sorted(candidates, key=lambda item: (-item[0], item[1]))[0][1] if candidates else None
    else:
        raise ProspectiveV2Error(f"UNKNOWN_STRATEGY:{strategy}")
    return bool(buy), bool(sell), desired


def _equity(cash: Decimal, quantity: Decimal, asset: str | None, quote: Quote | None, slippage_bps: Decimal, fee: Decimal) -> Decimal:
    if asset is None or quantity <= ZERO:
        return cash
    if quote is None or quote.symbol != asset:
        raise ProspectiveV2Error("EQUITY_MARK_QUOTE_MISSING")
    bid_after_slippage = quote.bid * (ONE - slippage_bps / Decimal("10000"))
    return cash + quantity * bid_after_slippage * (ONE - fee)


def _rounded_quantity(raw: Decimal, rules: Filters) -> Decimal:
    steps = (raw / rules.step_size).to_integral_value(rounding=ROUND_DOWN)
    return steps * rules.step_size


def _bootstrap_difference(values: Sequence[float], *, seed: int, replicates: int = 10_000, block: int = 7) -> dict[str, Any]:
    if not values or not all(math.isfinite(value) for value in values):
        raise ProspectiveV2Error("BOOTSTRAP_SERIES_INVALID")
    n = len(values)
    if n < block:
        raise ProspectiveV2Error("BOOTSTRAP_SERIES_TOO_SHORT")
    observed = sum(values) / n
    full_count, remainder = divmod(n, block)
    centered = [value - observed for value in values]

    def circular_block_sums(data: Sequence[float], size: int) -> list[float]:
        sums = []
        for start in range(n):
            sums.append(sum(data[(start + offset) % n] for offset in range(size)))
        return sums

    full_sums = circular_block_sums(values, block)
    partial_sums = circular_block_sums(values, remainder) if remainder else []
    centered_full = circular_block_sums(centered, block)
    centered_partial = circular_block_sums(centered, remainder) if remainder else []
    rng = random.Random(seed)
    sampled_means: list[float] = []
    null_means: list[float] = []
    for _ in range(replicates):
        total = 0.0
        null_total = 0.0
        for _part in range(full_count):
            choice = rng.randrange(n)
            total += full_sums[choice]
            null_total += centered_full[choice]
        if remainder:
            choice = rng.randrange(n)
            total += partial_sums[choice]
            null_total += centered_partial[choice]
        sampled_means.append(total / n)
        null_means.append(null_total / n)
    sampled_means.sort()
    lower = sampled_means[int(0.025 * (replicates - 1))]
    upper = sampled_means[int(0.975 * (replicates - 1))]
    p_value = (1 + sum(value >= observed for value in null_means)) / (replicates + 1)
    return {
        "daily_observations": n,
        "block_length_days": block,
        "replicates": replicates,
        "seed": seed,
        "mean_daily_log_excess": observed,
        "ci95_lower": lower,
        "ci95_upper": upper,
        "one_sided_p_value": p_value,
    }


def _holm(p_values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for rank, (name, p_value) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * p_value))
        adjusted[name] = running
    return adjusted


def _capture_window(
    candles: Mapping[str, Mapping[datetime, Candle]], t0: datetime, t1: datetime,
) -> tuple[dict[str, list[Candle]], list[datetime]]:
    if t0.minute or t0.second or t0.microsecond:
        raise ProspectiveV2Error("T0_MUST_ALIGN_TO_UTC_HOUR")
    if t1 != t0 + timedelta(days=365):
        raise ProspectiveV2Error("T1_MUST_EQUAL_T0_PLUS_365_DAYS")
    warm_start = t0 - timedelta(hours=WARMUP_HOURS)
    expected_starts = [warm_start + timedelta(hours=i) for i in range(WARMUP_HOURS + EVALUATION_HOURS)]
    aligned: dict[str, list[Candle]] = {}
    for symbol in SYMBOLS:
        observed = candles[symbol]
        missing = [stamp for stamp in expected_starts if stamp not in observed]
        if missing:
            raise ProspectiveV2Error(f"INCOMPLETE_KLINE_COVERAGE:{symbol}:missing={len(missing)}:first={missing[0].isoformat()}")
        rows = [observed[stamp] for stamp in expected_starts]
        if any(rows[i].end != rows[i + 1].start for i in range(len(rows) - 1)):
            raise ProspectiveV2Error(f"KLINE_CHRONOLOGY_GAP:{symbol}")
        aligned[symbol] = rows
    return aligned, expected_starts[WARMUP_HOURS:]


def _daily_log_returns(equities: Sequence[Decimal]) -> list[float]:
    if len(equities) != EVALUATION_HOURS + 1:
        raise ProspectiveV2Error("EQUITY_HOURLY_COVERAGE_INVALID")
    daily = [equities[0]]
    for index in range(24, EVALUATION_HOURS + 1, 24):
        daily.append(equities[index])
    if len(daily) != 366:
        raise ProspectiveV2Error("DAILY_EQUITY_COVERAGE_INVALID")
    return [math.log(float(daily[i] / daily[i - 1])) for i in range(1, len(daily))]


def _run_one(
    strategy: str,
    scenario: str,
    candles: Mapping[str, Sequence[Candle]],
    eval_starts: Sequence[datetime],
    t0: datetime,
    t1: datetime,
    filters: Mapping[str, Filters],
    store: QuoteStore,
    initial: Decimal,
    mark_quotes: Mapping[str, Sequence[Quote]],
) -> dict[str, Any]:
    cost = SCENARIOS[scenario]
    fee, slip = cost["fee"], cost["slippage_bps"] / Decimal("10000")
    cash, quantity, asset = initial, ZERO, None
    entries: list[datetime] = []
    fills: list[dict[str, Any]] = []
    equity_curve: list[Decimal] = [initial]
    daily_equities: list[Decimal] = [initial]
    total_fees, modeled_slippage = ZERO, ZERO
    buy_count = sell_count = rejected = 0
    features = {symbol: _features(candles[symbol]) for symbol in SYMBOLS}
    index_by_start = {stamp: WARMUP_HOURS + offset for offset, stamp in enumerate(eval_starts)}

    def sell_at(decision_at: datetime, mark_limit: datetime) -> datetime:
        nonlocal cash, quantity, asset, total_fees, modeled_slippage, sell_count
        if asset is None or quantity <= ZERO:
            return decision_at
        quote = store.first_at_or_after(asset, decision_at)
        if quote.available_at >= mark_limit:
            raise ProspectiveV2Error("FILL_AFTER_NEXT_MARK_BLOCKED")
        price = quote.bid * (ONE - slip)
        notional = quantity * price
        fee_amount = notional * fee
        cash += notional - fee_amount
        total_fees += fee_amount
        modeled_slippage += quantity * quote.bid * slip
        fills.append({
            "sequence": len(fills) + 1, "strategy": strategy,
            "scenario": scenario, "symbol": asset, "side": "SELL",
            "decision_at": decision_at.isoformat(), "available_at": quote.available_at.isoformat(),
            "update_id": quote.update_id, "bid": str(quote.bid), "ask": str(quote.ask),
            "fill_price": str(price), "quantity": str(quantity), "notional": str(notional),
            "fee": str(fee_amount), "modeled_slippage_usdt": str(quantity * quote.bid * slip),
            "reason": "SIGNAL_OR_TERMINAL_LIQUIDATION",
        })
        asset, quantity = None, ZERO
        sell_count += 1
        return quote.available_at

    def buy_at(symbol: str, decision_at: datetime, mark_limit: datetime, fraction: Decimal) -> None:
        nonlocal cash, quantity, asset, total_fees, modeled_slippage, buy_count, rejected
        buy_times = [stamp for stamp in entries if decision_at - stamp < timedelta(days=30)]
        entries[:] = buy_times
        if len(entries) >= MAX_ENTRIES_30D or cash <= ZERO:
            rejected += 1
            return
        quote = store.first_at_or_after(symbol, decision_at)
        if quote.available_at >= mark_limit:
            raise ProspectiveV2Error("FILL_AFTER_NEXT_MARK_BLOCKED")
        price = quote.ask * (ONE + slip)
        spend = cash * fraction
        raw_quantity = spend / (price * (ONE + fee))
        qty = _rounded_quantity(raw_quantity, filters[symbol])
        notional = qty * price
        if qty < filters[symbol].min_qty or qty > filters[symbol].max_qty or notional < filters[symbol].min_notional or qty <= ZERO:
            rejected += 1
            return
        fee_amount = notional * fee
        cash -= notional + fee_amount
        if cash < ZERO:
            raise ProspectiveV2Error("NEGATIVE_QUOTE_BALANCE_INVARIANT")
        quantity, asset = qty, symbol
        entries.append(quote.available_at)
        total_fees += fee_amount
        modeled_slippage += qty * quote.ask * slip
        fills.append({
            "sequence": len(fills) + 1, "strategy": strategy,
            "scenario": scenario, "symbol": symbol, "side": "BUY",
            "decision_at": decision_at.isoformat(), "available_at": quote.available_at.isoformat(),
            "update_id": quote.update_id, "bid": str(quote.bid), "ask": str(quote.ask),
            "fill_price": str(price), "quantity": str(qty), "notional": str(notional),
            "fee": str(fee_amount), "modeled_slippage_usdt": str(qty * quote.ask * slip),
            "reason": "SIGNAL_OR_BENCHMARK_ENTRY",
        })
        buy_count += 1

    for offset, stamp in enumerate(eval_starts):
        candle_index = index_by_start[stamp]
        boundary = stamp + timedelta(hours=1)
        # Mark the account at the hour boundary before consuming the just-closed candle signal.
        quote = mark_quotes[asset][offset] if asset is not None else None
        marked = _equity(cash, quantity, asset, quote, cost["slippage_bps"], fee)
        equity_curve.append(marked)
        if (offset + 1) % 24 == 0:
            daily_equities.append(marked)
        if boundary >= t1:
            continue
        buy_signal, sell_signal, rotation_target = _strategy_conditions(strategy, candle_index, candles, features)
        # Decisions only exist after each required closed candle has become available.
        required = [candles["ETHUSDT"][candle_index].available_at]
        if strategy in {"BTC_GATED_ETH_TREND", "BTC_ETH_RS_ROTATION_168H"}:
            required.append(candles["BTCUSDT"][candle_index].available_at)
        decision_at = max(required)
        next_mark = boundary + timedelta(hours=1)
        if strategy == "BTC_ETH_RS_ROTATION_168H":
            desired = rotation_target
        elif asset is None:
            desired = "ETHUSDT" if buy_signal else None
        else:
            desired = None if sell_signal else asset
        if desired == asset:
            continue
        completed_at = decision_at
        if asset is not None:
            completed_at = sell_at(decision_at, next_mark)
        if desired is not None and asset is None:
            buy_at(desired, completed_at, next_mark, POSITION_FRACTION)

    # Force flat exactly at T1 using the first valid quote after the boundary.
    if asset is not None:
        quote = store.first_at_or_after(asset, t1)
        price = quote.bid * (ONE - slip)
        notional = quantity * price
        fee_amount = notional * fee
        cash += notional - fee_amount
        total_fees += fee_amount
        modeled_slippage += quantity * quote.bid * slip
        fills.append({
            "sequence": len(fills) + 1, "strategy": strategy or "BUY_AND_HOLD",
            "scenario": scenario, "symbol": asset, "side": "SELL",
            "decision_at": t1.isoformat(), "available_at": quote.available_at.isoformat(),
            "update_id": quote.update_id, "bid": str(quote.bid), "ask": str(quote.ask),
            "fill_price": str(price), "quantity": str(quantity), "notional": str(notional),
            "fee": str(fee_amount), "modeled_slippage_usdt": str(quantity * quote.bid * slip),
            "reason": "TERMINAL_LIQUIDATION_T1",
        })
        quantity, asset = ZERO, None
        sell_count += 1
    if asset is not None or quantity != ZERO:
        raise ProspectiveV2Error("TERMINAL_FLAT_INVARIANT_FAILED")
    if len(equity_curve) != EVALUATION_HOURS + 1:
        raise ProspectiveV2Error("EQUITY_MARK_COUNT_INVALID")
    if len(daily_equities) != 366:
        raise ProspectiveV2Error("DAILY_EQUITY_MARK_COUNT_INVALID")
    peak = initial
    max_dd = ZERO
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > ZERO:
            max_dd = min(max_dd, value / peak - ONE)
    final = cash
    return {
        "strategy": strategy or "CASH",
        "scenario": scenario,
        "initial_usdt": str(initial),
        "final_usdt": str(final),
        "return_pct": str((final / initial - ONE) * Decimal("100")),
        "max_drawdown_pct": str(max_dd * Decimal("100")),
        "fills": len(fills),
        "buys": buy_count,
        "sells": sell_count,
        "rejected_entries": rejected,
        "fees_usdt": str(total_fees),
        "modeled_slippage_usdt": str(modeled_slippage),
        "daily_log_returns": _daily_log_returns(daily_equities),
        "equity_curve": [str(value) for value in equity_curve],
        "trades": fills,
    }


def _validate_and_cache_marks(
    store: QuoteStore, eval_starts: Sequence[datetime],
) -> dict[str, list[Quote]]:
    marks: dict[str, list[Quote]] = {symbol: [] for symbol in SYMBOLS}
    for start in eval_starts:
        boundary = start + timedelta(hours=1)
        for symbol in SYMBOLS:
            marks[symbol].append(store.first_at_or_after(symbol, boundary))
    return marks


def _run_buy_hold(
    symbol: str,
    scenario: str,
    candles: Mapping[str, Sequence[Candle]],
    eval_starts: Sequence[datetime],
    t0: datetime,
    t1: datetime,
    filters: Mapping[str, Filters],
    store: QuoteStore,
    initial: Decimal,
    mark_quotes: Mapping[str, Sequence[Quote]],
) -> dict[str, Any]:
    # Buy-and-hold uses the same event-time, quote, fee, and liquidation rules.
    cost = SCENARIOS[scenario]
    fee, slip = cost["fee"], cost["slippage_bps"] / Decimal("10000")
    cash, quantity = initial, ZERO
    entries: list[datetime] = []
    fills: list[dict[str, Any]] = []
    total_fees = modeled_slippage = ZERO
    entry_quote = store.first_at_or_after(symbol, t0)
    entry_price = entry_quote.ask * (ONE + slip)
    qty = _rounded_quantity(cash / (entry_price * (ONE + fee)), filters[symbol])
    notional = qty * entry_price
    if qty < filters[symbol].min_qty or qty > filters[symbol].max_qty or notional < filters[symbol].min_notional:
        raise ProspectiveV2Error(f"BUY_AND_HOLD_REJECTED_BY_SPOT_FILTERS:{symbol}")
    fee_amount = notional * fee
    cash -= notional + fee_amount
    total_fees += fee_amount
    modeled_slippage += qty * entry_quote.ask * slip
    quantity = qty
    fills.append({
        "sequence": 1, "strategy": "BUY_HOLD_" + symbol, "scenario": scenario,
        "symbol": symbol, "side": "BUY", "decision_at": t0.isoformat(),
        "available_at": entry_quote.available_at.isoformat(), "update_id": entry_quote.update_id,
        "bid": str(entry_quote.bid), "ask": str(entry_quote.ask), "fill_price": str(entry_price),
        "quantity": str(quantity), "notional": str(notional), "fee": str(fee_amount),
        "modeled_slippage_usdt": str(quantity * entry_quote.ask * slip), "reason": "BENCHMARK_ENTRY",
    })
    equity_curve = [initial]
    daily_equities = [initial]
    for offset, stamp in enumerate(eval_starts):
        quote = mark_quotes[symbol][offset]
        marked = _equity(cash, quantity, symbol, quote, cost["slippage_bps"], fee)
        equity_curve.append(marked)
        if (offset + 1) % 24 == 0:
            daily_equities.append(marked)
    terminal_quote = store.first_at_or_after(symbol, t1)
    sell_price = terminal_quote.bid * (ONE - slip)
    proceeds = quantity * sell_price
    terminal_fee = proceeds * fee
    cash += proceeds - terminal_fee
    total_fees += terminal_fee
    modeled_slippage += quantity * terminal_quote.bid * slip
    fills.append({
        "sequence": 2, "strategy": "BUY_HOLD_" + symbol, "scenario": scenario,
        "symbol": symbol, "side": "SELL", "decision_at": t1.isoformat(),
        "available_at": terminal_quote.available_at.isoformat(), "update_id": terminal_quote.update_id,
        "bid": str(terminal_quote.bid), "ask": str(terminal_quote.ask), "fill_price": str(sell_price),
        "quantity": str(quantity), "notional": str(proceeds), "fee": str(terminal_fee),
        "modeled_slippage_usdt": str(quantity * terminal_quote.bid * slip), "reason": "BENCHMARK_TERMINAL_LIQUIDATION",
    })
    peak = initial
    max_dd = ZERO
    for value in equity_curve:
        peak = max(peak, value)
        if peak > ZERO:
            max_dd = min(max_dd, value / peak - ONE)
    return {
        "strategy": "BUY_HOLD_" + symbol, "scenario": scenario,
        "initial_usdt": str(initial), "final_usdt": str(cash),
        "return_pct": str((cash / initial - ONE) * Decimal("100")),
        "max_drawdown_pct": str(max_dd * Decimal("100")), "fills": 2, "buys": 1, "sells": 1,
        "rejected_entries": 0, "fees_usdt": str(total_fees),
        "modeled_slippage_usdt": str(modeled_slippage),
        "daily_log_returns": _daily_log_returns(daily_equities),
        "equity_curve": [str(value) for value in equity_curve], "trades": fills,
    }


def _validate_daily_quote_coverage(store: QuoteStore, t0: datetime, t1: datetime) -> None:
    # Every required mark and the terminal liquidation need an executable quote after the boundary.
    current = t0 + timedelta(hours=1)
    while current <= t1:
        for symbol in SYMBOLS:
            store.first_at_or_after(symbol, current)
        current += timedelta(hours=1)
    for symbol in SYMBOLS:
        store.first_at_or_after(symbol, t0)
        store.first_at_or_after(symbol, t1)


def run_study(
    *,
    capture_root: Path,
    filters_path: Path,
    warmup_path: Path,
    preregistration_path: Path,
    t0: datetime,
    output_dir: Path,
    initial_usdt: Decimal = INITIAL_USDT,
    bootstrap_replicates: int = 10_000,
    allow_short_test_window: bool = False,
) -> dict[str, Any]:
    if initial_usdt <= ZERO or not initial_usdt.is_finite():
        raise ProspectiveV2Error("INITIAL_BALANCE_INVALID")
    if not allow_short_test_window and bootstrap_replicates != 10_000:
        raise ProspectiveV2Error("PREREGISTERED_BOOTSTRAP_REPLICATES_FIXED_AT_10000")
    t1 = t0 + timedelta(days=365)
    output_dir.mkdir(parents=True, exist_ok=True)
    store_path = output_dir / ".prospective-v2-working.sqlite3"
    if store_path.exists():
        raise ProspectiveV2Error("WORKING_DATABASE_REFUSES_OVERWRITE")
    store = QuoteStore(store_path)
    try:
        candle_map, capture_evidence = _load_pair_evidence(capture_root.resolve(strict=True), store)
        filters, filter_sha = load_filter_snapshot(filters_path, t0)
        warmup_map, warmup_sha = load_warmup_snapshot(warmup_path, t0)
        preregistration = _validate_preregistration(
            preregistration_path, t0, filter_sha, warmup_sha, capture_evidence["source_sha"],
        )
        for symbol in SYMBOLS:
            for stamp, warmup_candle in warmup_map[symbol].items():
                captured = candle_map[symbol].get(stamp)
                if captured is not None:
                    same = (
                        captured.open, captured.high, captured.low, captured.close,
                        captured.volume, captured.end,
                    ) == (
                        warmup_candle.open, warmup_candle.high, warmup_candle.low,
                        warmup_candle.close, warmup_candle.volume, warmup_candle.end,
                    )
                    if not same:
                        raise ProspectiveV2Error(f"WARMUP_CAPTURE_CONFLICT:{symbol}:{stamp.isoformat()}")
                else:
                    candle_map[symbol][stamp] = warmup_candle
        if allow_short_test_window:
            raise ProspectiveV2Error("SHORT_TEST_WINDOW_MODE_IS_NOT_EXPOSED_BY_CLI")
        candles, eval_starts = _capture_window(candle_map, t0, t1)
        if len(eval_starts) != EVALUATION_HOURS:
            raise ProspectiveV2Error("EVALUATION_WINDOW_COUNT_INVALID")
        _validate_daily_quote_coverage(store, t0, t1)
        mark_quotes = _validate_and_cache_marks(store, eval_starts)
        all_results: dict[str, dict[str, dict[str, Any]]] = {}
        for scenario in SCENARIOS:
            all_results[scenario] = {}
            for strategy in STRATEGIES:
                all_results[scenario][strategy] = _run_one(
                    strategy, scenario, candles, eval_starts, t0, t1, filters, store, initial_usdt, mark_quotes,
                )
            all_results[scenario]["CASH"] = {
                "strategy": "CASH", "scenario": scenario, "initial_usdt": str(initial_usdt),
                "final_usdt": str(initial_usdt), "return_pct": "0", "max_drawdown_pct": "0",
                "fills": 0, "buys": 0, "sells": 0, "rejected_entries": 0,
                "fees_usdt": "0", "modeled_slippage_usdt": "0",
                "daily_log_returns": [0.0] * 365,
                "equity_curve": [str(initial_usdt)] * (EVALUATION_HOURS + 1), "trades": [],
            }
            for symbol in SYMBOLS:
                name = "BUY_HOLD_" + symbol
                all_results[scenario][name] = _run_buy_hold(
                    symbol, scenario, candles, eval_starts, t0, t1, filters, store, initial_usdt, mark_quotes,
                )
        # Test the full preregistered strategy family against the strongest passive benchmark.
        stats: dict[str, dict[str, Any]] = {}
        p_values: dict[str, float] = {}
        for scenario in ("BASE", "STRESS_1"):
            benchmark_names = ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT")
            best_benchmark = max(benchmark_names, key=lambda name: Decimal(all_results[scenario][name]["final_usdt"]))
            for strategy in STRATEGIES:
                diffs = [
                    a - b for a, b in zip(
                        all_results[scenario][strategy]["daily_log_returns"],
                        all_results[scenario][best_benchmark]["daily_log_returns"],
                    )
                ]
                key = f"{scenario}:{strategy}"
                stats[key] = {
                    "benchmark": best_benchmark,
                    **_bootstrap_difference(diffs, seed=20261019 + len(stats), replicates=bootstrap_replicates),
                }
                p_values[key] = stats[key]["one_sided_p_value"]
        adjusted = _holm(p_values)
        for key, value in adjusted.items():
            stats[key]["holm_adjusted_p_value"] = value
        gates: dict[str, Any] = {}
        for strategy in STRATEGIES:
            base = all_results["BASE"][strategy]
            stress1 = all_results["STRESS_1"][strategy]
            stress2 = all_results["STRESS_2"][strategy]
            stats_base, stats_stress1 = stats[f"BASE:{strategy}"], stats[f"STRESS_1:{strategy}"]
            beats_passives = all(
                Decimal(base["final_usdt"]) > Decimal(all_results["BASE"][benchmark]["final_usdt"])
                for benchmark in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT")
            )
            eligible = (
                Decimal(base["final_usdt"]) > initial_usdt
                and Decimal(stress1["final_usdt"]) > initial_usdt
                and Decimal(stress2["final_usdt"]) >= initial_usdt
                and beats_passives
                and stats_base["ci95_lower"] > 0
                and stats_stress1["ci95_lower"] > 0
                and stats_base["holm_adjusted_p_value"] < 0.05
                and stats_stress1["holm_adjusted_p_value"] < 0.05
            )
            gates[strategy] = {
                "base_positive": Decimal(base["final_usdt"]) > initial_usdt,
                "stress1_positive": Decimal(stress1["final_usdt"]) > initial_usdt,
                "stress2_nonnegative": Decimal(stress2["final_usdt"]) >= initial_usdt,
                "beats_cash_and_both_buy_hold_base": beats_passives,
                "base_ci95_lower_positive": stats_base["ci95_lower"] > 0,
                "stress1_ci95_lower_positive": stats_stress1["ci95_lower"] > 0,
                "base_holm_p_below_005": stats_base["holm_adjusted_p_value"] < 0.05,
                "stress1_holm_p_below_005": stats_stress1["holm_adjusted_p_value"] < 0.05,
                "eligible_for_second_independent_review": eligible,
            }
        decision = "CANDIDATE_FOR_SECOND_INDEPENDENT_REVIEW" if any(item["eligible_for_second_independent_review"] for item in gates.values()) else "NO_ROBUST_EDGE_FOUND"
        results = []
        trades = []
        for scenario in SCENARIOS:
            for name, item in all_results[scenario].items():
                results.append({key: value for key, value in item.items() if key not in {"daily_log_returns", "equity_curve", "trades"}})
                trades.extend(item["trades"])
        summary = {
            "schema_version": SCHEMA_VERSION,
            "study": "BTC_ETH_PROSPECTIVE_STRATEGIES_V2",
            "status": "COMPLETED",
            "economic_classification": decision,
            "operational_decision": "NO_TRADE",
            "live_execution": "DISABLED",
            "capture_source_sha": capture_evidence["source_sha"],
            "capture_count": capture_evidence["capture_count"],
            "capture_manifests": capture_evidence["capture_manifests"],
            "input_file_sha256": capture_evidence["input_file_sha256"],
            "filters_snapshot_sha256": filter_sha,
            "warmup_snapshot_sha256": warmup_sha,
            "preregistration_sha256": preregistration["registration_sha256"],
            "preregistered_code_sha256": preregistration["code_sha256"],
            "preregistration_path": preregistration.get("registration_path"),
            "t0": t0.isoformat(),
            "t1_exclusive": t1.isoformat(),
            "warmup_hours": WARMUP_HOURS,
            "evaluation_hours": EVALUATION_HOURS,
            "symbols": list(SYMBOLS),
            "strategies": list(STRATEGIES),
            "scenarios": {name: {"fee_rate": str(value["fee"]), "slippage_bps_per_side": str(value["slippage_bps"]), "spread_source": "OBSERVED_BID_ASK"} for name, value in SCENARIOS.items()},
            "initial_usdt": str(initial_usdt),
            "metrics": results,
            "statistical_tests": stats,
            "promotion_gates": gates,
            "important_limit": "A candidate can only advance to a second independent review; the study never enables PAPER orders or LIVE.",
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / "prospective-v2-summary.json"
        trades_path = output_dir / "prospective-v2-trades.csv"
        manifest_path = output_dir / "prospective-v2-manifest.json"
        for path in (summary_path, trades_path, manifest_path):
            if path.exists():
                raise ProspectiveV2Error("OUTPUT_REFUSES_OVERWRITE")
        with summary_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(summary, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        fieldnames = sorted({key for row in trades for key in row})
        with trades_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames or ["sequence"])
            writer.writeheader()
            writer.writerows(trades)
        artifacts = {path.name: _file_sha256(path) for path in (summary_path, trades_path)}
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "study": "BTC_ETH_PROSPECTIVE_STRATEGIES_V2",
            "status": "COMPLETED",
            "t0": t0.isoformat(),
            "t1_exclusive": t1.isoformat(),
            "source_sha": capture_evidence["source_sha"],
            "filters_snapshot_sha256": filter_sha,
            "warmup_snapshot_sha256": warmup_sha,
            "preregistration_sha256": preregistration["registration_sha256"],
            "preregistration_path": preregistration.get("registration_path"),
            "capture_manifest_fingerprints": capture_evidence["capture_manifests"],
            "artifacts_sha256": artifacts,
            "economic_classification": decision,
            "operational_decision": "NO_TRADE",
            "trading_enabled": False,
        }
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        return {
            "status": "COMPLETED",
            "economic_classification": decision,
            "operational_decision": "NO_TRADE",
            "summary_path": str(summary_path),
            "trades_path": str(trades_path),
            "manifest_path": str(manifest_path),
            "metrics_count": len(results),
            "strategy_count": len(STRATEGIES),
            "scenario_count": len(SCENARIOS),
            "statistical_test_count": len(stats),
            "quote_records": store.quote_count(),
            "source_sha": capture_evidence["source_sha"],
            "t0": t0.isoformat(),
            "t1_exclusive": t1.isoformat(),
        }
    finally:
        store.close()
        try:
            store_path.unlink(missing_ok=True)
        Path(str(store_path) + "-wal").unlink(missing_ok=True)
        Path(str(store_path) + "-shm").unlink(missing_ok=True)
        except OSError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot-cripto prospective-v2",
        description="Run the preregistered 365-day public-data BTC/ETH economic replay. Research only; no orders.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    filters = sub.add_parser("capture-filters", help="capture and hash public Spot exchangeInfo filters before T0")
    filters.add_argument("--output", type=Path, required=True)
    warmup = sub.add_parser("capture-warmup", help="freeze 90 days of warm-up candles, excluded from scoring")
    warmup.add_argument("--t0", required=True, help="future preregistered UTC hour")
    warmup.add_argument("--output", type=Path, required=True)
    prereg = sub.add_parser("preregister", help="write the hash-bound V2 registration to commit before T0")
    prereg.add_argument("--t0", required=True)
    prereg.add_argument("--filters-json", type=Path, required=True)
    prereg.add_argument("--warmup-json", type=Path, required=True)
    prereg.add_argument("--output", type=Path, required=True, help="path inside the repository; commit this file to main before T0")
    capture = sub.add_parser("capture-period", help="capture public Kline + bookTicker continuously until T1; no performance output")
    capture.add_argument("--t0", required=True, help="T0 from the committed preregistration")
    capture.add_argument("--output-dir", type=Path, required=True)
    run = sub.add_parser("replay", help="validate paired captures and replay the preregistered 365-day interval")
    run.add_argument("--capture-root", type=Path, required=True, help="directory tree containing accepted pair-manifest.json captures")
    run.add_argument("--filters-json", type=Path, required=True, help="filter snapshot captured before T0")
    run.add_argument("--warmup-json", type=Path, required=True, help="hash-bound 90-day warm-up snapshot")
    run.add_argument("--preregistration-json", type=Path, required=True, help="exact preregistration committed to source main before T0")
    run.add_argument("--t0", required=True, help="preregistered UTC hour, ISO-8601 with Z or +00:00")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--initial-usdt", default="31")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "capture-filters":
            print(json.dumps(capture_filter_snapshot(args.output), sort_keys=True, indent=2))
            return 0
        if args.command == "capture-warmup":
            print(json.dumps(capture_warmup_snapshot(_dt(args.t0, "T0"), args.output), sort_keys=True, indent=2))
            return 0
        if args.command == "preregister":
            print(json.dumps(create_preregistration(
                t0=_dt(args.t0, "T0"),
                filters_path=args.filters_json,
                warmup_path=args.warmup_json,
                output_path=args.output,
            ), sort_keys=True, indent=2))
            return 0
        if args.command == "capture-period":
            t0 = _dt(args.t0, "T0")
            now = datetime.now(timezone.utc)
            if now >= t0:
                raise ProspectiveV2Error("CAPTURE_PERIOD_MUST_START_BEFORE_T0")
            t1 = t0 + timedelta(days=365)
            duration = math.ceil((t1 - now).total_seconds())
            if duration < 24 * 60 * 60 or duration > 500 * 24 * 60 * 60:
                raise ProspectiveV2Error("CAPTURE_PERIOD_DURATION_OUT_OF_RANGE")
            from .prospective_capture_pair import run_capture_pair
            result = run_capture_pair(
                output_dir=args.output_dir,
                duration_seconds=duration,
                align_to_utc_midnight=False,
            )
            result = {**result, "t0": t0.isoformat(), "t1_exclusive": t1.isoformat(), "requested_duration_seconds": duration}
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if result.get("status") == "VALID" else 2
        t0 = _dt(args.t0, "T0")
        result = run_study(
            capture_root=args.capture_root,
            filters_path=args.filters_json,
            warmup_path=args.warmup_json,
            preregistration_path=args.preregistration_json,
            t0=t0,
            output_dir=args.output_dir,
            initial_usdt=_dec(args.initial_usdt, "initial_usdt", positive=True),
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (ProspectiveV2Error, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({
            "status": "INCOMPLETE",
            "economic_classification": "INCOMPLETE",
            "operational_decision": "NO_TRADE",
            "error": f"{type(exc).__name__}:{exc}",
            "trading_enabled": False,
        }, sort_keys=True, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
