from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from bot_obrero.execution import EvidenceBundle, EvidenceRecord, ExecutionOrchestrator, OrderIntent
from bot_obrero.financial_admission import FinancialAdmissionBoundary
from bot_obrero.financial_execution_runtime import FinancialExecutionRuntime, FinancialExecutionRuntimeStatus
from bot_obrero.murphy import MurphyGuard
from bot_obrero.persistent_ledger import SQLiteIdempotencyLedger
from bot_obrero.reservation import SQLiteReservationStore
from bot_obrero.reservation_execution_bridge import ReservationExecutionBridge
from bot_obrero.risk_engine import RiskEngine
from bot_obrero.risk_limit_applicability import RiskLimitApplicabilityResolver
from bot_obrero.paper_adapter import PaperAdapterError, PaperExecutionAdapter, PaperMarketQuote
from test_hueso_02h2_financial_admission import BASE, make_context, make_policy, make_proposal, make_state

NOW = BASE + timedelta(minutes=1)
REQUIRED = ("reconciliation_match", "clock_valid", "stream_ready", "resources_healthy",
            "permissions_safe", "configuration_valid", "protection_safe")


def quote(symbol, *, now=NOW, bid="99.9", ask="100"):
    return PaperMarketQuote(
        symbol=symbol, bid=Decimal(bid), ask=Decimal(ask),
        observed_at=now - timedelta(seconds=5),
        available_at=now - timedelta(seconds=10),
        source="binance-public:deterministic-test-fixture",
    )


def make_order(*, side="BUY", quantity="0.2", max_spend="25", symbol="BTC/USDT",
               account_id="paper-local", client_order_id="paper-order-1"):
    base, quote_asset = symbol.split("/")
    payload = {
        "schema": "reservation-order-intent-v1", "reservation_id": "reservation-1",
        "client_order_id": client_order_id, "proposal_id": "proposal-1", "signal_id": "signal-1",
        "account_id": account_id, "instrument_id": "binance:SPOT:BTCUSDT", "market": "SPOT",
        "instrument_type": "SPOT", "symbol": symbol, "base_asset": base, "quote_asset": quote_asset,
        "side": side, "order_type": "MARKET", "quantity": quantity, "price": None,
        "max_quote_spend": max_spend if side == "BUY" else None, "price_policy": "MARKET_REFERENCE",
        "strategy_identity": "strategy.test", "strategy_version": "1.0.0",
        "decision_timestamp": BASE.isoformat(), "correlation_id": "correlation-1",
        "risk_decision_id": "risk-1", "authorization_id": "authorization-1",
        "authorization_fingerprint": "f" * 64, "evaluation_context_id": "context-1",
        "policy_id": "policy-1", "policy_version": "1.0.0",
        "resource_kind": "QUOTE" if side == "BUY" else "BASE",
        "asset": quote_asset if side == "BUY" else base,
        "reserved_amount": max_spend if side == "BUY" else quantity, "terms_hash": "a" * 64,
    }
    return OrderIntent(client_order_id, payload, "correlation-1")


def make_adapter(path, *, quote_provider=None, account_id="paper-local", initial="100",
                 initial_balances=None, clock=lambda: NOW):
    configured = initial_balances if initial_balances is not None else {
        "USDT": Decimal(initial), "BTC": Decimal("0")
    }
    return PaperExecutionAdapter(
        database_path=path, account_id=account_id, quote_asset="USDT",
        initial_quote_balance=Decimal(initial), quote_provider=quote_provider or (lambda symbol: quote(symbol)),
        initial_balances=configured,
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("5"), clock=clock,
    )


def test_buy_fill_uses_decimal_costs_and_persists_across_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    adapter = make_adapter(path)
    try:
        result = adapter._submit(make_order())
        assert (result["mode"], result["status"], result["price"], result["fee"]) == (
            "PAPER", "PAPER_FILLED", "100.05", "0.02001")
        snapshot = adapter.snapshot()
        assert snapshot["mode"] == "PAPER" and snapshot["fill_count"] == 1
        assert snapshot["balances"]["USDT"] == "79.96999"
        assert snapshot["balances"]["BTC"] == "0.2"
    finally:
        adapter.close()

    recovered = make_adapter(path)
    try:
        assert recovered.snapshot()["fill_count"] == 1
        assert recovered.snapshot()["balances"]["USDT"] == "79.96999"
        assert recovered.snapshot()["balances"]["BTC"] == "0.2"
        duplicate = recovered._submit(make_order())
        assert duplicate["status"] == "PAPER_ALREADY_FILLED"
        assert recovered.snapshot()["fill_count"] == 1
    finally:
        recovered.close()


def test_sell_fill_updates_quote_and_base_balances_with_fee(tmp_path):
    adapter = make_adapter(tmp_path / "paper.sqlite3")
    try:
        adapter._submit(make_order())
        result = adapter._submit(make_order(side="SELL", quantity="0.1", max_spend=None,
                                            client_order_id="paper-order-2"))
        assert result["status"] == "PAPER_FILLED"
        assert result["price"] == "99.85005"
        assert result["fee"] == "0.009985005"
        snapshot = adapter.snapshot()
        assert snapshot["balances"]["BTC"] == "0.1"
        assert snapshot["balances"]["USDT"] == "89.945009995"
        assert snapshot["fill_count"] == 2
    finally:
        adapter.close()


def test_unknown_or_stale_quote_fails_closed_without_fill(tmp_path):
    def unknown(symbol):
        return PaperMarketQuote(symbol=symbol, bid=Decimal("99"), ask=Decimal("100"),
            observed_at=NOW, available_at=None, source="binance-public:test")
    adapter = make_adapter(tmp_path / "unknown.sqlite3", quote_provider=unknown)
    try:
        with pytest.raises(PaperAdapterError, match="available_at is UNKNOWN"):
            adapter._submit(make_order())
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()

    adapter = make_adapter(tmp_path / "stale.sqlite3",
                           quote_provider=lambda symbol: quote(symbol, now=NOW - timedelta(minutes=2)))
    try:
        with pytest.raises(PaperAdapterError, match="PAPER_QUOTE_STALE"):
            adapter._submit(make_order())
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()


def test_future_quote_and_wrong_symbol_fail_closed(tmp_path):
    future = lambda symbol: PaperMarketQuote(symbol=symbol, bid=Decimal("99"), ask=Decimal("100"),
        observed_at=NOW + timedelta(seconds=1), available_at=NOW, source="binance-public:test")
    adapter = make_adapter(tmp_path / "future.sqlite3", quote_provider=future)
    try:
        with pytest.raises(PaperAdapterError, match="TEMPORAL_ORDER_INVALID"):
            adapter._submit(make_order())
    finally:
        adapter.close()

    adapter = make_adapter(tmp_path / "wrong.sqlite3", quote_provider=lambda symbol: quote("ETH/USDT"))
    try:
        with pytest.raises(PaperAdapterError, match="QUOTE_SYMBOL_MISMATCH"):
            adapter._submit(make_order())
    finally:
        adapter.close()


def test_direct_adapter_call_is_blocked_by_existing_execution_boundary(tmp_path):
    adapter = make_adapter(tmp_path / "paper.sqlite3")
    try:
        with pytest.raises(PermissionError, match="EXECUTION_BOUNDARY_REQUIRED"):
            adapter.submit(make_order())
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()


def test_buy_max_quote_spend_is_enforced(tmp_path):
    adapter = make_adapter(tmp_path / "cap.sqlite3")
    try:
        with pytest.raises(PaperAdapterError, match="MAX_QUOTE_SPEND"):
            adapter._submit(make_order(quantity="1", max_spend="25"))
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()


def test_existing_financial_runtime_can_terminate_in_paper_with_synthetic_proposal(tmp_path):
    # This tests the canonical risk/admission/reservation/execution chain with a
    # synthetic proposal. It intentionally does not invent a Product V2 signal bridge.
    state = make_state(available="100")
    proposal = replace(make_proposal(max_quote_spend=Decimal("25")), requested_quantity=Decimal("0.2"))
    context = make_context(proposal, state)
    context = replace(context, instrument=replace(context.instrument, market="SPOT"))
    policy = make_policy()
    adapter = make_adapter(tmp_path / "paper-account.sqlite3", account_id=state.account_id, clock=lambda: NOW)
    store = SQLiteReservationStore(tmp_path / "reservations.sqlite3")
    bridge = ReservationExecutionBridge(store)
    ledger = SQLiteIdempotencyLedger(tmp_path / "execution-ledger.sqlite3")
    orchestrator = ExecutionOrchestrator(adapter=adapter, ledger=ledger, murphy_guard=MurphyGuard(),
                                         reservation_bridge=bridge, clock=lambda: NOW)
    runtime = FinancialExecutionRuntime(
        store=store, risk_engine=RiskEngine(), risk_limit_resolver=RiskLimitApplicabilityResolver(),
        admission_boundary=FinancialAdmissionBoundary(store), reservation_bridge=bridge,
        execution_orchestrator=orchestrator,
    )

    def evidence_provider(prepared):
        binding = bridge.verify_prepared(prepared)
        correlation = binding.payload["correlation_id"]
        records = tuple(EvidenceRecord(kind=kind, value=True, source="deterministic-paper-test",
            observed_at=BASE, decision_at=BASE, intent_id=prepared.client_order_id,
            correlation_id=correlation, expires_at=BASE + timedelta(minutes=5)) for kind in REQUIRED)
        return EvidenceBundle(records, correlation)

    try:
        result = runtime.start(proposal=proposal, context=context, policy=policy,
            canonical_account_state=state, account_id=state.account_id,
            created_at=BASE + timedelta(seconds=30), evidence_provider=evidence_provider)
        assert result.status is FinancialExecutionRuntimeStatus.SUBMITTED
        assert result.execution_result is not None and result.execution_result.reason == "SUBMITTED"
        snapshot = adapter.snapshot()
        assert snapshot["mode"] == "PAPER" and snapshot["fill_count"] == 1
        assert snapshot["fills"][0]["proposal_id"] == proposal.proposal_id
        assert snapshot["fills"][0]["mode"] == "PAPER"
    finally:
        ledger.close()
        store.close()
        adapter.close()


def test_paper_adapter_has_no_live_or_financial_runtime_imports():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("bot_obrero/paper_adapter.py").read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
    assert not any("financial_execution_runtime" in name for name in imported)
    assert not any("binance" in name.lower() for name in imported)

def test_account_universe_persists_explicit_zero_and_rejects_unknown_asset(tmp_path):
    path = tmp_path / "account-universe.sqlite3"
    configured = {"USDT": Decimal("100"), "BTC": Decimal("0"), "ETH": Decimal("0")}
    adapter = make_adapter(path, initial_balances=configured)
    try:
        snapshot = adapter.snapshot()
        assert snapshot["completeness"] == "COMPLETE"
        assert snapshot["asset_universe"] == {"BTC": "0", "ETH": "0", "USDT": "100"}
        assert snapshot["balances"]["BTC"] == "0"
        before_version = snapshot["state_version"]
        with pytest.raises(PaperAdapterError, match="ASSET_UNKNOWN"):
            adapter._submit(make_order(symbol="DOGE/USDT"))
        assert adapter.snapshot()["fill_count"] == 0
        assert adapter.snapshot()["state_version"] == before_version
    finally:
        adapter.close()
    recovered = make_adapter(path, initial_balances=configured)
    try:
        snapshot = recovered.snapshot()
        assert snapshot["completeness"] == "COMPLETE"
        assert snapshot["balances"]["BTC"] == "0"
        assert snapshot["state_version"] == 0
    finally:
        recovered.close()


def test_account_version_increments_atomically_with_fill(tmp_path):
    adapter = make_adapter(tmp_path / "version.sqlite3")
    try:
        before = adapter.snapshot()
        result = adapter._submit(make_order())
        after = adapter.snapshot()
        assert result["status"] == "PAPER_FILLED"
        assert after["state_version"] == before["state_version"] + 1
        assert after["as_of"] == after["fills"][0]["filled_at"]
        assert after["completeness"] == "COMPLETE"
    finally:
        adapter.close()


def test_snapshot_is_coherent_across_two_adapter_instances(tmp_path):
    import threading
    path = tmp_path / "concurrent.sqlite3"
    reader, writer = make_adapter(path), make_adapter(path)
    failures = []
    def write_fills():
        try:
            for index in range(1, 11):
                writer._submit(make_order(client_order_id=f"concurrent-order-{index}"))
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=write_fills)
    try:
        thread.start()
        while thread.is_alive():
            snapshot = reader.snapshot()
            assert snapshot["state_version"] == snapshot["fill_count"]
            assert set(snapshot["asset_universe"]) == set(snapshot["balances"])
            assert snapshot["completeness"] == "COMPLETE"
        thread.join()
        assert not failures
        final = reader.snapshot()
        assert final["state_version"] == final["fill_count"] == 10
    finally:
        thread.join()
        writer.close()
        reader.close()


def test_missing_balance_for_known_asset_blocks_fill_and_completeness(tmp_path):
    adapter = make_adapter(tmp_path / "missing-balance.sqlite3")
    try:
        adapter._connection.execute("DELETE FROM paper_balances WHERE asset='BTC'")
        adapter._connection.commit()
        assert adapter.snapshot()["completeness"] == "UNKNOWN"
        with pytest.raises(PaperAdapterError, match="BALANCE_MISSING"):
            adapter._submit(make_order())
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()


def test_legacy_fill_history_without_asset_universe_is_not_promoted_to_complete(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.sqlite3"
    adapter = make_adapter(path)
    adapter._submit(make_order())
    adapter.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE paper_assets")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperAdapterError, match="UNIVERSE_UNKNOWN"):
        make_adapter(path)
