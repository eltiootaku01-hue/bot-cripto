from pathlib import Path

import pytest

from bot_obrero.persistent_ledger import IdempotencyConflict, SQLiteIdempotencyLedger


def test_duplicate_id_survives_restart(tmp_path: Path):
    path = tmp_path / "ledger.sqlite"
    first = SQLiteIdempotencyLedger(path)
    assert first.register("order-1", {"symbol": "BTCUSDT", "qty": "1"})
    first.mark_result("order-1", "UNKNOWN")
    first.close()

    second = SQLiteIdempotencyLedger(path)
    assert not second.register("order-1", {"qty": "1", "symbol": "BTCUSDT"})
    assert second.get("order-1").result == "UNKNOWN"
    second.close()


def test_same_id_with_different_intent_is_blocked(tmp_path: Path):
    ledger = SQLiteIdempotencyLedger(tmp_path / "ledger.sqlite")
    assert ledger.register("order-1", {"symbol": "BTCUSDT", "qty": "1"})
    with pytest.raises(IdempotencyConflict, match="INTENT_MISMATCH"):
        ledger.register("order-1", {"symbol": "BTCUSDT", "qty": "2"})
    ledger.close()


def test_unknown_result_is_persisted_before_retry_decision(tmp_path: Path):
    ledger = SQLiteIdempotencyLedger(tmp_path / "ledger.sqlite")
    assert ledger.register("order-1", {"symbol": "BTCUSDT", "qty": "1"})
    ledger.mark_result("order-1", "ORDER_RESULT_UNKNOWN")
    assert ledger.get("order-1").result == "ORDER_RESULT_UNKNOWN"
    ledger.close()
