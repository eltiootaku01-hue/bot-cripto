from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    ContractError,
    Provenance,
    Hypothesis,
)
from bot_obrero.analysis_snapshot import AnalysisSnapshot
from bot_obrero.strategy_contracts import (
    StrategyCalculationConfig,
    StrategyContractError,
    StrategicArtifact,
    build_hypothesis,
    build_strategic_artifact,
)


BASE = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)
DECISION = BASE
CREATED = DECISION - timedelta(seconds=1)
EXPIRES = DECISION + timedelta(minutes=5)


def make_snapshot(snapshot_id: str = "snapshot-hypothesis-001") -> AnalysisSnapshot:
    results = []
    for analysis_type in ("indicator.ema", "indicator.rsi", "indicator.sma"):
        results.append(
            AnalysisResult(
                symbol="BTC/USDT",
                observation_ids=("obs-1", "obs-2", "obs-3"),
                analysis_type=analysis_type,
                values={"value": Decimal("101")},
                calculated_at=DECISION + timedelta(seconds=1),
                decision_timestamp=DECISION,
                provenance=Provenance(
                    source="analysis-engine",
                    nature=ArtifactNature.DERIVED,
                    reference=None,
                    metadata={
                        "analysis_type": analysis_type,
                        "algorithm_identity": analysis_type,
                        "algorithm_version": "1.0.0",
                    },
                ),
            )
        )
    return AnalysisSnapshot.from_results(results, snapshot_id=snapshot_id)


def make_artifact(snapshot: AnalysisSnapshot | None = None) -> StrategicArtifact:
    source = make_snapshot() if snapshot is None else snapshot
    configuration = StrategyCalculationConfig(
        strategy_type="neutral-test",
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
        effective_configuration={"mode": "trace"},
        effective_parameters={"window": "ALL"},
    )
    return build_strategic_artifact(
        source,
        configuration,
        values={"score": Decimal("12.5")},
    )


def make_provenance() -> Provenance:
    return Provenance(
        source="strategy-producer",
        nature=ArtifactNature.DERIVED,
        reference="phase1.40-test",
        metadata={"producer": "build_hypothesis"},
    )


def build(
    artifact: StrategicArtifact,
    *,
    expected_direction="TEST_DIRECTION",
    expected_horizon="TEST_HORIZON",
    invalidation_conditions=("condition-a", "condition-b"),
    created_at=CREATED,
    provenance=None,
    expires_at=EXPIRES,
    status="UNRESOLVED",
):
    return build_hypothesis(
        artifact,
        expected_direction=expected_direction,
        expected_horizon=expected_horizon,
        invalidation_conditions=invalidation_conditions,
        created_at=created_at,
        provenance=make_provenance() if provenance is None else provenance,
        expires_at=expires_at,
        status=status,
    )


def logical_fields(hypothesis: Hypothesis) -> tuple:
    return (
        hypothesis.symbol,
        hypothesis.strategic_artifact_id,
        hypothesis.supporting_analysis_ids,
        hypothesis.expected_direction,
        hypothesis.expected_horizon,
        hypothesis.invalidation_conditions,
        hypothesis.created_at,
        hypothesis.decision_timestamp,
        hypothesis.provenance,
        hypothesis.expires_at,
        hypothesis.status,
    )


def test_build_hypothesis_inherits_exact_artifact_evidence():
    artifact = make_artifact()
    hypothesis = build(artifact)

    assert hypothesis.strategic_artifact_id == artifact.strategic_artifact_id
    assert hypothesis.supporting_analysis_ids == artifact.supporting_analysis_ids
    assert hypothesis.symbol == artifact.symbol
    assert hypothesis.decision_timestamp == artifact.decision_timestamp
    assert hypothesis.expected_direction == "TEST_DIRECTION"
    assert hypothesis.expected_horizon == "TEST_HORIZON"
    assert hypothesis.invalidation_conditions == ("condition-a", "condition-b")
    assert hypothesis.created_at == CREATED
    assert hypothesis.expires_at == EXPIRES
    assert hypothesis.status == "UNRESOLVED"


def test_full_traceability_reaches_analysis_snapshot_without_relookup():
    snapshot = make_snapshot("snapshot-trace-001")
    artifact = make_artifact(snapshot)
    hypothesis = build(artifact)

    expected_analysis_ids = tuple(
        result.analysis_id for result in snapshot.results
    )

    assert hypothesis.strategic_artifact_id == artifact.strategic_artifact_id
    assert hypothesis.supporting_analysis_ids == artifact.supporting_analysis_ids
    assert hypothesis.supporting_analysis_ids == expected_analysis_ids
    assert artifact.snapshot_id == snapshot.snapshot_id


def test_supporting_analysis_ids_are_exactly_the_artifact_values():
    artifact = make_artifact()
    hypothesis = build(artifact)

    assert hypothesis.supporting_analysis_ids == artifact.supporting_analysis_ids
    assert isinstance(hypothesis.supporting_analysis_ids, tuple)


def test_direction_and_horizon_are_preserved_without_operational_normalization():
    artifact = make_artifact()

    hypothesis = build(
        artifact,
        expected_direction="UNSPECIFIED",
        expected_horizon="TEST/HORIZON",
    )

    assert hypothesis.expected_direction == "UNSPECIFIED"
    assert hypothesis.expected_horizon == "TEST/HORIZON"


@pytest.mark.parametrize("field", ["expected_direction", "expected_horizon"])
def test_empty_direction_or_horizon_is_rejected(field):
    artifact = make_artifact()
    kwargs = {
        "expected_direction": "TEST_DIRECTION",
        "expected_horizon": "TEST_HORIZON",
    }
    kwargs[field] = "   "

    with pytest.raises(ContractError):
        build(artifact, **kwargs)


def test_invalidation_conditions_are_copied_to_an_immutable_tuple():
    artifact = make_artifact()
    conditions = ["condition-a", "condition-b"]

    hypothesis = build(artifact, invalidation_conditions=conditions)

    conditions.append("condition-c")

    assert hypothesis.invalidation_conditions == (
        "condition-a",
        "condition-b",
    )
    with pytest.raises(TypeError):
        hypothesis.invalidation_conditions[0] = "other"


@pytest.mark.parametrize(
    "value",
    [
        "not-a-sequence",
        b"not-a-sequence",
        {"condition": "value"},
        [1],
    ],
)
def test_invalid_invalidation_conditions_are_rejected(value):
    artifact = make_artifact()

    with pytest.raises(StrategyContractError):
        build(artifact, invalidation_conditions=value)


def test_wrong_input_type_is_rejected():
    with pytest.raises(StrategyContractError, match="StrategicArtifact"):
        build_hypothesis(
            object(),
            expected_direction="TEST_DIRECTION",
            expected_horizon="TEST_HORIZON",
            invalidation_conditions=(),
            created_at=CREATED,
            provenance=make_provenance(),
        )


def test_non_derived_provenance_is_rejected():
    artifact = make_artifact()
    provenance = Provenance(
        source="test",
        nature=ArtifactNature.OBSERVED,
        reference="observed",
    )

    with pytest.raises(StrategyContractError, match="DERIVED"):
        build(artifact, provenance=provenance)


def test_wrong_provenance_type_is_rejected():
    artifact = make_artifact()

    with pytest.raises(StrategyContractError, match="Provenance"):
        build(artifact, provenance=object())


def test_provenance_is_preserved_without_relationship_duplication():
    artifact = make_artifact()
    provenance = Provenance(
        source="strategy-producer",
        nature=ArtifactNature.DERIVED,
        reference="external-reference",
        metadata={"kind": "test"},
    )

    hypothesis = build(artifact, provenance=provenance)

    assert hypothesis.provenance is provenance
    assert hypothesis.provenance.reference == "external-reference"
    assert "strategic_artifact_id" not in hypothesis.provenance.metadata
    assert "snapshot_id" not in hypothesis.provenance.metadata
    assert "analysis_id" not in hypothesis.provenance.metadata


def test_created_at_after_decision_timestamp_is_rejected():
    artifact = make_artifact()

    with pytest.raises(ContractError, match="created_at"):
        build(
            artifact,
            created_at=DECISION + timedelta(microseconds=1),
        )


@pytest.mark.parametrize(
    "expires_at",
    [
        DECISION,
        DECISION - timedelta(microseconds=1),
    ],
)
def test_invalid_expiration_is_rejected(expires_at):
    artifact = make_artifact()

    with pytest.raises(ContractError, match="expires_at"):
        build(artifact, expires_at=expires_at)


def test_expiration_none_is_preserved():
    artifact = make_artifact()

    hypothesis = build(artifact, expires_at=None)

    assert hypothesis.expires_at is None


def test_status_is_explicit_and_passthrough():
    artifact = make_artifact()

    hypothesis = build(artifact, status="CUSTOM_STATUS")

    assert hypothesis.status == "CUSTOM_STATUS"


def test_hypothesis_is_immutable():
    artifact = make_artifact()
    hypothesis = build(artifact)

    with pytest.raises(FrozenInstanceError):
        hypothesis.expected_direction = "OTHER"

    with pytest.raises(FrozenInstanceError):
        hypothesis.decision_timestamp = DECISION


def test_build_hypothesis_does_not_mutate_artifact():
    artifact = make_artifact()
    snapshot = (
        artifact.snapshot_id,
        artifact.strategic_artifact_id,
        artifact.symbol,
        artifact.decision_timestamp,
        artifact.observation_ids,
        artifact.supporting_analysis_ids,
        artifact.strategy_type,
        artifact.strategy_identity,
        artifact.strategy_version,
        artifact.effective_configuration,
        artifact.effective_parameters,
        artifact.values,
    )

    build(
        artifact,
        invalidation_conditions=["condition-a", "condition-b"],
    )

    assert (
        artifact.snapshot_id,
        artifact.strategic_artifact_id,
        artifact.symbol,
        artifact.decision_timestamp,
        artifact.observation_ids,
        artifact.supporting_analysis_ids,
        artifact.strategy_type,
        artifact.strategy_identity,
        artifact.strategy_version,
        artifact.effective_configuration,
        artifact.effective_parameters,
        artifact.values,
    ) == snapshot


def test_logical_determinism_excludes_generated_hypothesis_id():
    artifact = make_artifact()
    provenance = make_provenance()

    first = build(
        artifact,
        expected_direction="TEST_DIRECTION",
        expected_horizon="TEST_HORIZON",
        invalidation_conditions=("condition-a", "condition-b"),
        created_at=CREATED,
        provenance=provenance,
        expires_at=EXPIRES,
        status="UNRESOLVED",
    )
    second = build(
        artifact,
        expected_direction="TEST_DIRECTION",
        expected_horizon="TEST_HORIZON",
        invalidation_conditions=("condition-a", "condition-b"),
        created_at=CREATED,
        provenance=provenance,
        expires_at=EXPIRES,
        status="UNRESOLVED",
    )

    assert first.hypothesis_id != second.hypothesis_id
    assert logical_fields(first) == logical_fields(second)


def test_producer_does_not_use_signal_or_operational_direction_vocabulary():
    artifact = make_artifact()
    hypothesis = build(
        artifact,
        expected_direction="UNSPECIFIED",
        expected_horizon="TEST_HORIZON",
    )

    assert hypothesis.expected_direction == "UNSPECIFIED"
    assert not hasattr(hypothesis, "signal_id")


def test_created_at_is_injected_and_not_clock_derived():
    artifact = make_artifact()
    earlier = DECISION - timedelta(hours=1)

    first = build(artifact, created_at=earlier)
    second = build(artifact, created_at=CREATED)

    assert first.created_at == earlier
    assert second.created_at == CREATED
    assert first.created_at != second.created_at
