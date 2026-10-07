from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.evidence_binding import (
    AvailabilityBinding,
    AvailabilitySubjectKind,
    RiskLimitResolutionStatus,
)
from bot_obrero.risk_contracts import RiskEvidenceRef
from bot_obrero.risk_evaluation_policy import (
    ApprovalEvidencePolicy,
    RiskEvaluationPolicy,
    ValuationFreshnessPolicy,
    ValuationFreshnessStatus,
)

BASE = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def make_policy(**overrides):
    values = {
        "policy_id": "risk-policy-v1",
        "policy_version": "1.0.0",
        "required_availability_subjects": (
            AvailabilitySubjectKind.ACCOUNT_STATE,
            AvailabilitySubjectKind.RISK_LIMIT_SET,
        ),
        "require_context_complete": True,
        "require_risk_evidence_for_approval": True,
        "max_valuation_age": timedelta(minutes=5),
    }
    values.update(overrides)
    return RiskEvaluationPolicy(**values)


def make_evidence(reference_id="evidence-1"):
    return RiskEvidenceRef(
        kind="ACCOUNT_STATE",
        reference_id=reference_id,
        as_of=BASE,
    )


def make_binding(kind, subject_id, available_at=BASE):
    return AvailabilityBinding(
        subject_kind=kind,
        subject_id=subject_id,
        available_at=available_at,
        source="synthetic",
        evidence_reference=f"ref-{subject_id}",
    )


def test_policy_is_immutable():
    policy = make_policy()
    with pytest.raises(FrozenInstanceError):
        policy.policy_id = "changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_id", ""),
        ("policy_version", ""),
        ("require_context_complete", 1),
        ("require_risk_evidence_for_approval", 1),
        ("max_valuation_age", timedelta(seconds=-1)),
        (
            "required_availability_subjects",
            (AvailabilitySubjectKind.ACCOUNT_STATE, AvailabilitySubjectKind.ACCOUNT_STATE),
        ),
        ("required_availability_subjects", ("ACCOUNT_STATE",)),
    ],
)
def test_invalid_policy_configuration_is_rejected(field, value):
    overrides = {field: value}
    with pytest.raises((TypeError, ValueError)):
        make_policy(**overrides)


def test_policy_identity_and_version_are_explicit():
    policy = make_policy()
    assert policy.policy_id == "risk-policy-v1"
    assert policy.policy_version == "1.0.0"


def test_policy_exposes_explicit_availability_requirements():
    policy = make_policy()
    assert policy.required_availability_subjects == (
        AvailabilitySubjectKind.ACCOUNT_STATE,
        AvailabilitySubjectKind.RISK_LIMIT_SET,
    )


@pytest.mark.parametrize(
    ("valuation_as_of", "evaluation_timestamp", "expected"),
    [
        (BASE, BASE + timedelta(minutes=5), ValuationFreshnessStatus.FRESH),
        (BASE, BASE + timedelta(minutes=5, seconds=1), ValuationFreshnessStatus.STALE),
        (None, BASE, ValuationFreshnessStatus.UNKNOWN),
        (BASE, None, ValuationFreshnessStatus.UNKNOWN),
        (
            BASE + timedelta(minutes=1),
            BASE,
            ValuationFreshnessStatus.UNKNOWN,
        ),
    ],
)
def test_valuation_freshness_policy_is_deterministic(
    valuation_as_of,
    evaluation_timestamp,
    expected,
):
    policy = ValuationFreshnessPolicy(timedelta(minutes=5))
    assert (
        policy.evaluate(
            valuation_as_of=valuation_as_of,
            evaluation_timestamp=evaluation_timestamp,
        )
        is expected
    )


def test_valuation_freshness_without_max_age_is_structurally_fresh_when_timestamps_exist():
    policy = ValuationFreshnessPolicy(None)
    assert (
        policy.evaluate(
            valuation_as_of=BASE,
            evaluation_timestamp=BASE + timedelta(days=30),
        )
        is ValuationFreshnessStatus.FRESH
    )


@pytest.mark.parametrize("value", [BASE.replace(tzinfo=None)])
def test_valuation_timestamps_must_be_timezone_aware(value):
    with pytest.raises(ValueError, match="timezone-aware"):
        ValuationFreshnessPolicy(timedelta(minutes=5)).evaluate(
            valuation_as_of=value,
            evaluation_timestamp=BASE,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        ValuationFreshnessPolicy(timedelta(minutes=5)).evaluate(
            valuation_as_of=BASE,
            evaluation_timestamp=value,
        )


def test_approval_evidence_policy_requires_explicit_evidence():
    policy = ApprovalEvidencePolicy(True)
    assert policy.has_minimum_evidence(()) is False
    assert policy.has_minimum_evidence(None) is False
    assert policy.has_minimum_evidence((make_evidence(),)) is True
    assert policy.has_minimum_evidence((object(),)) is False


def test_approval_evidence_policy_can_explicitly_disable_requirement():
    policy = ApprovalEvidencePolicy(False)
    assert policy.has_minimum_evidence(()) is True


def test_availability_requirements_are_fail_closed():
    policy = make_policy()

    complete = (
        make_binding(AvailabilitySubjectKind.ACCOUNT_STATE, "account-1"),
        make_binding(AvailabilitySubjectKind.RISK_LIMIT_SET, "limits-1"),
    )
    assert policy.availability_requirements_satisfied(
        bindings=complete,
        evaluation_timestamp=BASE + timedelta(minutes=1),
    )

    missing = (complete[0],)
    assert not policy.availability_requirements_satisfied(
        bindings=missing,
        evaluation_timestamp=BASE + timedelta(minutes=1),
    )

    future = (
        complete[0],
        make_binding(
            AvailabilitySubjectKind.RISK_LIMIT_SET,
            "limits-1",
            BASE + timedelta(minutes=2),
        ),
    )
    assert not policy.availability_requirements_satisfied(
        bindings=future,
        evaluation_timestamp=BASE + timedelta(minutes=1),
    )


def test_approval_prerequisites_fail_closed_for_unknown_states():
    policy = make_policy()
    evidence = (make_evidence(),)

    assert not policy.approval_prerequisites_satisfied(
        context_complete=False,
        risk_limit_status=RiskLimitResolutionStatus.AVAILABLE,
        risk_evidence=evidence,
        valuation_freshness=ValuationFreshnessStatus.FRESH,
    )
    assert not policy.approval_prerequisites_satisfied(
        context_complete=True,
        risk_limit_status=RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
        risk_evidence=evidence,
        valuation_freshness=ValuationFreshnessStatus.FRESH,
    )
    assert not policy.approval_prerequisites_satisfied(
        context_complete=True,
        risk_limit_status=RiskLimitResolutionStatus.AVAILABLE,
        risk_evidence=evidence,
        valuation_freshness=ValuationFreshnessStatus.UNKNOWN,
    )
    assert not policy.approval_prerequisites_satisfied(
        context_complete=True,
        risk_limit_status=RiskLimitResolutionStatus.AVAILABLE,
        risk_evidence=(),
        valuation_freshness=ValuationFreshnessStatus.FRESH,
    )


def test_approval_prerequisites_are_true_only_when_all_policy_gates_are_satisfied():
    policy = make_policy()
    assert policy.approval_prerequisites_satisfied(
        context_complete=True,
        risk_limit_status=RiskLimitResolutionStatus.AVAILABLE,
        risk_evidence=(make_evidence(),),
        valuation_freshness=ValuationFreshnessStatus.FRESH,
    )


def test_provider_and_runtime_static_safety():
    module_path = "bot_obrero/risk_evaluation_policy.py"
    source = __import__("pathlib").Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_text = (
        "binance",
        "requests",
        "httpx",
        "websocket",
        "RiskEngine",
        "RiskAuthorization",
        "FinalAdmission",
        "ExecutionBoundary",
        "ExchangeAdapter",
    )
    lowered = source.lower()
    assert all(item.lower() not in lowered for item in forbidden_text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert not (
                isinstance(node.func, ast.Name)
                and node.func.id == "float"
            )
            if isinstance(node.func, ast.Attribute):
                assert not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "datetime"
                    and node.func.attr == "now"
                )


def test_policy_has_no_decision_production_surface():
    source = __import__("pathlib").Path(
        "bot_obrero/risk_evaluation_policy.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assert "RiskEngine" not in class_names
    assert "RiskAuthorization" not in class_names
    assert "FinalAdmission" not in class_names
