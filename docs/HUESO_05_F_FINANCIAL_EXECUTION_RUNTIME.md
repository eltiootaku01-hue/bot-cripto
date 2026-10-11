# HUESO 05-F — Reservation-Anchored Financial Execution Runtime v1.0

## Scope

This module composes the existing local risk, authorization, admission, reservation,
execution bridge, orchestrator, idempotency ledger and `ExchangeAdapter` contract.
It is a local runtime only. It does not implement external exchange reconciliation,
production automation, exchange credentials, or live order submission.

## Composition

```text
TradeProposal + RiskEvaluationContext + RiskEvaluationPolicy
        |
        v
RiskEngine.evaluate
        | APPROVED only
        v
authorize_risk_decision
        | AUTHORIZED only
        v
build_financial_admission_request
        |
        v
FinancialAdmissionBoundary.admit
        | ADMITTED for this invocation only
        v
SQLiteReservationStore (canonical snapshot + authorization binding)
        |
        v
ReservationExecutionBridge.prepare / verify_prepared
        |
        v
Explicit EvidenceProvider(PreparedExecutionIntent)
        |
        v
ExecutionOrchestrator.execute
        |
        v
ExecutionBoundary -> FakeAdapter in tests
```

## Public API

- `FinancialExecutionRuntime(...)`: requires one shared `SQLiteReservationStore`,
  `RiskEngine`, `RiskLimitApplicabilityResolver`, `FinancialAdmissionBoundary`,
  `ReservationExecutionBridge`, and `ExecutionOrchestrator`. Construction fails if
  admission and bridge use different stores or the orchestrator uses another bridge.
- `start(...)`: requires proposal, matching context/policy/account snapshot, account ID,
  aware `created_at`, and explicit `evidence_provider(prepared_intent)`. It looks up
  `list_for_proposal(proposal_id)` before risk evaluation. Any existing reservation
  requires explicit resume; only a fresh `ADMITTED` result continues to preparation.
- `resume(reservation_id, evidence_provider=...)`: resolves only the supplied persisted
  reservation ID. It does not re-evaluate risk, re-authorize, or re-admit.
- `FinancialExecutionRuntimeResult`: immutable typed status, non-empty reason, optional
  proposal/reservation IDs, and optional local `ExecutionDecision`.

## Conservative state handling

- `REJECTED` risk returns `RISK_REJECTED`; `UNKNOWN` or malformed risk returns
  `RISK_UNKNOWN`. Neither reaches admission.
- Only an `AUTHORIZED` result can reach the public admission factory.
- `ALREADY_ADMITTED`, an admission race, or a previously found proposal reservation
  returns `RESUME_REQUIRED`; it never starts execution automatically.
- `PREPARED` / `ALREADY_PREPARED` plus valid explicit evidence can reach the orchestrator.
  `CONFLICT`, `BLOCKED`, `REJECTED`, and bridge `UNKNOWN` stop the new-start path.
- Missing, contradictory, stale, future, wrongly bound or provider-failed evidence does
  not call `ExecutionOrchestrator.execute`; the persisted intent remains `PREPARED`
  unless a pre-existing state says otherwise.
- Resume of `SUBMITTED` returns `ALREADY_SUBMITTED`; it does not send again.
- Resume of `SUBMISSION_STARTED` or `UNKNOWN` invokes only the existing local
  `recover_projection(reservation_id)` path and returns `UNKNOWN`. It does not retry.
- Store, snapshot, authorization binding, or intent-integrity failures stop the flow.

## Identity and idempotency

The original `proposal_id` is checked before evaluating risk. Once admitted,
`reservation_id` is the only supported resume identity. The runtime does not infer
identity from symbol, side, quantity, price, or other economic similarity. If the
original proposal identity is lost before a reservation ID is known, automatic discovery
is not provided.

Atomic admission remains owned by `SQLiteReservationStore.admit()`. The runtime does
not claim that concurrent risk evaluations share a stable `risk_decision_id`. Execution
idempotency and the one-send boundary remain owned by the existing bridge, orchestrator,
execution boundary and SQLite ledger. No retry loop is added here.

## Interpretation limits

- `SUBMITTED` is the local adapter/orchestrator result, not proof of exchange acceptance,
  order execution, or fill.
- Recovery is limited to local persistence and projection repair.
- This phase does not implement external reconciliation with Binance.
- Tests using `FakeAdapter` are not evidence of real exchange orders.
- Hashes in existing persistence contracts are deterministic consistency bindings, not
  cryptographic signatures.
- Evidence is supplied explicitly by the caller. A syntactically valid bundle does not
  independently prove source authenticity or real-world coverage, including reconciliation.
- The runtime does not establish autonomous production readiness.
- Loss of the original identity before retaining `reservation_id` remains unresolved.

## Validation record

Observed branch-push CI:
- Run: [38136340861](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38136237545)
- Tested HEAD: `c5235a411b45f5738bca9ae86707e453b0951a1d`
- Conclusion: `success`
- PRE1 request-factory suite: 14 passed in 0.16s
- Financial-admission 02-H2 suite: 36 passed in 0.18s
- Final-admission 05-D suite: 44 passed in 0.39s
- Full suite, including the new runtime tests: 1364 passed in 12.97s

The existing protected workflow runs those three named legacy suites individually and then
runs `pytest -q`. It does not invoke the new runtime test file, bridge suite, or execution
boundary suite as separate commands. Those tests are included in the successful full suite,
but their isolated command durations have **not** been observed. The workflow was not
modified because workflow changes are outside this phase's authorization.

Required isolated commands for a future authorized validation run:

```bash
pytest -q tests/test_hueso_05f_financial_execution_runtime.py
pytest -q tests/test_hueso_05f_pre1_admission_request_factory.py
pytest -q tests/test_hueso_05e_reservation_execution_bridge.py
pytest -q tests/test_hueso_02h2_financial_admission.py
pytest -q tests/test_hueso_05d_final_admission.py
pytest -q tests/test_phase14_execution_boundary.py
pytest -q
```

CI evidence must identify the exact branch HEAD SHA. PR merge-preview checks and post-merge
checks on `main` are distinct from branch-push CI. No production-readiness conclusion is
implied by these tests.
