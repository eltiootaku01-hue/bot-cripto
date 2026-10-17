"""Canonical, immutable-by-hash public Spot quote evidence."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping

class PaperQuoteEvidenceError(ValueError):
    """Quote evidence cannot be safely bound to a PAPER decision."""

@dataclass(frozen=True)
class ValidatedPaperQuote:
    evidence: Mapping[str, Any]
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    available_at: datetime
    decision_timestamp: datetime

def canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise PaperQuoteEvidenceError("PAPER_QUOTE_CANONICAL_JSON_INVALID") from exc

def evidence_hash(evidence: Mapping[str, Any]) -> str:
    payload = dict(evidence)
    payload.pop("evidence_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

def _timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or not value:
        raise PaperQuoteEvidenceError(f"{field}: ISO-8601 timestamp required")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PaperQuoteEvidenceError(f"{field}: invalid ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise PaperQuoteEvidenceError(f"{field}: timezone-aware timestamp required")
    return result.astimezone(timezone.utc)

def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if type(value) is not str or not value:
        raise PaperQuoteEvidenceError(f"{field}: canonical decimal string required")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaperQuoteEvidenceError(f"{field}: invalid decimal") from exc
    if not result.is_finite():
        raise PaperQuoteEvidenceError(f"{field}: finite decimal required")
    rendered = format(result, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        rendered = "0"
    if rendered != value or (positive and result <= 0):
        raise PaperQuoteEvidenceError(f"{field}: non-canonical or non-positive decimal")
    return result

def validate_paper_quote_evidence(
    evidence: Any, *, expected_symbol: str,
    expected_decision_timestamp: datetime | None = None,
    expected_context_id: str | None = None,
    now: datetime | None = None, max_age: timedelta | None = None,
) -> ValidatedPaperQuote:
    """Require event time <= first availability <= decision time <= fill time."""
    if not isinstance(evidence, Mapping):
        raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_MISSING")
    required = (
        "schema", "observation_id", "observation_type", "symbol", "bid", "ask",
        "observed_at", "available_at", "decision_timestamp", "evaluation_context_id",
        "source", "source_reference", "venue", "evidence_hash",
    )
    for key in required:
        if key not in evidence:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_FIELDS_INCOMPLETE")
    for key in ("schema", "observation_id", "observation_type", "symbol", "observed_at",
                "available_at", "decision_timestamp", "evaluation_context_id", "source",
                "evidence_hash"):
        if type(evidence[key]) is not str or not evidence[key]:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_FIELDS_INCOMPLETE")
    for key in ("source_reference", "venue"):
        if type(evidence[key]) is not str:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_PROVENANCE_INVALID")
    if evidence["schema"] != "paper-quote-evidence-v1":
        raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_SCHEMA_UNSUPPORTED")
    if evidence["observation_type"] != "BINANCE_SPOT_BOOK_TICKER":
        raise PaperQuoteEvidenceError("PAPER_QUOTE_OBSERVATION_TYPE_UNSUPPORTED")
    if evidence["symbol"] != expected_symbol or not re.fullmatch(r"[A-Z0-9]+/[A-Z0-9]+", expected_symbol):
        raise PaperQuoteEvidenceError("PAPER_QUOTE_SYMBOL_MISMATCH")
    if not evidence["source"].startswith("binance-public:"):
        raise PaperQuoteEvidenceError("PAPER_QUOTE_PROVENANCE_NOT_PUBLIC_BINANCE")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence["evidence_hash"]):
        raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_HASH_INVALID")
    if evidence_hash(evidence) != evidence["evidence_hash"]:
        raise PaperQuoteEvidenceError("PAPER_QUOTE_EVIDENCE_HASH_MISMATCH")
    bid = _decimal(evidence["bid"], "bid", positive=True)
    ask = _decimal(evidence["ask"], "ask", positive=True)
    if ask < bid:
        raise PaperQuoteEvidenceError("PAPER_QUOTE_CROSSED")
    observed = _timestamp(evidence["observed_at"], "observed_at")
    available = _timestamp(evidence["available_at"], "available_at")
    decision = _timestamp(evidence["decision_timestamp"], "decision_timestamp")
    if observed > available or available > decision:
        raise PaperQuoteEvidenceError("PAPER_QUOTE_TEMPORAL_ORDER_INVALID")
    if expected_decision_timestamp is not None:
        if expected_decision_timestamp.tzinfo is None or expected_decision_timestamp.utcoffset() is None:
            raise PaperQuoteEvidenceError("EXPECTED_DECISION_TIMESTAMP_INVALID")
        if decision != expected_decision_timestamp.astimezone(timezone.utc):
            raise PaperQuoteEvidenceError("PAPER_QUOTE_DECISION_TIMESTAMP_MISMATCH")
    if expected_context_id is not None and evidence["evaluation_context_id"] != expected_context_id:
        raise PaperQuoteEvidenceError("PAPER_QUOTE_EVALUATION_CONTEXT_MISMATCH")
    if now is not None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_CLOCK_INVALID")
        now_utc = now.astimezone(timezone.utc)
        if decision > now_utc or available > now_utc:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_TEMPORAL_ORDER_INVALID")
        if max_age is None or max_age.total_seconds() <= 0:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_MAX_AGE_INVALID")
        if now_utc - available > max_age:
            raise PaperQuoteEvidenceError("PAPER_QUOTE_STALE")
    return ValidatedPaperQuote(dict(evidence), bid, ask, observed, available, decision)

__all__ = ["PaperQuoteEvidenceError", "ValidatedPaperQuote", "canonical_json",
           "evidence_hash", "validate_paper_quote_evidence"]
