"""Global consistency checks for the local PAPER account and its execution projections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from .paper_adapter import PaperExecutionAdapter
from .paper_economic_replay import PaperEconomicReplayStatus, replay_paper_economics
from .paper_quote_evidence import PaperQuoteEvidenceError, validate_paper_quote_evidence
from .persistent_ledger import SQLiteIdempotencyLedger
from .reservation import NON_TERMINAL_STATES, ReservationState, SQLiteReservationStore
from .reservation_execution_bridge import ReservationExecutionBridge


class PaperRuntimeReconciliationStatus(str, Enum):
    RECONCILED = "RECONCILED"
    INCOMPLETE = "INCOMPLETE"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PaperRuntimeReconciliationResult:
    status: PaperRuntimeReconciliationStatus
    account_id: str | None
    fill_count: int
    reconciled_fill_count: int
    discrepancies: tuple[str, ...]


def _timestamp(value: Any) -> datetime:
    if type(value) is not str or not value:
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _actual_reservation_consumption(fill: Mapping[str, Any]) -> Decimal:
    quantity = Decimal(fill["quantity"])
    price = Decimal(fill["price"])
    fee = Decimal(fill["fee"])
    if not all(item.is_finite() for item in (quantity, price, fee)):
        raise ValueError("non-finite fill economics")
    if quantity <= 0 or price <= 0 or fee < 0:
        raise ValueError("invalid fill economics")
    if fill.get("side") == "BUY":
        return quantity * price + fee
    if fill.get("side") == "SELL":
        return quantity
    raise ValueError("unknown fill side")


def _result(status, account_id, fill_count, reconciled_count, discrepancies):
    return PaperRuntimeReconciliationResult(
        status=status,
        account_id=account_id,
        fill_count=fill_count,
        reconciled_fill_count=reconciled_count,
        discrepancies=tuple(discrepancies),
    )


def reconcile_paper_runtime(
    *,
    adapter: PaperExecutionAdapter,
    store: SQLiteReservationStore,
    bridge: ReservationExecutionBridge,
    ledger: SQLiteIdempotencyLedger,
    occurred_at: datetime | None = None,
    apply: bool = True,
) -> PaperRuntimeReconciliationResult:
    """Reconcile fills, account balances, reservation, execution binding and idempotency ledger.

    The complete observable state is validated before any mutation. Settling a fill updates
    its reservation and execution binding in their shared database transaction; the separate
    idempotency ledger is then repaired idempotently. Any partial repair returns UNKNOWN and
    can be retried safely, but never authorizes an ambiguous submission.
    """
    if not (
        isinstance(adapter, PaperExecutionAdapter)
        and isinstance(store, SQLiteReservationStore)
        and isinstance(bridge, ReservationExecutionBridge)
        and isinstance(ledger, SQLiteIdempotencyLedger)
    ):
        return _result(PaperRuntimeReconciliationStatus.UNKNOWN, None, 0, 0, ("PAPER_COMPONENT_INVALID",))
    try:
        snapshot = adapter.snapshot()
    except Exception as exc:
        return _result(PaperRuntimeReconciliationStatus.UNKNOWN, None, 0, 0,
                       (f"PAPER_SNAPSHOT_FAILED:{type(exc).__name__}",))
    account_id = snapshot.get("account_id")
    fills = snapshot.get("fills")
    count = snapshot.get("fill_count", 0)
    if (
        snapshot.get("mode") != "PAPER"
        or snapshot.get("completeness") != "COMPLETE"
        or type(account_id) is not str or not account_id.strip()
        or not isinstance(fills, (tuple, list))
        or isinstance(count, bool) or type(count) is not int or count != len(fills)
    ):
        return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, 0, 0,
                       ("PAPER_ACCOUNT_SNAPSHOT_INCOMPLETE",))
    replay = replay_paper_economics(snapshot)
    if replay.status is not PaperEconomicReplayStatus.REPLAYED:
        mapped = {
            PaperEconomicReplayStatus.INCOMPLETE: PaperRuntimeReconciliationStatus.INCOMPLETE,
            PaperEconomicReplayStatus.MISMATCH: PaperRuntimeReconciliationStatus.MISMATCH,
            PaperEconomicReplayStatus.UNKNOWN: PaperRuntimeReconciliationStatus.UNKNOWN,
        }.get(replay.status, PaperRuntimeReconciliationStatus.UNKNOWN)
        return _result(mapped, account_id, count, 0, replay.discrepancies)

    try:
        read_set = store.read_set_for_account(account_id)
        if read_set.completeness.value != "COMPLETE":
            return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, count, 0,
                           ("PAPER_RESERVATION_READ_SET_INCOMPLETE",))
        reservations = tuple(read_set.reservations)
        bindings = {item.reservation_id: bridge.get_binding(item.reservation_id) for item in reservations}
        records = ledger.all_records()
    except Exception as exc:
        return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, count, 0,
                       (f"PAPER_CROSS_LEDGER_READ_FAILED:{type(exc).__name__}",))

    fill_by_client = {}
    for fill in fills:
        client_id = fill.get("client_order_id") if isinstance(fill, Mapping) else None
        if type(client_id) is not str or not client_id or client_id in fill_by_client:
            return _result(PaperRuntimeReconciliationStatus.MISMATCH, account_id, count, 0,
                           ("PAPER_FILL_IDENTITY_DUPLICATE_OR_MISSING",))
        fill_by_client[client_id] = fill
    binding_by_client = {binding.client_order_id: binding for binding in bindings.values() if binding is not None}
    record_by_client = {record.client_order_id: record for record in records}
    mismatches = []
    unknowns = []
    plans = []

    for record in records:
        binding = binding_by_client.get(record.client_order_id)
        if binding is None:
            mismatches.append(f"LEDGER_WITHOUT_BINDING:{record.client_order_id}")
        elif record.intent_hash != binding.intent_hash:
            mismatches.append(f"LEDGER_INTENT_HASH_MISMATCH:{record.client_order_id}")

    for reservation in reservations:
        binding = bindings.get(reservation.reservation_id)
        fill = None if reservation.client_order_id is None else fill_by_client.get(reservation.client_order_id)
        if fill is None:
            if binding is None:
                if reservation.client_order_id is not None:
                    mismatches.append(f"RESERVATION_CLIENT_ORDER_WITHOUT_BINDING:{reservation.reservation_id}")
                elif reservation.state in NON_TERMINAL_STATES:
                    unknowns.append(f"UNBOUND_NONTERMINAL_RESERVATION:{reservation.reservation_id}")
            elif binding.state == "PREPARED" and reservation.state is ReservationState.ACTIVE:
                if binding.client_order_id in record_by_client:
                    mismatches.append(f"PREPARED_BINDING_HAS_LEDGER_RECORD:{binding.client_order_id}")
                else:
                    unknowns.append(f"PREPARED_EXECUTION_WITHOUT_PAPER_FILL:{binding.client_order_id}")
            elif binding.state in {"SUBMISSION_STARTED", "UNKNOWN"} or reservation.state is ReservationState.UNKNOWN:
                unknowns.append(f"AMBIGUOUS_EXECUTION_WITHOUT_PAPER_FILL:{binding.client_order_id}")
            elif binding.state == "SUBMITTED":
                mismatches.append(f"SUBMITTED_BINDING_WITHOUT_PAPER_FILL:{binding.client_order_id}")
            else:
                unknowns.append(f"RESERVATION_NOT_RECONCILED:{reservation.reservation_id}:{binding.state}")
            continue

        if binding is None or binding.state not in {"SUBMITTED", "SUBMISSION_STARTED", "UNKNOWN"}:
            mismatches.append(f"FILL_BINDING_STATE_INVALID:{fill['client_order_id']}")
            continue
        if (
            fill.get("mode") != "PAPER"
            or fill.get("account_id") != account_id
            or fill.get("reservation_id") != reservation.reservation_id
            or fill.get("proposal_id") != reservation.proposal_id
            or fill.get("client_order_id") != binding.client_order_id
            or fill.get("intent_hash") != binding.intent_hash
            or reservation.client_order_id != binding.client_order_id
        ):
            mismatches.append(f"FILL_CANONICAL_IDENTITY_MISMATCH:{fill['client_order_id']}")
            continue

        try:
            payload = binding.payload
            filled_at = _timestamp(fill.get("filled_at"))
            age_raw = fill.get("quote_max_age_microseconds")
            if type(age_raw) is not str or not age_raw.isdigit() or int(age_raw) <= 0:
                raise ValueError("invalid quote max age")
            quote = validate_paper_quote_evidence(
                payload.get("paper_quote_evidence"),
                expected_symbol=payload["symbol"],
                expected_decision_timestamp=datetime.fromisoformat(payload["decision_timestamp"]),
                expected_context_id=payload["evaluation_context_id"],
                now=filled_at,
                max_age=timedelta(microseconds=int(age_raw)),
            )
            evidence = quote.evidence
            auth = store.get_authorization_binding(reservation.reservation_id)
            if (
                fill.get("evaluation_context_id") != evidence["evaluation_context_id"]
                or fill.get("quote_observation_id") != evidence["observation_id"]
                or fill.get("quote_observation_type") != evidence["observation_type"]
                or fill.get("quote_evidence_hash") != evidence["evidence_hash"]
                or fill.get("quote_source") != evidence["source"]
                or fill.get("quote_bid") != evidence["bid"] or fill.get("quote_ask") != evidence["ask"]
                or _timestamp(fill.get("quote_observed_at")) != quote.observed_at
                or _timestamp(fill.get("quote_available_at")) != quote.available_at
                or auth is None
                or not any(
                    item.kind == "MARKET_OBSERVATION"
                    and item.reference_id == evidence["observation_id"]
                    and item.as_of.astimezone(timezone.utc) == quote.available_at
                    for item in auth.risk_evidence
                )
            ):
                raise ValueError("quote differs from evidence authorized by risk")
            if (
                payload.get("side") != fill.get("side")
                or payload.get("quantity") != fill.get("quantity")
                or payload.get("symbol") != fill.get("symbol")
                or payload.get("base_asset") != fill.get("base_asset")
                or payload.get("quote_asset") != fill.get("quote_asset")
                or payload.get("evaluation_context_id") != fill.get("evaluation_context_id")
            ):
                raise ValueError("fill economic terms differ from intent")
            amount = _actual_reservation_consumption(fill)
            if amount > reservation.reserved_amount or reservation.consumed_amount not in {Decimal("0"), amount}:
                raise ValueError("reservation consumption differs from fill")
            if reservation.state in {ReservationState.CONSUMED, ReservationState.RELEASED}                     and reservation.consumed_amount != amount:
                raise ValueError("terminal reservation consumption differs")
        except (KeyError, TypeError, ValueError, ArithmeticError, PaperQuoteEvidenceError) as exc:
            mismatches.append(f"FILL_EVIDENCE_OR_ECONOMICS_MISMATCH:{fill['client_order_id']}:{type(exc).__name__}")
            continue

        record = record_by_client.get(binding.client_order_id)
        if record is not None and record.result == "BLOCKED":
            mismatches.append(f"FILL_CONFLICTS_WITH_BLOCKED_LEDGER:{binding.client_order_id}")
            continue
        plans.append((reservation, binding, fill, amount))

    for client_id in fill_by_client:
        if client_id not in binding_by_client:
            mismatches.append(f"ORPHAN_PAPER_FILL:{client_id}")

    if mismatches:
        return _result(PaperRuntimeReconciliationStatus.MISMATCH, account_id, count, 0, mismatches)
    if unknowns:
        return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, count, 0, unknowns)

    if not apply:
        for reservation, binding, fill, amount in plans:
            record = record_by_client.get(binding.client_order_id)
            if (
                binding.state != "SUBMITTED"
                or record is None or record.result != "SUBMITTED" or record.intent_hash != binding.intent_hash
                or reservation.consumed_amount != amount
                or reservation.state not in {ReservationState.RELEASED, ReservationState.CONSUMED}
            ):
                return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, count, 0,
                               (f"FINAL_RECONCILIATION_INCOMPLETE:{binding.client_order_id}",))
        final = adapter.snapshot()
        if final.get("state_version") != snapshot.get("state_version") or final.get("fill_count") != count:
            return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, count, 0,
                           ("PAPER_ACCOUNT_CHANGED_DURING_RECONCILIATION",))
        return _result(PaperRuntimeReconciliationStatus.RECONCILED, account_id, count, len(plans), ())

    repaired = 0
    for reservation, binding, fill, amount in plans:
        record = record_by_client.get(binding.client_order_id)
        already_settled = (
            binding.state == "SUBMITTED"
            and reservation.consumed_amount == amount
            and reservation.state in {ReservationState.RELEASED, ReservationState.CONSUMED}
            and record is not None and record.result == "SUBMITTED"
        )
        try:
            if (
                binding.state != "SUBMITTED"
                or reservation.consumed_amount != amount
                or reservation.state not in {ReservationState.RELEASED, ReservationState.CONSUMED}
            ):
                bridge.reconcile_paper_fill(
                    reservation.reservation_id,
                    fill=fill,
                    occurred_at=occurred_at or datetime.now(timezone.utc),
                )
            binding_now = bridge.get_binding(reservation.reservation_id)
            if binding_now is None:
                raise ValueError("binding missing after settlement")
            ledger.synchronize_projection(binding.client_order_id, binding_now.payload, "SUBMITTED")
            if not already_settled:
                repaired += 1
        except Exception as exc:
            return _result(PaperRuntimeReconciliationStatus.UNKNOWN, account_id, count, repaired,
                           (f"PAPER_SETTLEMENT_FAILED:{binding.client_order_id}:{type(exc).__name__}",))
    return reconcile_paper_runtime(
        adapter=adapter, store=store, bridge=bridge, ledger=ledger,
        occurred_at=occurred_at, apply=False,
    )


__all__ = [
    "PaperRuntimeReconciliationStatus",
    "PaperRuntimeReconciliationResult",
    "reconcile_paper_runtime",
]
