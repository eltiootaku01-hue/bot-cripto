from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.prospective_book_ticker_capture import (
    DEFAULT_DURATION_SECONDS,
    ProspectiveBookTickerCaptureError,
    _assess_common_capture_window,
)

UTC = timezone.utc
DAY = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)


def test_pair_window_accepts_full_overlap_with_one_common_complete_utc_day():
    result = _assess_common_capture_window(
        kline_start=DAY,
        kline_end=DAY + timedelta(days=1, minutes=5),
        bookticker_start=DAY + timedelta(seconds=3),
        bookticker_end=DAY + timedelta(days=1, seconds=3),
        common_complete_utc_days=["2026-10-18"],
    )

    assert result["status"] == "VALID"
    assert result["overlap_seconds"] == DEFAULT_DURATION_SECONDS
    assert result["common_complete_utc_days"] == ["2026-10-18"]


@pytest.mark.parametrize(
    "overlap_seconds",
    [1, 86399, 86399.9],
)
def test_pair_window_rejects_less_than_24_hours_of_overlap(overlap_seconds):
    with pytest.raises(ProspectiveBookTickerCaptureError, match="OVERLAP_TOO_SHORT"):
        _assess_common_capture_window(
            kline_start=DAY,
            kline_end=DAY + timedelta(seconds=overlap_seconds),
            bookticker_start=DAY,
            bookticker_end=DAY + timedelta(seconds=overlap_seconds),
            common_complete_utc_days=["2026-10-18"],
        )


def test_pair_window_rejects_day_not_shared_by_both_klines():
    with pytest.raises(ProspectiveBookTickerCaptureError, match="NO_COMMON_COMPLETE_UTC_DAY"):
        _assess_common_capture_window(
            kline_start=DAY,
            kline_end=DAY + timedelta(days=1),
            bookticker_start=DAY,
            bookticker_end=DAY + timedelta(days=1),
            common_complete_utc_days=[],
        )


def test_pair_window_requires_common_day_to_begin_near_overlap_start():
    with pytest.raises(ProspectiveBookTickerCaptureError, match="NO_COMMON_COMPLETE_UTC_DAY"):
        _assess_common_capture_window(
            kline_start=DAY + timedelta(minutes=1),
            kline_end=DAY + timedelta(days=1, minutes=1),
            bookticker_start=DAY + timedelta(minutes=1),
            bookticker_end=DAY + timedelta(days=1, minutes=1),
            common_complete_utc_days=["2026-10-18"],
        )


def test_pair_window_rejects_naive_timestamps():
    with pytest.raises(ProspectiveBookTickerCaptureError, match="TIMEZONE_AWARE"):
        _assess_common_capture_window(
            kline_start=datetime(2026, 10, 18),
            kline_end=DAY + timedelta(days=1),
            bookticker_start=DAY,
            bookticker_end=DAY + timedelta(days=1),
            common_complete_utc_days=["2026-10-18"],
        )


def test_pair_window_rejects_reversed_capture_bounds():
    with pytest.raises(ProspectiveBookTickerCaptureError, match="CAPTURE_WINDOW_INVALID"):
        _assess_common_capture_window(
            kline_start=DAY + timedelta(days=1),
            kline_end=DAY,
            bookticker_start=DAY,
            bookticker_end=DAY + timedelta(days=1),
            common_complete_utc_days=["2026-10-18"],
        )


def test_pair_window_rejects_boolean_duration_that_looks_like_integer():
    with pytest.raises(ProspectiveBookTickerCaptureError, match="MINIMUM_COMMON_OVERLAP"):
        _assess_common_capture_window(
            kline_start=DAY,
            kline_end=DAY + timedelta(days=1),
            bookticker_start=DAY,
            bookticker_end=DAY + timedelta(days=1),
            common_complete_utc_days=["2026-10-18"],
            minimum_overlap_seconds=True,
        )

def test_validate_paired_manifests_composes_independent_validators(tmp_path, monkeypatch):
    import json

    from bot_obrero import prospective_book_ticker_capture as module

    kline_path = tmp_path / "kline-manifest.json"
    quote_path = tmp_path / "quote-manifest.json"
    kline_start = DAY
    kline_end = DAY + timedelta(days=1, minutes=5)
    quote_start = DAY + timedelta(seconds=2)
    quote_end = DAY + timedelta(days=1, seconds=2)

    kline_path.write_text(json.dumps({
        "status": "COMPLETED",
        "run_id": "kline-run",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "capture_started_at": kline_start.isoformat(),
        "capture_ended_at": kline_end.isoformat(),
    }), encoding="utf-8")
    quote_path.write_text(json.dumps({
        "status": "COMPLETED",
        "run_id": "quote-run",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "capture_started_at": quote_start.isoformat(),
        "capture_ended_at": quote_end.isoformat(),
        "files": {
            "BTCUSDT-bookTicker.jsonl": {"sha256": "a" * 64},
            "ETHUSDT-bookTicker.jsonl": {"sha256": "b" * 64},
        },
    }), encoding="utf-8")

    monkeypatch.setattr(module, "validate_kline_manifest", lambda _path: {
        "status": "VALID",
        "run_id": "kline-run",
        "capture_started_at": kline_start.isoformat(),
        "capture_ended_at": kline_end.isoformat(),
        "dataset_sha256": {"BTCUSDT.jsonl": "c" * 64, "ETHUSDT.jsonl": "d" * 64},
        "acceptance": {
            "symbols": {
                "BTCUSDT": {"complete_utc_days": ["2026-10-18"]},
                "ETHUSDT": {"complete_utc_days": ["2026-10-18"]},
            },
        },
    })
    monkeypatch.setattr(module, "validate_manifest", lambda _path: {
        "status": "VALID", "run_id": "quote-run"
    })

    result = module.validate_paired_manifests(
        kline_manifest_path=kline_path,
        bookticker_manifest_path=quote_path,
    )
    assert result["status"] == "VALID"
    assert result["overlap_seconds"] == DEFAULT_DURATION_SECONDS
    assert result["common_complete_utc_days"] == ["2026-10-18"]
    assert result["kline"]["run_id"] == "kline-run"
    assert result["bookticker"]["run_id"] == "quote-run"
    assert result["bookticker"]["dataset_sha256"]["BTCUSDT-bookTicker.jsonl"] == "a" * 64


def test_validate_paired_manifests_rejects_unhashable_raw_symbols_without_type_error(tmp_path, monkeypatch):
    import json

    from bot_obrero import prospective_book_ticker_capture as module

    kline_path = tmp_path / "kline-manifest.json"
    quote_path = tmp_path / "quote-manifest.json"
    start = DAY
    end = DAY + timedelta(days=1)
    kline_path.write_text(json.dumps({
        "status": "COMPLETED",
        "run_id": "kline-run",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "capture_started_at": start.isoformat(),
        "capture_ended_at": end.isoformat(),
    }), encoding="utf-8")
    quote_path.write_text(json.dumps({
        "status": "COMPLETED",
        "run_id": "quote-run",
        "symbols": [[], {}],
        "capture_started_at": start.isoformat(),
        "capture_ended_at": end.isoformat(),
        "files": {},
    }), encoding="utf-8")
    monkeypatch.setattr(module, "validate_kline_manifest", lambda _path: {
        "status": "VALID",
        "run_id": "kline-run",
        "capture_started_at": start.isoformat(),
        "capture_ended_at": end.isoformat(),
        "dataset_sha256": {},
        "acceptance": {"symbols": {
            "BTCUSDT": {"complete_utc_days": ["2026-10-18"]},
            "ETHUSDT": {"complete_utc_days": ["2026-10-18"]},
        }},
    })
    monkeypatch.setattr(module, "validate_manifest", lambda _path: {
        "status": "VALID", "run_id": "quote-run"
    })
    with pytest.raises(ProspectiveBookTickerCaptureError, match="PAIRED_CAPTURE_SYMBOL_SET_INVALID"):
        module.validate_paired_manifests(
            kline_manifest_path=kline_path,
            bookticker_manifest_path=quote_path,
        )


def test_validate_paired_manifests_rejects_symbol_universe_mismatch(tmp_path, monkeypatch):
    import json

    from bot_obrero import prospective_book_ticker_capture as module

    kline_path = tmp_path / "kline-manifest.json"
    quote_path = tmp_path / "quote-manifest.json"
    start = DAY
    end = DAY + timedelta(days=1)
    kline_path.write_text(json.dumps({
        "status": "COMPLETED",
        "run_id": "kline-run",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "capture_started_at": start.isoformat(),
        "capture_ended_at": end.isoformat(),
    }), encoding="utf-8")
    quote_path.write_text(json.dumps({
        "status": "COMPLETED",
        "run_id": "quote-run",
        "symbols": ["BTCUSDT", "DOGEUSDT"],
        "capture_started_at": start.isoformat(),
        "capture_ended_at": end.isoformat(),
        "files": {},
    }), encoding="utf-8")
    monkeypatch.setattr(module, "validate_kline_manifest", lambda _path: {
        "status": "VALID",
        "run_id": "kline-run",
        "capture_started_at": start.isoformat(),
        "capture_ended_at": end.isoformat(),
        "dataset_sha256": {},
        "acceptance": {"symbols": {
            "BTCUSDT": {"complete_utc_days": ["2026-10-18"]},
            "ETHUSDT": {"complete_utc_days": ["2026-10-18"]},
        }},
    })
    monkeypatch.setattr(module, "validate_manifest", lambda _path: {
        "status": "VALID", "run_id": "quote-run"
    })
    with pytest.raises(ProspectiveBookTickerCaptureError, match="PAIRED_CAPTURE_SYMBOLS_DIFFER"):
        module.validate_paired_manifests(
            kline_manifest_path=kline_path,
            bookticker_manifest_path=quote_path,
        )
