from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.evidence_binding import (
    RiskLimitResolutionStatus,
)
from bot_obrero.risk_contracts import RiskLimit, RiskLimitSet
from bot_obrero.risk_limit_applicability import RiskLimitApplicabilityResolver


BASE = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
PROVENANCE = Provenance(
    source="synthetic-risk-fixture",
    nature=ArtifactNature.OBSERVED,
)


def make_limit(
    risk_limit_id,
    *,
    scope="ACCOUNT",
    metric="MAX_NOTIONAL",
    effective_from=BASE,
    effective_until=None,
    threshold="1000",
):
    return RiskLimit(
        risk_limit_id=risk_limit_id,
        scope=scope,
        metric=metric,
        threshold=Decimal(threshold),
        unit="USDT",
        effective_from=effective_from,
        effective_until=effective_until,
        provenance=PROVENANCE,
    )


def make_set(*limits):
    return RiskLimitSet(
        risk_limit_set_id="limits-1",
        limits=tuple(limits),
        as_of=BASE,
        provenance=PROVENANCE,
    )


def test_exact_scope_and_metric_match_is_available():
    resolver = RiskLimitApplicabilityResolver()
    result = resolver.resolve(
        make_set(make_limit("limit-1")),
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    assert result.status is RiskLimitResolutionStatus.AVAILABLE
    assert result.risk_limit_set_id == "limits-1"
    assert result.applicable_limit_ids == ("limit-1",)


def test_scope_and_metric_are_exact_no_fallback():
    resolver = RiskLimitApplicabilityResolver()
    limits = make_set(
        make_limit("limit-account"),
        make_limit("limit-symbol", scope="BTC/USDT"),
        make_limit("limit-other-metric", metric="MAX_DRAWDOWN"),
    )
    result = resolver.resolve(
        limits,
        scope="ACCOUNT",
        metric="NON_EXISTENT_METRIC",
        evaluation_timestamp=BASE,
    )
    assert result.status is RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT


def test_effective_from_is_inclusive():
    result = RiskLimitApplicabilityResolver().resolve(
        make_set(make_limit("limit-1", effective_from=BASE)),
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    assert result.status is RiskLimitResolutionStatus.AVAILABLE


def test_effective_until_is_exclusive():
    until = BASE + timedelta(hours=1)
    result = RiskLimitApplicabilityResolver().resolve(
        make_set(make_limit("limit-1", effective_until=until)),
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=until,
    )
    assert result.status is RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT


def test_no_applicable_limit_is_explicit():
    result = RiskLimitApplicabilityResolver().resolve(
        make_set(make_limit("limit-1")),
        scope="SYMBOL",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    assert result.status is RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT
    assert result.risk_limit_set_id == "limits-1"
    assert result.applicable_limit_ids == ()


def test_unavailable_set_is_explicit():
    result = RiskLimitApplicabilityResolver().resolve(
        None,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    assert result.status is RiskLimitResolutionStatus.LIMITS_UNAVAILABLE
    assert result.risk_limit_set_id is None
    assert result.applicable_limit_ids == ()


def test_overlapping_limits_are_incomplete():
    result = RiskLimitApplicabilityResolver().resolve(
        make_set(
            make_limit("limit-a"),
            make_limit("limit-b"),
        ),
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    assert result.status is RiskLimitResolutionStatus.LIMITS_INCOMPLETE
    assert result.risk_limit_set_id == "limits-1"
    assert result.applicable_limit_ids == ()


def test_duplicate_logical_applicability_is_incomplete():
    until = BASE + timedelta(hours=1)
    result = RiskLimitApplicabilityResolver().resolve(
        make_set(
            make_limit("limit-a", effective_until=until, threshold="1000"),
            make_limit("limit-b", effective_until=until, threshold="900"),
        ),
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE + timedelta(minutes=30),
    )
    assert result.status is RiskLimitResolutionStatus.LIMITS_INCOMPLETE


def test_deterministic_resolution_for_equal_inputs():
    limit_set = make_set(
        make_limit("limit-1"),
    )
    resolver = RiskLimitApplicabilityResolver()
    first = resolver.resolve(
        limit_set,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    second = resolver.resolve(
        limit_set,
        scope="ACCOUNT",
        metric="MAX_NOTIONAL",
        evaluation_timestamp=BASE,
    )
    assert first == second


def test_risk_limit_set_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="duplicate"):
        make_set(
            make_limit("limit-1"),
            make_limit("limit-1"),
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 10, 6, 12, 0),
    ],
)
def test_evaluation_timestamp_must_be_timezone_aware(timestamp):
    with pytest.raises(ValueError, match="timezone-aware"):
        RiskLimitApplicabilityResolver().resolve(
            make_set(make_limit("limit-1")),
            scope="ACCOUNT",
            metric="MAX_NOTIONAL",
            evaluation_timestamp=timestamp,
        )


def test_scope_and_metric_must_be_non_empty_strings():
    resolver = RiskLimitApplicabilityResolver()
    with pytest.raises(ValueError):
        resolver.resolve(
            make_set(make_limit("limit-1")),
            scope="",
            metric="MAX_NOTIONAL",
            evaluation_timestamp=BASE,
        )
    with pytest.raises(ValueError):
        resolver.resolve(
            make_set(make_limit("limit-1")),
            scope="ACCOUNT",
            metric="",
            evaluation_timestamp=BASE,
        )


def test_resolver_does_not_access_provider_or_runtime_surfaces():
    source = __import__("pathlib").Path(
        "bot_obrero/risk_limit_applicability.py"
    ).read_text(encoding="utf-8")
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
