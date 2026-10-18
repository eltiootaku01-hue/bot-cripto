from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot_obrero.binance_book_ticker import (
    BinanceBookTicker,
    parse_binance_book_ticker_event,
)
from bot_obrero.prospective_book_ticker_capture import (
    ProspectiveBookTickerCaptureError,
    ProspectiveBookTickerJournal,
    validate_manifest,
)

UTC = timezone.utc
START = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)


def make_quote(symbol: str, update_id: int, received_at: datetime) -> BinanceBookTicker:
    event = {
        "u": update_id,
        "s": symbol,
        "b": "65000.1",
        "B": "0.12",
        "a": "65000.2",
        "A": "0.34",
    }
    return parse_binance_book_ticker_event(
        event,
        requested_symbol=symbol,
        received_at=received_at,
        available_at=received_at,
    )


def test_reconnect_counter_tracks_new_connection_attempts_not_transport_errors(tmp_path):
    journal = ProspectiveBookTickerJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        now=lambda: START,
        run_id="bookticker-reconnect-counter",
    )
    journal.start(START)
    journal.note_connection_attempt("BTCUSDT")
    journal.note_transport_error("BTCUSDT", TimeoutError("socket timeout"))
    journal.note_connection_attempt("BTCUSDT")

    summary = journal._manifest["symbols_summary"]["BTCUSDT"]
    assert summary["connection_attempts"] == 2
    assert summary["reconnects"] == 1
    assert summary["transport_errors"] == 1

    result = journal.finalize(
        status="BLOCKED",
        ended_at=START + timedelta(seconds=1),
        elapsed_seconds=1,
        reason="unit_test_short_run",
    )
    assert result["status"] == "BLOCKED"


def test_journal_rejects_non_string_symbol_sequence_without_type_error(tmp_path):
    with pytest.raises(ProspectiveBookTickerCaptureError, match="EXACT_PAIR_BTCUSDT_ETHUSDT"):
        ProspectiveBookTickerJournal(
            output_dir=tmp_path,
            symbols=[[], {}],
            duration_seconds=86400,
            now=lambda: START,
            run_id="bookticker-bad-symbol-types",
        )


def test_journal_writes_hash_bound_quote_envelopes_and_rejects_unapproved_symbol(tmp_path):
    journal = ProspectiveBookTickerJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        fsync_every_records=10,
        now=lambda: START,
        run_id="bookticker-test-hash",
    )
    from dataclasses import replace

    journal.start(START)
    journal.accept(make_quote("BTCUSDT", 100, START))
    unapproved = replace(make_quote("ETHUSDT", 101, START + timedelta(seconds=1)), symbol="DOGEUSDT")
    with pytest.raises(ProspectiveBookTickerCaptureError, match="SYMBOL_NOT_APPROVED"):
        journal.accept(unapproved)
    result = journal.finalize(
        status="BLOCKED",
        ended_at=START + timedelta(seconds=1),
        elapsed_seconds=1,
        reason="unit_test_short_run",
    )
    filename = journal.run_dir / "BTCUSDT-bookTicker.jsonl"
    envelope = json.loads(filename.read_text(encoding="utf-8").splitlines()[0])
    assert envelope["schema_version"] == 1
    assert envelope["run_id"] == "bookticker-test-hash"
    assert envelope["sequence"] == 1
    assert len(envelope["record_sha256"]) == 64
    assert envelope["quote"]["source"] == "binance-public:spot-websocket-bookTicker"
    assert result["status"] == "BLOCKED"
    with pytest.raises(ProspectiveBookTickerCaptureError, match="STATUS_NOT_COMPLETED"):
        validate_manifest(journal.manifest_path)


def test_journal_rejects_an_item_whose_evidence_hash_was_changed(tmp_path):
    from dataclasses import replace

    journal = ProspectiveBookTickerJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        now=lambda: START,
        run_id="bookticker-test-hash-mismatch",
    )
    journal.start(START)
    quote = make_quote("BTCUSDT", 5, START)
    with pytest.raises(ProspectiveBookTickerCaptureError, match="EVIDENCE_HASH_MISMATCH"):
        journal.accept(replace(quote, bid="1", evidence_hash="0" * 64))
    result = journal.finalize(
        status="BLOCKED",
        ended_at=START + timedelta(seconds=1),
        elapsed_seconds=1,
        reason="unit_test_invalid_record",
    )
    assert result["status"] == "BLOCKED"
    assert result["symbols_summary"]["BTCUSDT"]["records"] == 0


def test_validator_rejects_unhashable_manifest_symbols_without_type_error(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "status": "COMPLETED",
        "symbols": [[], {}],
    }), encoding="utf-8")

    with pytest.raises(ProspectiveBookTickerCaptureError, match="MANIFEST_SYMBOL_SET_INVALID"):
        validate_manifest(manifest)


def test_validator_accepts_a_full_synthetic_24_hour_quote_capture_and_detects_file_tampering(tmp_path):
    # Synthetic data exercises the journal/validator contract only; it is not market evidence.
    journal = ProspectiveBookTickerJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        fsync_every_records=5000,
        max_inter_event_gap_seconds=10,
        now=lambda: START,
        run_id="bookticker-test-full-day",
    )
    journal.start(START)
    for second in range(0, 86401, 10):
        stamp = START + timedelta(seconds=second)
        journal.accept(make_quote("BTCUSDT", 100000 + second, stamp))
        journal.accept(make_quote("ETHUSDT", 200000 + second, stamp))
    result = journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(seconds=86400),
        elapsed_seconds=86400,
    )
    assert result["status"] == "COMPLETED"
    assert result["acceptance"]["accepted"] is True
    validation = validate_manifest(journal.manifest_path)
    assert validation["status"] == "VALID"
    assert validation["acceptance"]["accepted"] is True
    assert validation["symbols"] == ["BTCUSDT", "ETHUSDT"]

    btc_file = journal.run_dir / "BTCUSDT-bookTicker.jsonl"
    btc_file.write_text(btc_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ProspectiveBookTickerCaptureError, match="FILE_SIZE_MISMATCH|FILE_HASH_MISMATCH"):
        validate_manifest(journal.manifest_path)


def test_journal_does_not_accept_large_gap_or_duplicate_updates(tmp_path):
    journal = ProspectiveBookTickerJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        max_inter_event_gap_seconds=10,
        now=lambda: START,
        run_id="bookticker-test-gap",
    )
    journal.start(START)
    journal.accept(make_quote("BTCUSDT", 1, START))
    journal.accept(make_quote("BTCUSDT", 1, START + timedelta(seconds=20)))
    journal.accept(make_quote("ETHUSDT", 1, START))
    result = journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(seconds=86400),
        elapsed_seconds=86400,
    )
    assert result["status"] == "BLOCKED"
    assert result["acceptance"]["accepted"] is False
    assert result["symbols_summary"]["BTCUSDT"]["duplicate_update_ids"] == 1
    assert result["symbols_summary"]["BTCUSDT"]["inter_event_gap_count"] == 1
    assert "seen_update_ids" not in journal._state["BTCUSDT"]
