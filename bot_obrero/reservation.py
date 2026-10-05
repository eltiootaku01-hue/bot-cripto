"""Durable, provider-neutral Reservation contract with atomic local admission.

Reservation remains separate from account state, risk decisions, order lifecycle,
execution, reconciliation, and idempotency. Atomic admission serializes the
local Reservation read/check/write boundary on one SQLite connection.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
import sqlite3
from uuid import uuid4

from .risk_contracts import (
    CanonicalAccountState,
    Completeness,
    RiskDecision,
    RiskDecisionOutcome,
)
from .trade_proposal import TradeProposal


class ReservationContractError(ValueError):
    """Base error for invalid Reservation contract state."""


class ReservationConflict(ReservationContractError):
    """A Proposal already has a non-terminal Reservation."""


class ReservationAdmissionError(ReservationContractError):
    """Base error for fail-closed atomic Reservation admission."""


class ReservationAdmissionBusy(ReservationAdmissionError):
    """SQLite could not acquire the admission write lock."""


class ReservationAdmissionRejected(ReservationAdmissionError):
    """The local admission preconditions or effective capacity rejected a request."""


class ReservationSchemaConflict(ReservationContractError):
    """Existing persisted data prevents the Reservation schema guard from being installed."""


class InvalidReservationTransition(ReservationContractError):
    """A requested Reservation lifecycle transition is not permitted."""


class ReservationResourceKind(str, Enum):
    QUOTE = "QUOTE"
    BASE = "BASE"


class ReservationState(str, Enum):
    ACTIVE = "ACTIVE"
    PARTIALLY_CONSUMED = "PARTIALLY_CONSUMED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    UNKNOWN = "UNKNOWN"


NON_TERMINAL_STATES = frozenset(
    {
        ReservationState.ACTIVE,
        ReservationState.PARTIALLY_CONSUMED,
        ReservationState.UNKNOWN,
    }
)

TERMINAL_STATES = frozenset(
    {
        ReservationState.CONSUMED,
        ReservationState.RELEASED,
    }
)


def _nonempty(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ReservationContractError(f"{field_name} must be a non-empty str")


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ReservationContractError(f"{field_name} must be timezone-aware")


def _decimal(value: Decimal, field_name: str) -> None:
    if type(value) is not Decimal:
        raise ReservationContractError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ReservationContractError(f"{field_name} must be finite")


def _is_sqlite_busy(exc: sqlite3.OperationalError) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


@dataclass(frozen=True)
class ReservationTransitionEvidence:
    kind: str
    reference_id: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _nonempty(self.kind, "kind")
        _nonempty(self.reference_id, "reference_id")
        _aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True)
class ReservationTransition:
    reservation_id: str
    from_state: ReservationState | None
    to_state: ReservationState
    occurred_at: datetime
    evidence_kind: str
    evidence_reference_id: str
    consumed_delta: Decimal

    def __post_init__(self) -> None:
        _nonempty(self.reservation_id, "reservation_id")
        if self.from_state is not None and not isinstance(
            self.from_state, ReservationState
        ):
            raise ReservationContractError("from_state must be ReservationState or None")
        if not isinstance(self.to_state, ReservationState):
            raise ReservationContractError("to_state must be ReservationState")
        _aware(self.occurred_at, "occurred_at")
        _nonempty(self.evidence_kind, "evidence_kind")
        _nonempty(self.evidence_reference_id, "evidence_reference_id")
        _decimal(self.consumed_delta, "consumed_delta")
        if self.consumed_delta < 0:
            raise ReservationContractError("consumed_delta must be non-negative")


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    account_id: str
    resource_kind: ReservationResourceKind
    asset: str
    reserved_amount: Decimal
    consumed_amount: Decimal
    remaining_amount: Decimal
    state: ReservationState
    proposal_id: str
    risk_decision_id: str
    correlation_id: str
    client_order_id: str | None
    exchange_order_id: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "reservation_id",
            "account_id",
            "asset",
            "proposal_id",
            "risk_decision_id",
            "correlation_id",
        ):
            _nonempty(getattr(self, name), name)

        if not isinstance(self.resource_kind, ReservationResourceKind):
            raise ReservationContractError(
                "resource_kind must be ReservationResourceKind"
            )
        if not isinstance(self.state, ReservationState):
            raise ReservationContractError("state must be ReservationState")

        if self.reservation_id in {
            self.proposal_id,
            self.risk_decision_id,
            self.correlation_id,
            self.account_id,
        }:
            raise ReservationContractError(
                "reservation_id must differ from core reference identities"
            )

        if self.client_order_id is not None:
            _nonempty(self.client_order_id, "client_order_id")
            if self.client_order_id == self.reservation_id:
                raise ReservationContractError(
                    "client_order_id must differ from reservation_id"
                )
        if self.exchange_order_id is not None:
            _nonempty(self.exchange_order_id, "exchange_order_id")
            if self.exchange_order_id == self.reservation_id:
                raise ReservationContractError(
                    "exchange_order_id must differ from reservation_id"
                )

        for name in ("reserved_amount", "consumed_amount", "remaining_amount"):
            _decimal(getattr(self, name), name)

        if (
            self.reserved_amount < 0
            or self.consumed_amount < 0
            or self.remaining_amount < 0
        ):
            raise ReservationContractError("reservation quantities must be non-negative")
        if self.consumed_amount > self.reserved_amount:
            raise ReservationContractError(
                "consumed_amount cannot exceed reserved_amount"
            )
        if self.remaining_amount > self.reserved_amount:
            raise ReservationContractError(
                "remaining_amount cannot exceed reserved_amount"
            )
        if self.remaining_amount != self.reserved_amount - self.consumed_amount:
            raise ReservationContractError(
                "remaining_amount must equal reserved_amount - consumed_amount"
            )

        if self.state is ReservationState.ACTIVE and self.consumed_amount != 0:
            raise ReservationContractError(
                "ACTIVE reservation must have zero consumed_amount"
            )
        if self.state is ReservationState.PARTIALLY_CONSUMED and not (
            Decimal("0") < self.consumed_amount < self.reserved_amount
        ):
            raise ReservationContractError(
                "PARTIALLY_CONSUMED requires consumed_amount between zero and reserved_amount"
            )
        if self.state is ReservationState.CONSUMED:
            if self.consumed_amount != self.reserved_amount:
                raise ReservationContractError(
                    "CONSUMED reservation must have consumed_amount == reserved_amount"
                )
            if self.remaining_amount != 0:
                raise ReservationContractError(
                    "CONSUMED reservation must have zero remaining_amount"
                )

        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ReservationContractError(
                "updated_at cannot precede created_at"
            )

    @classmethod
    def from_trade_proposal_and_risk_decision(
        cls,
        *,
        proposal: TradeProposal,
        risk_decision: RiskDecision,
        account_id: str,
        resource_kind: ReservationResourceKind,
        asset: str,
        reserved_amount: Decimal,
        created_at: datetime,
        reservation_id: str | None = None,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> "Reservation":
        if not isinstance(proposal, TradeProposal):
            raise TypeError("proposal must be TradeProposal")
        if not isinstance(risk_decision, RiskDecision):
            raise TypeError("risk_decision must be RiskDecision")
        if risk_decision.outcome is not RiskDecisionOutcome.APPROVED:
            raise ReservationContractError(
                "Reservation requires RiskDecisionOutcome.APPROVED"
            )
        if risk_decision.proposal_id != proposal.proposal_id:
            raise ReservationContractError(
                "risk_decision.proposal_id must match proposal.proposal_id"
            )
        if risk_decision.correlation_id != proposal.correlation_id:
            raise ReservationContractError(
                "risk_decision.correlation_id must match proposal.correlation_id"
            )

        _nonempty(account_id, "account_id")
        _aware(created_at, "created_at")
        _decimal(reserved_amount, "reserved_amount")
        if reserved_amount < 0:
            raise ReservationContractError("reserved_amount must be non-negative")

        identifier = uuid4().hex if reservation_id is None else reservation_id
        return cls(
            reservation_id=identifier,
            account_id=account_id,
            resource_kind=resource_kind,
            asset=asset,
            reserved_amount=reserved_amount,
            consumed_amount=Decimal("0"),
            remaining_amount=reserved_amount,
            state=ReservationState.ACTIVE,
            proposal_id=proposal.proposal_id,
            risk_decision_id=risk_decision.risk_decision_id,
            correlation_id=proposal.correlation_id,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            created_at=created_at,
            updated_at=created_at,
        )

    @property
    def protected_capacity(self) -> Decimal:
        if self.state in NON_TERMINAL_STATES:
            return self.remaining_amount
        return Decimal("0")

    def consume(
        self,
        amount: Decimal,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> tuple["Reservation", ReservationTransition]:
        _decimal(amount, "amount")
        if amount <= 0:
            raise InvalidReservationTransition("consumption amount must be greater than zero")
        if self.state in TERMINAL_STATES:
            raise InvalidReservationTransition(
                "terminal Reservation cannot be consumed"
            )
        if evidence.occurred_at < self.updated_at:
            raise InvalidReservationTransition(
                "transition evidence timestamp cannot move backwards"
            )
        if amount > self.remaining_amount:
            raise InvalidReservationTransition(
                "consumption amount cannot exceed remaining_amount"
            )

        consumed = self.consumed_amount + amount
        state = (
            ReservationState.CONSUMED
            if consumed == self.reserved_amount
            else ReservationState.PARTIALLY_CONSUMED
        )
        updated = replace(
            self,
            consumed_amount=consumed,
            remaining_amount=self.reserved_amount - consumed,
            state=state,
            updated_at=evidence.occurred_at,
        )
        transition = ReservationTransition(
            reservation_id=self.reservation_id,
            from_state=self.state,
            to_state=state,
            occurred_at=evidence.occurred_at,
            evidence_kind=evidence.kind,
            evidence_reference_id=evidence.reference_id,
            consumed_delta=amount,
        )
        return updated, transition

    def release(
        self,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> tuple["Reservation", ReservationTransition]:
        if self.state in TERMINAL_STATES:
            raise InvalidReservationTransition(
                "terminal Reservation cannot be released"
            )
        if evidence.occurred_at < self.updated_at:
            raise InvalidReservationTransition(
                "transition evidence timestamp cannot move backwards"
            )

        updated = replace(
            self,
            state=ReservationState.RELEASED,
            updated_at=evidence.occurred_at,
        )
        transition = ReservationTransition(
            reservation_id=self.reservation_id,
            from_state=self.state,
            to_state=ReservationState.RELEASED,
            occurred_at=evidence.occurred_at,
            evidence_kind=evidence.kind,
            evidence_reference_id=evidence.reference_id,
            consumed_delta=Decimal("0"),
        )
        return updated, transition

    def mark_unknown(
        self,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> tuple["Reservation", ReservationTransition]:
        if self.state in TERMINAL_STATES:
            raise InvalidReservationTransition(
                "terminal Reservation cannot become UNKNOWN"
            )
        if evidence.occurred_at < self.updated_at:
            raise InvalidReservationTransition(
                "transition evidence timestamp cannot move backwards"
            )

        updated = replace(
            self,
            state=ReservationState.UNKNOWN,
            updated_at=evidence.occurred_at,
        )
        transition = ReservationTransition(
            reservation_id=self.reservation_id,
            from_state=self.state,
            to_state=ReservationState.UNKNOWN,
            occurred_at=evidence.occurred_at,
            evidence_kind=evidence.kind,
            evidence_reference_id=evidence.reference_id,
            consumed_delta=Decimal("0"),
        )
        return updated, transition



@dataclass(frozen=True)
class ReservationReadSet:
    """Complete logical enumeration of Reservations for one account."""

    account_id: str
    reservations: tuple[Reservation, ...]
    read_at: datetime
    completeness: Completeness

    def __post_init__(self) -> None:
        _nonempty(self.account_id, "account_id")
        _aware(self.read_at, "read_at")
        if not isinstance(self.completeness, Completeness):
            raise ReservationContractError("completeness must be Completeness")
        reservations = tuple(self.reservations)
        if not all(isinstance(item, Reservation) for item in reservations):
            raise ReservationContractError(
                "reservations must contain only Reservation values"
            )
        if any(item.account_id != self.account_id for item in reservations):
            raise ReservationContractError("INCONSISTENT READ SET")
        reservation_ids = [item.reservation_id for item in reservations]
        if len(reservation_ids) != len(set(reservation_ids)):
            raise ReservationContractError(
                "reservations must not contain duplicate reservation_id values"
            )
        object.__setattr__(self, "reservations", reservations)

    @property
    def reservation_count(self) -> int:
        return len(self.reservations)

    @property
    def relevant_reservations(self) -> tuple[Reservation, ...]:
        return tuple(
            item for item in self.reservations if item.state in NON_TERMINAL_STATES
        )

    @property
    def relevant_reservation_count(self) -> int:
        return len(self.relevant_reservations)


class SQLiteReservationStore:
    """Durable Reservation store using tables separate from the idempotency ledger."""

    _NON_TERMINAL_SQL = tuple(
        state.value
        for state in (
            ReservationState.ACTIVE,
            ReservationState.PARTIALLY_CONSUMED,
            ReservationState.UNKNOWN,
        )
    )

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                asset TEXT NOT NULL,
                reserved_amount TEXT NOT NULL,
                consumed_amount TEXT NOT NULL,
                remaining_amount TEXT NOT NULL,
                state TEXT NOT NULL,
                proposal_id TEXT NOT NULL,
                risk_decision_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                client_order_id TEXT,
                exchange_order_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reservation_transitions (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                reservation_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                evidence_kind TEXT NOT NULL,
                evidence_reference_id TEXT NOT NULL,
                consumed_delta TEXT NOT NULL,
                FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id)
            )
            """
        )
        self._ensure_non_terminal_proposal_index()
        self._connection.commit()

    def _ensure_non_terminal_proposal_index(self) -> None:
        if sqlite3.sqlite_version_info < (3, 8, 0):
            raise ReservationSchemaConflict(
                "SQLite >= 3.8.0 is required for the non-terminal proposal partial index"
            )

        duplicate = self._connection.execute(
            """
            SELECT proposal_id, COUNT(*)
            FROM reservations
            WHERE state IN (?, ?, ?)
            GROUP BY proposal_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """,
            self._NON_TERMINAL_SQL,
        ).fetchone()
        if duplicate is not None:
            raise ReservationSchemaConflict(
                "existing non-terminal Reservation multiplicity prevents unique index creation"
            )

        state_literals = ", ".join(repr(item) for item in self._NON_TERMINAL_SQL)
        self._connection.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS
                ux_reservations_non_terminal_proposal
            ON reservations(proposal_id)
            WHERE state IN ({state_literals})
            """
        )

    def create(
        self,
        reservation: Reservation,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> Reservation:
        if not isinstance(reservation, Reservation):
            raise TypeError("reservation must be Reservation")
        if reservation.state is not ReservationState.ACTIVE:
            raise ReservationContractError(
                "newly created Reservation must start ACTIVE"
            )
        if evidence.occurred_at != reservation.created_at:
            raise ReservationContractError(
                "creation evidence timestamp must equal created_at"
            )

        existing = self._connection.execute(
            """
            SELECT reservation_id
            FROM reservations
            WHERE proposal_id = ?
              AND state IN (?, ?, ?)
            LIMIT 1
            """,
            (reservation.proposal_id, *self._NON_TERMINAL_SQL),
        ).fetchone()
        if existing is not None:
            raise ReservationConflict(
                "Proposal already has a non-terminal Reservation"
            )

        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO reservations(
                        reservation_id, account_id, resource_kind, asset,
                        reserved_amount, consumed_amount, remaining_amount,
                        state, proposal_id, risk_decision_id, correlation_id,
                        client_order_id, exchange_order_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reservation.reservation_id,
                        reservation.account_id,
                        reservation.resource_kind.value,
                        reservation.asset,
                        str(reservation.reserved_amount),
                        str(reservation.consumed_amount),
                        str(reservation.remaining_amount),
                        reservation.state.value,
                        reservation.proposal_id,
                        reservation.risk_decision_id,
                        reservation.correlation_id,
                        reservation.client_order_id,
                        reservation.exchange_order_id,
                        reservation.created_at.isoformat(),
                        reservation.updated_at.isoformat(),
                    ),
                )
                self._insert_transition(
                    reservation=reservation,
                    transition=ReservationTransition(
                        reservation_id=reservation.reservation_id,
                        from_state=None,
                        to_state=ReservationState.ACTIVE,
                        occurred_at=evidence.occurred_at,
                        evidence_kind=evidence.kind,
                        evidence_reference_id=evidence.reference_id,
                        consumed_delta=Decimal("0"),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ReservationContractError("reservation_id already exists") from exc
        return reservation


    def admit(
        self,
        *,
        proposal: TradeProposal,
        risk_decision: RiskDecision,
        account_id: str,
        resource_kind: ReservationResourceKind,
        asset: str,
        reserved_amount: Decimal,
        canonical_account_state: CanonicalAccountState,
        created_at: datetime,
        reservation_id: str | None = None,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
        evidence: ReservationTransitionEvidence | None = None,
    ) -> Reservation:
        """Atomically validate capacity and persist a Reservation plus creation transition."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_sqlite_busy(exc):
                raise ReservationAdmissionBusy(
                    "SQLite admission write lock is busy"
                ) from exc
            raise

        try:
            if not isinstance(canonical_account_state, CanonicalAccountState):
                raise TypeError("canonical_account_state must be CanonicalAccountState")
            _nonempty(account_id, "account_id")
            if not isinstance(resource_kind, ReservationResourceKind):
                raise TypeError("resource_kind must be ReservationResourceKind")
            _nonempty(asset, "asset")
            _aware(created_at, "created_at")
            _decimal(reserved_amount, "reserved_amount")
            if reserved_amount <= 0:
                raise ReservationAdmissionRejected(
                    "reserved_amount must be greater than zero for admission"
                )
            if canonical_account_state.account_id != account_id:
                raise ReservationAdmissionRejected(
                    "canonical account_id must match admission account_id"
                )
            if canonical_account_state.completeness is not Completeness.COMPLETE:
                raise ReservationAdmissionRejected(
                    "CanonicalAccountState completeness must be COMPLETE"
                )

            existing = self._connection.execute(
                """
                SELECT reservation_id
                FROM reservations
                WHERE proposal_id = ?
                  AND state IN (?, ?, ?)
                LIMIT 1
                """,
                (proposal.proposal_id, *self._NON_TERMINAL_SQL),
            ).fetchone()
            if existing is not None:
                raise ReservationConflict(
                    "Proposal already has a non-terminal Reservation"
                )

            reservation_read_set = self.read_set_for_account(account_id)

            from .effective_capacity import (
                EffectiveCapacityStatus,
                calculate_effective_capacity,
            )

            effective_capacity = calculate_effective_capacity(
                canonical_account_state,
                reservation_read_set,
                resource_kind=resource_kind,
                asset=asset,
            )
            if effective_capacity.completeness is not Completeness.COMPLETE:
                raise ReservationAdmissionRejected(
                    "Reservation effective-capacity inputs are not COMPLETE"
                )
            if effective_capacity.status is EffectiveCapacityStatus.OVERCOMMITTED:
                raise ReservationAdmissionRejected(
                    "effective capacity is already OVERCOMMITTED"
                )
            if reserved_amount > effective_capacity.effective_available:
                raise ReservationAdmissionRejected(
                    "reserved_amount exceeds effective available capacity"
                )

            reservation = Reservation.from_trade_proposal_and_risk_decision(
                proposal=proposal,
                risk_decision=risk_decision,
                account_id=account_id,
                resource_kind=resource_kind,
                asset=asset,
                reserved_amount=reserved_amount,
                created_at=created_at,
                reservation_id=reservation_id,
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
            )
            transition_evidence = (
                evidence
                if evidence is not None
                else ReservationTransitionEvidence(
                    kind="RESERVATION_CREATED",
                    reference_id=proposal.proposal_id,
                    occurred_at=created_at,
                )
            )
            if not isinstance(transition_evidence, ReservationTransitionEvidence):
                raise TypeError("evidence must be ReservationTransitionEvidence")
            if transition_evidence.occurred_at != created_at:
                raise ReservationContractError(
                    "creation evidence timestamp must equal created_at"
                )

            self._insert_reservation_and_transition(
                reservation=reservation,
                evidence=transition_evidence,
            )
            self._connection.commit()
            return reservation
        except sqlite3.OperationalError as exc:
            self._connection.rollback()
            if _is_sqlite_busy(exc):
                raise ReservationAdmissionBusy(
                    "SQLite admission write lock is busy"
                ) from exc
            raise
        except Exception:
            self._connection.rollback()
            raise

    def create_from_trade_proposal_and_risk_decision(
        self,
        *,
        proposal: TradeProposal,
        risk_decision: RiskDecision,
        account_id: str,
        resource_kind: ReservationResourceKind,
        asset: str,
        reserved_amount: Decimal,
        created_at: datetime,
        reservation_id: str | None = None,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
        evidence: ReservationTransitionEvidence | None = None,
    ) -> Reservation:
        reservation = Reservation.from_trade_proposal_and_risk_decision(
            proposal=proposal,
            risk_decision=risk_decision,
            account_id=account_id,
            resource_kind=resource_kind,
            asset=asset,
            reserved_amount=reserved_amount,
            created_at=created_at,
            reservation_id=reservation_id,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
        )
        transition_evidence = (
            evidence
            if evidence is not None
            else ReservationTransitionEvidence(
                kind="RESERVATION_CREATED",
                reference_id=proposal.proposal_id,
                occurred_at=created_at,
            )
        )
        return self.create(reservation, evidence=transition_evidence)

    def get(self, reservation_id: str) -> Reservation | None:
        _nonempty(reservation_id, "reservation_id")
        row = self._connection.execute(
            "SELECT * FROM reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def list_for_proposal(self, proposal_id: str) -> tuple[Reservation, ...]:
        _nonempty(proposal_id, "proposal_id")
        rows = self._connection.execute(
            """
            SELECT *
            FROM reservations
            WHERE proposal_id=?
            ORDER BY created_at ASC, reservation_id ASC
            """,
            (proposal_id,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)


    def read_set_for_account(self, account_id: str) -> ReservationReadSet:
        _nonempty(account_id, "account_id")
        rows = self._connection.execute(
            """
            SELECT *
            FROM reservations
            WHERE account_id=?
            ORDER BY created_at ASC, reservation_id ASC
            """,
            (account_id,),
        ).fetchall()

        reservations: list[Reservation] = []
        for row in rows:
            reservation = self._from_row(row)
            if reservation.account_id != account_id:
                raise ReservationContractError("INCONSISTENT READ SET")
            reservations.append(reservation)

        return ReservationReadSet(
            account_id=account_id,
            reservations=tuple(reservations),
            read_at=datetime.now(timezone.utc),
            completeness=Completeness.COMPLETE,
        )

    def transitions(self, reservation_id: str) -> tuple[ReservationTransition, ...]:
        _nonempty(reservation_id, "reservation_id")
        rows = self._connection.execute(
            """
            SELECT reservation_id, from_state, to_state, occurred_at,
                   evidence_kind, evidence_reference_id, consumed_delta
            FROM reservation_transitions
            WHERE reservation_id=?
            ORDER BY transition_id ASC
            """,
            (reservation_id,),
        ).fetchall()
        return tuple(
            ReservationTransition(
                reservation_id=row[0],
                from_state=(
                    None if row[1] is None else ReservationState(row[1])
                ),
                to_state=ReservationState(row[2]),
                occurred_at=datetime.fromisoformat(row[3]),
                evidence_kind=row[4],
                evidence_reference_id=row[5],
                consumed_delta=Decimal(row[6]),
            )
            for row in rows
        )

    def consume(
        self,
        reservation_id: str,
        amount: Decimal,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> Reservation:
        return self._apply_transition(
            reservation_id,
            lambda reservation: reservation.consume(amount, evidence=evidence),
        )

    def release(
        self,
        reservation_id: str,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> Reservation:
        return self._apply_transition(
            reservation_id,
            lambda reservation: reservation.release(evidence=evidence),
        )

    def mark_unknown(
        self,
        reservation_id: str,
        *,
        evidence: ReservationTransitionEvidence,
    ) -> Reservation:
        return self._apply_transition(
            reservation_id,
            lambda reservation: reservation.mark_unknown(evidence=evidence),
        )

    def _apply_transition(self, reservation_id: str, operation):
        reservation = self.get(reservation_id)
        if reservation is None:
            raise KeyError(f"UNKNOWN_RESERVATION_ID:{reservation_id}")

        updated, transition = operation(reservation)
        with self._connection:
            self._connection.execute(
                """
                UPDATE reservations
                SET consumed_amount=?,
                    remaining_amount=?,
                    state=?,
                    updated_at=?
                WHERE reservation_id=?
                """,
                (
                    str(updated.consumed_amount),
                    str(updated.remaining_amount),
                    updated.state.value,
                    updated.updated_at.isoformat(),
                    updated.reservation_id,
                ),
            )
            self._insert_transition(reservation=updated, transition=transition)
        return updated


    def _insert_reservation_and_transition(
        self,
        *,
        reservation: Reservation,
        evidence: ReservationTransitionEvidence,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO reservations(
                reservation_id, account_id, resource_kind, asset,
                reserved_amount, consumed_amount, remaining_amount,
                state, proposal_id, risk_decision_id, correlation_id,
                client_order_id, exchange_order_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation.reservation_id,
                reservation.account_id,
                reservation.resource_kind.value,
                reservation.asset,
                str(reservation.reserved_amount),
                str(reservation.consumed_amount),
                str(reservation.remaining_amount),
                reservation.state.value,
                reservation.proposal_id,
                reservation.risk_decision_id,
                reservation.correlation_id,
                reservation.client_order_id,
                reservation.exchange_order_id,
                reservation.created_at.isoformat(),
                reservation.updated_at.isoformat(),
            ),
        )
        self._insert_transition(
            reservation=reservation,
            transition=ReservationTransition(
                reservation_id=reservation.reservation_id,
                from_state=None,
                to_state=ReservationState.ACTIVE,
                occurred_at=evidence.occurred_at,
                evidence_kind=evidence.kind,
                evidence_reference_id=evidence.reference_id,
                consumed_delta=Decimal("0"),
            ),
        )

    def _insert_transition(
        self,
        *,
        reservation: Reservation,
        transition: ReservationTransition,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO reservation_transitions(
                reservation_id, from_state, to_state, occurred_at,
                evidence_kind, evidence_reference_id, consumed_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation.reservation_id,
                None if transition.from_state is None else transition.from_state.value,
                transition.to_state.value,
                transition.occurred_at.isoformat(),
                transition.evidence_kind,
                transition.evidence_reference_id,
                str(transition.consumed_delta),
            ),
        )

    @staticmethod
    def _from_row(row) -> Reservation:
        return Reservation(
            reservation_id=row[0],
            account_id=row[1],
            resource_kind=ReservationResourceKind(row[2]),
            asset=row[3],
            reserved_amount=Decimal(row[4]),
            consumed_amount=Decimal(row[5]),
            remaining_amount=Decimal(row[6]),
            state=ReservationState(row[7]),
            proposal_id=row[8],
            risk_decision_id=row[9],
            correlation_id=row[10],
            client_order_id=row[11],
            exchange_order_id=row[12],
            created_at=datetime.fromisoformat(row[13]),
            updated_at=datetime.fromisoformat(row[14]),
        )

    def close(self) -> None:
        self._connection.close()


__all__ = [
    "InvalidReservationTransition",
    "ReservationAdmissionBusy",
    "ReservationAdmissionError",
    "ReservationAdmissionRejected",
    "NON_TERMINAL_STATES",
    "Reservation",
    "ReservationConflict",
    "ReservationContractError",
    "ReservationReadSet",
    "ReservationResourceKind",
    "ReservationSchemaConflict",
    "ReservationState",
    "ReservationTransition",
    "ReservationTransitionEvidence",
    "SQLiteReservationStore",
    "TERMINAL_STATES",
]