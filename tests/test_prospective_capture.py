from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
import hashlib
import time

import pytest

from bot_obrero.market_data import (
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    InstrumentType,
    MarketData,
    SourceIdentity,
)
from bot_obrero.prospective_capture import (
    ProspectiveCaptureError,
    ProspectiveCaptureJournal,
    _complete_utc_days,
    run_capture,
    validate_manifest,
)


START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _item(symbol: str, index: int, *, available: bool = True, close: str = "100") -> MarketData:
    base = symbol.removesuffix("USDT")
    candle_start = START + timedelta(hours=index)
    candle_end = candle_start + timedelta(hours=1) - timedelta(milliseconds=1)
    price = Decimal(close)
    candle = Candle(
        start=candle_start,
        end=candle_end,
        timeframe="1h",
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("10"),
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.FINAL,
    )
    instrument = InstrumentIdentity(
        instrument_id=f"binance:SPOT:{symbol}",
        symbol=f"{base}/USDT",
        market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT,
        base_asset=base,
        quote_asset="USDT",
    )
    source = SourceIdentity(
        source_id="binance-spot-websocket",
        provider="binance",
        venue="BINANCE",
    )
    received_at = candle_end + timedelta(seconds=1)
    return MarketData(
        instrument=instrument,
        source=source,
        data_type="CANDLE",
        observed_at=candle_end - timedelta(seconds=1),
        received_at=received_at,
        available_at=received_at + timedelta(seconds=1) if available else None,
        payload=candle,
        quality=DataQuality.VALID,
        completeness=DataCompleteness.UNKNOWN,
    )


def _complete_journal(tmp_path, *, max_gap_seconds=3600.0):
    journal = ProspectiveCaptureJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        grace_seconds=300,
        max_inter_event_gap_seconds=max_gap_seconds,
        fsync_every_records=1,
        now=lambda: START,
        run_id="capture-test-001",
    )
    journal.start(START)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(24):
            journal.accept(_item(symbol, index))
    return journal


def _finalized_capture(tmp_path):
    journal = _complete_journal(tmp_path)
    journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24, seconds=3),
        elapsed_seconds=86403.0,
    )
    return journal


def _rewrite_dataset_and_manifest(journal, manifest, filename, envelopes):
    data_path = journal.run_dir / filename
    data_bytes = (
        ("\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in envelopes) + "\n")
    ).encode("utf-8")
    data_path.write_bytes(data_bytes)
    manifest["files"][filename]["bytes"] = len(data_bytes)
    manifest["files"][filename]["sha256"] = hashlib.sha256(data_bytes).hexdigest()
    journal.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_capture_journal_records_a_complete_utc_day_and_validates_hashes(tmp_path):
    journal = _complete_journal(tmp_path)
    manifest = journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24, seconds=3),
        elapsed_seconds=86403.0,
    )
    assert manifest["status"] == "COMPLETED"
    assert manifest["acceptance"]["accepted"] is True
    assert manifest["acceptance"]["symbols"]["BTCUSDT"]["complete_utc_days"] == ["2025-01-01"]
    assert manifest["acceptance"]["symbols"]["ETHUSDT"]["unique_final_candle_starts"] == 24

    validated = validate_manifest(journal.manifest_path)
    assert validated["status"] == "VALID"
    assert validated["run_id"] == "capture-test-001"
    assert set(validated["dataset_sha256"]) == {"BTCUSDT.jsonl", "ETHUSDT.jsonl"}


def test_capture_never_accepts_unknown_availability(tmp_path):
    journal = ProspectiveCaptureJournal(
        output_dir=tmp_path, duration_seconds=86400, now=lambda: START, run_id="capture-test-unknown"
    )
    journal.start(START)
    with pytest.raises(ProspectiveCaptureError, match="AVAILABILITY_UNKNOWN"):
        journal.accept(_item("BTCUSDT", 0, available=False))
    manifest = journal.finalize(
        status="BLOCKED",
        ended_at=START + timedelta(seconds=1),
        elapsed_seconds=1,
        reason="test_unknown_availability",
    )
    assert manifest["status"] == "BLOCKED"


def test_missing_utc_hour_fails_closed_even_when_requested_elapsed_time_is_met(tmp_path):
    journal = ProspectiveCaptureJournal(
        output_dir=tmp_path, duration_seconds=86400, max_inter_event_gap_seconds=3600,
        now=lambda: START, run_id="capture-test-gap"
    )
    journal.start(START)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(23):
            journal.accept(_item(symbol, index))
    manifest = journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24),
        elapsed_seconds=86400,
    )
    assert manifest["status"] == "BLOCKED"
    assert manifest["acceptance"]["elapsed_duration_met"] is True
    assert manifest["acceptance"]["all_symbols_have_complete_utc_day"] is False
    with pytest.raises(ProspectiveCaptureError, match="STATUS_NOT_COMPLETED"):
        validate_manifest(journal.manifest_path)


def test_large_inter_event_gap_blocks_capture_acceptance(tmp_path):
    journal = ProspectiveCaptureJournal(
        output_dir=tmp_path, duration_seconds=86400, max_inter_event_gap_seconds=10,
        now=lambda: START, run_id="capture-test-inter-event-gap"
    )
    journal.start(START)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(24):
            journal.accept(_item(symbol, index))
    manifest = journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24, seconds=3),
        elapsed_seconds=86403,
    )
    assert manifest["status"] == "BLOCKED"
    assert manifest["acceptance"]["hourly_integrity_pass"] is False
    assert manifest["acceptance"]["symbols"]["BTCUSDT"]["inter_event_gap_count"] == 23


def test_dataset_tampering_is_detected_after_finalization(tmp_path):
    journal = _complete_journal(tmp_path)
    journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24, seconds=3),
        elapsed_seconds=86403,
    )
    data_path = journal.run_dir / "BTCUSDT.jsonl"
    with data_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tampered": True}) + "\n")
    with pytest.raises(ProspectiveCaptureError, match="FILE_HASH_MISMATCH"):
        validate_manifest(journal.manifest_path)


def test_capture_only_accepts_the_canonical_btc_eth_hourly_pair():
    with pytest.raises(ProspectiveCaptureError, match="EXACT_PAIR"):
        ProspectiveCaptureJournal(
            output_dir="unused",
            symbols=("BTCUSDT", "XRPUSDT"),
            duration_seconds=86400,
            run_id="capture-test-invalid-pair",
        )


def test_complete_utc_day_helper_requires_each_of_24_hour_starts():
    starts = [START + timedelta(hours=index) for index in range(24)]
    assert _complete_utc_days(starts) == ["2025-01-01"]
    assert _complete_utc_days(starts[:-1]) == []
    assert _complete_utc_days(starts[:12] + starts[13:]) == []



def test_run_capture_accepts_only_after_both_streams_supply_a_complete_utc_day(tmp_path):
    from threading import Event

    class FakeAdapter:
        def __init__(self, symbol):
            self.symbol = symbol
            self.sent = False

        def listen(self, *, symbol, interval, max_messages, on_item, should_stop):
            assert symbol == self.symbol
            assert interval == "1h"
            assert max_messages > 0
            if not self.sent:
                for index in range(24):
                    on_item(_item(self.symbol, index))
                self.sent = True
            while not should_stop():
                Event().wait(0.001)
            return []

    now_calls = {"count": 0}

    def fake_now():
        now_calls["count"] += 1
        return START + timedelta(hours=24, seconds=3) if now_calls["count"] >= 5 else START

    fake_mono = {"value": 0.0, "advanced": False}

    def fake_wait(_seconds):
        if not fake_mono["advanced"]:
            fake_mono["value"] += 86401.0
            fake_mono["advanced"] = True
        time.sleep(0.01)

    code, manifest = run_capture(
        output_dir=tmp_path,
        duration_seconds=86400,
        grace_seconds=300,
        max_messages_per_connection=10,
        max_inter_event_gap_seconds=3600,
        align_next_utc_midnight=False,
        adapter_factory=lambda symbol, _clock: FakeAdapter(symbol),
        now=fake_now,
        monotonic=lambda: fake_mono["value"],
        wait=fake_wait,
    )
    assert code == 0
    assert manifest["status"] == "COMPLETED"
    assert manifest["acceptance"]["accepted"] is True
    assert manifest["acceptance"]["symbols"]["BTCUSDT"]["complete_utc_days"] == ["2025-01-01"]
    assert manifest["acceptance"]["symbols"]["ETHUSDT"]["complete_utc_days"] == ["2025-01-01"]
    assert validate_manifest(tmp_path / manifest["run_id"] / "manifest.json")["status"] == "VALID"



def test_duplicate_final_hour_is_flagged_and_blocks_capture_acceptance(tmp_path):
    journal = ProspectiveCaptureJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        max_inter_event_gap_seconds=3600,
        now=lambda: START,
        run_id="capture-test-duplicate-final",
    )
    journal.start(START)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(24):
            journal.accept(_item(symbol, index))
        journal.accept(_item(symbol, 23))
    manifest = journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24, seconds=3),
        elapsed_seconds=86403,
    )
    assert manifest["status"] == "BLOCKED"
    assert manifest["acceptance"]["symbols"]["BTCUSDT"]["duplicate_final_candles"] == 1
    with pytest.raises(ProspectiveCaptureError, match="STATUS_NOT_COMPLETED"):
        validate_manifest(journal.manifest_path)



def test_capture_journal_rejects_closed_candle_without_finality(tmp_path):
    from bot_obrero.market_data import CandleFinality

    journal = ProspectiveCaptureJournal(
        output_dir=tmp_path,
        duration_seconds=86400,
        now=lambda: START,
        run_id="capture-test-invalid-candle-state",
    )
    journal.start(START)
    invalid = _item("BTCUSDT", 0)
    invalid_candle = replace(invalid.payload, finality=CandleFinality.UNKNOWN)
    invalid = replace(invalid, payload=invalid_candle)
    with pytest.raises(ProspectiveCaptureError, match="CANDLE_STATE_CONTRACT_INVALID"):
        journal.accept(invalid)
    manifest = journal.finalize(
        status="BLOCKED",
        ended_at=START + timedelta(seconds=1),
        elapsed_seconds=1,
        reason="test_invalid_candle_state",
    )
    assert manifest["status"] == "BLOCKED"


def test_validator_rejects_records_that_precede_the_declared_capture_run(tmp_path):
    journal = _complete_journal(tmp_path)
    journal.finalize(
        status="COMPLETED",
        ended_at=START + timedelta(hours=24, seconds=3),
        elapsed_seconds=86403.0,
    )
    manifest_path = journal.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Forge internally consistent duration fields but claim the actual run began
    # two hours later. Existing first-hour records must fail the run-bound check.
    manifest["capture_started_at"] = (START + timedelta(hours=2)).isoformat()
    manifest["capture_ended_at"] = (START + timedelta(hours=26, seconds=3)).isoformat()
    manifest["actual_elapsed_seconds"] = 86403.0

    # Recompute the file's own SHA/count metadata so the independent temporal
    # check, not the outer file hash, is the reason for rejection.
    data_path = journal.run_dir / "BTCUSDT.jsonl"
    lines = data_path.read_text(encoding="utf-8").splitlines()
    assert lines
    envelopes = [json.loads(line) for line in lines]
    data_bytes = (("\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in envelopes)) + "\n").encode("utf-8")
    data_path.write_bytes(data_bytes)
    manifest["files"]["BTCUSDT.jsonl"]["bytes"] = len(data_bytes)
    manifest["files"]["BTCUSDT.jsonl"]["sha256"] = hashlib.sha256(data_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ProspectiveCaptureError, match="RECORD_OUTSIDE_RUN_INTERVAL"):
        validate_manifest(manifest_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_duration_seconds", "86400"),
        ("actual_elapsed_seconds", "86403.0"),
    ],
)
def test_validator_rejects_string_encoded_duration_numbers(tmp_path, field, value):
    journal = _finalized_capture(tmp_path)
    manifest = json.loads(journal.manifest_path.read_text(encoding="utf-8"))
    if field == "target_duration_seconds":
        manifest["target_duration_seconds"] = value
        manifest["configuration"]["target_duration_seconds"] = value
        canonical_config = json.dumps(
            manifest["configuration"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        manifest["configuration_sha256"] = hashlib.sha256(canonical_config).hexdigest()
        expected_error = "CAPTURE_CONFIGURATION_CONTRACT_INVALID"
    else:
        manifest[field] = value
        expected_error = "CAPTURE_TIMESTAMPS_OR_DURATION_INVALID"
    journal.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProspectiveCaptureError, match=expected_error):
        validate_manifest(journal.manifest_path)


def test_validator_rejects_boolean_manifest_schema_version(tmp_path):
    journal = _finalized_capture(tmp_path)
    manifest = json.loads(journal.manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = True
    journal.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProspectiveCaptureError, match="MANIFEST_SCHEMA_INVALID"):
        validate_manifest(journal.manifest_path)


def test_validator_rejects_boolean_configuration_schema_version(tmp_path):
    journal = _finalized_capture(tmp_path)
    manifest = json.loads(journal.manifest_path.read_text(encoding="utf-8"))
    manifest["configuration"]["schema_version"] = True
    canonical_config = json.dumps(
        manifest["configuration"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest["configuration_sha256"] = hashlib.sha256(canonical_config).hexdigest()
    journal.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProspectiveCaptureError, match="CAPTURE_CONFIGURATION_SCHEMA_INVALID"):
        validate_manifest(journal.manifest_path)


def test_validator_rejects_boolean_record_schema_version(tmp_path):
    journal = _finalized_capture(tmp_path)
    manifest = json.loads(journal.manifest_path.read_text(encoding="utf-8"))
    filename = "BTCUSDT.jsonl"
    data_path = journal.run_dir / filename
    envelopes = [json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines()]
    envelopes[0]["schema_version"] = True
    _rewrite_dataset_and_manifest(journal, manifest, filename, envelopes)

    with pytest.raises(ProspectiveCaptureError, match="CAPTURE_RECORD_ENVELOPE_INVALID"):
        validate_manifest(journal.manifest_path)


def test_validator_rejects_boolean_record_sequence(tmp_path):
    journal = _finalized_capture(tmp_path)
    manifest = json.loads(journal.manifest_path.read_text(encoding="utf-8"))
    filename = "BTCUSDT.jsonl"
    data_path = journal.run_dir / filename
    envelopes = [json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines()]
    envelopes[0]["sequence"] = True
    _rewrite_dataset_and_manifest(journal, manifest, filename, envelopes)

    with pytest.raises(ProspectiveCaptureError, match="CAPTURE_RECORD_SEQUENCE_INVALID"):
        validate_manifest(journal.manifest_path)
