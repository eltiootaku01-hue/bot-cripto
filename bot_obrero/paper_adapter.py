"""Strictly local, SQLite-backed PAPER execution adapter.

This adapter implements the existing ExchangeAdapter boundary. It has no network
client, credentials, private endpoint, or delegation path to a live adapter.
A caller must provide an explicitly timestamped public-market quote.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Callable, Mapping, Any

from .execution import ExchangeAdapter, OrderIntent

_SYMBOL_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS = Decimal("10000")


class PaperAdapterError(ValueError):
    """A PAPER-only order or local account invariant was not satisfied."""


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperAdapterError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal:
        raise PaperAdapterError(f"{field} must be Decimal")
    if not value.is_finite() or (positive and value <= _ZERO):
        raise PaperAdapterError(f"{field} must be finite" + (" and positive" if positive else ""))
    return value


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _payload_decimal(payload: Mapping[str, Any], key: str, *, optional: bool = False) -> Decimal | None:
    value = payload.get(key)
    if value is None and optional:
        return None
    if type(value) is not str:
        raise PaperAdapterError(f"canonical {key} must be a decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaperAdapterError(f"canonical {key} is invalid") from exc
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        raise PaperAdapterError(f"canonical {key} is non-finite or not normalized")
    return parsed


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise PaperAdapterError("order payload is not canonical JSON data") from exc


class PaperMarketQuote:
    """Explicit quote evidence used only to calculate a local virtual fill."""

    __slots__ = ("symbol", "bid", "ask", "observed_at", "available_at", "source")

    def __init__(self, *, symbol: str, bid: Decimal, ask: Decimal, observed_at: datetime,
                 available_at: datetime | None, source: str) -> None:
        if type(symbol) is not str or not _SYMBOL_RE.fullmatch(symbol):
            raise PaperAdapterError("quote symbol must be canonical BASE/QUOTE")
        _decimal(bid, "bid", positive=True)
        _decimal(ask, "ask", positive=True)
        if ask < bid:
            raise PaperAdapterError("crossed public quote is inadmissible")
        observed = _aware(observed_at, "observed_at")
        if available_at is None:
            raise PaperAdapterError("quote available_at is UNKNOWN; PAPER fill blocked")
        available = _aware(available_at, "available_at")
        if observed > available:
            raise PaperAdapterError("quote observed_at cannot follow available_at")
        if type(source) is not str or not source.startswith("binance-public:"):
            raise PaperAdapterError("quote provenance must identify a public Binance source")
        self.symbol, self.bid, self.ask = symbol, bid, ask
        self.observed_at, self.available_at, self.source = observed, available, source


class PaperExecutionAdapter(ExchangeAdapter):
    """SQLite-backed local fills; it cannot send or cancel an exchange order.

    quote_provider must return explicit, fresh public Binance Spot bid/ask
    evidence. The adapter itself performs no network access.
    """

    def __init__(
        self, *, database_path: str | Path, account_id: str, quote_asset: str,
        initial_quote_balance: Decimal, quote_provider: Callable[[str], PaperMarketQuote],
        initial_balances: Mapping[str, Decimal] | None = None,
        fee_rate: Decimal = Decimal("0.001"), slippage_bps: Decimal = Decimal("5"),
        max_quote_age: timedelta = timedelta(seconds=15),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(account_id) is not str or not account_id.strip():
            raise PaperAdapterError("account_id must be non-empty")
        if type(quote_asset) is not str or not quote_asset.strip():
            raise PaperAdapterError("quote_asset must be non-empty")
        _decimal(initial_quote_balance, "initial_quote_balance")
        if initial_quote_balance < _ZERO:
            raise PaperAdapterError("initial_quote_balance must be non-negative")
        if not re.fullmatch(r"[A-Z0-9]+", quote_asset):
            raise PaperAdapterError("quote_asset must be canonical uppercase asset code")
        if initial_balances is None:
            configured_balances = {quote_asset: initial_quote_balance}
        else:
            if not isinstance(initial_balances, Mapping) or not initial_balances:
                raise PaperAdapterError("initial_balances must explicitly define the PAPER asset universe")
            configured_balances = dict(initial_balances)
            if quote_asset not in configured_balances:
                raise PaperAdapterError("initial_balances must include quote_asset")
            if configured_balances[quote_asset] != initial_quote_balance:
                raise PaperAdapterError("initial_quote_balance must match initial_balances quote asset")
        for asset, balance in configured_balances.items():
            if type(asset) is not str or not re.fullmatch(r"[A-Z0-9]+", asset):
                raise PaperAdapterError("initial balance asset must be canonical uppercase code")
            _decimal(balance, f"initial_balances[{asset}]")
            if balance < _ZERO:
                raise PaperAdapterError("initial asset balances must be non-negative")
        _decimal(fee_rate, "fee_rate")
        _decimal(slippage_bps, "slippage_bps")
        if not _ZERO <= fee_rate <= Decimal("0.05"):
            raise PaperAdapterError("fee_rate must be between 0 and 0.05")
        if not _ZERO <= slippage_bps <= Decimal("1000"):
            raise PaperAdapterError("slippage_bps must be between 0 and 1000")
        if not isinstance(max_quote_age, timedelta) or max_quote_age.total_seconds() <= 0:
            raise PaperAdapterError("max_quote_age must be a positive timedelta")
        if not callable(quote_provider):
            raise PaperAdapterError("quote_provider must be callable")

        self.account_id, self.quote_asset = account_id, quote_asset
        self.initial_quote_balance = initial_quote_balance
        self.quote_provider, self.fee_rate = quote_provider, fee_rate
        self.slippage_bps, self.max_quote_age = slippage_bps, max_quote_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        path = str(database_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        journal = self._connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal is None or str(journal[0]).lower() != "delete":
            self._connection.close()
            raise PaperAdapterError("PAPER_SQLITE_DELETE_JOURNAL_REQUIRED")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_account (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                mode TEXT NOT NULL CHECK(mode='PAPER'),
                account_id TEXT NOT NULL,
                quote_asset TEXT NOT NULL,
                initial_quote_balance TEXT NOT NULL,
                created_at TEXT NOT NULL,
                state_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS paper_assets (
                asset TEXT PRIMARY KEY,
                initial_balance TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_balances (
                asset TEXT PRIMARY KEY,
                balance TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_fills (
                client_order_id TEXT PRIMARY KEY,
                intent_hash TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode='PAPER'),
                account_id TEXT NOT NULL,
                reservation_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                base_asset TEXT NOT NULL,
                quote_asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NOT NULL,
                filled_at TEXT NOT NULL,
                quote_balance_after TEXT NOT NULL,
                base_balance_after TEXT NOT NULL,
                fee_asset TEXT NOT NULL DEFAULT '',
                fee_rate TEXT NOT NULL DEFAULT '',
                slippage_bps TEXT NOT NULL DEFAULT '',
                quote_bid TEXT NOT NULL DEFAULT '',
                quote_ask TEXT NOT NULL DEFAULT '',
                quote_observed_at TEXT NOT NULL DEFAULT '',
                quote_available_at TEXT NOT NULL DEFAULT '',
                quote_max_age_microseconds TEXT NOT NULL DEFAULT '',
                quote_source TEXT NOT NULL,
                quote_observation_id TEXT NOT NULL DEFAULT '',
                quote_observation_type TEXT NOT NULL DEFAULT '',
                quote_evidence_hash TEXT NOT NULL DEFAULT '',
                evaluation_context_id TEXT NOT NULL DEFAULT ''
            );
            """
        )
        # Additive migration for databases created by the earlier PAPER adapter.
        account_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(paper_account)")}
        if "state_version" not in account_columns:
            self._connection.execute("ALTER TABLE paper_account ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0")
        if "updated_at" not in account_columns:
            self._connection.execute("ALTER TABLE paper_account ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        fill_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(paper_fills)")}
        fill_migrations = {
            "fee_asset": "TEXT NOT NULL DEFAULT ''",
            "fee_rate": "TEXT NOT NULL DEFAULT ''",
            "slippage_bps": "TEXT NOT NULL DEFAULT ''",
            "quote_bid": "TEXT NOT NULL DEFAULT ''",
            "quote_ask": "TEXT NOT NULL DEFAULT ''",
            "quote_observed_at": "TEXT NOT NULL DEFAULT ''",
            "quote_available_at": "TEXT NOT NULL DEFAULT ''",
            "quote_max_age_microseconds": "TEXT NOT NULL DEFAULT ''",
            "quote_observation_id": "TEXT NOT NULL DEFAULT ''",
            "quote_observation_type": "TEXT NOT NULL DEFAULT ''",
            "quote_evidence_hash": "TEXT NOT NULL DEFAULT ''",
            "evaluation_context_id": "TEXT NOT NULL DEFAULT ''",
        }
        for column, declaration in fill_migrations.items():
            if column not in fill_columns:
                self._connection.execute(
                    f"ALTER TABLE paper_fills ADD COLUMN {column} {declaration}"
                )
        self._connection.commit()
        now = _aware(self.clock(), "clock")
        with self._connection:
            self._connection.execute(
                """INSERT OR IGNORE INTO paper_account
                   (singleton,mode,account_id,quote_asset,initial_quote_balance,created_at,state_version,updated_at)
                   VALUES (1,'PAPER',?,?,?,?,0,?)""",
                (account_id, quote_asset, _decimal_text(initial_quote_balance), now.isoformat(), now.isoformat()),
            )
            account = self._connection.execute("SELECT * FROM paper_account WHERE singleton=1").fetchone()
            if (account["mode"] != "PAPER" or account["account_id"] != account_id
                    or account["quote_asset"] != quote_asset
                    or account["initial_quote_balance"] != _decimal_text(initial_quote_balance)):
                self._connection.close()
                raise PaperAdapterError("PERSISTED_PAPER_ACCOUNT_CONFIGURATION_MISMATCH")
            existing_assets = {row["asset"]: row["initial_balance"] for row in self._connection.execute(
                "SELECT asset,initial_balance FROM paper_assets")}
            existing_balances = {row["asset"]: row["balance"] for row in self._connection.execute(
                "SELECT asset,balance FROM paper_balances")}
            fill_count = self._connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
            if not existing_assets:
                if fill_count or set(existing_balances) - {quote_asset}:
                    raise PaperAdapterError("PAPER_ACCOUNT_UNIVERSE_UNKNOWN_REQUIRES_REVIEW")
                for asset, balance in sorted(configured_balances.items()):
                    self._connection.execute("INSERT INTO paper_assets(asset,initial_balance) VALUES (?,?)",
                                              (asset, _decimal_text(balance)))
                    self._connection.execute("INSERT OR IGNORE INTO paper_balances(asset,balance) VALUES (?,?)",
                                              (asset, _decimal_text(balance)))
            else:
                expected = {asset: _decimal_text(balance) for asset, balance in configured_balances.items()}
                if existing_assets != expected:
                    raise PaperAdapterError("PERSISTED_PAPER_ASSET_UNIVERSE_MISMATCH")
                if set(existing_balances) != set(existing_assets):
                    raise PaperAdapterError("PAPER_ACCOUNT_BALANCES_INCOMPLETE_OR_INCONSISTENT")
            account = self._connection.execute("SELECT updated_at FROM paper_account WHERE singleton=1").fetchone()
            if not account["updated_at"]:
                self._connection.execute("UPDATE paper_account SET updated_at=created_at WHERE singleton=1")

    def _validated_order(self, order: OrderIntent) -> tuple[dict[str, Any], str]:
        if not isinstance(order, OrderIntent):
            raise PaperAdapterError("PAPER_REQUIRES_CANONICAL_ORDER_INTENT")
        payload = dict(order.payload)
        if payload.get("schema") != "reservation-order-intent-v1":
            raise PaperAdapterError("PAPER_REQUIRES_PERSISTED_RESERVATION_INTENT")
        if payload.get("client_order_id") != order.client_order_id:
            raise PaperAdapterError("PAPER_CLIENT_ORDER_ID_MISMATCH")
        if payload.get("correlation_id") != order.correlation_id:
            raise PaperAdapterError("PAPER_CORRELATION_ID_MISMATCH")
        if payload.get("account_id") != self.account_id:
            raise PaperAdapterError("PAPER_ACCOUNT_ID_MISMATCH")
        if payload.get("quote_asset") != self.quote_asset:
            raise PaperAdapterError("PAPER_QUOTE_ASSET_MISMATCH")
        symbol, base_asset, quote_asset = payload.get("symbol"), payload.get("base_asset"), payload.get("quote_asset")
        if (type(symbol) is not str or not _SYMBOL_RE.fullmatch(symbol)
                or type(base_asset) is not str or type(quote_asset) is not str
                or symbol != f"{base_asset}/{quote_asset}"):
            raise PaperAdapterError("PAPER_INSTRUMENT_IDENTITY_INVALID")
        if payload.get("market") != "SPOT":
            raise PaperAdapterError("PAPER_ONLY_SUPPORTS_SPOT_INSTRUMENTS")
        if payload.get("side") not in {"BUY", "SELL"}:
            raise PaperAdapterError("PAPER_SIDE_INVALID")
        if payload.get("order_type") not in {"MARKET", "LIMIT"}:
            raise PaperAdapterError("PAPER_ORDER_TYPE_INVALID")
        quantity = _payload_decimal(payload, "quantity")
        if quantity is None or quantity <= _ZERO:
            raise PaperAdapterError("PAPER_QUANTITY_MUST_BE_POSITIVE")
        price = _payload_decimal(payload, "price", optional=True)
        max_spend = _payload_decimal(payload, "max_quote_spend", optional=True)
        if payload["order_type"] == "MARKET":
            if price is not None or payload.get("price_policy") != "MARKET_REFERENCE":
                raise PaperAdapterError("PAPER_MARKET_TERMS_INVALID")
        elif price is None or price <= _ZERO or payload.get("price_policy") != "FIXED":
            raise PaperAdapterError("PAPER_LIMIT_TERMS_INVALID")
        if payload["side"] == "BUY" and payload["order_type"] == "MARKET":
            if max_spend is None or max_spend <= _ZERO:
                raise PaperAdapterError("PAPER_BUY_MARKET_REQUIRES_MAX_QUOTE_SPEND")
        elif max_spend is not None:
            raise PaperAdapterError("PAPER_MAX_QUOTE_SPEND_NOT_ALLOWED_FOR_ORDER")
        return payload, _canonical_json(payload)

    def _quote_from_persisted_evidence(
        self, payload: Mapping[str, Any], now: datetime
    ) -> PaperMarketQuote:
        from .paper_quote_evidence import PaperQuoteEvidenceError, validate_paper_quote_evidence

        try:
            validated = validate_paper_quote_evidence(
                payload.get("paper_quote_evidence"),
                expected_symbol=payload["symbol"],
                expected_decision_timestamp=datetime.fromisoformat(payload["decision_timestamp"]),
                expected_context_id=payload["evaluation_context_id"],
                now=now,
                max_age=self.max_quote_age,
            )
        except (KeyError, TypeError, ValueError, PaperQuoteEvidenceError) as exc:
            raise PaperAdapterError("PAPER_QUOTE_EVIDENCE_INVALID:" + str(exc)) from exc
        quote = validated.evidence
        return PaperMarketQuote(
            symbol=quote["symbol"],
            bid=validated.bid,
            ask=validated.ask,
            observed_at=validated.observed_at,
            available_at=validated.available_at,
            source=quote["source"],
        )

    def _load_fill(self, client_order_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM paper_fills WHERE client_order_id=?", (client_order_id,)
        ).fetchone()
        return None if row is None else dict(row)

    def _submit(self, order: OrderIntent) -> Mapping[str, Any]:
        payload, intent_json = self._validated_order(order)
        intent_hash = hashlib.sha256(intent_json.encode("utf-8")).hexdigest()
        with self._lock:
            existing = self._load_fill(order.client_order_id)
            if existing is not None:
                if existing["intent_hash"] != intent_hash or existing["intent_json"] != intent_json:
                    raise PaperAdapterError("PAPER_CLIENT_ORDER_ID_INTENT_CONFLICT")
                return {"mode": "PAPER", "status": "PAPER_ALREADY_FILLED",
                        "client_order_id": order.client_order_id, "price": existing["price"],
                        "quantity": existing["quantity"]}

        now = _aware(self.clock(), "clock")
        quote = self._quote_from_persisted_evidence(payload, now)
        side = payload["side"]
        quantity = _payload_decimal(payload, "quantity")
        requested_price = _payload_decimal(payload, "price", optional=True)
        assert quantity is not None
        raw_price = quote.ask if side == "BUY" else quote.bid
        slip = self.slippage_bps / _BPS
        fill_price = raw_price * (_ONE + slip if side == "BUY" else _ONE - slip)
        if fill_price <= _ZERO:
            raise PaperAdapterError("PAPER_FILL_PRICE_NON_POSITIVE")
        if payload["order_type"] == "LIMIT":
            assert requested_price is not None
            if (side == "BUY" and fill_price > requested_price) or (side == "SELL" and fill_price < requested_price):
                raise PaperAdapterError("PAPER_LIMIT_NOT_MARKETABLE")

        gross, fee = quantity * fill_price, quantity * fill_price * self.fee_rate
        base_asset, quote_asset = payload["base_asset"], payload["quote_asset"]
        reservation_id, proposal_id = payload.get("reservation_id"), payload.get("proposal_id")
        if not isinstance(reservation_id, str) or not reservation_id or not isinstance(proposal_id, str) or not proposal_id:
            raise PaperAdapterError("PAPER_RESERVATION_OR_PROPOSAL_ID_MISSING")
        if side == "BUY" and payload["order_type"] == "MARKET":
            max_spend = _payload_decimal(payload, "max_quote_spend")
            assert max_spend is not None
            if gross + fee > max_spend:
                raise PaperAdapterError("PAPER_BUY_EXCEEDS_MAX_QUOTE_SPEND")

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                existing = self._load_fill(order.client_order_id)
                if existing is not None:
                    if existing["intent_hash"] != intent_hash or existing["intent_json"] != intent_json:
                        raise PaperAdapterError("PAPER_CLIENT_ORDER_ID_INTENT_CONFLICT")
                    self._connection.commit()
                    return {"mode": "PAPER", "status": "PAPER_ALREADY_FILLED",
                            "client_order_id": order.client_order_id, "price": existing["price"],
                            "quantity": existing["quantity"]}
                known_assets = {row["asset"] for row in self._connection.execute(
                    "SELECT asset FROM paper_assets").fetchall()}
                if quote_asset not in known_assets or base_asset not in known_assets:
                    raise PaperAdapterError("PAPER_ASSET_UNKNOWN_OR_OUTSIDE_ACCOUNT_UNIVERSE")
                quote_row = self._connection.execute(
                    "SELECT balance FROM paper_balances WHERE asset=?", (quote_asset,)
                ).fetchone()
                base_row = self._connection.execute(
                    "SELECT balance FROM paper_balances WHERE asset=?", (base_asset,)
                ).fetchone()
                if quote_row is None or base_row is None:
                    raise PaperAdapterError("PAPER_ACCOUNT_BALANCE_MISSING_FOR_KNOWN_ASSET")
                quote_balance = Decimal(quote_row["balance"])
                base_balance = Decimal(base_row["balance"])
                if side == "BUY":
                    debit = gross + fee
                    if debit > quote_balance:
                        raise PaperAdapterError("PAPER_INSUFFICIENT_QUOTE_BALANCE")
                    quote_after, base_after = quote_balance - debit, base_balance + quantity
                else:
                    if quantity > base_balance:
                        raise PaperAdapterError("PAPER_INSUFFICIENT_BASE_BALANCE")
                    proceeds = gross - fee
                    if proceeds < _ZERO:
                        raise PaperAdapterError("PAPER_FEE_EXCEEDS_SELL_PROCEEDS")
                    quote_after, base_after = quote_balance + proceeds, base_balance - quantity
                self._connection.execute(
                    "INSERT OR REPLACE INTO paper_balances(asset,balance) VALUES (?,?)",
                    (quote_asset, _decimal_text(quote_after)),
                )
                self._connection.execute(
                    "INSERT OR REPLACE INTO paper_balances(asset,balance) VALUES (?,?)",
                    (base_asset, _decimal_text(base_after)),
                )
                self._connection.execute(
                    """INSERT INTO paper_fills(
                        client_order_id,intent_hash,intent_json,mode,account_id,reservation_id,
                        proposal_id,symbol,base_asset,quote_asset,side,quantity,price,fee,
                        filled_at,quote_balance_after,base_balance_after,fee_asset,fee_rate,
                        slippage_bps,quote_bid,quote_ask,quote_observed_at,quote_available_at,
                        quote_max_age_microseconds,quote_source,quote_observation_id,
                        quote_observation_type,quote_evidence_hash,evaluation_context_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (order.client_order_id, intent_hash, intent_json, "PAPER", self.account_id,
                     reservation_id, proposal_id, payload["symbol"], base_asset, quote_asset,
                     side, _decimal_text(quantity), _decimal_text(fill_price), _decimal_text(fee),
                     now.isoformat(), _decimal_text(quote_after), _decimal_text(base_after),
                     quote_asset, _decimal_text(self.fee_rate), _decimal_text(self.slippage_bps),
                     _decimal_text(quote.bid), _decimal_text(quote.ask), quote.observed_at.isoformat(),
                     quote.available_at.isoformat(),
                     str(self.max_quote_age // timedelta(microseconds=1)), quote.source,
                      payload["paper_quote_evidence"]["observation_id"],
                      payload["paper_quote_evidence"]["observation_type"],
                      payload["paper_quote_evidence"]["evidence_hash"],
                      payload["evaluation_context_id"]),
                )
                self._connection.execute(
                    "UPDATE paper_account SET state_version=state_version+1, updated_at=? WHERE singleton=1",
                    (now.isoformat(),),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        return {"mode": "PAPER", "status": "PAPER_FILLED", "client_order_id": order.client_order_id,
                "reservation_id": reservation_id, "symbol": payload["symbol"], "side": side,
                "quantity": _decimal_text(quantity), "price": _decimal_text(fill_price),
                "fee": _decimal_text(fee), "fee_asset": quote_asset, "filled_at": now.isoformat(),
                "quote_balance_after": _decimal_text(quote_after), "base_balance_after": _decimal_text(base_after),
                "quote_source": quote.source}

    def _cancel(self, order: Any) -> Mapping[str, Any]:
        raise PaperAdapterError("PAPER_ORDERS_FILL_IMMEDIATELY; CANCELLATION_IS_NOT_AVAILABLE")

    def snapshot(self) -> Mapping[str, Any]:
        # A read transaction pins one SQLite snapshot across independent connections.
        with self._lock:
            try:
                self._connection.execute("BEGIN")
                account = self._connection.execute("SELECT * FROM paper_account WHERE singleton=1").fetchone()
                assets = {row["asset"]: row["initial_balance"] for row in self._connection.execute(
                    "SELECT asset,initial_balance FROM paper_assets ORDER BY asset").fetchall()}
                balances = {row["asset"]: row["balance"] for row in self._connection.execute(
                    "SELECT asset,balance FROM paper_balances ORDER BY asset").fetchall()}
                fills = tuple({key: value for key, value in dict(row).items() if key != "intent_json"}
                    for row in self._connection.execute(
                        "SELECT * FROM paper_fills ORDER BY filled_at,client_order_id").fetchall())
                complete = bool(assets) and set(assets) == set(balances) and all(
                    Decimal(value).is_finite() and Decimal(value) >= _ZERO for value in balances.values()
                ) and account["state_version"] == len(fills)
                result = {"mode": "PAPER", "account_id": account["account_id"],
                    "quote_asset": account["quote_asset"], "initial_quote_balance": account["initial_quote_balance"],
                    "asset_universe": assets, "balances": balances, "state_version": account["state_version"],
                    "as_of": account["updated_at"], "completeness": "COMPLETE" if complete else "UNKNOWN",
                    "fills": fills, "fill_count": len(fills)}
                self._connection.commit()
                return result
            except Exception:
                self._connection.rollback()
                raise

    def economic_replay(self):
        """Replay persisted PAPER fills against their stored quote/cost evidence."""
        from .paper_economic_replay import replay_paper_economics
        return replay_paper_economics(self.snapshot())

    def close(self) -> None:
        with self._lock:
            self._connection.close()



def canonical_account_state_from_paper_snapshot(snapshot: Mapping[str, Any]):
    """Project a complete persisted PAPER snapshot into canonical risk state."""
    if not isinstance(snapshot, Mapping) or snapshot.get("mode") != "PAPER":
        raise PaperAdapterError("PAPER_ACCOUNT_SNAPSHOT_REQUIRED")
    if snapshot.get("completeness") != "COMPLETE":
        raise PaperAdapterError("PAPER_ACCOUNT_SNAPSHOT_INCOMPLETE")
    account_id, balances, assets = snapshot.get("account_id"), snapshot.get("balances"), snapshot.get("asset_universe")
    version, fill_count, as_of_raw = snapshot.get("state_version"), snapshot.get("fill_count"), snapshot.get("as_of")
    if (
        type(account_id) is not str or not account_id.strip()
        or not isinstance(balances, Mapping) or not isinstance(assets, Mapping)
        or not assets or set(balances) != set(assets)
        or isinstance(version, bool) or not isinstance(version, int) or version < 0
        or fill_count != version or type(as_of_raw) is not str or not as_of_raw
    ):
        raise PaperAdapterError("PAPER_ACCOUNT_SNAPSHOT_FIELDS_INVALID")
    try:
        as_of = datetime.fromisoformat(as_of_raw)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be aware")
        parsed = {asset: Decimal(raw) for asset, raw in balances.items()}
        if any(not value.is_finite() or value < 0 for value in parsed.values()):
            raise ValueError("invalid persisted balance")
        canonical = {"mode":"PAPER","account_id":account_id,"state_version":version,
                     "as_of":as_of.astimezone(timezone.utc).isoformat(),
                     "balances":{asset:_decimal_text(value) for asset,value in sorted(parsed.items())}}
        digest = hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()
        from .analysis_contracts import ArtifactNature, Provenance
        from .risk_contracts import BalanceSnapshot, CanonicalAccountState, Completeness
        return CanonicalAccountState(
            account_state_id="paper-state-"+digest, account_id=account_id,
            as_of=as_of.astimezone(timezone.utc),
            balances=tuple(BalanceSnapshot(asset=a,total=v,available=v,locked=Decimal("0")) for a,v in sorted(parsed.items())),
            completeness=Completeness.COMPLETE,
            provenance=Provenance(source="paper-sqlite",nature=ArtifactNature.OBSERVED,
                reference=f"{account_id}:state_version={version}:sha256={digest}",
                metadata={"mode":"PAPER","state_version":version,"fill_count":fill_count}),
        )
    except (TypeError,ValueError,ArithmeticError) as exc:
        raise PaperAdapterError("PAPER_ACCOUNT_SNAPSHOT_CANNOT_BECOME_CANONICAL_STATE") from exc

