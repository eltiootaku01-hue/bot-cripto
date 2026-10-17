"""Read-only economic replay of a persisted SQLite PAPER account.

This validates one PAPER account's own fill history and balance projection. It
does not reconcile reservations, execution bindings, or the separate idempotency
ledger; those remain a distinct B1-R boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Any, Mapping, Sequence


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


def replay_paper_economics(snapshot: Mapping[str, Any]) -> PaperEconomicReplayResult:
    """Verify quote evidence, fill arithmetic, chronological balances and final snapshot."""
    if not isinstance(snapshot, Mapping) or snapshot.get("mode") != "PAPER":
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, 0, {}, ("PAPER_SNAPSHOT_REQUIRED",))
    raw_fills = snapshot.get("fills")
    if not isinstance(raw_fills, Sequence) or isinstance(raw_fills, (str, bytes)):
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, 0, {}, ("PAPER_FILL_HISTORY_UNKNOWN",))
    fills = tuple(raw_fills)
    if snapshot.get("completeness") != "COMPLETE":
        return PaperEconomicReplayResult(PaperEconomicReplayStatus.UNKNOWN, len(fills), {}, ("PAPER_SNAPSHOT_NOT_COMPLETE",))
    if (isinstance(snapshot.get("state_version"), bool) or not isinstance(snapshot.get("state_version"), int)
            or snapshot.get("state_version") != len(fills)
            or snapshot.get("fill_count") != len(fills)):
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

    if fills != tuple(sorted(fills, key=lambda f: (f.get("filled_at", ""), f.get("client_order_id", "")) if isinstance(f, Mapping) else ("", ""))):
        return _result(PaperEconomicReplayStatus.MISMATCH, len(fills), replayed, ("FILL_ORDER_NOT_DETERMINISTIC",))

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


__all__ = ["PaperEconomicReplayResult", "PaperEconomicReplayStatus", "replay_paper_economics"]
