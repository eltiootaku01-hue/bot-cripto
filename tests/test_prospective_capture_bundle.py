from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bot_obrero.binance_book_ticker import parse_binance_book_ticker_event
from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from bot_obrero.prospective_book_ticker_capture import ProspectiveBookTickerJournal
from bot_obrero.prospective_capture import ProspectiveCaptureJournal

import pytest

import bot_obrero.prospective_capture_bundle as bundle


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _valid_provenance():
    sha = "a" * 40
    return {
        "status": "VALID",
        "reason": None,
        "git_head_sha": sha,
        "origin_main_sha": sha,
        "live_main_sha": sha,
        "branch": "main",
        "source_status_entries": [],
    }


def _capture_tree(tmp_path, monkeypatch):
    root = tmp_path / "btceth-pair-test"
    (root / "kline" / "k-run").mkdir(parents=True)
    (root / "bookticker" / "q-run").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "kline" / "k-run" / "manifest.json").write_text('{"kind":"synthetic-test-fixture"}\n', encoding="utf-8")
    (root / "bookticker" / "q-run" / "manifest.json").write_text('{"kind":"synthetic-test-fixture"}\n', encoding="utf-8")
    (root / "kline" / "k-run" / "BTCUSDT.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "kline" / "k-run" / "ETHUSDT.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "bookticker" / "q-run" / "BTCUSDT-bookTicker.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "bookticker" / "q-run" / "ETHUSDT-bookTicker.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "logs" / "kline.log").write_text('{"status":"COMPLETED"}\n', encoding="utf-8")
    (root / "logs" / "bookticker.log").write_text('{"status":"COMPLETED"}\n', encoding="utf-8")

    provenance = _valid_provenance()
    config = {
        "schema_version": 1,
        "capture_kind": "PROSPECTIVE_BINANCE_SPOT_KLINE_AND_BOOK_TICKER_PAIR",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "interval": "1h",
        "target_duration_seconds": 86400,
        "kline_duration_seconds": 86460,
        "bookticker_duration_seconds": 86400,
        "pair_overlap_guard_seconds": 60,
        "kline_grace_seconds": 300,
        "bookticker_grace_seconds": 300,
        "minimum_overlap_seconds": 86400,
        "source": "binance-public:spot-websocket",
        "source_provenance": provenance,
        "trading_enabled": False,
    }
    validation = {
        "status": "VALID",
        "overlap_started_at": "2026-10-18T00:00:00+00:00",
        "overlap_ended_at": "2026-10-19T00:00:01+00:00",
        "overlap_seconds": 86401.0,
        "minimum_overlap_seconds": 86400,
        "boundary_tolerance_seconds": 10.0,
        "common_complete_utc_days": ["2026-10-18"],
    }
    pair = {
        "schema_version": 1,
        "run_id": "btceth-pair-test",
        "status": "VALID",
        "configuration": config,
        "configuration_sha256": hashlib.sha256(_canonical_json(config)).hexdigest(),
        "source_provenance": provenance,
        "source_provenance_at_launch": provenance,
        "source_provenance_at_finish": provenance,
        "logs": {"kline": "logs/kline.log", "bookticker": "logs/bookticker.log"},
        "kline_manifest": "kline/k-run/manifest.json",
        "bookticker_manifest": "bookticker/q-run/manifest.json",
        "validation": validation,
    }
    (root / "pair-manifest.json").write_text(json.dumps(pair, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    calls = []

    def fake_validate_paired_manifests(*, kline_manifest_path, bookticker_manifest_path, minimum_overlap_seconds):
        calls.append((Path(kline_manifest_path), Path(bookticker_manifest_path), minimum_overlap_seconds))
        return dict(validation)

    monkeypatch.setattr(bundle, "validate_paired_manifests", fake_validate_paired_manifests)
    return root, calls


def test_create_bundle_revalidates_pair_and_writes_sha256_sidecar(tmp_path, monkeypatch):
    root, calls = _capture_tree(tmp_path, monkeypatch)
    result = bundle.create_capture_bundle(root)

    archive = Path(result["archive"])
    sidecar = Path(result["sha256_file"])
    assert result["status"] == "VALID"
    assert result["trading_enabled"] is False
    assert result["overlap_seconds"] == 86401.0
    assert len(calls) == 1
    assert calls[0][2] == 86400
    assert calls[0][0].name == calls[0][1].name == "manifest.json"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["sha256"]
    assert sidecar.read_text(encoding="ascii") == f"{result['sha256']}  {archive.name}\n"
    with zipfile.ZipFile(archive) as zipped:
        names = set(zipped.namelist())
    assert "pair-manifest.json" in names
    assert "logs/kline.log" in names
    assert "logs/bookticker.log" in names
    assert "kline/k-run/manifest.json" in names
    assert "bookticker/q-run/manifest.json" in names


def test_bundle_refuses_non_valid_pair_without_calling_validator(tmp_path, monkeypatch):
    root, calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["status"] = "BLOCKED"
    pair_path.write_text(json.dumps(pair), encoding="utf-8")

    with pytest.raises(bundle.CaptureBundleError, match="PAIR_STATUS_NOT_VALID"):
        bundle.create_capture_bundle(root)
    assert calls == []


def test_bundle_rejects_configuration_hash_mismatch(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["configuration"]["minimum_overlap_seconds"] = 90000
    pair_path.write_text(json.dumps(pair), encoding="utf-8")

    with pytest.raises(bundle.CaptureBundleError, match="PAIR_CONFIGURATION_HASH_MISMATCH"):
        bundle.create_capture_bundle(root)


def test_bundle_rejects_manifest_path_traversal(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["kline_manifest"] = "../outside.json"
    pair["configuration_sha256"] = hashlib.sha256(_canonical_json(pair["configuration"])).hexdigest()
    pair_path.write_text(json.dumps(pair), encoding="utf-8")

    with pytest.raises(bundle.CaptureBundleError, match="KLINE_MANIFEST_PATH_UNSAFE"):
        bundle.create_capture_bundle(root)


def test_bundle_refuses_temporary_files_from_incomplete_write(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    (root / "pair-manifest.json.tmp").write_text("{}\n", encoding="utf-8")

    with pytest.raises(bundle.CaptureBundleError, match="TEMPORARY_FILE_IN_CAPTURE_TREE"):
        bundle.create_capture_bundle(root)


def test_bundle_does_not_overwrite_existing_archive(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    output = root.parent / f"{root.name}-evidence.zip"
    output.write_bytes(b"existing")
    with pytest.raises(bundle.CaptureBundleError, match="BUNDLE_OUTPUT_EXISTS_REFUSING_OVERWRITE"):
        bundle.create_capture_bundle(root)
    assert output.read_bytes() == b"existing"


def test_bundle_refuses_unexpected_files_instead_of_packaging_them(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    private_note = root / "logs" / "operator-notes.txt"
    private_note.write_text("not part of the captured evidence\n", encoding="utf-8")

    with pytest.raises(bundle.CaptureBundleError, match="UNEXPECTED_CAPTURE_FILE"):
        bundle.create_capture_bundle(root)

    assert not (root.parent / f"{root.name}-evidence.zip").exists()


def test_bundle_allows_remote_main_to_advance_after_frozen_capture_launch(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["source_provenance_at_finish"]["live_main_sha"] = "b" * 40
    pair_path.write_text(json.dumps(pair), encoding="utf-8")
    result = bundle.create_capture_bundle(root)
    assert result["status"] == "VALID"


def test_bundle_rejects_local_source_head_change_during_capture(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["source_provenance_at_finish"]["git_head_sha"] = "b" * 40
    pair_path.write_text(json.dumps(pair), encoding="utf-8")
    with pytest.raises(bundle.CaptureBundleError, match="CAPTURE_CODE_REVISION_CHANGED"):
        bundle.create_capture_bundle(root)


def test_bundle_refuses_boolean_trading_flag(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["configuration"]["trading_enabled"] = True
    pair["configuration_sha256"] = hashlib.sha256(_canonical_json(pair["configuration"])).hexdigest()
    pair_path.write_text(json.dumps(pair), encoding="utf-8")
    with pytest.raises(bundle.CaptureBundleError, match="TRADING_MUST_REMAIN_DISABLED"):
        bundle.create_capture_bundle(root)


def test_verify_received_bundle_rechecks_hash_allowlist_and_pair(tmp_path, monkeypatch):
    root, calls = _capture_tree(tmp_path, monkeypatch)
    created = bundle.create_capture_bundle(root)

    verified = bundle.verify_capture_bundle(Path(created["archive"]))

    assert verified["status"] == "VALID"
    assert verified["sha256"] == created["sha256"]
    assert verified["files_verified"] == 9
    assert verified["overlap_seconds"] == 86401.0
    assert verified["common_complete_utc_days"] == ["2026-10-18"]
    assert verified["trading_enabled"] is False
    # Initial packaging and receipt-side revalidation both call the pair validator.
    assert len(calls) == 2


def test_verify_received_bundle_rejects_sha256_mismatch(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    created = bundle.create_capture_bundle(root)
    sidecar = Path(created["sha256_file"])
    sidecar.write_text(f"{'0' * 64}  {Path(created['archive']).name}\n", encoding="ascii")

    with pytest.raises(bundle.CaptureBundleError, match="BUNDLE_SHA256_MISMATCH"):
        bundle.verify_capture_bundle(Path(created["archive"]))


def test_verify_received_bundle_rejects_path_traversal_member(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    created = bundle.create_capture_bundle(root)
    archive_path = Path(created["archive"])
    malicious = zipfile.ZipInfo("../outside.txt", date_time=(1980, 1, 1, 0, 0, 0))
    malicious.create_system = 3
    malicious.external_attr = 0o100644 << 16
    with zipfile.ZipFile(archive_path, mode="a") as archive:
        archive.writestr(malicious, "must not be extracted")
    sidecar = Path(created["sha256_file"])
    sidecar.write_text(f"{bundle._sha256_file(archive_path)}  {archive_path.name}\n", encoding="ascii")

    with pytest.raises(bundle.CaptureBundleError, match="ZIP_MEMBER_PATH_UNSAFE"):
        bundle.verify_capture_bundle(archive_path)


def test_verify_received_bundle_rejects_unexpected_regular_member(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    created = bundle.create_capture_bundle(root)
    archive_path = Path(created["archive"])
    extra = zipfile.ZipInfo("operator-notes.txt", date_time=(1980, 1, 1, 0, 0, 0))
    extra.create_system = 3
    extra.external_attr = 0o100644 << 16
    with zipfile.ZipFile(archive_path, mode="a") as archive:
        archive.writestr(extra, "not capture evidence")
    sidecar = Path(created["sha256_file"])
    sidecar.write_text(f"{bundle._sha256_file(archive_path)}  {archive_path.name}\n", encoding="ascii")

    with pytest.raises(bundle.CaptureBundleError, match="UNEXPECTED_CAPTURE_FILE_IN_BUNDLE"):
        bundle.verify_capture_bundle(archive_path)


# These journals intentionally build synthetic, timestamp-consistent fixtures.
# The test exercises both independent validators end-to-end; it is not market evidence.
_INTEGRATION_UTC = timezone.utc
_INTEGRATION_START = datetime(2025, 10, 18, 0, 0, tzinfo=_INTEGRATION_UTC)


def _synthetic_market_data(symbol: str, index: int):
    base = symbol.removesuffix("USDT")
    candle_start = _INTEGRATION_START + timedelta(hours=index)
    candle_end = candle_start + timedelta(hours=1) - timedelta(milliseconds=1)
    price = Decimal("65000") if symbol == "BTCUSDT" else Decimal("3000")
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
        available_at=received_at + timedelta(seconds=1),
        payload=candle,
        quality=DataQuality.VALID,
        completeness=DataCompleteness.UNKNOWN,
    )


def _synthetic_quote(symbol: str, update_id: int, received_at: datetime):
    return parse_binance_book_ticker_event(
        {
            "u": update_id,
            "s": symbol,
            "b": "65000.1" if symbol == "BTCUSDT" else "3000.1",
            "B": "0.12",
            "a": "65000.2" if symbol == "BTCUSDT" else "3000.2",
            "A": "0.34",
        },
        requested_symbol=symbol,
        received_at=received_at,
        available_at=received_at,
    )


def _valid_synthetic_pair_using_real_validators(tmp_path):
    root = tmp_path / "validator-integration-run"
    root.mkdir()
    kline_journal = ProspectiveCaptureJournal(
        output_dir=root / "kline",
        duration_seconds=86460,
        grace_seconds=1,
        max_inter_event_gap_seconds=3600.0,
        fsync_every_records=1,
        align_next_utc_midnight=False,
        now=lambda: _INTEGRATION_START,
        run_id="kline-real-validator-fixture",
    )
    kline_journal.start(_INTEGRATION_START)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(24):
            kline_journal.accept(_synthetic_market_data(symbol, index))
    kline_journal.finalize(
        status="COMPLETED",
        ended_at=_INTEGRATION_START + timedelta(seconds=86464),
        elapsed_seconds=86464.0,
    )

    bookticker_journal = ProspectiveBookTickerJournal(
        output_dir=root / "bookticker",
        duration_seconds=86400,
        capture_grace_seconds=0,
        max_inter_event_gap_seconds=86400.0,
        fsync_every_records=1,
        now=lambda: _INTEGRATION_START,
        run_id="bookticker-real-validator-fixture",
    )
    bookticker_journal.start(_INTEGRATION_START)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        bookticker_journal.accept(_synthetic_quote(symbol, 1, _INTEGRATION_START))
        bookticker_journal.accept(
            _synthetic_quote(symbol, 2, _INTEGRATION_START + timedelta(seconds=86400))
        )
    bookticker_journal.finalize(
        status="COMPLETED",
        ended_at=_INTEGRATION_START + timedelta(seconds=86400),
        elapsed_seconds=86400.0,
    )

    logs = root / "logs"
    logs.mkdir()
    (logs / "kline.log").write_text("SYNTHETIC TEST FIXTURE ONLY\n", encoding="utf-8")
    (logs / "bookticker.log").write_text("SYNTHETIC TEST FIXTURE ONLY\n", encoding="utf-8")

    provenance = _valid_provenance()
    relative_kline_manifest = "kline/kline-real-validator-fixture/manifest.json"
    relative_bookticker_manifest = "bookticker/bookticker-real-validator-fixture/manifest.json"
    validation = bundle.validate_paired_manifests(
        kline_manifest_path=root / relative_kline_manifest,
        bookticker_manifest_path=root / relative_bookticker_manifest,
        minimum_overlap_seconds=86400,
    )
    assert validation["status"] == "VALID", validation
    configuration = {
        "schema_version": 1,
        "capture_kind": "PROSPECTIVE_BINANCE_SPOT_KLINE_AND_BOOK_TICKER_PAIR",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "interval": "1h",
        "target_duration_seconds": 86400,
        "kline_duration_seconds": 86460,
        "bookticker_duration_seconds": 86400,
        "pair_overlap_guard_seconds": 60,
        "kline_grace_seconds": 1,
        "bookticker_grace_seconds": 0,
        "minimum_overlap_seconds": 86400,
        "source": "binance-public:spot-websocket",
        "source_provenance": provenance,
        "trading_enabled": False,
    }
    pair = {
        "schema_version": 1,
        "run_id": "btceth-pair-real-validator-fixture",
        "status": "VALID",
        "configuration": configuration,
        "configuration_sha256": hashlib.sha256(_canonical_json(configuration)).hexdigest(),
        "source_provenance": provenance,
        "source_provenance_at_launch": provenance,
        "source_provenance_at_finish": provenance,
        "logs": {"kline": "logs/kline.log", "bookticker": "logs/bookticker.log"},
        "kline_manifest": relative_kline_manifest,
        "bookticker_manifest": relative_bookticker_manifest,
        "validation": validation,
    }
    (root / "pair-manifest.json").write_text(
        json.dumps(pair, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def test_receiver_verification_revalidates_real_manifests_and_synthetic_datasets(tmp_path):
    root = _valid_synthetic_pair_using_real_validators(tmp_path)
    created = bundle.create_capture_bundle(root)

    received = bundle.verify_capture_bundle(Path(created["archive"]))

    assert created["status"] == "VALID"
    assert received["status"] == "VALID"
    assert received["files_verified"] == 9
    assert received["overlap_seconds"] == 86400.0
    assert received["common_complete_utc_days"] == ["2025-10-18"]
    assert received["trading_enabled"] is False


def test_receiver_verification_rejects_manipulated_dataset_after_checksum_recalculation(tmp_path):
    root = _valid_synthetic_pair_using_real_validators(tmp_path)
    created = bundle.create_capture_bundle(root)
    source_archive = Path(created["archive"])
    manipulated_archive = tmp_path / "manipulated-evidence.zip"

    with zipfile.ZipFile(source_archive, mode="r") as source, zipfile.ZipFile(
        manipulated_archive, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as altered:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "kline/kline-real-validator-fixture/BTCUSDT.jsonl":
                payload = payload + b"\n"
            altered.writestr(info, payload)

    sidecar = Path(str(manipulated_archive) + ".sha256")
    sidecar.write_text(
        f"{bundle._sha256_file(manipulated_archive)}  {manipulated_archive.name}\n",
        encoding="ascii",
    )

    # The outer checksum matches the altered ZIP, so only inner-manifest validation
    # can detect that the original capture dataset was changed.
    with pytest.raises(bundle.CaptureBundleError, match="INDEPENDENT_PAIR_VALIDATION_FAILED"):
        bundle.verify_capture_bundle(manipulated_archive)

