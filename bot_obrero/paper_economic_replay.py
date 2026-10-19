"""Read-only economic replay of a persisted SQLite PAPER account.

This validates one PAPER account's own fill history and balance projection. It
does not reconcile reservations, execution bindings, or the separate idempotency
ledger; those remain a distinct B1-R boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, Sequence

from .paper_economic_contract import (
    PAPER_DECIMAL_PRECISION,
    PAPER_DECIMAL_ROUNDING,
    PAPER_ECONOMIC_CONTRACT_VERSION,
    paper_decimal_context,
)
from .paper_quote_evidence import (
    PaperQuoteEvidenceError,
    canonical_json,
    validate_paper_quote_evidence,
)


class PaperEconomicReplayStatus(str, Enum):
    REPLAYED = "REPLAYED"
    INCOMPLETE = "INCOMPLETE"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PaperEconomicReplayResult:
    status: PaperEconomicReplayStatus
    fill_count: int
    final_balances: Mapping[str, str]
    discrepancies: tuple[str, ...]


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in ("", "-0") else rendered


def _read_decimal(raw: Any, label: str) -> Decimal:
    if type(raw) is not str or not raw:
        raise ValueError(f"{label}: decimal string required")
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid decimal") from exc
    if not value.is_finite() or _canonical_decimal(value) != raw:
        raise ValueError(f"{label}: decimal is non-finite or not canonical")
    return value


def _read_timestamp(raw: Any, label: str) -> datetime:
    if type(raw) is not str or not raw:
        raise ValueError(f"{label}: timestamp string required")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label}: timestamp must be timezone-aware")
    return value


def _result(status: PaperEconomicReplayStatus, fills: int, balances: Mapping[str, Decimal], errors: Sequence[str]):
    return PaperEconomicReplayResult(
        status=status,
        fill_count=fills,
        final_balances={asset: _canonical_decimal(value) for asset, value in sorted(balances.items())},
        discrepancies=tuple(errors),
    )


def _replay_paper_economics(snapshot: Mapping[str, Any]) -> PaperEconomicReplayResult:
    """Verify quote evidence, fill arithmetic, chronological balances and final snapshot."""
    if not isinstance(snapshot, Mapping) or snapshot.get("mode") != "PAPER":
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, 0, {}, ("PAPER_SNAPSHOT_REQUIRED",))
    raw_fills = snapshot.get("fills")
    if not isinstance(raw_fills, Sequence) or isinstance(raw_fills, (str, bytes)):
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, 0, {}, ("PAPER_FILL_HISTORY_UNKNOWN",))
    fills = tuple(raw_fills)
    if snapshot.get("completeness") != "COMPLETE":
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, len(fills), {}, ("PAPER_SNAPSHOT_NOT_COMPLETE",))
    reported_fill_count = snapshot.get("fill_count")
    if (
        isinstance(snapshot.get("state_version"), bool)
        or not isinstance(snapshot.get("state_version"), int)
        or snapshot.get("state_version") != len(fills)
        or isinstance(reported_fill_count, bool)
        or not isinstance(reported_fill_count, int)
        or reported_fill_count != len(fills)
    ):
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, len(fills), {}, ("PAPER_VERSION_FILL_COUNT_MISMATCH",))

    initial = snapshot.get("asset_universe")
    reported_balances = snapshot.get("balances")
    if not isinstance(initial, Mapping) or not initial or not isinstance(reported_balances, Mapping):
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, len(fills), {}, ("PAPER_BALANCE_SNAPSHOT_INVALID",))
    if set(initial) != set(reported_balances):
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.MISMATCH, len(fills), {}, ("PAPER_ASSET_UNIVERSE_BALANCE_KEYS_DIFFER",))

    try:
        replayed = {asset: _read_decimal(value, f"initial[{asset}]") for asset, value in initial.items()}
        final_reported = {asset: _read_decimal(value, f"balance[{asset}]") for asset, value in reported_balances.items()}
    except ValueError as exc:
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, len(fills), {}, (str(exc),))

    if any(value < 0 for value in replayed.values()):
        return _result(PaperEconomicReplayStatus.MISMATCH, len(fills), replayed, ("NEGATIVE_INITIAL_BALANCE",))

    previous_fill_time = None
    for index, fill in enumerate(fills, start=1):
        if not isinstance(fill, Mapping):
            return _result(
                PaperEconomicReplayStatus.MISMATCH,
                len(fills),
                replayed,
                (f"FILL_{index}_NOT_MAPPING",),
            )
        contract_version = fill.get("economic_contract_version")
        if type(contract_version) is not int:
            return _result(PaperEconomicReplayStatus.UNKNOWN, len(fills), replayed,
                           (f"FILL_{index}_ECONOMIC_CONTRACT_VERSION_INVALID",))
        if contract_version == 0:
            return _result(PaperEconomicReplayStatus.INCOMPLETE, len(fills), replayed,
                           (f"FILL_{index}_LEGACY_ECONOMIC_CONTRACT_UNVERSIONED",))
        if contract_version != PAPER_ECONOMIC_CONTRACT_VERSION:
            return _result(PaperEconomicReplayStatus.UNKNOWN, len(fills), replayed,
                           (f"FILL_{index}_ECONOMIC_CONTRACT_VERSION_UNSUPPORTED",))
        sequence = fill.get("fill_sequence")
        if type(sequence) is not int or sequence != index:
            return _result(PaperEconomicReplayStatus.MISMATCH, len(fills), replayed,
                           (f"FILL_{index}_SEQUENCE_INVALID",))
        precision = fill.get("decimal_precision")
        if type(precision) is not int or precision != PAPER_DECIMAL_PRECISION:
            return _result(PaperEconomicReplayStatus.UNKNOWN, len(fills), replayed,
                           (f"FILL_{index}_DECIMAL_PRECISION_UNSUPPORTED",))
        if fill.get("decimal_rounding") != PAPER_DECIMAL_ROUNDING:
            return _result(PaperEconomicReplayStatus.UNKNOWN, len(fills), replayed,
                           (f"FILL_{index}_DECIMAL_ROUNDING_UNSUPPORTED",))
        filled_at_raw = fill.get("filled_at")
        client_order_id = fill.get("client_order_id")
        if type(filled_at_raw) is not str or not filled_at_raw or type(client_order_id) is not str or not client_order_id:
            return _result(
                PaperEconomicReplayStatus.UNKNOWN,
                len(fills),
                replayed,
                (f"FILL_{index}_ORDER_KEY_INVALID",),
            )
        try:
            filled_at = _read_timestamp(filled_at_raw, f"fill[{index}].filled_at")
        except ValueError:
            return _result(
                PaperEconomicReplayStatus.UNKNOWN,
                len(fills),
                replayed,
                (f"FILL_{index}_ORDER_TIMESTAMP_INVALID",),
            )
        instant = filled_at.astimezone(timezone.utc)
        if previous_fill_time is not None and instant < previous_fill_time:
            return _result(PaperEconomicReplayStatus.MISMATCH, len(fills), replayed,
                           ("FILL_TIMESTAMP_REGRESSION",))
        previous_fill_time = instant

    ids: set[str] = set()
    errors: list[str] = []
    for index, fill in enumerate(fills, start=1):
        if not isinstance(fill, Mapping):
            errors.append(f"FILL_{index}_NOT_MAPPING")
            continue
        label = f"fill[{index}]"
        required_evidence = (
            "fee_asset", "fee_rate", "slippage_bps", "quote_bid", "quote_ask",
            "quote_observed_at", "quote_available_at", "quote_max_age_microseconds", "quote_source",
            "quote_observation_id", "quote_observation_type", "quote_evidence_hash",
            "evaluation_context_id",
        )
        if any(fill.get(key) in (None, "") for key in required_evidence):
            return _result(PaperEconomicReplayStatus.INCOMPLETE, len(fills), replayed,
                           (f"{label}:QUOTE_OR_COST_EVIDENCE_MISSING",))

        client_order_id = fill.get("client_order_id")
        if type(client_order_id) is not str or not client_order_id or client_order_id in ids:
            errors.append(f"{label}:DUPLICATE_OR_MISSING_CLIENT_ORDER_ID")
            continue
        ids.add(client_order_id)
        if fill.get("mode") != "PAPER" or fill.get("account_id") != snapshot.get("account_id"):
            errors.append(f"{label}:MODE_OR_ACCOUNT_MISMATCH")
            continue

        base_asset, quote_asset = fill.get("base_asset"), fill.get("quote_asset")
        side, symbol = fill.get("side"), fill.get("symbol")
        if (type(base_asset) is not str or type(quote_asset) is not str or base_asset == quote_asset
                or symbol != f"{base_asset}/{quote_asset}" or side not in ("BUY", "SELL")
                or base_asset not in replayed or quote_asset not in replayed):
            errors.append(f"{label}:INSTRUMENT_OR_SIDE_INVALID")
            continue
        if fill.get("fee_asset") != quote_asset:
            errors.append(f"{label}:UNSUPPORTED_FEE_ASSET")
            continue

        try:
            quantity = _read_decimal(fill.get("quantity"), f"{label}.quantity")
            price = _read_decimal(fill.get("price"), f"{label}.price")
            fee = _read_decimal(fill.get("fee"), f"{label}.fee")
            fee_rate = _read_decimal(fill.get("fee_rate"), f"{label}.fee_rate")
            slippage_bps = _read_decimal(fill.get("slippage_bps"), f"{label}.slippage_bps")
            bid = _read_decimal(fill.get("quote_bid"), f"{label}.quote_bid")
            ask = _read_decimal(fill.get("quote_ask"), f"{label}.quote_ask")
            quote_after = _read_decimal(fill.get("quote_balance_after"), f"{label}.quote_balance_after")
            base_after = _read_decimal(fill.get("base_balance_after"), f"{label}.base_balance_after")
            observed_at = _read_timestamp(fill.get("quote_observed_at"), f"{label}.quote_observed_at")
            available_at = _read_timestamp(fill.get("quote_available_at"), f"{label}.quote_available_at")
            filled_at = _read_timestamp(fill.get("filled_at"), f"{label}.filled_at")
            max_age = fill.get("quote_max_age_microseconds")
            if type(max_age) is not str or not max_age.isdigit() or int(max_age) <= 0:
                raise ValueError(f"{label}.quote_max_age_microseconds: positive integer string required")
            max_age_delta = timedelta(microseconds=int(max_age))
        except ValueError as exc:
            errors.append(str(exc))
            continue

        intent_payload = fill.get("intent_payload")
        intent_hash = fill.get("intent_hash")
        if not isinstance(intent_payload, Mapping) or type(intent_hash) is not str:
            return _result(
                PaperEconomicReplayStatus.INCOMPLETE,
                len(fills),
                replayed,
                (f"{label}:PERSISTED_INTENT_PAYLOAD_MISSING",),
            )
        try:
            computed_intent_hash = hashlib.sha256(canonical_json(intent_payload).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            errors.append(f"{label}:PERSISTED_INTENT_PAYLOAD_INVALID")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", intent_hash) or computed_intent_hash != intent_hash:
            errors.append(f"{label}:PERSISTED_INTENT_HASH_MISMATCH")
            continue
        try:
            quote_evidence = intent_payload.get("paper_quote_evidence")
            decision_at = _read_timestamp(intent_payload.get("decision_timestamp"), f"{label}.decision_timestamp")
            validated_quote = validate_paper_quote_evidence(
                quote_evidence,
                expected_symbol=symbol,
                expected_decision_timestamp=decision_at,
                expected_context_id=fill.get("evaluation_context_id"),
                now=filled_at,
                max_age=max_age_delta,
            )
        except (KeyError, TypeError, ValueError, PaperQuoteEvidenceError) as exc:
            errors.append(f"{label}:PERSISTED_QUOTE_EVIDENCE_INVALID:{type(exc).__name__}")
            continue

        quote_payload = validated_quote.evidence
        if (
            intent_payload.get("schema") != "reservation-order-intent-v1"
            or intent_payload.get("client_order_id") != client_order_id
            or intent_payload.get("account_id") != snapshot.get("account_id")
            or intent_payload.get("symbol") != symbol
            or intent_payload.get("base_asset") != base_asset
            or intent_payload.get("quote_asset") != quote_asset
            or intent_payload.get("side") != side
            or intent_payload.get("quantity") != fill.get("quantity")
            or intent_payload.get("evaluation_context_id") != fill.get("evaluation_context_id")
            or quote_payload.get("bid") != fill.get("quote_bid")
            or quote_payload.get("ask") != fill.get("quote_ask")
            or validated_quote.observed_at != observed_at
            or validated_quote.available_at != available_at
            or quote_payload.get("source") != fill.get("quote_source")
            or quote_payload.get("observation_id") != fill.get("quote_observation_id")
            or quote_payload.get("observation_type") != fill.get("quote_observation_type")
            or quote_payload.get("evidence_hash") != fill.get("quote_evidence_hash")
        ):
            errors.append(f"{label}:FILL_DIFFERS_FROM_HASHED_INTENT_OR_QUOTE")
            continue

        if quantity <= 0 or price <= 0 or fee < 0 or fee_rate < 0 or slippage_bps < 0 or bid <= 0 or ask < bid:
            errors.append(f"{label}:NON_POSITIVE_OR_CROSSED_ECONOMICS")
            continue
        if not isinstance(fill.get("quote_source"), str) or not fill["quote_source"].startswith("binance-public:"):
            errors.append(f"{label}:QUOTE_PROVENANCE_INVALID")
            continue
        if observed_at > available_at or available_at > filled_at or filled_at - available_at > max_age_delta:
            errors.append(f"{label}:QUOTE_TIMING_INVALID_OR_STALE")
            continue
        if (
            fill.get("quote_observation_type") != "BINANCE_SPOT_BOOK_TICKER"
            or not re.fullmatch(r"[0-9a-f]{64}", str(fill.get("quote_evidence_hash", "")))
            or not fill.get("quote_observation_id")
            or not fill.get("evaluation_context_id")
        ):
            errors.append(f"{label}:QUOTE_NOT_BOUND_TO_RISK_CONTEXT")
            continue

        slip = slippage_bps / Decimal("10000")
        reference = ask if side == "BUY" else bid
        expected_price = reference * (Decimal("1") + slip if side == "BUY" else Decimal("1") - slip)
        expected_fee = quantity * expected_price * fee_rate
        if price != expected_price:
            errors.append(f"{label}:FILL_PRICE_DOES_NOT_MATCH_QUOTE_AND_SLIPPAGE")
            continue
        if fee != expected_fee:
            errors.append(f"{label}:FEE_DOES_NOT_MATCH_RECORDED_RATE")
            continue

        gross = quantity * price
        if side == "BUY":
            debit = gross + fee
            if debit > replayed[quote_asset]:
                errors.append(f"{label}:QUOTE_BALANCE_WOULD_BE_NEGATIVE")
                continue
            replayed[quote_asset] -= debit
            replayed[base_asset] += quantity
        else:
            if quantity > replayed[base_asset]:
                errors.append(f"{label}:BASE_BALANCE_WOULD_BE_NEGATIVE")
                continue
            proceeds = gross - fee
            if proceeds < 0:
                errors.append(f"{label}:SELL_FEE_EXCEEDS_PROCEEDS")
                continue
            replayed[quote_asset] += proceeds
            replayed[base_asset] -= quantity

        if replayed[quote_asset] != quote_after or replayed[base_asset] != base_after:
            errors.append(f"{label}:PERSISTED_POST_FILL_BALANCES_DIFFER_FROM_REPLAY")

    if errors:
        return _result(PaperEconomicReplayStatus.MISMATCH, len(fills), replayed, errors)
    if replayed != final_reported:
        return _result(PaperEconomicReplayStatus.MISMATCH, len(fills), replayed, ("FINAL_BALANCES_DIFFER_FROM_REPLAY",))
    return _result(PaperEconomicReplayStatus.REPLAYED, len(fills), replayed, ())


def replay_paper_economics(snapshot: Mapping[str, Any]) -> PaperEconomicReplayResult:
    """Replay a PAPER account using the immutable Decimal semantics of contract v1."""
    with paper_decimal_context():
        return _replay_paper_economics(snapshot)


__all__ = ["PaperEconomicReplayResult", "PaperEconomicReplayStatus", "replay_paper_economics"]
