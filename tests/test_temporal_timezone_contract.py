from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from bot_obrero.execution import EvidenceRecord
from bot_obrero.temporal import EvidenceTimestamp


class NullOffsetTimezone(tzinfo):
    """Invalid pseudo-timezone: tzinfo exists but provides no UTC offset."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return "NULL-OFFSET"


NOW = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)


def test_evidence_timestamp_rejects_tzinfo_without_utc_offset():
    invalid_timezone = NullOffsetTimezone()
    earlier = datetime(2026, 10, 17, 19, 0, tzinfo=invalid_timezone)
    later = datetime(2026, 10, 17, 20, 0, tzinfo=invalid_timezone)

    with pytest.raises(ValueError, match="timezone-aware"):
        EvidenceTimestamp(
            event_timestamp=earlier,
            available_timestamp=earlier,
            decision_timestamp=later,
        )


def test_evidence_record_rejects_tzinfo_without_utc_offset():
    invalid_timezone = NullOffsetTimezone()
    earlier = datetime(2026, 10, 17, 19, 0, tzinfo=invalid_timezone)
    later = datetime(2026, 10, 17, 20, 0, tzinfo=invalid_timezone)

    with pytest.raises(ValueError, match="EVIDENCE_TIMESTAMP_MUST_BE_TIMEZONE_AWARE"):
        EvidenceRecord(
            kind="clock_valid",
            value=True,
            source="test",
            observed_at=earlier,
            decision_at=later,
            intent_id="intent-1",
            correlation_id="correlation-1",
            expires_at=later + timedelta(minutes=1),
        )


def test_evidence_record_rejects_validation_time_without_utc_offset():
    record = EvidenceRecord(
        kind="clock_valid",
        value=True,
        source="test",
        observed_at=NOW,
        decision_at=NOW,
        intent_id="intent-1",
        correlation_id="correlation-1",
    )
    invalid_now = datetime(2026, 10, 17, 20, 1, tzinfo=NullOffsetTimezone())

    with pytest.raises(ValueError, match="EVIDENCE_VALIDATION_TIMEZONE_REQUIRED"):
        record.valid_for("intent-1", invalid_now)
