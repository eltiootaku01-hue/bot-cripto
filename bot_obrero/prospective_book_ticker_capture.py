"""Durable, fail-closed public Spot bookTicker capture and independent validator.

This captures only public best-bid/ask observations. It records local receive and
consumer-handoff clocks because Spot bookTicker payloads do not normally include
an exchange event timestamp. Historical availability is never reconstructed later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .binance_book_ticker import (
    APPROVED_SYMBOLS,
    BOOK_TICKER_OBSERVATION_TYPE,
    BOOK_TICKER_SOURCE,
    BinanceBookTicker,
    BinanceSpotBookTickerWebSocketAdapter,
)
from .binance_websocket import (
    BinanceWebSocketError,
    BinanceWebSocketServerShutdown,
    BinanceWebSocketTransportError,
)

SCHEMA_VERSION = 1
DEFAULT_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_MAX_MESSAGES_PER_CONNECTION = 2_147_483_647  # hold one connection open until stop/disconnect
DEFAULT_MAX_INTER_EVENT_GAP_SECONDS = 10.0
DEFAULT_RECONNECT_BASE_SECONDS = 1.0
DEFAULT_RECONNECT_MAX_SECONDS = 60.0


class ProspectiveBookTickerCaptureError(RuntimeError):
    """Invalid quote-capture arguments, evidence, or durable sink state."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProspectiveBookTickerCaptureError(f"{label.upper()}_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or not value:
        raise ProspectiveBookTickerCaptureError(f"{label.upper()}_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProspectiveBookTickerCaptureError(f"{label.upper()}_TIMESTAMP_INVALID") from exc
    return _aware_utc(parsed, label)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_payload(item: BinanceBookTicker) -> dict[str, Any]:
    return {
        "schema": "prospective-book-ticker-v1",
        "symbol": item.symbol,
        "update_id": item.update_id,
        "bid": item.bid,
        "bid_quantity": item.bid_quantity,
        "ask": item.ask,
        "ask_quantity": item.ask_quantity,
        "observed_at": item.observed_at.isoformat(),
        "received_at": item.received_at.isoformat(),
        "available_at": item.available_at.isoformat(),
        "provider_event_time": (
            None if item.provider_event_time is None else item.provider_event_time.isoformat()
        ),
        "source": item.source,
        "observation_type": item.observation_type,
        "venue": "BINANCE",
        "market": "SPOT",
    }


def _quote_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(payload))).hexdigest()


def _safe_run_id(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", value):
        raise ProspectiveBookTickerCaptureError("RUN_ID_INVALID")
    return value


def _next_utc_midnight(value: datetime) -> datetime:
    stamp = _aware_utc(value, "schedule_time")
    midnight = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight if stamp == midnight else midnight + timedelta(days=1)


def _evaluate(
    *,
    symbols: Sequence[str],
    summary: Mapping[str, Mapping[str, Any]],
    start_at: datetime | None,
    end_at: datetime | None,
    elapsed_seconds: float,
    target_duration_seconds: int,
    max_inter_event_gap_seconds: float,
    fatal_errors: Sequence[Any],
) -> dict[str, Any]:
    elapsed_met = elapsed_seconds >= target_duration_seconds
    no_fatal = not fatal_errors
    wall_elapsed = None
    wall_consistency = False
    if start_at is not None and end_at is not None:
        wall_elapsed = (end_at - start_at).total_seconds()
        wall_consistency = wall_elapsed >= 0 and abs(wall_elapsed - elapsed_seconds) <= 120.0

    symbol_checks: dict[str, Any] = {}
    for symbol in symbols:
        row = summary[symbol]
        first_raw = row.get("first_received_at")
        last_raw = row.get("latest_received_at")
        first_gap = (
            (_timestamp(first_raw, "first_received_at") - start_at).total_seconds()
            if first_raw and start_at is not None else None
        )
        tail_gap = (
            (end_at - _timestamp(last_raw, "latest_received_at")).total_seconds()
            if last_raw and end_at is not None else None
        )
        has_data = int(row.get("records", 0)) > 0
        received_span = (
            (_timestamp(last_raw, "latest_received_at") -
             _timestamp(first_raw, "first_received_at")).total_seconds()
            if first_raw and last_raw else 0.0
        )
        passed = (
            has_data
            and first_gap is not None and 0 <= first_gap <= max_inter_event_gap_seconds
            and tail_gap is not None and 0 <= tail_gap <= max_inter_event_gap_seconds
            and received_span >= max(0.0, elapsed_seconds - 2 * max_inter_event_gap_seconds)
            and int(row.get("duplicate_update_ids", 0)) == 0
            and int(row.get("out_of_order_update_ids", 0)) == 0
            and int(row.get("inter_event_gap_count", 0)) == 0
            and int(row.get("clock_regressions", 0)) == 0
            and float(row.get("maximum_inter_event_gap_seconds", 0.0)) <= max_inter_event_gap_seconds
        )
        symbol_checks[symbol] = {
            "has_quote_data": has_data,
            "first_receive_gap_seconds": None if first_gap is None else round(first_gap, 3),
            "tail_receive_gap_seconds": None if tail_gap is None else round(tail_gap, 3),
            "received_span_seconds": round(received_span, 3),
            "max_inter_event_gap_seconds": max_inter_event_gap_seconds,
            "passed": passed,
        }

    accepted = elapsed_met and no_fatal and wall_consistency and all(
        item["passed"] for item in symbol_checks.values()
    )
    return {
        "elapsed_duration_met": elapsed_met,
        "target_duration_seconds": target_duration_seconds,
        "actual_elapsed_seconds": round(elapsed_seconds, 3),
        "wall_clock_elapsed_seconds": None if wall_elapsed is None else round(wall_elapsed, 3),
        "wall_clock_consistency_pass": wall_consistency,
        "no_fatal_errors": no_fatal,
        "symbols": symbol_checks,
        "accepted": accepted,
    }


class ProspectiveBookTickerJournal:
    """Thread-safe, per-symbol JSONL quote sink with a hashed manifest."""

    def __init__(
        self,
        *,
        output_dir: Path,
        symbols: Sequence[str] = APPROVED_SYMBOLS,
        duration_seconds: int = DEFAULT_DURATION_SECONDS,
        fsync_every_records: int = 1,
        max_inter_event_gap_seconds: float = DEFAULT_MAX_INTER_EVENT_GAP_SECONDS,
        run_id: str | None = None,
        now=_now,
    ) -> None:
        normalized = tuple(symbols)
        if len(normalized) != 2 or set(normalized) != set(APPROVED_SYMBOLS):
            raise ProspectiveBookTickerCaptureError("CAPTURE_REQUIRES_EXACT_PAIR_BTCUSDT_ETHUSDT")
        if isinstance(duration_seconds, bool) or type(duration_seconds) is not int or duration_seconds < DEFAULT_DURATION_SECONDS:
            raise ProspectiveBookTickerCaptureError("CAPTURE_DURATION_MUST_BE_AT_LEAST_24_HOURS")
        if isinstance(fsync_every_records, bool) or type(fsync_every_records) is not int or fsync_every_records < 1:
            raise ProspectiveBookTickerCaptureError("FSYNC_EVERY_RECORDS_INVALID")
        if (not math.isfinite(max_inter_event_gap_seconds) or max_inter_event_gap_seconds <= 0):
            raise ProspectiveBookTickerCaptureError("MAX_INTER_EVENT_GAP_INVALID")

        self.symbols = normalized
        self.duration_seconds = duration_seconds
        self.fsync_every_records = fsync_every_records
        self.max_inter_event_gap_seconds = max_inter_event_gap_seconds
        self._now = now
        self._lock = threading.RLock()
        self.created_at = _aware_utc(now(), "created_at")
        self.run_id = _safe_run_id(run_id or self.created_at.strftime("bookticker-%Y%m%dT%H%M%S%fZ"))
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.run_dir = root / self.run_id
        self.run_dir.mkdir(parents=False, exist_ok=False)
        self.manifest_path = self.run_dir / "manifest.json"
        self._handles = {
            symbol: (self.run_dir / f"{symbol}-bookTicker.jsonl").open("x", encoding="utf-8", newline="\n")
            for symbol in self.symbols
        }
        self._state: dict[str, dict[str, Any]] = {
            symbol: {
                "records": 0,
                "connection_attempts": 0,
                "reconnects": 0,
                "transport_errors": 0,
                "last_error": None,
                "first_received_at": None,
                "latest_received_at": None,
                "last_received_datetime": None,
                "last_update_id": None,
                "seen_update_ids": set(),
                "duplicate_update_ids": 0,
                "out_of_order_update_ids": 0,
                "inter_event_gap_count": 0,
                "maximum_inter_event_gap_seconds": 0.0,
                "clock_regressions": 0,
                "line_count_since_fsync": 0,
            }
            for symbol in self.symbols
        }
        config = {
            "schema_version": SCHEMA_VERSION,
            "capture_kind": "PROSPECTIVE_PUBLIC_BINANCE_SPOT_BOOK_TICKER",
            "symbols": list(self.symbols),
            "target_duration_seconds": duration_seconds,
            "maximum_inter_event_gap_seconds": max_inter_event_gap_seconds,
            "availability_policy": "LOCAL_RECEIVE_AND_CONSUMER_HANDOFF_CLOCKS; NO_RETROACTIVE_AVAILABILITY",
            "exchange_event_time_policy": "OPTIONAL_ONLY_IF_PRESENT; OTHERWISE_OBSERVED_AT_EQUALS_LOCAL_RECEIVE_TIME",
            "quote_policy": "CANONICAL_DECIMAL_BID_ASK; ASK_MUST_NOT_BE_BELOW_BID",
            "source": {
                "provider": "binance",
                "market": "SPOT",
                "venue": "BINANCE",
                "source_id": BOOK_TICKER_SOURCE,
                "observation_type": BOOK_TICKER_OBSERVATION_TYPE,
            },
            "mode": "PUBLIC_DATA_ONLY_NO_ORDERS",
        }
        self._manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": "PREPARING",
            "created_at": self.created_at.isoformat(),
            "scheduled_capture_start_at": None,
            "capture_started_at": None,
            "capture_ended_at": None,
            "target_duration_seconds": duration_seconds,
            "actual_elapsed_seconds": None,
            "symbols": list(self.symbols),
            "configuration": config,
            "configuration_sha256": hashlib.sha256(_canonical_json(config)).hexdigest(),
            "symbols_summary": {},
            "fatal_errors": [],
            "acceptance": {},
            "files": {},
            "reason": None,
        }
        self._write_manifest_locked()

    @property
    def fatal(self) -> bool:
        with self._lock:
            return bool(self._manifest["fatal_errors"])

    def schedule(self, value: datetime) -> None:
        stamp = _aware_utc(value, "scheduled_capture_start_at")
        with self._lock:
            self._manifest["scheduled_capture_start_at"] = stamp.isoformat()
            self._manifest["status"] = "WAITING_FOR_UTC_BOUNDARY"
            self._write_manifest_locked()

    def start(self, value: datetime) -> None:
        stamp = _aware_utc(value, "capture_started_at")
        with self._lock:
            if self._manifest["capture_started_at"] is not None:
                raise ProspectiveBookTickerCaptureError("CAPTURE_ALREADY_STARTED")
            self._manifest["capture_started_at"] = stamp.isoformat()
            self._manifest["status"] = "RUNNING"
            self._write_manifest_locked()

    def note_connection_attempt(self, symbol: str) -> None:
        with self._lock:
            state = self._state[symbol]
            if state["connection_attempts"] > 0:
                state["reconnects"] += 1
            state["connection_attempts"] += 1
            self._refresh_summaries_locked()
            self._write_manifest_locked()

    def note_transport_error(self, symbol: str, error: BaseException) -> None:
        """Record transport failures; reconnects count actual subsequent connection attempts only."""
        with self._lock:
            state = self._state[symbol]
            state["transport_errors"] += 1
            state["last_error"] = f"{type(error).__name__}:{str(error)[:500]}"
            self._refresh_summaries_locked()
            self._write_manifest_locked()

    def note_fatal_error(self, symbol: str | None, error: BaseException) -> None:
        with self._lock:
            self._manifest["fatal_errors"].append({
                "at": _aware_utc(self._now(), "fatal_error_at").isoformat(),
                "symbol": symbol,
                "type": type(error).__name__,
                "message": str(error)[:1000],
            })
            self._manifest["status"] = "BLOCKED"
            self._manifest["reason"] = "FATAL_CAPTURE_OR_PERSISTENCE_ERROR"
            self._write_manifest_locked()

    def accept(self, item: BinanceBookTicker) -> None:
        if not isinstance(item, BinanceBookTicker):
            raise ProspectiveBookTickerCaptureError("BOOK_TICKER_ITEM_CONTRACT_INVALID")
        if item.symbol not in self.symbols:
            raise ProspectiveBookTickerCaptureError("BOOK_TICKER_SYMBOL_NOT_APPROVED")
        if item.source != BOOK_TICKER_SOURCE or item.observation_type != BOOK_TICKER_OBSERVATION_TYPE:
            raise ProspectiveBookTickerCaptureError("BOOK_TICKER_SOURCE_INVALID")
        received = _aware_utc(item.received_at, "received_at")
        available = _aware_utc(item.available_at, "available_at")
        observed = _aware_utc(item.observed_at, "observed_at")
        if observed > received or received > available:
            raise ProspectiveBookTickerCaptureError("BOOK_TICKER_TEMPORAL_ORDER_INVALID")
        quote = _quote_payload(item)
        digest = _quote_hash(quote)
        if digest != item.evidence_hash:
            raise ProspectiveBookTickerCaptureError("BOOK_TICKER_EVIDENCE_HASH_MISMATCH")
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "symbol": item.symbol,
            "sequence": 0,
            "record_sha256": digest,
            "quote": quote,
        }

        with self._lock:
            if self._manifest["status"] != "RUNNING":
                raise ProspectiveBookTickerCaptureError("CAPTURE_NOT_RUNNING")
            state = self._state[item.symbol]
            sequence = state["records"] + 1
            envelope["sequence"] = sequence
            handle = self._handles[item.symbol]
            handle.write(json.dumps(envelope, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            state["line_count_since_fsync"] += 1
            if state["line_count_since_fsync"] >= self.fsync_every_records:
                os.fsync(handle.fileno())
                state["line_count_since_fsync"] = 0

            previous_received = state["last_received_datetime"]
            if previous_received is not None:
                raw_gap = (received - previous_received).total_seconds()
                if raw_gap < 0:
                    state["clock_regressions"] += 1
                gap = max(0.0, raw_gap)
                state["maximum_inter_event_gap_seconds"] = max(state["maximum_inter_event_gap_seconds"], gap)
                if gap > self.max_inter_event_gap_seconds:
                    state["inter_event_gap_count"] += 1
            if state["first_received_at"] is None:
                state["first_received_at"] = received.isoformat()
            state["latest_received_at"] = received.isoformat()
            state["last_received_datetime"] = received
            if item.update_id in state["seen_update_ids"]:
                state["duplicate_update_ids"] += 1
            if state["last_update_id"] is not None and item.update_id < state["last_update_id"]:
                state["out_of_order_update_ids"] += 1
            state["seen_update_ids"].add(item.update_id)
            if state["last_update_id"] is None or item.update_id > state["last_update_id"]:
                state["last_update_id"] = item.update_id
            state["records"] = sequence
            if sequence % 25 == 0 or state["duplicate_update_ids"] or state["out_of_order_update_ids"]:
                self._refresh_summaries_locked()
                self._write_manifest_locked()

    def _summary_locked(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for symbol, state in self._state.items():
            out[symbol] = {
                key: (round(value, 3) if key == "maximum_inter_event_gap_seconds" else value)
                for key, value in state.items()
                if key not in {"seen_update_ids", "last_received_datetime", "last_update_id", "line_count_since_fsync"}
            }
        return out

    def _refresh_summaries_locked(self) -> None:
        self._manifest["symbols_summary"] = self._summary_locked()

    def _write_manifest_locked(self) -> None:
        temporary = self.manifest_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(self._manifest, stream, sort_keys=True, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.manifest_path)

    def acceptance(self, elapsed_seconds: float, ended_at: datetime) -> dict[str, Any]:
        summary = self._summary_locked()
        start_raw = self._manifest.get("capture_started_at")
        start = _timestamp(start_raw, "capture_started_at") if start_raw else None
        end = _aware_utc(ended_at, "capture_ended_at")
        return _evaluate(
            symbols=self.symbols,
            summary=summary,
            start_at=start,
            end_at=end,
            elapsed_seconds=elapsed_seconds,
            target_duration_seconds=self.duration_seconds,
            max_inter_event_gap_seconds=self.max_inter_event_gap_seconds,
            fatal_errors=self._manifest["fatal_errors"],
        )

    def finalize(
        self,
        *,
        status: str,
        ended_at: datetime,
        elapsed_seconds: float,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"COMPLETED", "BLOCKED", "INTERRUPTED"}:
            raise ProspectiveBookTickerCaptureError("FINAL_STATUS_INVALID")
        end = _aware_utc(ended_at, "capture_ended_at")
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ProspectiveBookTickerCaptureError("CAPTURE_ELAPSED_TIME_INVALID")
        with self._lock:
            start_raw = self._manifest.get("capture_started_at")
            if start_raw and end < _timestamp(start_raw, "capture_started_at"):
                raise ProspectiveBookTickerCaptureError("CAPTURE_END_PRECEDES_START")
            for handle in self._handles.values():
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
            self._handles.clear()

            acceptance = self.acceptance(elapsed_seconds, end)
            if status == "COMPLETED" and not acceptance["accepted"]:
                status = "BLOCKED"
                reason = reason or "ACCEPTANCE_CRITERIA_NOT_MET"
            self._manifest["capture_ended_at"] = end.isoformat()
            self._manifest["actual_elapsed_seconds"] = round(elapsed_seconds, 3)
            self._manifest["acceptance"] = acceptance
            self._manifest["status"] = status
            self._manifest["reason"] = reason
            self._refresh_summaries_locked()
            files: dict[str, Any] = {}
            for symbol in self.symbols:
                path = self.run_dir / f"{symbol}-bookTicker.jsonl"
                files[path.name] = {
                    "bytes": path.stat().st_size,
                    "sha256": _file_hash(path),
                    "records": self._state[symbol]["records"],
                }
            self._manifest["files"] = files
            self._write_manifest_locked()
            return json.loads(json.dumps(self._manifest))


def validate_manifest(path: Path) -> dict[str, Any]:
    """Independently verify manifest, hashes, quote schema, chronology, IDs and duration."""
    manifest_path = Path(path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProspectiveBookTickerCaptureError("MANIFEST_UNREADABLE") from exc
    if not isinstance(manifest, Mapping) or type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ProspectiveBookTickerCaptureError("MANIFEST_SCHEMA_INVALID")
    if manifest.get("status") != "COMPLETED":
        raise ProspectiveBookTickerCaptureError("STATUS_NOT_COMPLETED")
    symbols = manifest.get("symbols")
    if not isinstance(symbols, list) or len(symbols) != 2 or set(symbols) != set(APPROVED_SYMBOLS):
        raise ProspectiveBookTickerCaptureError("MANIFEST_SYMBOL_SET_INVALID")
    config = manifest.get("configuration")
    if not isinstance(config, Mapping) or type(config.get("schema_version")) is not int or config.get("schema_version") != SCHEMA_VERSION:
        raise ProspectiveBookTickerCaptureError("CAPTURE_CONFIGURATION_SCHEMA_INVALID")
    if config.get("capture_kind") != "PROSPECTIVE_PUBLIC_BINANCE_SPOT_BOOK_TICKER" or config.get("symbols") != symbols:
        raise ProspectiveBookTickerCaptureError("CAPTURE_CONFIGURATION_IDENTITY_MISMATCH")
    configured_source = config.get("source")
    if (
        not isinstance(configured_source, Mapping)
        or configured_source.get("provider") != "binance"
        or configured_source.get("market") != "SPOT"
        or configured_source.get("venue") != "BINANCE"
        or configured_source.get("source_id") != BOOK_TICKER_SOURCE
        or configured_source.get("observation_type") != BOOK_TICKER_OBSERVATION_TYPE
    ):
        raise ProspectiveBookTickerCaptureError("CAPTURE_CONFIGURATION_SOURCE_INVALID")
    max_gap = config.get("maximum_inter_event_gap_seconds")
    if isinstance(max_gap, bool) or not isinstance(max_gap, (int, float)) or not math.isfinite(max_gap) or max_gap <= 0:
        raise ProspectiveBookTickerCaptureError("CAPTURE_CONFIGURATION_MAX_GAP_INVALID")
    if hashlib.sha256(_canonical_json(config)).hexdigest() != manifest.get("configuration_sha256"):
        raise ProspectiveBookTickerCaptureError("CONFIGURATION_HASH_MISMATCH")
    target_duration = manifest.get("target_duration_seconds")
    elapsed = manifest.get("actual_elapsed_seconds")
    if type(target_duration) is not int or isinstance(target_duration, bool) or target_duration < DEFAULT_DURATION_SECONDS:
        raise ProspectiveBookTickerCaptureError("TARGET_DURATION_INVALID")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < target_duration:
        raise ProspectiveBookTickerCaptureError("ELAPSED_DURATION_INVALID")
    start = _timestamp(manifest.get("capture_started_at"), "capture_started_at")
    end = _timestamp(manifest.get("capture_ended_at"), "capture_ended_at")
    if end < start or abs((end - start).total_seconds() - float(elapsed)) > 120.0:
        raise ProspectiveBookTickerCaptureError("WALL_CLOCK_DURATION_MISMATCH")

    files = manifest.get("files")
    summaries = manifest.get("symbols_summary")
    if not isinstance(files, Mapping) or not isinstance(summaries, Mapping):
        raise ProspectiveBookTickerCaptureError("MANIFEST_FILE_INDEX_INVALID")
    recomputed_summary: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        filename = f"{symbol}-bookTicker.jsonl"
        metadata = files.get(filename)
        if not isinstance(metadata, Mapping):
            raise ProspectiveBookTickerCaptureError("DATASET_FILE_MISSING")
        dataset_path = manifest_path.parent / filename
        if not dataset_path.is_file():
            raise ProspectiveBookTickerCaptureError("DATASET_FILE_MISSING")
        if type(metadata.get("bytes")) is not int or metadata["bytes"] != dataset_path.stat().st_size:
            raise ProspectiveBookTickerCaptureError("DATASET_FILE_SIZE_MISMATCH")
        if metadata.get("sha256") != _file_hash(dataset_path):
            raise ProspectiveBookTickerCaptureError("DATASET_FILE_HASH_MISMATCH")
        raw_lines = dataset_path.read_text(encoding="utf-8").splitlines()
        if type(metadata.get("records")) is not int or metadata["records"] != len(raw_lines):
            raise ProspectiveBookTickerCaptureError("DATASET_RECORD_COUNT_MISMATCH")

        previous_received: datetime | None = None
        previous_update: int | None = None
        seen_updates: set[int] = set()
        duplicates = out_of_order = gap_count = clock_regressions = 0
        maximum_gap = 0.0
        first: datetime | None = None
        latest: datetime | None = None
        for index, raw_line in enumerate(raw_lines, start=1):
            try:
                envelope = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ProspectiveBookTickerCaptureError("RECORD_JSON_INVALID") from exc
            if not isinstance(envelope, Mapping) or type(envelope.get("schema_version")) is not int or envelope.get("schema_version") != SCHEMA_VERSION:
                raise ProspectiveBookTickerCaptureError("RECORD_ENVELOPE_INVALID")
            sequence = envelope.get("sequence")
            if type(sequence) is not int or sequence != index:
                raise ProspectiveBookTickerCaptureError("RECORD_SEQUENCE_INVALID")
            if envelope.get("run_id") != manifest.get("run_id") or envelope.get("symbol") != symbol:
                raise ProspectiveBookTickerCaptureError("RECORD_RUN_OR_SYMBOL_MISMATCH")
            quote = envelope.get("quote")
            if not isinstance(quote, Mapping):
                raise ProspectiveBookTickerCaptureError("QUOTE_RECORD_INVALID")
            expected_fields = {
                "schema", "symbol", "update_id", "bid", "bid_quantity", "ask", "ask_quantity",
                "observed_at", "received_at", "available_at", "provider_event_time", "source",
                "observation_type", "venue", "market",
            }
            if set(quote) != expected_fields or quote.get("schema") != "prospective-book-ticker-v1":
                raise ProspectiveBookTickerCaptureError("QUOTE_SCHEMA_INVALID")
            update_id = quote.get("update_id")
            if type(update_id) is not int or update_id < 0:
                raise ProspectiveBookTickerCaptureError("QUOTE_UPDATE_ID_INVALID")
            if quote.get("source") != BOOK_TICKER_SOURCE or quote.get("observation_type") != BOOK_TICKER_OBSERVATION_TYPE or quote.get("venue") != "BINANCE" or quote.get("market") != "SPOT":
                raise ProspectiveBookTickerCaptureError("QUOTE_PROVENANCE_INVALID")
            for name, positive in (("bid", True), ("ask", True), ("bid_quantity", False), ("ask_quantity", False)):
                raw = quote.get(name)
                if type(raw) is not str or not raw:
                    raise ProspectiveBookTickerCaptureError("QUOTE_DECIMAL_INVALID")
                try:
                    from decimal import Decimal
                    number = Decimal(raw)
                except Exception as exc:
                    raise ProspectiveBookTickerCaptureError("QUOTE_DECIMAL_INVALID") from exc
                if not number.is_finite() or (positive and number <= 0) or (not positive and number < 0):
                    raise ProspectiveBookTickerCaptureError("QUOTE_DECIMAL_INVALID")
                rendered = format(number, "f")
                if "." in rendered:
                    rendered = rendered.rstrip("0").rstrip(".")
                if rendered in {"", "-0"}:
                    rendered = "0"
                if rendered != raw:
                    raise ProspectiveBookTickerCaptureError("QUOTE_DECIMAL_NOT_CANONICAL")
            if Decimal(quote["ask"]) < Decimal(quote["bid"]):
                raise ProspectiveBookTickerCaptureError("QUOTE_MARKET_CROSSED")

            observed = _timestamp(quote.get("observed_at"), "observed_at")
            received = _timestamp(quote.get("received_at"), "received_at")
            available = _timestamp(quote.get("available_at"), "available_at")
            provider_event_raw = quote.get("provider_event_time")
            provider_event = _timestamp(provider_event_raw, "provider_event_time") if provider_event_raw is not None else None
            if observed > received or received > available or (provider_event is not None and provider_event > received):
                raise ProspectiveBookTickerCaptureError("QUOTE_TEMPORAL_ORDER_INVALID")
            expected_observed = provider_event if provider_event is not None else received
            if observed != expected_observed:
                raise ProspectiveBookTickerCaptureError("QUOTE_OBSERVATION_TIME_SOURCE_MISMATCH")
            if received < start or received > end or available > end:
                raise ProspectiveBookTickerCaptureError("RECORD_OUTSIDE_RUN_INTERVAL")
            if quote.get("symbol") != symbol:
                raise ProspectiveBookTickerCaptureError("QUOTE_SYMBOL_MISMATCH")
            canonical_quote = dict(quote)
            calculated_hash = _quote_hash(canonical_quote)
            if envelope.get("record_sha256") != calculated_hash:
                raise ProspectiveBookTickerCaptureError("RECORD_HASH_MISMATCH")

            if first is None:
                first = received
            if previous_received is not None:
                raw_gap = (received - previous_received).total_seconds()
                if raw_gap < 0:
                    clock_regressions += 1
                gap = max(0.0, raw_gap)
                maximum_gap = max(maximum_gap, gap)
                if gap > float(config["maximum_inter_event_gap_seconds"]):
                    gap_count += 1
            if update_id in seen_updates:
                duplicates += 1
            if previous_update is not None and update_id < previous_update:
                out_of_order += 1
            seen_updates.add(update_id)
            previous_update = update_id
            previous_received = received
            latest = received

        recomputed_summary[symbol] = {
            "records": len(raw_lines),
            "duplicate_update_ids": duplicates,
            "out_of_order_update_ids": out_of_order,
            "inter_event_gap_count": gap_count,
            "maximum_inter_event_gap_seconds": round(maximum_gap, 3),
            "clock_regressions": clock_regressions,
            "first_received_at": None if first is None else first.isoformat(),
            "latest_received_at": None if latest is None else latest.isoformat(),
        }
        saved = summaries.get(symbol)
        if not isinstance(saved, Mapping):
            raise ProspectiveBookTickerCaptureError("SYMBOL_SUMMARY_MISSING")
        for key in (
            "records", "duplicate_update_ids", "out_of_order_update_ids",
            "inter_event_gap_count", "maximum_inter_event_gap_seconds",
            "clock_regressions", "first_received_at", "latest_received_at",
        ):
            if saved.get(key) != recomputed_summary[symbol][key]:
                raise ProspectiveBookTickerCaptureError("SYMBOL_SUMMARY_MISMATCH")

    if not isinstance(manifest.get("fatal_errors"), list) or manifest["fatal_errors"]:
        raise ProspectiveBookTickerCaptureError("FATAL_ERRORS_PRESENT_OR_INVALID")
    if not isinstance(manifest.get("acceptance"), Mapping) or manifest["acceptance"].get("accepted") is not True:
        raise ProspectiveBookTickerCaptureError("ACCEPTANCE_NOT_PASSED")
    recomputed = _evaluate(
        symbols=symbols,
        summary=recomputed_summary,
        start_at=start,
        end_at=end,
        elapsed_seconds=float(elapsed),
        target_duration_seconds=target_duration,
        max_inter_event_gap_seconds=float(config["maximum_inter_event_gap_seconds"]),
        fatal_errors=manifest.get("fatal_errors", []),
    )
    if not recomputed["accepted"]:
        raise ProspectiveBookTickerCaptureError("INDEPENDENT_ACCEPTANCE_FAILED")
    return {"status": "VALID", "run_id": manifest["run_id"], "symbols": symbols, "acceptance": recomputed}


def capture(
    *,
    output_dir: Path,
    symbols: Sequence[str] = APPROVED_SYMBOLS,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    max_messages_per_connection: int = DEFAULT_MAX_MESSAGES_PER_CONNECTION,
    timeout_seconds: float = 10.0,
    reconnect_base_seconds: float = DEFAULT_RECONNECT_BASE_SECONDS,
    reconnect_max_seconds: float = DEFAULT_RECONNECT_MAX_SECONDS,
    max_inter_event_gap_seconds: float = DEFAULT_MAX_INTER_EVENT_GAP_SECONDS,
    align_next_utc_midnight: bool = True,
) -> dict[str, Any]:
    """Capture quote streams for both symbols with fail-closed persistence and bounded reconnects."""
    if isinstance(max_messages_per_connection, bool) or type(max_messages_per_connection) is not int or max_messages_per_connection < 1:
        raise ProspectiveBookTickerCaptureError("MAX_MESSAGES_PER_CONNECTION_INVALID")
    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ProspectiveBookTickerCaptureError("TIMEOUT_INVALID")
    if not math.isfinite(reconnect_base_seconds) or not math.isfinite(reconnect_max_seconds) or reconnect_base_seconds <= 0 or reconnect_max_seconds < reconnect_base_seconds:
        raise ProspectiveBookTickerCaptureError("RECONNECT_BACKOFF_INVALID")

    journal = ProspectiveBookTickerJournal(
        output_dir=output_dir,
        symbols=symbols,
        duration_seconds=duration_seconds,
        max_inter_event_gap_seconds=max_inter_event_gap_seconds,
    )
    stop = threading.Event()
    scheduled: datetime | None = None
    if align_next_utc_midnight:
        scheduled = _next_utc_midnight(_now())
        journal.schedule(scheduled)
        while _now() < scheduled:
            time.sleep(min(1.0, max(0.05, (scheduled - _now()).total_seconds())))

    started_at = _now()
    monotonic_start = time.monotonic()
    journal.start(started_at)

    def worker(symbol: str) -> None:
        backoff = reconnect_base_seconds
        while not stop.is_set() and time.monotonic() - monotonic_start < duration_seconds:
            journal.note_connection_attempt(symbol)
            adapter = BinanceSpotBookTickerWebSocketAdapter(timeout_seconds=timeout_seconds)
            try:
                adapter.listen(
                    symbol=symbol,
                    max_messages=max_messages_per_connection,
                    on_item=journal.accept,
                    should_stop=stop.is_set,
                    retain_results=False,
                )
                backoff = reconnect_base_seconds
            except BinanceWebSocketServerShutdown as exc:
                journal.note_transport_error(symbol, exc)
                stop.wait(backoff)
                backoff = min(reconnect_max_seconds, backoff * 2)
            except BinanceWebSocketTransportError as exc:
                journal.note_transport_error(symbol, exc, reconnecting=True)
                stop.wait(backoff)
                backoff = min(reconnect_max_seconds, backoff * 2)
            except BinanceWebSocketError as exc:
                journal.note_fatal_error(symbol, exc)
                stop.set()
                return
            except Exception as exc:
                journal.note_fatal_error(symbol, exc)
                stop.set()
                return

    threads = [threading.Thread(target=worker, args=(symbol,), name=f"bookticker-{symbol}", daemon=True) for symbol in symbols]
    for thread in threads:
        thread.start()
    while time.monotonic() - monotonic_start < duration_seconds and not stop.is_set():
        time.sleep(min(1.0, max(0.05, duration_seconds - (time.monotonic() - monotonic_start))))
    elapsed = max(0.0, time.monotonic() - monotonic_start)
    stop.set()
    for thread in threads:
        thread.join(timeout=timeout_seconds + 2.0)
        if thread.is_alive():
            journal.note_fatal_error(None, RuntimeError("BOOK_TICKER_WORKER_DID_NOT_STOP"))
    ended_at = _now()
    status = "BLOCKED" if journal.fatal else "COMPLETED"
    return journal.finalize(
        status=status,
        ended_at=ended_at,
        elapsed_seconds=elapsed,
        reason="FATAL_CAPTURE_OR_PERSISTENCE_ERROR" if journal.fatal else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture and validate public Binance Spot bookTicker observations.")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("capture", help="capture bookTicker data for BTCUSDT/ETHUSDT")
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--symbols", nargs=2, default=list(APPROVED_SYMBOLS))
    run_parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    run_parser.add_argument("--max-messages-per-connection", type=int, default=DEFAULT_MAX_MESSAGES_PER_CONNECTION)
    run_parser.add_argument("--timeout-seconds", type=float, default=10.0)
    run_parser.add_argument("--max-inter-event-gap-seconds", type=float, default=DEFAULT_MAX_INTER_EVENT_GAP_SECONDS)
    run_parser.add_argument("--no-align-next-utc-midnight", action="store_true")
    validate_parser = sub.add_parser("validate", help="independently validate a completed manifest")
    validate_parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if args.command == "capture":
        manifest = capture(
            output_dir=args.output_dir,
            symbols=args.symbols,
            duration_seconds=args.duration_seconds,
            max_messages_per_connection=args.max_messages_per_connection,
            timeout_seconds=args.timeout_seconds,
            max_inter_event_gap_seconds=args.max_inter_event_gap_seconds,
            align_next_utc_midnight=not args.no_align_next_utc_midnight,
        )
        print(json.dumps({"status": manifest["status"], "manifest": str(Path(args.output_dir) / manifest["run_id"] / "manifest.json"), "acceptance": manifest.get("acceptance")}, sort_keys=True))
        return 0 if manifest["status"] == "COMPLETED" else 2
    result = validate_manifest(args.manifest)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ProspectiveBookTickerCaptureError",
    "ProspectiveBookTickerJournal",
    "capture",
    "main",
    "validate_manifest",
]
