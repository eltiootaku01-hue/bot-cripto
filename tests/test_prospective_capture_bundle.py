from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

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
    (root / "bookticker" / "q-run" / "BTCUSDT-bookTicker.jsonl").write_text("{}\n", encoding="utf-8")
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


def test_bundle_rejects_changed_source_provenance_between_boundaries(tmp_path, monkeypatch):
    root, _calls = _capture_tree(tmp_path, monkeypatch)
    pair_path = root / "pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    pair["source_provenance_at_finish"]["live_main_sha"] = "b" * 40
    pair_path.write_text(json.dumps(pair), encoding="utf-8")
    with pytest.raises(bundle.CaptureBundleError, match="SOURCE_PROVENANCE_CHANGED_DURING_CAPTURE"):
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
