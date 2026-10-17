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
from bot_obrero.paper_adapter import (
    PaperAdapterError, PaperExecutionAdapter, PaperMarketQuote,
    canonical_account_state_from_paper_snapshot,
)
from bot_obrero.analysis_contracts import ArtifactNature, MarketObservation, Provenance
from bot_obrero.evidence_binding import AvailabilityBinding, AvailabilitySubjectKind
from bot_obrero.paper_quote_evidence import evidence_hash
from bot_obrero.paper_runtime_reconciliation import (
    PaperRuntimeReconciliationStatus, reconcile_paper_runtime,
)
from bot_obrero.persistent_ledger import SQLiteIdempotencyLedger
from test_hueso_02h2_financial_admission import BASE, make_context, make_policy, make_proposal, make_state

NOW = BASE + timedelta(minutes=1)
REQUIRED = ("reconciliation_match", "clock_valid", "stream_ready", "resources_healthy",
            "permissions_safe", "configuration_valid", "protection_safe")


def quote(symbol, *, now=NOW, bid="99.9", ask="100"):
    return PaperMarketQuote(
        symbol=symbol, bid=Decimal(bid), ask=Decimal(ask),
        observed_at=now - timedelta(seconds=10),
        available_at=now - timedelta(seconds=5),
        source="binance-public:deterministic-test-fixture",
    )


def quote_evidence(*, symbol="BTC/USDT", decision_timestamp=BASE,
                   observed_at=None, available_at=None, bid="99.9", ask="100",
                   observation_id="market-observation-1", context_id="context-1",
                   source="binance-public:deterministic-test-fixture"):
    observed_at = decision_timestamp - timedelta(seconds=10) if observed_at is None else observed_at
    available_at = decision_timestamp - timedelta(seconds=5) if available_at is None else available_at
    result = {
        "schema": "paper-quote-evidence-v1",
        "observation_id": observation_id,
        "observation_type": "BINANCE_SPOT_BOOK_TICKER",
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "observed_at": observed_at.isoformat(),
        "available_at": available_at.isoformat(),
        "decision_timestamp": decision_timestamp.isoformat(),
        "evaluation_context_id": context_id,
        "source": source,
        "source_reference": "deterministic-test-fixture",
        "venue": "BINANCE",
    }
    result["evidence_hash"] = evidence_hash(result)
    return result


def make_order(*, side="BUY", quantity="0.2", max_spend="25", symbol="BTC/USDT",
               account_id="paper-local", client_order_id="paper-order-1",
               quote_evidence_payload=None, include_quote=True):
    base, quote_asset = symbol.split("/")
    payload = {
        "schema": "reservation-order-intent-v1", "reservation_id": "reservation-1",
        "client_order_id": client_order_id, "proposal_id": "proposal-1", "signal_id": "signal-1",
        "account_id": account_id, "instrument_id": "binance:SPOT:BTCUSDT", "market": "SPOT",
        "instrument_type": "CRYPTO_SPOT", "symbol": symbol, "base_asset": base, "quote_asset": quote_asset,
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
    if include_quote:
        payload["paper_quote_evidence"] = quote_evidence(symbol=symbol) if quote_evidence_payload is None else quote_evidence_payload
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
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("5"),
        max_quote_age=timedelta(minutes=2), clock=clock,
    )



def test_paper_and_idempotency_databases_use_rollback_journal(tmp_path):
    adapter = make_adapter(tmp_path / "journal-paper.sqlite3")
    ledger = SQLiteIdempotencyLedger(tmp_path / "journal-ledger.sqlite3")
    try:
        assert adapter._connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert ledger._connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    finally:
        ledger.close()
        adapter.close()


def test_paper_quote_evidence_is_hash_bound_and_cannot_be_tampered(tmp_path):
    adapter = make_adapter(tmp_path / "quote-tamper.sqlite3")
    try:
        tampered = quote_evidence()
        tampered["bid"] = "98"
        with pytest.raises(PaperAdapterError, match="HASH_MISMATCH"):
            adapter._submit(make_order(quote_evidence_payload=tampered))
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()


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


def test_missing_or_stale_quote_evidence_fails_closed_without_fill(tmp_path):
    adapter = make_adapter(tmp_path / "missing-quote.sqlite3")
    try:
        with pytest.raises(PaperAdapterError, match="PAPER_QUOTE_EVIDENCE_MISSING"):
            adapter._submit(make_order(include_quote=False))
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()

    stale_quote = quote_evidence(
        observed_at=BASE - timedelta(seconds=242),
        available_at=BASE - timedelta(seconds=241),
    )
    adapter = make_adapter(tmp_path / "stale.sqlite3")
    try:
        with pytest.raises(PaperAdapterError, match="PAPER_QUOTE_STALE"):
            adapter._submit(make_order(quote_evidence_payload=stale_quote))
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()


def test_future_quote_and_wrong_symbol_fail_closed(tmp_path):
    future_quote = quote_evidence(
        observed_at=BASE - timedelta(seconds=2),
        available_at=BASE + timedelta(seconds=1),
    )
    adapter = make_adapter(tmp_path / "future.sqlite3")
    try:
        with pytest.raises(PaperAdapterError, match="TEMPORAL_ORDER_INVALID"):
            adapter._submit(make_order(quote_evidence_payload=future_quote))
        assert adapter.snapshot()["fill_count"] == 0
    finally:
        adapter.close()

    wrong_quote = quote_evidence(symbol="ETH/USDT")
    adapter = make_adapter(tmp_path / "wrong.sqlite3")
    try:
        with pytest.raises(PaperAdapterError, match="SYMBOL_MISMATCH"):
            adapter._submit(make_order(quote_evidence_payload=wrong_quote))
        assert adapter.snapshot()["fill_count"] == 0
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
    proposal = replace(make_proposal(max_quote_spend=Decimal("25")), requested_quantity=Decimal("0.2"))
    template_state = make_state(available="100")
    adapter = make_adapter(
        tmp_path / "paper-account.sqlite3", account_id=template_state.account_id, clock=lambda: BASE
    )
    state = canonical_account_state_from_paper_snapshot(adapter.snapshot())
    context = make_context(proposal, state)
    observation = MarketObservation(
        symbol=proposal.symbol,
        observation_timestamp=BASE - timedelta(seconds=10),
        available_timestamp=BASE - timedelta(seconds=5),
        observation_type="BINANCE_SPOT_BOOK_TICKER",
        values={"bid": "99.9", "ask": "100"},
        provenance=Provenance(
            source="binance-public:deterministic-test-fixture",
            nature=ArtifactNature.OBSERVED,
            reference="paper-runtime-test-quote",
        ),
        venue="BINANCE",
        observation_id="paper-runtime-market-observation",
    )
    availability = AvailabilityBinding(
        subject_kind=AvailabilitySubjectKind.MARKET_OBSERVATION,
        subject_id=observation.observation_id,
        available_at=observation.available_timestamp,
        source=observation.provenance.source,
        evidence_reference=observation.observation_id,
    )
    context = replace(
        context,
        instrument=replace(context.instrument, market="SPOT"),
        market_observation=observation,
        availability_bindings=context.availability_bindings + (availability,),
    )
    policy = make_policy()
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
        fill = snapshot["fills"][0]
        binding = bridge.get_binding(result.reservation_id)
        assert binding is not None and binding.state == "SUBMITTED"
        assert fill["quote_observation_id"] == observation.observation_id
        assert fill["quote_evidence_hash"] == binding.payload["paper_quote_evidence"]["evidence_hash"]
        assert fill["evaluation_context_id"] == binding.payload["evaluation_context_id"]
        reservation = store.get(result.reservation_id)
        assert reservation.state.value == "RELEASED"
        assert reservation.consumed_amount == (
            Decimal(fill["quantity"]) * Decimal(fill["price"]) + Decimal(fill["fee"])
        )
        projection = ledger.get(binding.client_order_id)
        assert projection is not None and projection.result == "SUBMITTED"
        reconciled = reconcile_paper_runtime(
            adapter=adapter, store=store, bridge=bridge, ledger=ledger, apply=True
        )
        assert reconciled.status is PaperRuntimeReconciliationStatus.RECONCILED
        assert reconciled.reconciled_fill_count == 1
    finally:
        ledger.close()
        store.close()
        adapter.close()



def test_paper_fill_recovers_after_interrupted_finalization_and_restart(tmp_path, monkeypatch):
    """A durable PAPER fill is reconciled after every SQLite store is reopened.

    Inject a failure after the adapter commits a fill but before the execution
    binding can persist SUBMITTED. Recovery must use only durable evidence and
    repeated recovery must not change balances, versions, or reservation usage.
    """
    paper_path = tmp_path / "paper-account.sqlite3"
    reservation_path = tmp_path / "reservations.sqlite3"
    ledger_path = tmp_path / "execution-ledger.sqlite3"
    proposal = replace(make_proposal(max_quote_spend=Decimal("25")), requested_quantity=Decimal("0.2"))
    template_state = make_state(available="100")
    adapter = make_adapter(paper_path, account_id=template_state.account_id, clock=lambda: BASE)
    state = canonical_account_state_from_paper_snapshot(adapter.snapshot())
    context = make_context(proposal, state)
    observation = MarketObservation(
        symbol=proposal.symbol,
        observation_timestamp=BASE - timedelta(seconds=10),
        available_timestamp=BASE - timedelta(seconds=5),
        observation_type="BINANCE_SPOT_BOOK_TICKER",
        values={"bid": "99.9", "ask": "100"},
        provenance=Provenance(
            source="binance-public:deterministic-test-fixture",
            nature=ArtifactNature.OBSERVED,
            reference="paper-crash-recovery-test-quote",
        ),
        venue="BINANCE",
        observation_id="paper-crash-recovery-market-observation",
    )
    availability = AvailabilityBinding(
        subject_kind=AvailabilitySubjectKind.MARKET_OBSERVATION,
        subject_id=observation.observation_id,
        available_at=observation.available_timestamp,
        source=observation.provenance.source,
        evidence_reference=observation.observation_id,
    )
    context = replace(
        context,
        instrument=replace(context.instrument, market="SPOT"),
        market_observation=observation,
        availability_bindings=context.availability_bindings + (availability,),
    )
    policy = make_policy()
    store = SQLiteReservationStore(reservation_path)
    bridge = ReservationExecutionBridge(store)
    ledger = SQLiteIdempotencyLedger(ledger_path)
    orchestrator = ExecutionOrchestrator(
        adapter=adapter, ledger=ledger, murphy_guard=MurphyGuard(),
        reservation_bridge=bridge, clock=lambda: NOW,
    )
    runtime = FinancialExecutionRuntime(
        store=store, risk_engine=RiskEngine(),
        risk_limit_resolver=RiskLimitApplicabilityResolver(),
        admission_boundary=FinancialAdmissionBoundary(store),
        reservation_bridge=bridge, execution_orchestrator=orchestrator,
    )

    def evidence_provider(prepared):
        binding = bridge.verify_prepared(prepared)
        correlation = binding.payload["correlation_id"]
        records = tuple(
            EvidenceRecord(
                kind=kind, value=True, source="deterministic-paper-crash-recovery-test",
                observed_at=BASE, decision_at=BASE, intent_id=prepared.client_order_id,
                correlation_id=correlation, expires_at=BASE + timedelta(minutes=5),
            )
            for kind in REQUIRED
        )
        return EvidenceBundle(records, correlation)

    original_finish = bridge.finish_submission

    def fail_after_fill_before_finalization(prepared, *, outcome, occurred_at, error=None):
        if outcome == "SUBMITTED":
            raise RuntimeError("INJECTED_CRASH_AFTER_PAPER_FILL_COMMIT")
        return original_finish(prepared, outcome=outcome, occurred_at=occurred_at, error=error)

    try:
        monkeypatch.setattr(bridge, "finish_submission", fail_after_fill_before_finalization)
        result = runtime.start(
            proposal=proposal, context=context, policy=policy,
            canonical_account_state=state, account_id=state.account_id,
            created_at=BASE + timedelta(seconds=30), evidence_provider=evidence_provider,
        )
        assert result.status is FinancialExecutionRuntimeStatus.UNKNOWN
        assert result.reservation_id is not None
        before_close = adapter.snapshot()
        assert before_close["mode"] == "PAPER"
        assert before_close["fill_count"] == 1
        assert before_close["state_version"] == 1
        reservation_id = result.reservation_id
        fill = before_close["fills"][0]
        assert fill["client_order_id"]
        assert fill["quote_evidence_hash"]
        binding = bridge.get_binding(reservation_id)
        assert binding is not None and binding.state == "SUBMISSION_STARTED"
        assert store.get(reservation_id).state.value == "ACTIVE"
        ledger_record = ledger.get(binding.client_order_id)
        assert ledger_record is not None and ledger_record.result == "ORDER_RESULT_UNKNOWN"
        expected_balances = dict(before_close["balances"])
        expected_consumption = Decimal(fill["quantity"]) * Decimal(fill["price"]) + Decimal(fill["fee"])
    finally:
        ledger.close()
        store.close()
        adapter.close()

    # Reconstruct every store from its durable file; no in-memory first-run
    # object is allowed to participate in recovery.
    recovered_adapter = make_adapter(
        paper_path, account_id=template_state.account_id, clock=lambda: NOW
    )
    recovered_store = SQLiteReservationStore(reservation_path)
    recovered_bridge = ReservationExecutionBridge(recovered_store)
    recovered_ledger = SQLiteIdempotencyLedger(ledger_path)
    try:
        monkeypatch.setattr(
            recovered_adapter, "_submit",
            lambda *_args, **_kwargs: pytest.fail("reconciliation must never submit an order"),
        )
        first = reconcile_paper_runtime(
            adapter=recovered_adapter, store=recovered_store, bridge=recovered_bridge,
            ledger=recovered_ledger, occurred_at=NOW, apply=True,
        )
        assert first.status is PaperRuntimeReconciliationStatus.RECONCILED
        assert first.reconciled_fill_count == 1

        after_first = recovered_adapter.snapshot()
        reservation_after_first = recovered_store.get(reservation_id)
        binding_after_first = recovered_bridge.get_binding(reservation_id)
        ledger_after_first = recovered_ledger.get(fill["client_order_id"])
        transitions_after_first = recovered_store.transitions(reservation_id)
        assert after_first["fill_count"] == 1
        assert after_first["state_version"] == 1
        assert dict(after_first["balances"]) == expected_balances
        assert reservation_after_first.state.value == "RELEASED"
        assert reservation_after_first.consumed_amount == expected_consumption
        assert binding_after_first is not None and binding_after_first.state == "SUBMITTED"
        assert ledger_after_first is not None and ledger_after_first.result == "SUBMITTED"

        second = reconcile_paper_runtime(
            adapter=recovered_adapter, store=recovered_store, bridge=recovered_bridge,
            ledger=recovered_ledger, occurred_at=NOW + timedelta(seconds=1), apply=True,
        )
        assert second.status is PaperRuntimeReconciliationStatus.RECONCILED
        after_second = recovered_adapter.snapshot()
        reservation_after_second = recovered_store.get(reservation_id)
        binding_after_second = recovered_bridge.get_binding(reservation_id)
        ledger_after_second = recovered_ledger.get(fill["client_order_id"])
        assert after_second["fill_count"] == after_first["fill_count"] == 1
        assert after_second["state_version"] == after_first["state_version"] == 1
        assert dict(after_second["balances"]) == expected_balances
        assert reservation_after_second.consumed_amount == reservation_after_first.consumed_amount
        assert reservation_after_second.state == reservation_after_first.state
        assert binding_after_second.state == binding_after_first.state == "SUBMITTED"
        assert ledger_after_second.result == ledger_after_first.result == "SUBMITTED"
        assert recovered_store.transitions(reservation_id) == transitions_after_first
    finally:
        recovered_ledger.close()
        recovered_store.close()
        recovered_adapter.close()

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
                writer._submit(make_order(quantity="0.01", max_spend="2", client_order_id=f"concurrent-order-{index}"))
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



def test_paper_economic_replay_checks_quote_fee_and_balance_arithmetic(tmp_path):
    from copy import deepcopy
    from bot_obrero.paper_economic_replay import PaperEconomicReplayStatus, replay_paper_economics

    adapter = make_adapter(tmp_path / "economic-replay.sqlite3")
    try:
        adapter._submit(make_order())
        snapshot = adapter.snapshot()
        result = replay_paper_economics(snapshot)
        assert result.status is PaperEconomicReplayStatus.REPLAYED
        assert result.fill_count == 1
        assert result.final_balances == snapshot["balances"]

        corrupted = deepcopy(snapshot)
        corrupted["fills"][0]["price"] = "101"
        mismatch = replay_paper_economics(corrupted)
        assert mismatch.status is PaperEconomicReplayStatus.MISMATCH
        assert any("FILL_PRICE" in reason for reason in mismatch.discrepancies)
    finally:
        adapter.close()


def test_paper_economic_replay_marks_legacy_fills_without_quote_evidence_incomplete(tmp_path):
    from copy import deepcopy
    from bot_obrero.paper_economic_replay import PaperEconomicReplayStatus, replay_paper_economics

    adapter = make_adapter(tmp_path / "legacy-replay.sqlite3")
    try:
        adapter._submit(make_order())
        snapshot = deepcopy(adapter.snapshot())
        for field in ("quote_bid", "quote_ask", "quote_observed_at", "quote_available_at",
                      "quote_observation_id", "quote_observation_type", "quote_evidence_hash",
                      "evaluation_context_id"):
            snapshot["fills"][0][field] = ""
        result = replay_paper_economics(snapshot)
        assert result.status is PaperEconomicReplayStatus.INCOMPLETE
        assert result.discrepancies == ("fill[1]:QUOTE_OR_COST_EVIDENCE_MISSING",)
    finally:
        adapter.close()


def test_paper_adapter_exposes_read_only_economic_replay(tmp_path):
    from bot_obrero.paper_economic_replay import PaperEconomicReplayStatus

    adapter = make_adapter(tmp_path / "adapter-replay.sqlite3")
    try:
        adapter._submit(make_order())
        assert adapter.economic_replay().status is PaperEconomicReplayStatus.REPLAYED
    finally:
        adapter.close()


def test_canonical_market_signal_flows_through_risk_admission_and_paper_reconciliation(tmp_path):
    """End-to-end synthetic signal may submit only through the existing PAPER runtime."""
    from test_market_signal_pipeline import build_market_data, config as signal_config
    from bot_obrero.market_signal_paper_runtime import (
        MarketSignalPaperStatus,
        run_market_signal_to_paper_runtime,
    )
    from bot_obrero.market_signal_pipeline import STRATEGY_IDENTITY, STRATEGY_VERSION
    from bot_obrero.signal_trade_proposal_bridge import PaperProposalPolicy
    from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeSide

    decision_timestamp = BASE + timedelta(seconds=5)
    items = build_market_data(
        [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 11, 10, 11, 12, 100],
        now=decision_timestamp,
    )
    adapter = make_adapter(
        tmp_path / "signal-paper-account.sqlite3",
        account_id=make_state(available="100").account_id,
        clock=lambda: BASE,
    )
    state = canonical_account_state_from_paper_snapshot(adapter.snapshot())
    store = SQLiteReservationStore(tmp_path / "signal-paper-reservations.sqlite3")
    bridge = ReservationExecutionBridge(store)
    ledger = SQLiteIdempotencyLedger(tmp_path / "signal-paper-ledger.sqlite3")
    orchestrator = ExecutionOrchestrator(
        adapter=adapter,
        ledger=ledger,
        murphy_guard=MurphyGuard(),
        reservation_bridge=bridge,
        clock=lambda: NOW,
    )
    runtime = FinancialExecutionRuntime(
        store=store,
        risk_engine=RiskEngine(),
        risk_limit_resolver=RiskLimitApplicabilityResolver(),
        admission_boundary=FinancialAdmissionBoundary(store),
        reservation_bridge=bridge,
        execution_orchestrator=orchestrator,
    )
    proposal_policy = PaperProposalPolicy(
        direction_to_side={"UP": TradeSide.BUY},
        requested_quantity=Decimal("0.2"),
        order_type=TradeOrderType.MARKET,
        price_policy=PricePolicy.MARKET_REFERENCE,
        strategy_identity=STRATEGY_IDENTITY,
        strategy_version=STRATEGY_VERSION,
        max_quote_spend=Decimal("25"),
    )

    def context_factory(signal, proposal, pipeline, persisted_account_state):
        context = make_context(proposal, persisted_account_state)
        observation = MarketObservation(
            symbol=proposal.symbol,
            observation_timestamp=BASE,
            available_timestamp=BASE + timedelta(seconds=1),
            observation_type="BINANCE_SPOT_BOOK_TICKER",
            values={"bid": "99.9", "ask": "100"},
            provenance=Provenance(
                source="binance-public:deterministic-signal-paper-fixture",
                nature=ArtifactNature.OBSERVED,
                reference="signal-paper-quote-evidence",
            ),
            venue="BINANCE",
            observation_id="signal-paper-quote-observation",
        )
        availability = AvailabilityBinding(
            subject_kind=AvailabilitySubjectKind.MARKET_OBSERVATION,
            subject_id=observation.observation_id,
            available_at=observation.available_timestamp,
            source=observation.provenance.source,
            evidence_reference=observation.observation_id,
        )
        return replace(
            context,
            instrument=replace(context.instrument, market="SPOT"),
            market_observation=observation,
            analysis_result=pipeline.analysis_snapshot.results[0],
            signal=signal,
            availability_bindings=context.availability_bindings + (availability,),
        )

    def readiness_evidence_provider(prepared):
        binding = bridge.verify_prepared(prepared)
        correlation = binding.payload["correlation_id"]
        records = tuple(
            EvidenceRecord(
                kind=kind,
                value=True,
                source="deterministic-signal-paper-test",
                observed_at=decision_timestamp,
                decision_at=decision_timestamp,
                intent_id=prepared.client_order_id,
                correlation_id=correlation,
                expires_at=decision_timestamp + timedelta(minutes=5),
            )
            for kind in REQUIRED
        )
        return EvidenceBundle(records, correlation)

    try:
        result = run_market_signal_to_paper_runtime(
            items,
            decision_timestamp=decision_timestamp,
            proposal_policy=proposal_policy,
            runtime=runtime,
            risk_policy=make_policy(),
            context_factory=context_factory,
            created_at=BASE + timedelta(seconds=35),
            evidence_provider=readiness_evidence_provider,
            config=signal_config(),
            expected_symbol="BTC/USDT",
        )
        assert result.status is MarketSignalPaperStatus.RUNTIME_RETURNED
        assert result.pipeline_result.trade_proposal is not None
        assert result.runtime_result is not None
        assert result.runtime_result.status is FinancialExecutionRuntimeStatus.SUBMITTED
        snapshot = adapter.snapshot()
        assert snapshot["mode"] == "PAPER"
        assert snapshot["fill_count"] == 1
        assert snapshot["fills"][0]["proposal_id"] == result.pipeline_result.trade_proposal.proposal_id
        assert snapshot["fills"][0]["mode"] == "PAPER"
        assert snapshot["fills"][0]["quote_observation_id"] == "signal-paper-quote-observation"
        reconciliation = reconcile_paper_runtime(
            adapter=adapter, store=store, bridge=bridge, ledger=ledger, apply=True
        )
        assert reconciliation.status is PaperRuntimeReconciliationStatus.RECONCILED
        assert reconciliation.reconciled_fill_count == 1
    finally:
        ledger.close()
        store.close()
        adapter.close()


def test_no_trade_signal_never_calls_context_factory_or_financial_runtime():
    from test_market_signal_pipeline import build_market_data, config as signal_config
    from bot_obrero.market_signal_paper_runtime import (
        MarketSignalPaperStatus,
        run_market_signal_to_paper_runtime,
    )
    from bot_obrero.market_signal_pipeline import STRATEGY_IDENTITY, STRATEGY_VERSION
    from bot_obrero.signal_trade_proposal_bridge import PaperProposalPolicy
    from bot_obrero.trade_proposal import PricePolicy, TradeOrderType, TradeSide

    decision_timestamp = BASE + timedelta(seconds=5)
    items = build_market_data(
        [30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16],
        now=decision_timestamp,
    )

    def forbidden_context_factory(*args):
        raise AssertionError("NO_TRADE must not construct a financial risk context")

    result = run_market_signal_to_paper_runtime(
        items,
        decision_timestamp=decision_timestamp,
        proposal_policy=PaperProposalPolicy(
            direction_to_side={"UP": TradeSide.BUY},
            requested_quantity=Decimal("0.2"),
            order_type=TradeOrderType.MARKET,
            price_policy=PricePolicy.MARKET_REFERENCE,
            strategy_identity=STRATEGY_IDENTITY,
            strategy_version=STRATEGY_VERSION,
            max_quote_spend=Decimal("25"),
        ),
        runtime=object(),  # Must remain untouched because the pipeline returns NO_TRADE first.
        risk_policy=object(),
        context_factory=forbidden_context_factory,
        created_at=BASE + timedelta(seconds=35),
        evidence_provider=None,
        config=signal_config(),
        expected_symbol="BTC/USDT",
    )
    assert result.status is MarketSignalPaperStatus.NO_TRADE
    assert result.runtime_result is None
    assert result.pipeline_result.trade_proposal is None
