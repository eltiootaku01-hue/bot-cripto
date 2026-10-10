"""HUESO 05-E: durable Reservation -> Execution admission bridge v1.0.

The bridge prepares intents exclusively from the canonical terms and authorization
state persisted during financial admission. It does not contact or reconcile an exchange.
Stored hashes detect local representation mismatches; they are not cryptographic signatures.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import sqlite3
from typing import Any

from .reservation import (
    PersistedReservationTradeTermsSnapshot,
    Reservation,
    ReservationState,
    ReservationTransitionEvidence,
    SQLiteReservationStore,
)
from .risk_authorization import (
    RiskAuthorization,
    RiskAuthorizationStatus,
    risk_authorization_semantic_fingerprint,
)


class ExecutionBridgeStatus(str, Enum):
    PREPARED = "PREPARED"
    ALREADY_PREPARED = "ALREADY_PREPARED"
    REJECTED = "REJECTED"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class ExecutionBridgeError(ValueError):
    """Base error for an unverifiable reservation execution binding."""


class ExecutionBridgeRejected(ExecutionBridgeError):
    """A business precondition was not satisfied."""


class ExecutionBridgeConflict(ExecutionBridgeError):
    """A persisted identity is reused with different terms."""


class ExecutionBridgeBlocked(ExecutionBridgeError):
    """Persistence or integrity evidence is insufficient to proceed."""


@dataclass(frozen=True)
class PreparedExecutionIntent:
    """Stable reference to a persisted intent; carries no caller-supplied economics."""

    reservation_id: str
    client_order_id: str
    intent_hash: str

    def __post_init__(self) -> None:
        for name in ("reservation_id", "client_order_id", "intent_hash"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ExecutionBridgeError(f"{name} must be a non-empty string")
        if len(self.intent_hash) != 64 or any(c not in "0123456789abcdef" for c in self.intent_hash):
            raise ExecutionBridgeError("intent_hash must be lowercase SHA-256 hex")


@dataclass(frozen=True)
class ExecutionBridgePreparation:
    status: ExecutionBridgeStatus
    intent: PreparedExecutionIntent | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionBridgeStatus):
            raise ExecutionBridgeError("status must be ExecutionBridgeStatus")
        if type(self.reason) is not str or not self.reason.strip():
            raise ExecutionBridgeError("reason must be non-empty")
        if self.status in {
            ExecutionBridgeStatus.PREPARED,
            ExecutionBridgeStatus.ALREADY_PREPARED,
            ExecutionBridgeStatus.UNKNOWN,
        }:
            if not isinstance(self.intent, PreparedExecutionIntent):
                raise ExecutionBridgeError("prepared/unknown status requires an intent identity")
        elif self.intent is not None:
            raise ExecutionBridgeError("rejected/conflict/blocked must not expose an intent")


@dataclass(frozen=True)
class PersistedExecutionBinding:
    reservation_id: str
    client_order_id: str
    authorization_fingerprint: str
    terms_hash: str
    intent_hash: str
    intent_json: str
    state: str
    created_at: datetime
    updated_at: datetime
    exchange_order_id: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "reservation_id", "client_order_id", "authorization_fingerprint",
            "terms_hash", "intent_hash", "intent_json", "state",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise ExecutionBridgeBlocked(f"persisted binding {name} must be non-empty")
        if _digest(self.intent_json) != self.intent_hash:
            raise ExecutionBridgeBlocked("persisted intent hash mismatch")
        try:
            payload = json.loads(self.intent_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExecutionBridgeBlocked("persisted intent JSON is malformed") from exc
        if not isinstance(payload, dict):
            raise ExecutionBridgeBlocked("persisted intent payload must be an object")
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ExecutionBridgeBlocked("persisted execution timestamps must be timezone-aware")

    @property
    def payload(self) -> dict[str, Any]:
        result = json.loads(self.intent_json)
        if not isinstance(result, dict):
            raise ExecutionBridgeBlocked("persisted intent payload must be an object")
        return result


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionBridgeBlocked("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decimal_text(value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise ExecutionBridgeBlocked("economic value must be a finite Decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _decimal_field(value: Any, name: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise ExecutionBridgeBlocked(f"{name} must be stored as a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ExecutionBridgeBlocked(f"{name} is not a decimal string") from exc
    if not result.is_finite() or _decimal_text(result) != value:
        raise ExecutionBridgeBlocked(f"{name} is non-finite or not canonically normalized")
    return result


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReservationExecutionBridge:
    """Derive, persist, and verify order identity from admitted reservation state."""

    def __init__(self, store: SQLiteReservationStore):
        if not isinstance(store, SQLiteReservationStore):
            raise TypeError("store must be SQLiteReservationStore")
        self.store = store
        # This binding shares the reservation database/connection. The separate
        # idempotency ledger is only a repairable projection, not an atomic peer.
        self.store._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reservation_execution_bindings (
                reservation_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL UNIQUE,
                authorization_fingerprint TEXT NOT NULL,
                terms_hash TEXT NOT NULL,
                intent_hash TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                exchange_order_id TEXT,
                last_error TEXT,
                FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id)
            )
            """
        )
        self.store._connection.commit()

    def _load_binding(self, reservation_id: str) -> PersistedExecutionBinding | None:
        row = self.store._connection.execute(
            """
            SELECT reservation_id, client_order_id, authorization_fingerprint,
                   terms_hash, intent_hash, intent_json, state, created_at,
                   updated_at, exchange_order_id, last_error
            FROM reservation_execution_bindings
            WHERE reservation_id=?
            """,
            (reservation_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return PersistedExecutionBinding(
                reservation_id=row[0],
                client_order_id=row[1],
                authorization_fingerprint=row[2],
                terms_hash=row[3],
                intent_hash=row[4],
                intent_json=row[5],
                state=row[6],
                created_at=datetime.fromisoformat(row[7]),
                updated_at=datetime.fromisoformat(row[8]),
                exchange_order_id=row[9],
                last_error=row[10],
            )
        except (TypeError, ValueError) as exc:
            raise ExecutionBridgeBlocked("persisted execution binding is malformed") from exc

    @staticmethod
    def _verify_authorization(snapshot: dict[str, Any], binding) -> str:
        stored = snapshot.get("authorization")
        if not isinstance(stored, dict):
            raise ExecutionBridgeBlocked("canonical snapshot has no authorization envelope")
        expected = {
            "authorization_id": binding.authorization_id,
            "semantic_fingerprint": binding.semantic_fingerprint,
            "risk_decision_id": binding.risk_decision_id,
            "proposal_id": binding.proposal_id,
            "signal_id": binding.signal_id,
            "correlation_id": binding.correlation_id,
            "evaluation_context_id": binding.evaluation_context_id,
            "policy_id": binding.policy_id,
            "policy_version": binding.policy_version,
            "decision_timestamp": _utc(binding.decision_timestamp),
            "risk_evidence": [
                {"kind": item.kind, "reference_id": item.reference_id, "as_of": _utc(item.as_of)}
                for item in binding.risk_evidence
            ],
        }
        if stored != expected:
            raise ExecutionBridgeBlocked("authorization binding disagrees with trade-terms snapshot")
        if snapshot.get("risk_decision_id") != binding.risk_decision_id:
            raise ExecutionBridgeBlocked("risk decision identity mismatch")
        authorization = RiskAuthorization(
            authorization_id=binding.authorization_id,
            risk_decision_id=binding.risk_decision_id,
            proposal_id=binding.proposal_id,
            signal_id=binding.signal_id,
            correlation_id=binding.correlation_id,
            evaluation_context_id=binding.evaluation_context_id,
            policy_id=binding.policy_id,
            policy_version=binding.policy_version,
            decision_timestamp=binding.decision_timestamp,
            risk_evidence=binding.risk_evidence,
            status=RiskAuthorizationStatus.AUTHORIZED,
        )
        fingerprint = risk_authorization_semantic_fingerprint(authorization)
        if fingerprint != binding.semantic_fingerprint:
            raise ExecutionBridgeBlocked("authorization fingerprint cannot be reconstructed")
        return fingerprint

    def _derive_intent(
        self,
        reservation: Reservation,
        snapshot_row: PersistedReservationTradeTermsSnapshot,
        authorization_binding,
        client_order_id: str | None = None,
    ) -> tuple[str, dict[str, Any], str, str]:
        terms = snapshot_row.terms
        if terms.get("schema") != "reservation-trade-terms-v1":
            raise ExecutionBridgeBlocked("unsupported canonical terms schema")
        instrument = terms.get("instrument")
        if not isinstance(instrument, dict):
            raise ExecutionBridgeBlocked("canonical instrument is missing")
        for field in ("instrument_id", "symbol", "market", "instrument_type", "base_asset", "quote_asset"):
            if type(instrument.get(field)) is not str or not instrument[field].strip():
                raise ExecutionBridgeBlocked(f"canonical instrument {field} is missing")
        if instrument["instrument_type"] != "CRYPTO_SPOT":
            raise ExecutionBridgeBlocked("unsupported canonical instrument type")
        if instrument["symbol"] != terms.get("symbol"):
            raise ExecutionBridgeBlocked("canonical symbol does not match instrument identity")

        canonical_fields = (
            ("reservation_id", reservation.reservation_id),
            ("account_id", reservation.account_id),
            ("resource_kind", reservation.resource_kind.value),
            ("asset", reservation.asset),
            ("reserved_amount", _decimal_text(reservation.reserved_amount)),
            ("proposal_id", reservation.proposal_id),
            ("risk_decision_id", reservation.risk_decision_id),
            ("correlation_id", reservation.correlation_id),
        )
        for field, expected in canonical_fields:
            if terms.get(field) != expected:
                raise ExecutionBridgeBlocked(f"reservation and snapshot disagree on {field}")

        for field in ("proposal_id", "signal_id", "correlation_id", "strategy_identity", "strategy_version"):
            if type(terms.get(field)) is not str or not terms[field].strip():
                raise ExecutionBridgeBlocked(f"canonical {field} is missing")
        if terms.get("symbol") != instrument["symbol"]:
            raise ExecutionBridgeBlocked("proposal/instrument symbol mismatch")

        fingerprint = self._verify_authorization(terms, authorization_binding)
        for field in ("proposal_id", "signal_id", "correlation_id"):
            if terms.get(field) != getattr(authorization_binding, field):
                raise ExecutionBridgeBlocked(f"authorization {field} mismatch")
        if terms.get("decision_timestamp") != _utc(authorization_binding.decision_timestamp):
            raise ExecutionBridgeBlocked("decision timestamp mismatch")

        quantity = _decimal_field(terms.get("requested_quantity"), "requested_quantity")
        price = _decimal_field(terms.get("requested_price"), "requested_price", optional=True)
        max_spend = _decimal_field(terms.get("max_quote_spend"), "max_quote_spend", optional=True)
        reserved = _decimal_field(terms.get("reserved_amount"), "reserved_amount")
        if quantity is None or quantity <= 0 or reserved is None or reserved <= 0:
            raise ExecutionBridgeBlocked("canonical quantity/reserve must be positive")

        side = terms.get("side")
        order_type = terms.get("order_type")
        price_policy = terms.get("price_policy")
        if side not in {"BUY", "SELL"} or order_type not in {"MARKET", "LIMIT"}:
            raise ExecutionBridgeBlocked("unsupported side/order type")
        if order_type == "MARKET":
            if price is not None or price_policy != "MARKET_REFERENCE":
                raise ExecutionBridgeBlocked("MARKET price terms are inconsistent")
        else:
            if price is None or price <= 0 or price_policy != "FIXED":
                raise ExecutionBridgeBlocked("LIMIT price terms are inconsistent")

        if side == "BUY" and order_type == "MARKET":
            if max_spend is None or max_spend <= 0:
                raise ExecutionBridgeBlocked("BUY MARKET requires max_quote_spend")
            expected_kind, expected_asset, expected_reserve = "QUOTE", instrument["quote_asset"], max_spend
        elif side == "BUY" and order_type == "LIMIT":
            if max_spend is not None:
                raise ExecutionBridgeBlocked("BUY LIMIT cannot have max_quote_spend")
            expected_kind, expected_asset, expected_reserve = "QUOTE", instrument["quote_asset"], quantity * price
        else:
            if max_spend is not None:
                raise ExecutionBridgeBlocked("SELL cannot have max_quote_spend")
            expected_kind, expected_asset, expected_reserve = "BASE", instrument["base_asset"], quantity
        if terms.get("resource_kind") != expected_kind or terms.get("asset") != expected_asset:
            raise ExecutionBridgeBlocked("reservation resource/asset conflicts with order economics")
        if expected_reserve != reserved:
            raise ExecutionBridgeBlocked("reservation amount conflicts with canonical order economics")

        expected_client_id = "h05e-" + _digest(reservation.reservation_id + ":" + fingerprint)[:24]
        if client_order_id is not None and client_order_id != expected_client_id:
            raise ExecutionBridgeConflict("client_order_id differs from stable reservation binding")

        payload = {
            "schema": "reservation-order-intent-v1",
            "reservation_id": reservation.reservation_id,
            "client_order_id": expected_client_id,
            "proposal_id": terms["proposal_id"],
            "signal_id": terms["signal_id"],
            "account_id": terms["account_id"],
            "instrument_id": instrument["instrument_id"],
            "market": instrument["market"],
            "instrument_type": instrument["instrument_type"],
            "symbol": terms["symbol"],
            "base_asset": instrument["base_asset"],
            "quote_asset": instrument["quote_asset"],
            "side": side,
            "order_type": order_type,
            "quantity": _decimal_text(quantity),
            "price": None if price is None else _decimal_text(price),
            "max_quote_spend": None if max_spend is None else _decimal_text(max_spend),
            "price_policy": price_policy,
            "strategy_identity": terms["strategy_identity"],
            "strategy_version": terms["strategy_version"],
            "decision_timestamp": terms["decision_timestamp"],
            "correlation_id": terms["correlation_id"],
            "risk_decision_id": terms["risk_decision_id"],
            "authorization_id": authorization_binding.authorization_id,
            "authorization_fingerprint": fingerprint,
            "evaluation_context_id": authorization_binding.evaluation_context_id,
            "policy_id": authorization_binding.policy_id,
            "policy_version": authorization_binding.policy_version,
            "resource_kind": expected_kind,
            "asset": expected_asset,
            "reserved_amount": _decimal_text(reserved),
            "terms_hash": snapshot_row.terms_hash,
        }
        intent_json = _canonical_json(payload)
        return expected_client_id, payload, intent_json, _digest(intent_json)

    @staticmethod
    def _map_exception(error: Exception) -> ExecutionBridgePreparation:
        if isinstance(error, ExecutionBridgeConflict):
            return ExecutionBridgePreparation(ExecutionBridgeStatus.CONFLICT, None, type(error).__name__)
        if isinstance(error, ExecutionBridgeRejected):
            return ExecutionBridgePreparation(ExecutionBridgeStatus.REJECTED, None, type(error).__name__)
        if isinstance(error, ExecutionBridgeBlocked):
            return ExecutionBridgePreparation(ExecutionBridgeStatus.BLOCKED, None, type(error).__name__)
        return ExecutionBridgePreparation(ExecutionBridgeStatus.BLOCKED, None, "PERSISTENCE_OR_INTEGRITY_ERROR:" + type(error).__name__)

    def prepare(self, reservation_id: str) -> ExecutionBridgePreparation:
        if type(reservation_id) is not str or not reservation_id.strip():
            return ExecutionBridgePreparation(ExecutionBridgeStatus.REJECTED, None, "INVALID_RESERVATION_ID")
        connection = self.store._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self.store.get(reservation_id)
            if reservation is None:
                connection.rollback()
                return ExecutionBridgePreparation(ExecutionBridgeStatus.REJECTED, None, "RESERVATION_NOT_FOUND")
            snapshot = self.store.get_trade_terms_snapshot(reservation_id)
            auth_binding = self.store.get_authorization_binding(reservation_id)
            if snapshot is None or auth_binding is None:
                connection.rollback()
                return ExecutionBridgePreparation(ExecutionBridgeStatus.BLOCKED, None, "CANONICAL_TERMS_OR_AUTHORIZATION_MISSING")

            client_id, _, intent_json, intent_hash = self._derive_intent(reservation, snapshot, auth_binding)
            existing = self._load_binding(reservation_id)
            if existing is not None:
                self._verify_binding(existing, allow_non_prepared=True)
                if (
                    existing.client_order_id != client_id
                    or existing.intent_hash != intent_hash
                    or existing.intent_json != intent_json
                    or existing.terms_hash != snapshot.terms_hash
                    or existing.authorization_fingerprint != auth_binding.semantic_fingerprint
                ):
                    connection.rollback()
                    return ExecutionBridgePreparation(ExecutionBridgeStatus.CONFLICT, None, "PERSISTED_INTENT_IDENTITY_CONFLICT")
                if reservation.client_order_id != existing.client_order_id:
                    connection.rollback()
                    return ExecutionBridgePreparation(ExecutionBridgeStatus.BLOCKED, None, "RESERVATION_CLIENT_ORDER_BINDING_MISMATCH")
                connection.commit()
                prepared = PreparedExecutionIntent(reservation_id, existing.client_order_id, existing.intent_hash)
                if existing.state in {"UNKNOWN", "SUBMISSION_STARTED"} or reservation.state is ReservationState.UNKNOWN:
                    return ExecutionBridgePreparation(ExecutionBridgeStatus.UNKNOWN, prepared, "EXECUTION_RESULT_REQUIRES_RESOLUTION")
                if existing.state == "BLOCKED":
                    return ExecutionBridgePreparation(ExecutionBridgeStatus.BLOCKED, None, "EXECUTION_BINDING_BLOCKED")
                if existing.state == "SUBMITTED":
                    return ExecutionBridgePreparation(ExecutionBridgeStatus.REJECTED, None, "ORDER_ALREADY_SUBMITTED")
                if reservation.state is not ReservationState.ACTIVE:
                    return ExecutionBridgePreparation(ExecutionBridgeStatus.REJECTED, None, "RESERVATION_STATE_NOT_ALLOWED")
                return ExecutionBridgePreparation(ExecutionBridgeStatus.ALREADY_PREPARED, prepared, "IDENTICAL_PERSISTED_BINDING")

            if reservation.state is not ReservationState.ACTIVE:
                connection.rollback()
                return ExecutionBridgePreparation(ExecutionBridgeStatus.REJECTED, None, "RESERVATION_STATE_NOT_ALLOWED")
            if reservation.client_order_id is not None:
                connection.rollback()
                return ExecutionBridgePreparation(ExecutionBridgeStatus.CONFLICT, None, "RESERVATION_HAS_UNBOUND_CLIENT_ORDER_ID")

            now = _utc(datetime.now(timezone.utc))
            connection.execute(
                """
                INSERT INTO reservation_execution_bindings(
                    reservation_id, client_order_id, authorization_fingerprint,
                    terms_hash, intent_hash, intent_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?)
                """,
                (reservation_id, client_id, auth_binding.semantic_fingerprint, snapshot.terms_hash, intent_hash, intent_json, now, now),
            )
            cursor = connection.execute(
                "UPDATE reservations SET client_order_id=? WHERE reservation_id=? AND client_order_id IS NULL AND state=?",
                (client_id, reservation_id, ReservationState.ACTIVE.value),
            )
            if cursor.rowcount != 1:
                raise ExecutionBridgeConflict("RESERVATION_CLIENT_ORDER_BINDING_RACE")
            connection.commit()
            prepared = PreparedExecutionIntent(reservation_id, client_id, intent_hash)
            return ExecutionBridgePreparation(ExecutionBridgeStatus.PREPARED, prepared, "CANONICAL_INTENT_PREPARED")
        except Exception as exc:
            connection.rollback()
            return self._map_exception(exc)

    def _verify_binding(
        self,
        binding: PersistedExecutionBinding,
        *,
        allow_non_prepared: bool = False,
        expected_state: str | None = None,
    ) -> PersistedExecutionBinding:
        reservation = self.store.get(binding.reservation_id)
        if reservation is None:
            raise ExecutionBridgeBlocked("reservation disappeared")
        snapshot = self.store.get_trade_terms_snapshot(binding.reservation_id)
        auth_binding = self.store.get_authorization_binding(binding.reservation_id)
        if snapshot is None or auth_binding is None:
            raise ExecutionBridgeBlocked("canonical terms or authorization missing")
        client_id, _, intent_json, intent_hash = self._derive_intent(
            reservation, snapshot, auth_binding, client_order_id=binding.client_order_id
        )
        if (
            binding.client_order_id != client_id
            or binding.intent_hash != intent_hash
            or binding.intent_json != intent_json
            or binding.terms_hash != snapshot.terms_hash
            or binding.authorization_fingerprint != auth_binding.semantic_fingerprint
            or reservation.client_order_id != binding.client_order_id
        ):
            raise ExecutionBridgeBlocked("persisted reservation/execution binding integrity mismatch")
        if expected_state is not None and binding.state != expected_state:
            if binding.state in {"UNKNOWN", "SUBMISSION_STARTED"}:
                raise ExecutionBridgeBlocked("execution result is UNKNOWN; automatic resubmission prohibited")
            raise ExecutionBridgeRejected("execution state mismatch")
        if not allow_non_prepared and binding.state != "PREPARED":
            if binding.state in {"UNKNOWN", "SUBMISSION_STARTED"}:
                raise ExecutionBridgeBlocked("execution result is UNKNOWN; automatic resubmission prohibited")
            raise ExecutionBridgeRejected("execution binding is not PREPARED")
        if binding.state == "PREPARED" and reservation.state is not ReservationState.ACTIVE:
            raise ExecutionBridgeRejected("reservation state is not ACTIVE")
        return binding

    def verify_prepared(self, prepared: PreparedExecutionIntent) -> PersistedExecutionBinding:
        if type(prepared) is not PreparedExecutionIntent:
            raise ExecutionBridgeRejected("PREPARED_EXECUTION_INTENT_REQUIRED")
        binding = self._load_binding(prepared.reservation_id)
        if binding is None:
            raise ExecutionBridgeRejected("PERSISTED_EXECUTION_BINDING_NOT_FOUND")
        if binding.client_order_id != prepared.client_order_id or binding.intent_hash != prepared.intent_hash:
            raise ExecutionBridgeConflict("PREPARED_INTENT_DOES_NOT_MATCH_AUTHORITATIVE_BINDING")
        return self._verify_binding(binding)

    def begin_submission(
        self,
        prepared: PreparedExecutionIntent,
        *,
        occurred_at: datetime,
    ) -> PersistedExecutionBinding:
        connection = self.store._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load_binding(prepared.reservation_id)
            if current is None:
                raise ExecutionBridgeRejected("PERSISTED_EXECUTION_BINDING_NOT_FOUND")
            if current.client_order_id != prepared.client_order_id or current.intent_hash != prepared.intent_hash:
                raise ExecutionBridgeConflict("PREPARED_INTENT_DOES_NOT_MATCH_AUTHORITATIVE_BINDING")
            self._verify_binding(current, expected_state="PREPARED")
            occurred_text = _utc(occurred_at)
            cursor = connection.execute(
                """
                UPDATE reservation_execution_bindings
                SET state='SUBMISSION_STARTED', updated_at=?
                WHERE reservation_id=? AND state='PREPARED'
                """,
                (occurred_text, prepared.reservation_id),
            )
            if cursor.rowcount != 1:
                raise ExecutionBridgeConflict("EXECUTION_SUBMISSION_STATE_RACE")
            connection.commit()
            result = self._load_binding(prepared.reservation_id)
            if result is None:
                raise ExecutionBridgeBlocked("submission marker vanished")
            return result
        except Exception:
            connection.rollback()
            raise

    def finish_submission(
        self,
        prepared: PreparedExecutionIntent,
        *,
        outcome: str,
        occurred_at: datetime,
        error: str | None = None,
    ) -> PersistedExecutionBinding:
        if outcome not in {"SUBMITTED", "UNKNOWN", "BLOCKED"}:
            raise ExecutionBridgeRejected("unsupported execution outcome")
        connection = self.store._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load_binding(prepared.reservation_id)
            if current is None:
                raise ExecutionBridgeRejected("PERSISTED_EXECUTION_BINDING_NOT_FOUND")
            if current.client_order_id != prepared.client_order_id or current.intent_hash != prepared.intent_hash:
                raise ExecutionBridgeConflict("PREPARED_INTENT_DOES_NOT_MATCH_AUTHORITATIVE_BINDING")
            self._verify_binding(current, allow_non_prepared=True, expected_state="SUBMISSION_STARTED")
            occurred_text = _utc(occurred_at)
            cursor = connection.execute(
                """
                UPDATE reservation_execution_bindings
                SET state=?, updated_at=?, last_error=?
                WHERE reservation_id=? AND state='SUBMISSION_STARTED'
                """,
                (outcome, occurred_text, error, prepared.reservation_id),
            )
            if cursor.rowcount != 1:
                raise ExecutionBridgeConflict("EXECUTION_RESULT_STATE_RACE")
            if outcome == "UNKNOWN":
                reservation = self.store.get(prepared.reservation_id)
                if reservation is None:
                    raise ExecutionBridgeBlocked("reservation missing while recording UNKNOWN")
                if reservation.state is ReservationState.ACTIVE:
                    updated, transition = reservation.mark_unknown(
                        evidence=ReservationTransitionEvidence(
                            kind="EXECUTION_RESULT_UNKNOWN",
                            reference_id=prepared.client_order_id,
                            occurred_at=occurred_at,
                        )
                    )
                    connection.execute(
                        "UPDATE reservations SET state=?, updated_at=? WHERE reservation_id=?",
                        (updated.state.value, _utc(updated.updated_at), updated.reservation_id),
                    )
                    self.store._insert_transition(reservation=updated, transition=transition)
                elif reservation.state is not ReservationState.UNKNOWN:
                    raise ExecutionBridgeBlocked("reservation state cannot preserve ambiguous execution")
            connection.commit()
            result = self._load_binding(prepared.reservation_id)
            if result is None:
                raise ExecutionBridgeBlocked("execution result vanished")
            return result
        except Exception:
            connection.rollback()
            raise

    def recover_ambiguous(
        self,
        reservation_id: str,
        *,
        occurred_at: datetime,
    ) -> PersistedExecutionBinding | None:
        """Normalize a crash-left submission marker to durable UNKNOWN; never resend."""
        connection = self.store._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load_binding(reservation_id)
            if current is None:
                connection.rollback()
                return None
            self._verify_binding(current, allow_non_prepared=True)
            occurred_text = _utc(occurred_at)
            if current.state == "SUBMISSION_STARTED":
                cursor = connection.execute(
                    """
                    UPDATE reservation_execution_bindings
                    SET state='UNKNOWN', updated_at=?, last_error=?
                    WHERE reservation_id=? AND state='SUBMISSION_STARTED'
                    """,
                    (occurred_text, "RECOVERED_AFTER_CRASH_RESULT_UNKNOWN", reservation_id),
                )
                if cursor.rowcount != 1:
                    raise ExecutionBridgeConflict("EXECUTION_RECOVERY_STATE_RACE")
                reservation = self.store.get(reservation_id)
                if reservation is None:
                    raise ExecutionBridgeBlocked("reservation missing during recovery")
                if reservation.state is ReservationState.ACTIVE:
                    updated, transition = reservation.mark_unknown(
                        evidence=ReservationTransitionEvidence(
                            kind="EXECUTION_RESULT_UNKNOWN",
                            reference_id=current.client_order_id,
                            occurred_at=occurred_at,
                        )
                    )
                    connection.execute(
                        "UPDATE reservations SET state=?, updated_at=? WHERE reservation_id=?",
                        (updated.state.value, _utc(updated.updated_at), reservation_id),
                    )
                    self.store._insert_transition(reservation=updated, transition=transition)
            connection.commit()
            return self._load_binding(reservation_id)
        except Exception:
            connection.rollback()
            raise

    def get_binding(self, reservation_id: str) -> PersistedExecutionBinding | None:
        binding = self._load_binding(reservation_id)
        if binding is None:
            return None
        return self._verify_binding(binding, allow_non_prepared=True)


__all__ = [
    "ExecutionBridgeError",
    "ExecutionBridgeStatus",
    "ExecutionBridgePreparation",
    "ExecutionBridgeRejected",
    "ExecutionBridgeConflict",
    "ExecutionBridgeBlocked",
    "PreparedExecutionIntent",
    "PersistedExecutionBinding",
    "ReservationExecutionBridge",
]
