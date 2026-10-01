from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import sqlite3
from typing import Any


class IdempotencyConflict(ValueError):
    """The same client order ID was reused with different request intent."""


class FillIdentityConflict(ValueError):
    """A venue fill identity was associated with another order."""


@dataclass(frozen=True)
class LedgerRecord:
    client_order_id: str
    intent_hash: str
    result: str


class SQLiteIdempotencyLedger:
    """Durable order and external-fill idempotency barrier."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS idempotency_ledger (
                client_order_id TEXT PRIMARY KEY,
                intent_hash TEXT NOT NULL,
                result TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS applied_fills (
                fill_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL
            )"""
        )
        self._connection.commit()

    @staticmethod
    def _intent_hash(intent: Any) -> str:
        canonical = json.dumps(
            intent,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def register(self, client_order_id: str, intent: Any) -> bool:
        if not client_order_id:
            raise ValueError("INVALID_CLIENT_ORDER_ID")
        digest = self._intent_hash(intent)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO idempotency_ledger(client_order_id,intent_hash,result) VALUES (?, ?, ?)",
                    (client_order_id, digest, "PENDING_SUBMIT"),
                )
            return True
        except sqlite3.IntegrityError:
            row = self._connection.execute(
                "SELECT intent_hash FROM idempotency_ledger WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise
            if row[0] != digest:
                raise IdempotencyConflict("CLIENT_ORDER_ID_INTENT_MISMATCH")
            return False

    def mark_result(self, client_order_id: str, result: str) -> None:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE idempotency_ledger SET result=? WHERE client_order_id=?",
                (result, client_order_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("UNKNOWN_CLIENT_ORDER_ID")

    def get(self, client_order_id: str) -> LedgerRecord | None:
        row = self._connection.execute(
            "SELECT client_order_id,intent_hash,result FROM idempotency_ledger WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        return None if row is None else LedgerRecord(*row)

    def register_fill(self, fill_id: str, client_order_id: str) -> bool:
        if not fill_id or not client_order_id:
            raise ValueError("INVALID_FILL_IDENTITY")
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO applied_fills(fill_id,client_order_id) VALUES (?,?)",
                    (fill_id, client_order_id),
                )
            return True
        except sqlite3.IntegrityError:
            row = self._connection.execute(
                "SELECT client_order_id FROM applied_fills WHERE fill_id=?",
                (fill_id,),
            ).fetchone()
            if row is None:
                raise
            if row[0] != client_order_id:
                raise FillIdentityConflict("FILL_ID_REUSED_FOR_DIFFERENT_ORDER")
            return False

    def unregister_fill(self, fill_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM applied_fills WHERE fill_id=?",
                (fill_id,),
            )

    def close(self) -> None:
        self._connection.close()
