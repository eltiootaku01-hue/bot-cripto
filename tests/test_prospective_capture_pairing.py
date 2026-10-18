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
