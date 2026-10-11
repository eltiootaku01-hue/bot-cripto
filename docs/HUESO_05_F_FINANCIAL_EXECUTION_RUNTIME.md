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
- If the validation clock raises or returns a value that is not an aware `datetime`,
  the runtime returns `READINESS_BLOCKED` with reason
  `VALIDATION_CLOCK_UNAVAILABLE`; there is no fallback timestamp and the orchestrator
  is not called. The already prepared intent remains persisted as `PREPARED`, with no
  ledger entry claiming submission.
- In `resume()`, exceptions from either read of the persisted execution binding return
  `BLOCKED` with reason `EXECUTION_BINDING_LOOKUP_FAILED:<ExceptionType>`. Failure of
  the post-`prepare()` read stops before evidence evaluation or execution and does not
  recreate or infer a binding.

## Identity and idempotency

The original `proposal_id` is checked before evaluating risk. Once admitted,
`reservation_id` is the only supported resume identity. The runtime does not infer
identity from symbol, side, quantity, price, or other economic similarity. If the
original proposal identity is lost before a reservation ID is known, automatic discovery
is not provided.

Atomic admission remains owned by `SQLiteReservationStore.admit()`. The runtime does
not claim that concurrent risk evaluations share a stable `risk_decision_id`. Concurrent
`start()` calls for the same proposal are covered by a synchronized regression test:
one succeeds as `SUBMITTED`, the competing call returns `RESUME_REQUIRED` with the
same canonical `reservation_id`, and exactly one reservation and adapter send are
observed. The losing call does not auto-resume the existing reservation. Execution
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

### Baseline branch-push CI before isolated validation

- Run: [38136385546](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38136385546)
- Tested HEAD: `b36959ff68ad49314eb56725826cdaa20e9751ea`
- Conclusion: `success`
- PRE1 request-factory suite: 14 passed in 0.18s
- Financial-admission 02-H2 suite: 36 passed in 0.16s
- Final-admission 05-D suite: 44 passed in 0.39s
- Full suite: 1364 passed in 10.98s

### Isolated validation using temporary workflow instrumentation

- Actions run: [38139379169](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38139379169)
- Tested commit/HEAD: `5684f19e785346e63b48cb2edc1b61653d412059`
- Source/runtime/test baseline: `b36959ff68ad49314eb56725826cdaa20e9751ea`
- Temporary instrumentation commit: [5684f19](https://github.com/eltiootaku01-hue/bot-cripto/commit/5684f19e785346e63b48cb2edc1b61653d412059)
- Temporary commit diff: only `.github/workflows/tests.yml`; runtime and test files were unchanged.
- Actions conclusion: `success`; all seven pytest commands exited successfully.

Observed command results (exact command output from run 38139379169):

| Command | Result | Duration | Exit |
|---|---:|---:|---:|
| `pytest -q tests/test_hueso_05f_pre1_admission_request_factory.py` | 14 passed | 0.20s | 0 |
| `pytest -q tests/test_hueso_02h2_financial_admission.py` | 36 passed | 0.23s | 0 |
| `pytest -q tests/test_hueso_05d_final_admission.py` | 44 passed | 0.55s | 0 |
| `pytest -q tests/test_hueso_05f_financial_execution_runtime.py` | 30 passed | 0.69s | 0 |
| `pytest -q tests/test_hueso_05e_reservation_execution_bridge.py` | 33 passed | 0.71s | 0 |
| `pytest -q tests/test_phase14_execution_boundary.py` | 20 passed | 0.20s | 0 |
| `pytest -q` | 1364 passed | 9.83s | 0 |

The isolated run executed on the temporary instrumentation commit, not on the final restored HEAD. Its only source-tree difference from the baseline HEAD was the three additional individual test steps in the workflow. The workflow was restored to the original `main` blob `ab7dfd90392aed670b86b5287644af685d7db625` in the following restoration commit. The full-suite result on the final restored HEAD is tracked separately by that HEAD's push CI.

### Scope and evidence limits

These tests establish local regression results only. Tests using `FakeAdapter` are not evidence of real exchange orders. PR merge-preview checks and post-merge checks on `main` are distinct from branch-push CI. No production-readiness or architectural-approval conclusion is implied.

 
## HUESO 05-F-R1-C1 — corrective validation record

- Corrective implementation/test commit: [877a448](https://github.com/eltiootaku01-hue/bot-cripto/commit/877a448dfd287d457c702b77c1c87c81e4c220c0)
- Temporary CI-instrumentation commit: [8c2ed43](https://github.com/eltiootaku01-hue/bot-cripto/commit/8c2ed437a8b2b0ef3e2e06facceda2b509cf8b1f); its only changed file was `.github/workflows/tests.yml`.
- Isolated Actions run: [38143301041](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38143301041), tested HEAD `8c2ed437a8b2b0ef3e2e06facceda2b509cf8b1f`, conclusion `success`.
- Runtime and test code at the isolated HEAD were identical to corrective commit `877a448dfd287d457c702b77c1c87c81e4c220c0`; only the workflow had temporary extra test steps.
- Every individual command below exited 0 in run `38143301041`.

| Command | Observed result | Duration | Exit |
|---|---:|---:|---:|
| `pytest -q tests/test_hueso_05f_pre1_admission_request_factory.py` | 14 passed | 0.35s | 0 |
| `pytest -q tests/test_hueso_02h2_financial_admission.py` | 36 passed | 0.20s | 0 |
| `pytest -q tests/test_hueso_05d_final_admission.py` | 44 passed | 0.44s | 0 |
| `pytest -q tests/test_hueso_05f_financial_execution_runtime.py` | 32 passed | 0.69s | 0 |
| `pytest -q tests/test_hueso_05e_reservation_execution_bridge.py` | 33 passed | 0.61s | 0 |
| `pytest -q tests/test_phase14_execution_boundary.py` | 20 passed | 0.19s | 0 |
| `pytest -q` | 1366 passed | 9.66s | 0 |

The first non-instrumented branch-push run on corrective code, [38143237798](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38143237798), also completed successfully: `1366 passed in 8.13s`. The final restored HEAD's full-suite result will be recorded separately after restoration; the isolated test results above are attributed only to the instrumented SHA.

The clock-failure test confirms a typed `READINESS_BLOCKED` result, a persisted `PREPARED` execution binding, no adapter invocation, and no ledger send record. The resume binding-read test confirms the second read's `OSError` returns `BLOCKED`, does not call risk/admission/evidence, and leaves the reservation/binding persisted as `ACTIVE`/`PREPARED`. The synchronized concurrent-start test asserts exactly one canonical reservation, exactly one adapter call, one `SUBMITTED`, one `RESUME_REQUIRED`, and the same reservation ID in both results.

These results demonstrate local regression behavior only. No exchange acceptance, order execution, or fill is implied.
