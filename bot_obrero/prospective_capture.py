"""Fail-closed prospective BTC/ETH Spot WebSocket capture with durable evidence.

This command captures public market data only. It does not calculate signals,
create proposals, submit orders, or enable LIVE execution. A run is accepted
only after the requested elapsed time and a complete UTC day of final 1h candles
have been demonstrated for both BTCUSDT and ETHUSDT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .binance_instruments import BinanceSpotInstrumentMetadata
from .binance_spot import BinanceSpotRestConfig
from .binance_websocket import (
    BinanceSpotWebSocketAdapter,
    BinanceWebSocketError,
    BinanceWebSocketTransportError,
)
from .market_data import MarketData
from .virtual_backtest import _market_data_record

SCHEMA_VERSION = 1
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_INTERVAL = "1h"
DEFAULT_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_GRACE_SECONDS = 5 * 60
DEFAULT_MAX_MESSAGES_PER_CONNECTION = 1000
DEFAULT_RECONNECT_BASE_SECONDS = 1.0
DEFAULT_RECONNECT_MAX_SECONDS = 60.0
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


class ProspectiveCaptureError(RuntimeError):
    """Invalid capture parameters, evidence, or durable sink state."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProspectiveCaptureError(f"{name.upper()}_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _complete_utc_days(candle_starts: Sequence[datetime]) -> list[str]:
    """Return UTC dates whose complete final 1h candle starts 00:00 through 23:00 exist."""
    hours_by_day: dict[str, set[int]] = {}
    for raw in candle_starts:
        stamp = _aware_utc(raw, "candle_start")
        if stamp.minute != 0 or stamp.second != 0 or stamp.microsecond != 0:
            continue
        hours_by_day.setdefault(stamp.date().isoformat(), set()).add(stamp.hour)
    expected = set(range(24))
    return sorted(day for day, hours in hours_by_day.items() if hours == expected)


def _next_utc_midnight(value: datetime) -> datetime:
    stamp = _aware_utc(value, "schedule_time")
    today = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return today if stamp == today else today + timedelta(days=1)


def _validate_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(symbols)
    if len(normalized) != 2 or set(normalized) != set(DEFAULT_SYMBOLS):
        raise ProspectiveCaptureError("CAPTURE_REQUIRES_EXACT_PAIR_BTCUSDT_ETHUSDT")
    if any(not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol) for symbol in normalized):
        raise ProspectiveCaptureError("SYMBOL_FORMAT_INVALID")
    return normalized


def _safe_run_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", value):
        raise ProspectiveCaptureError("RUN_ID_INVALID")
    return value


class ProspectiveCaptureJournal:
    """Thread-safe JSONL event sink and auditable run manifest."""

    def __init__(
        self,
        *,
        output_dir: Path,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        interval: str = DEFAULT_INTERVAL,
        duration_seconds: int = DEFAULT_DURATION_SECONDS,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        fsync_every_records: int = 1,
        run_id: str | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.symbols = _validate_symbols(symbols)
        if interval != DEFAULT_INTERVAL:
            raise ProspectiveCaptureError("PROSPECTIVE_BTC_ETH_CAPTURE_REQUIRES_1H")
        for value, name in (
            (duration_seconds, "duration_seconds"),
            (grace_seconds, "grace_seconds"),
            (fsync_every_records, "fsync_every_records"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ProspectiveCaptureError(f"{name.upper()}_MUST_BE_POSITIVE_INTEGER")
        if duration_seconds < DEFAULT_DURATION_SECONDS:
            raise ProspectiveCaptureError("CAPTURE_DURATION_MUST_BE_AT_LEAST_24_HOURS")
        self.interval = interval
        self.duration_seconds = duration_seconds
        self.grace_seconds = grace_seconds
        self.fsync_every_records = fsync_every_records
        self._now = now
        self._lock = threading.RLock()
        self.created_at = _aware_utc(now(), "created_at")
        self.run_id = _safe_run_id(run_id or self.created_at.strftime("btceth-%Y%m%dT%H%M%S%fZ"))
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir = output_dir / self.run_id
        self.run_dir.mkdir(parents=False, exist_ok=False)
        self.manifest_path = self.run_dir / "manifest.json"
        self._handles = {
            symbol: (self.run_dir / f"{symbol}.jsonl").open("x", encoding="utf-8", newline="\n")
            for symbol in self.symbols
        }
        self._state: dict[str, dict[str, Any]] = {
            symbol: {
                "records": 0,
                "final_candles": 0,
                "duplicate_exact_records": 0,
                "duplicate_final_candles": 0,
                "out_of_order_final_candles": 0,
                "gap_hours": 0,
                "unaligned_final_candles": 0,
                "connection_attempts": 0,
                "transport_errors": 0,
                "reconnects": 0,
                "last_error": None,
                "latest_received_at": None,
                "first_received_at": None,
                "latest_final_candle_start": None,
                "final_candle_starts": [],
                "seen_record_hashes": set(),
                "last_final_start": None,
                "line_count_since_fsync": 0,
                "file_path": f"{symbol}.jsonl",
            }
            for symbol in self.symbols
        }
        config = {
            "schema_version": SCHEMA_VERSION,
            "capture_kind": "PROSPECTIVE_PUBLIC_BINANCE_SPOT_KLINE",
            "symbols": list(self.symbols),
            "interval": self.interval,
            "target_duration_seconds": self.duration_seconds,
            "completion_grace_seconds": self.grace_seconds,
            "maximum_messages_per_connection": DEFAULT_MAX_MESSAGES_PER_CONNECTION,
            "reconnect_backoff_seconds": {
                "base": DEFAULT_RECONNECT_BASE_SECONDS,
                "maximum": DEFAULT_RECONNECT_MAX_SECONDS,
                "strategy": "exponential_capped_no_jitter",
            },
            "availability_policy": "EXPLICIT_CONSUMER_HANDOFF_CLOCK",
            "finality_policy": "BINANCE_KLINE_X_FLAG",
            "acceptance_policy": "24h elapsed plus at least one complete UTC day of 24 unique FINAL 1h candles per symbol, without hourly gaps",
            "mode": "PUBLIC_DATA_ONLY_NO_ORDERS",
            "source": {
                "provider": "binance",
                "venue": "BINANCE",
                "market": "SPOT",
                "source_id": "binance-spot-websocket",
            },
        }
        self.configuration_sha256 = _json_hash(config)
        self._manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": "PREPARING",
            "created_at": self.created_at.isoformat(),
            "scheduled_capture_start_at": None,
            "capture_started_at": None,
            "capture_ended_at": None,
            "target_duration_seconds": self.duration_seconds,
            "actual_elapsed_seconds": None,
            "interval": self.interval,
            "symbols": list(self.symbols),
            "configuration": config,
            "configuration_sha256": self.configuration_sha256,
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
                raise ProspectiveCaptureError("CAPTURE_ALREADY_STARTED")
            self._manifest["capture_started_at"] = stamp.isoformat()
            self._manifest["status"] = "RUNNING"
            self._write_manifest_locked()

    def note_connection_attempt(self, symbol: str) -> None:
        with self._lock:
            self._state[symbol]["connection_attempts"] += 1
            self._refresh_symbol_summaries_locked()
            self._write_manifest_locked()

    def note_transport_error(self, symbol: str, error: BaseException, *, reconnecting: bool) -> None:
        with self._lock:
            state = self._state[symbol]
            state["transport_errors"] += 1
            if reconnecting:
                state["reconnects"] += 1
            state["last_error"] = f"{type(error).__name__}:{str(error)[:500]}"
            self._refresh_symbol_summaries_locked()
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

    def accept(self, item: MarketData) -> None:
        if not isinstance(item, MarketData):
            raise ProspectiveCaptureError("CAPTURE_ITEM_CONTRACT_INVALID")
        symbol = item.instrument.symbol.replace("/", "")
        if symbol not in self.symbols:
            raise ProspectiveCaptureError("CAPTURE_SYMBOL_NOT_IN_APPROVED_PAIR")
        if (
            item.source.source_id != "binance-spot-websocket"
            or item.source.provider != "binance"
            or item.source.venue != "BINANCE"
            or item.instrument.market != "SPOT"
            or item.payload.timeframe != self.interval
        ):
            raise ProspectiveCaptureError("CAPTURE_SOURCE_OR_TIMEFRAME_INVALID")
        if item.available_at is None:
            raise ProspectiveCaptureError("CAPTURE_AVAILABILITY_UNKNOWN")
        received = _aware_utc(item.received_at, "received_at")
        available = _aware_utc(item.available_at, "available_at")
        observed = _aware_utc(item.observed_at, "observed_at")
        if observed > received or received > available:
            raise ProspectiveCaptureError("CAPTURE_TEMPORAL_ORDER_INVALID")
        record = _market_data_record(item)
        record_bytes = _canonical_json(record)
        fingerprint = hashlib.sha256(record_bytes).hexdigest()

        with self._lock:
            if self._manifest["status"] != "RUNNING":
                raise ProspectiveCaptureError("CAPTURE_NOT_RUNNING")
            state = self._state[symbol]
            seq = state["records"] + 1
            duplicate_exact = fingerprint in state["seen_record_hashes"]
            envelope = {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "symbol": symbol,
                "sequence": seq,
                "record_sha256": fingerprint,
                "duplicate_exact": duplicate_exact,
                "market_data": record,
            }
            handle = self._handles[symbol]
            handle.write(json.dumps(envelope, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            state["line_count_since_fsync"] += 1
            if state["line_count_since_fsync"] >= self.fsync_every_records:
                os.fsync(handle.fileno())
                state["line_count_since_fsync"] = 0

            state["records"] = seq
            state["seen_record_hashes"].add(fingerprint)
            if duplicate_exact:
                state["duplicate_exact_records"] += 1
            if state["first_received_at"] is None:
                state["first_received_at"] = received.isoformat()
            state["latest_received_at"] = received.isoformat()
            candle = item.payload
            if (
                candle.candle_state.value == "CLOSED"
                and candle.completeness.value == "COMPLETE"
                and candle.finality.value == "FINAL"
            ):
                start = _aware_utc(candle.start, "candle_start")
                state["final_candles"] += 1
                state["final_candle_starts"].append(start)
                last = state["last_final_start"]
                if start.minute != 0 or start.second != 0 or start.microsecond != 0:
                    state["unaligned_final_candles"] += 1
                if last is not None:
                    delta = start - last
                    if delta == timedelta(0):
                        state["duplicate_final_candles"] += 1
                    elif delta < timedelta(0):
                        state["out_of_order_final_candles"] += 1
                    elif delta > timedelta(hours=1):
                        state["gap_hours"] += max(1, int(delta.total_seconds() // 3600) - 1)
                if last is None or start > last:
                    state["last_final_start"] = start
                    state["latest_final_candle_start"] = start.isoformat()
            if state["records"] % 25 == 0 or duplicate_exact or candle.finality.value == "FINAL":
                self._refresh_symbol_summaries_locked()
                self._write_manifest_locked()

    def has_complete_utc_day(self, symbol: str) -> bool:
        with self._lock:
            starts = tuple(self._state[symbol]["final_candle_starts"])
        return bool(_complete_utc_days(starts))

    def acceptance(self, elapsed_seconds: float) -> dict[str, Any]:
        with self._lock:
            summary: dict[str, Any] = {}
            for symbol in self.symbols:
                state = self._state[symbol]
                days = _complete_utc_days(state["final_candle_starts"])
                summary[symbol] = {
                    "records": state["records"],
                    "final_candle_events": state["final_candles"],
                    "unique_final_candle_starts": len(set(state["final_candle_starts"])),
                    "complete_utc_days": days,
                    "has_complete_utc_day": bool(days),
                    "gap_hours": state["gap_hours"],
                    "duplicate_final_candles": state["duplicate_final_candles"],
                    "out_of_order_final_candles": state["out_of_order_final_candles"],
                    "unaligned_final_candles": state["unaligned_final_candles"],
                    "duplicate_exact_records": state["duplicate_exact_records"],
                    "transport_errors": state["transport_errors"],
                    "reconnects": state["reconnects"],
                    "connection_attempts": state["connection_attempts"],
                    "first_received_at": state["first_received_at"],
                    "latest_received_at": state["latest_received_at"],
                    "latest_final_candle_start": state["latest_final_candle_start"],
                }
            elapsed_met = elapsed_seconds >= self.duration_seconds
            no_fatal = not self._manifest["fatal_errors"]
            symbols_pass = all(
                summary[symbol]["has_complete_utc_day"]
                and summary[symbol]["gap_hours"] == 0
                and summary[symbol]["out_of_order_final_candles"] == 0
                and summary[symbol]["unaligned_final_candles"] == 0
                for symbol in self.symbols
            )
            return {
                "elapsed_duration_met": elapsed_met,
                "target_duration_seconds": self.duration_seconds,
                "actual_elapsed_seconds": round(elapsed_seconds, 3),
                "all_symbols_have_complete_utc_day": all(summary[symbol]["has_complete_utc_day"] for symbol in self.symbols),
                "hourly_integrity_pass": symbols_pass,
                "no_fatal_errors": no_fatal,
                "accepted": elapsed_met and symbols_pass and no_fatal,
                "symbols": summary,
            }

    def finalize(self, *, status: str, ended_at: datetime, elapsed_seconds: float, reason: str | None = None) -> dict[str, Any]:
        if status not in {"COMPLETED", "BLOCKED", "INTERRUPTED"}:
            raise ProspectiveCaptureError("FINAL_STATUS_INVALID")
        stamp = _aware_utc(ended_at, "capture_ended_at")
        with self._lock:
            for handle in self._handles.values():
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
            self._handles.clear()
            acceptance = self.acceptance(elapsed_seconds)
            if status == "COMPLETED" and not acceptance["accepted"]:
                status = "BLOCKED"
                reason = reason or "ACCEPTANCE_CRITERIA_NOT_MET"
            files: dict[str, Any] = {}
            for symbol in self.symbols:
                path = self.run_dir / f"{symbol}.jsonl"
                files[path.name] = {
                    "bytes": path.stat().st_size,
                    "sha256": _file_hash(path),
                    "records": self._state[symbol]["records"],
                }
            self._manifest["status"] = status
            self._manifest["capture_ended_at"] = stamp.isoformat()
            self._manifest["actual_elapsed_seconds"] = round(elapsed_seconds, 3)
            self._manifest["acceptance"] = acceptance
            self._manifest["files"] = files
            self._manifest["reason"] = reason
            self._refresh_symbol_summaries_locked()
            self._write_manifest_locked()
            return json.loads(json.dumps(self._manifest))

    def _refresh_symbol_summaries_locked(self) -> None:
        summaries: dict[str, Any] = {}
        for symbol, state in self._state.items():
            summaries[symbol] = {
                key: value for key, value in state.items()
                if key not in {"seen_record_hashes", "final_candle_starts", "last_final_start", "line_count_since_fsync"}
            }
            summaries[symbol]["complete_utc_days"] = _complete_utc_days(state["final_candle_starts"])
        self._manifest["symbols_summary"] = summaries

    def _write_manifest_locked(self) -> None:
        temp_path = self.manifest_path.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self._manifest, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.manifest_path)


AdapterFactory = Callable[[str, datetime_callable], BinanceSpotWebSocketAdapter]
datetime_callable = Callable[[], datetime]


def default_adapter_factory(symbol: str, clock: datetime_callable = _utc_now) -> BinanceSpotWebSocketAdapter:
    """Build the existing public Spot adapter; no private key or trading endpoint is used."""
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    return BinanceSpotWebSocketAdapter(
        metadata=metadata,
        clock=clock,
        consumer_handoff_clock=clock,
        timeout_seconds=10.0,
    )


def run_capture(
    *,
    output_dir: Path,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    interval: str = DEFAULT_INTERVAL,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    max_messages_per_connection: int = DEFAULT_MAX_MESSAGES_PER_CONNECTION,
    reconnect_base_seconds: float = DEFAULT_RECONNECT_BASE_SECONDS,
    reconnect_max_seconds: float = DEFAULT_RECONNECT_MAX_SECONDS,
    align_next_utc_midnight: bool = True,
    fsync_every_records: int = 1,
    adapter_factory: Callable[[str, Callable[[], datetime]], BinanceSpotWebSocketAdapter] = default_adapter_factory,
    now: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
    stop_event: threading.Event | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run two independent public streams; transport errors reconnect with capped backoff."""
    if isinstance(max_messages_per_connection, bool) or not isinstance(max_messages_per_connection, int) or max_messages_per_connection < 1:
        raise ProspectiveCaptureError("MAX_MESSAGES_PER_CONNECTION_INVALID")
    if reconnect_base_seconds <= 0 or reconnect_max_seconds < reconnect_base_seconds:
        raise ProspectiveCaptureError("RECONNECT_BACKOFF_INVALID")
    if duration_seconds < DEFAULT_DURATION_SECONDS:
        raise ProspectiveCaptureError("CAPTURE_DURATION_MUST_BE_AT_LEAST_24_HOURS")
    stop = stop_event or threading.Event()
    journal = ProspectiveCaptureJournal(
        output_dir=Path(output_dir),
        symbols=symbols,
        interval=interval,
        duration_seconds=duration_seconds,
        grace_seconds=grace_seconds,
        fsync_every_records=fsync_every_records,
        now=now,
    )
    created = _aware_utc(now(), "schedule_time")
    scheduled_start = _next_utc_midnight(created) if align_next_utc_midnight else created
    journal.schedule(scheduled_start)
    try:
        while _aware_utc(now(), "current_time") < scheduled_start:
            if stop.is_set():
                ended = _aware_utc(now(), "ended_at")
                manifest = journal.finalize(status="INTERRUPTED", ended_at=ended, elapsed_seconds=0.0, reason="STOPPED_BEFORE_SCHEDULED_START")
                return 2, manifest
            wait(min(5.0, max(0.1, (scheduled_start - _aware_utc(now(), "current_time")).total_seconds())))
    except KeyboardInterrupt:
        stop.set()
        ended = _aware_utc(now(), "ended_at")
        manifest = journal.finalize(status="INTERRUPTED", ended_at=ended, elapsed_seconds=0.0, reason="INTERRUPTED_BEFORE_SCHEDULED_START")
        return 130, manifest

    actual_start = _aware_utc(now(), "capture_started_at")
    start_mono = monotonic()
    journal.start(actual_start)
    fatal_errors: list[BaseException] = []

    def worker(symbol: str) -> None:
        consecutive_failures = 0
        try:
            adapter = adapter_factory(symbol, now)
        except BaseException as exc:
            journal.note_fatal_error(symbol, exc)
            fatal_errors.append(exc)
            stop.set()
            return
        while not stop.is_set():
            journal.note_connection_attempt(symbol)
            try:
                adapter.listen(
                    symbol=symbol,
                    interval=interval,
                    max_messages=max_messages_per_connection,
                    on_item=journal.accept,
                    should_stop=stop.is_set,
                )
                consecutive_failures = 0
                if not stop.is_set():
                    # The adapter intentionally rotates after each bounded batch.
                    continue
            except BinanceWebSocketTransportError as exc:
                if stop.is_set():
                    return
                consecutive_failures += 1
                delay = min(reconnect_max_seconds, reconnect_base_seconds * (2 ** min(consecutive_failures - 1, 10)))
                journal.note_transport_error(symbol, exc, reconnecting=True)
                stop.wait(delay)
            except BinanceWebSocketError as exc:
                journal.note_fatal_error(symbol, exc)
                fatal_errors.append(exc)
                stop.set()
                return
            except BaseException as exc:
                # Includes JSONL/fsync/manifest failures: stop rather than retrying a sink failure.
                journal.note_fatal_error(symbol, exc)
                fatal_errors.append(exc)
                stop.set()
                return

    threads = [
        threading.Thread(target=worker, args=(symbol,), name=f"capture-{symbol}", daemon=False)
        for symbol in journal.symbols
    ]
    for thread in threads:
        thread.start()

    interrupted = False
    try:
        deadline = start_mono + duration_seconds + grace_seconds
        while not stop.is_set():
            elapsed = max(0.0, monotonic() - start_mono)
            ready = elapsed >= duration_seconds and all(journal.has_complete_utc_day(symbol) for symbol in journal.symbols)
            if ready:
                break
            if monotonic() >= deadline:
                break
            wait(0.5)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=30.0)

    if any(thread.is_alive() for thread in threads):
        journal.note_fatal_error(None, ProspectiveCaptureError("CAPTURE_WORKER_DID_NOT_STOP"))
        fatal_errors.append(ProspectiveCaptureError("CAPTURE_WORKER_DID_NOT_STOP"))

    ended_at = _aware_utc(now(), "capture_ended_at")
    elapsed = max(0.0, monotonic() - start_mono)
    acceptance = journal.acceptance(elapsed)
    if interrupted:
        status, reason, exit_code = "INTERRUPTED", "USER_INTERRUPTED_CAPTURE", 130
    elif fatal_errors or journal.fatal:
        status, reason, exit_code = "BLOCKED", "FATAL_CAPTURE_OR_PERSISTENCE_ERROR", 2
    elif acceptance["accepted"]:
        status, reason, exit_code = "COMPLETED", None, 0
    else:
        status, reason, exit_code = "BLOCKED", "DURATION_OR_UTC_DAY_INTEGRITY_NOT_MET", 2
    manifest = journal.finalize(status=status, ended_at=ended_at, elapsed_seconds=elapsed, reason=reason)
    return exit_code, manifest


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    """Re-hash all datasets and independently validate a completed capture."""
    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProspectiveCaptureError(f"MANIFEST_READ_FAILED:{exc}") from exc
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ProspectiveCaptureError("MANIFEST_SCHEMA_INVALID")
    if manifest.get("status") != "COMPLETED":
        raise ProspectiveCaptureError("CAPTURE_STATUS_NOT_COMPLETED")
    if not isinstance(manifest.get("acceptance"), Mapping) or manifest["acceptance"].get("accepted") is not True:
        raise ProspectiveCaptureError("CAPTURE_ACCEPTANCE_NOT_TRUE")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {f"{s}.jsonl" for s in _validate_symbols(manifest.get("symbols", []))}:
        raise ProspectiveCaptureError("MANIFEST_FILE_SET_INVALID")
    for filename, details in files.items():
        data_path = path.parent / filename
        if not data_path.is_file() or not isinstance(details, Mapping):
            raise ProspectiveCaptureError(f"CAPTURE_FILE_MISSING:{filename}")
        if data_path.stat().st_size != details.get("bytes") or _file_hash(data_path) != details.get("sha256"):
            raise ProspectiveCaptureError(f"CAPTURE_FILE_HASH_MISMATCH:{filename}")
        count = 0
        with data_path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                try:
                    envelope = json.loads(line)
                    record = envelope["market_data"]
                    fingerprint = _json_hash(record)
                except (ValueError, KeyError, TypeError) as exc:
                    raise ProspectiveCaptureError(f"CAPTURE_JSONL_INVALID:{filename}:{line_no}") from exc
                if envelope.get("schema_version") != SCHEMA_VERSION or envelope.get("run_id") != manifest.get("run_id"):
                    raise ProspectiveCaptureError(f"CAPTURE_RECORD_ENVELOPE_INVALID:{filename}:{line_no}")
                if envelope.get("record_sha256") != fingerprint:
                    raise ProspectiveCaptureError(f"CAPTURE_RECORD_HASH_MISMATCH:{filename}:{line_no}")
                count += 1
        if count != details.get("records"):
            raise ProspectiveCaptureError(f"CAPTURE_RECORD_COUNT_MISMATCH:{filename}")
    return {
        "status": "VALID",
        "run_id": manifest.get("run_id"),
        "manifest_path": str(path),
        "capture_started_at": manifest.get("capture_started_at"),
        "capture_ended_at": manifest.get("capture_ended_at"),
        "actual_elapsed_seconds": manifest.get("actual_elapsed_seconds"),
        "dataset_sha256": {filename: details["sha256"] for filename, details in files.items()},
        "acceptance": manifest["acceptance"],
    }


def _positive_integer(text: str) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not value > 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture prospective public Binance Spot BTC/ETH 1h evidence; no trading.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture", help="run until 24h elapsed and one UTC day is complete")
    capture.add_argument("--output-dir", type=Path, default=Path("evidence/prospective-btc-eth"))
    capture.add_argument("--symbols", nargs=2, choices=DEFAULT_SYMBOLS, default=DEFAULT_SYMBOLS)
    capture.add_argument("--interval", choices=(DEFAULT_INTERVAL,), default=DEFAULT_INTERVAL)
    capture.add_argument("--duration-seconds", type=_positive_integer, default=DEFAULT_DURATION_SECONDS)
    capture.add_argument("--grace-seconds", type=_positive_integer, default=DEFAULT_GRACE_SECONDS)
    capture.add_argument("--max-messages-per-connection", type=_positive_integer, default=DEFAULT_MAX_MESSAGES_PER_CONNECTION)
    capture.add_argument("--reconnect-base-seconds", type=_positive_float, default=DEFAULT_RECONNECT_BASE_SECONDS)
    capture.add_argument("--reconnect-max-seconds", type=_positive_float, default=DEFAULT_RECONNECT_MAX_SECONDS)
    capture.add_argument("--fsync-every-records", type=_positive_integer, default=1)
    capture.add_argument("--no-align-next-utc-midnight", action="store_true",
                         help="start immediately; the run may fail the full-UTC-day acceptance gate")
    validate = subparsers.add_parser("validate", help="validate completed manifest and file hashes")
    validate.add_argument("manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            print(json.dumps(validate_manifest(args.manifest), sort_keys=True, indent=2))
            return 0
        if args.duration_seconds < DEFAULT_DURATION_SECONDS:
            raise ProspectiveCaptureError("CAPTURE_DURATION_MUST_BE_AT_LEAST_24_HOURS")
        if args.reconnect_max_seconds < args.reconnect_base_seconds:
            raise ProspectiveCaptureError("RECONNECT_BACKOFF_INVALID")
        code, manifest = run_capture(
            output_dir=args.output_dir,
            duration_seconds=args.duration_seconds,
            symbols=args.symbols,
            interval=args.interval,
            grace_seconds=args.grace_seconds,
            max_messages_per_connection=args.max_messages_per_connection,
            reconnect_base_seconds=args.reconnect_base_seconds,
            reconnect_max_seconds=args.reconnect_max_seconds,
            align_next_utc_midnight=not args.no_align_next_utc_midnight,
            fsync_every_records=args.fsync_every_records,
        )
        print(json.dumps({
            "exit_code": code,
            "manifest_path": str(Path(args.output_dir) / manifest["run_id"] / "manifest.json"),
            "status": manifest["status"],
            "reason": manifest.get("reason"),
            "acceptance": manifest.get("acceptance"),
        }, sort_keys=True, indent=2))
        return code
    except ProspectiveCaptureError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
