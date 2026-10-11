# HUESO 05-F-PRE1 — Public Financial Admission Request Factory v1.0

## Purpose

The runtime composition review found that callers outside `financial_admission.py` would otherwise need to duplicate the private economic-term derivation or depend on a private helper. This phase adds a narrow public factory and does not implement the integrated financial execution runtime.

## Public interface

`build_financial_admission_request(...)` accepts explicit keyword-only values: `proposal`, `risk_decision`, `context`, `policy`, `authorization`, `account_id`, `canonical_account_state`, and timezone-aware `created_at`.

It derives resource kind, asset, and reserved amount by calling the existing `_required_reservation_terms` helper inside its defining module; computes the existing semantic authorization fingerprint; constructs the existing v2 admission idempotency key; takes evidence from `risk_decision.risk_evidence`; and returns a `FinancialAdmissionRequest`. The request's existing constructor and `validate()` remain responsible for validating all bindings and terms. Validation errors propagate; no partial request is returned.

The factory is a construction helper only. It does not evaluate risk, authorize decisions, admit reservations, change the boundary, or call an exchange.

## Validation

Tests are added in `tests/test_hueso_05f_pre1_admission_request_factory.py` for valid construction, canonical economic terms across BUY/SELL MARKET/LIMIT, authorization/account/time/evidence inconsistencies, and local admission through the existing boundary.

Execution status is intentionally not asserted in this document until GitHub Actions reports actual results. Required commands:
- `pytest -q tests/test_hueso_05f_pre1_admission_request_factory.py`
- `pytest -q tests/test_hueso_02h2_financial_admission.py`
- `pytest -q tests/test_hueso_05d_final_admission.py`
- `pytest -q`

## Limitations

This phase does not implement the integrated financial execution runtime, does not guarantee idempotent retries before admission, and does not establish external exchange reconciliation. No real exchange orders or credentials are used.
