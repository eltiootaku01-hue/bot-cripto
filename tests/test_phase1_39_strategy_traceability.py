from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    ContractError,
    Hypothesis,
    Provenance,
    Signal,
    SignalValidity,
)
from bot_obrero.analysis_snapshot import AnalysisSnapshot
from bot_obrero.strategy_contracts import (
    StrategyCalculationConfig,
    StrategyContractError,
    StrategicArtifact,
    build_strategic_artifact,
)

DECISION = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
CREATED = DECISION
GENERATED = DECISION + timedelta(seconds=2)
EXPIRES = DECISION + timedelta(minutes=5)
OBSERVATION_IDS = ("obs-1", "obs-2")
DERIVED = Provenance("strategy-test", ArtifactNature.DERIVED)


def make_snapshot(snapshot_id: str = "snapshot-trace-001") -> AnalysisSnapshot:
    result = AnalysisResult(
        symbol="BTC/USDT",
        observation_ids=OBSERVATION_IDS,
        analysis_type="indicator.ema",
        values={"ema": Decimal("101")},
        calculated_at=DECISION + timedelta(seconds=1),
        decision_timestamp=DECISION,
        provenance=Provenance(
            source="analysis-engine",
            nature=ArtifactNature.DERIVED,
            metadata={
                "analysis_type": "indicator.ema",
                "algorithm_identity": "indicator.ema",
                "algorithm_version": "1.0.0",
                "effective_parameters": {"period": 5},
            },
        ),
    )
    return AnalysisSnapshot.from_results([result], snapshot_id=snapshot_id)


def make_config() -> StrategyCalculationConfig:
    return StrategyCalculationConfig(
        strategy_type="neutral-test",
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
        effective_configuration={"mode": "traceability"},
        effective_parameters={"window": "all"},
    )


def make_artifact(
    *,
    snapshot_id: str = "snapshot-trace-001",
) -> StrategicArtifact:
    return build_strategic_artifact(
        make_snapshot(snapshot_id),
        make_config(),
        values={"score": Decimal("12.5")},
    )


def make_hypothesis(artifact: StrategicArtifact) -> Hypothesis:
    return Hypothesis(
        symbol=artifact.symbol,
        supporting_analysis_ids=("analysis-001",),
        strategic_artifact_id=artifact.strategic_artifact_id,
        expected_direction="UNSPECIFIED",
        expected_horizon="UNSPECIFIED",
        invalidation_conditions=("manual-review",),
        created_at=CREATED,
        decision_timestamp=DECISION,
        expires_at=EXPIRES,
        provenance=DERIVED,
    )


def test_strategic_artifact_has_own_identity_and_does_not_reuse_snapshot_id():
    artifact = make_artifact()

    assert artifact.strategic_artifact_id
    assert artifact.strategic_artifact_id != artifact.snapshot_id


def test_strategic_artifact_id_is_generated_independently_for_each_artifact():
    first = make_artifact()
    second = make_artifact()

    assert first.snapshot_id == second.snapshot_id
    assert first.strategic_artifact_id != second.strategic_artifact_id


def test_strategic_artifact_rejects_snapshot_id_reuse():
    snapshot = make_snapshot(snapshot_id="same-id")
    with pytest.raises(StrategyContractError, match="must not reuse snapshot_id"):
        StrategicArtifact(
            snapshot_id="same-id",
            strategic_artifact_id="same-id",
            symbol=snapshot.symbol,
            decision_timestamp=DECISION,
            observation_ids=snapshot.observation_ids,
            strategy_type="neutral-test",
            strategy_identity="strategy.test",
            strategy_version="1.0.0",
            effective_configuration={},
            effective_parameters={},
            values={},
        )


def test_hypothesis_requires_explicit_strategic_artifact_id():
    with pytest.raises(ContractError, match="strategic_artifact_id"):
        Hypothesis(
            symbol="BTC/USDT",
            supporting_analysis_ids=("analysis-001",),
            strategic_artifact_id="",
            expected_direction="UNSPECIFIED",
            expected_horizon="UNSPECIFIED",
            invalidation_conditions=("manual-review",),
            created_at=CREATED,
            decision_timestamp=DECISION,
            provenance=DERIVED,
        )


def test_hypothesis_traces_exactly_one_strategic_artifact():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    assert hypothesis.strategic_artifact_id == artifact.strategic_artifact_id
    assert hypothesis.strategic_artifact_id != artifact.snapshot_id
    assert hypothesis.supporting_analysis_ids == ("analysis-001",)


def test_full_signal_chain_is_reconstructible_without_duplicate_ids():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    signal = Signal(
        symbol=hypothesis.symbol,
        hypothesis_id=hypothesis.hypothesis_id,
        direction="UNSPECIFIED",
        generated_at=GENERATED,
        decision_timestamp=GENERATED,
        expires_at=EXPIRES,
        validity=SignalValidity.VALID,
        provenance=DERIVED,
    )

    assert signal.hypothesis_id == hypothesis.hypothesis_id
    assert hypothesis.strategic_artifact_id == artifact.strategic_artifact_id
    assert artifact.snapshot_id != artifact.strategic_artifact_id
    assert signal.signal_id != hypothesis.hypothesis_id

    ids = {
        artifact.snapshot_id,
        artifact.strategic_artifact_id,
        hypothesis.hypothesis_id,
        signal.signal_id,
    }
    assert len(ids) == 4


def test_signal_needs_no_direct_strategic_artifact_reference():
    field_names = {field.name for field in fields(Signal)}

    assert "hypothesis_id" in field_names
    assert "strategic_artifact_id" not in field_names


def test_provenance_does_not_duplicate_the_explicit_artifact_reference():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    assert hypothesis.provenance.reference is None
    assert hypothesis.strategic_artifact_id == artifact.strategic_artifact_id


def test_traceability_preserves_temporal_fields():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    signal = Signal(
        symbol=hypothesis.symbol,
        hypothesis_id=hypothesis.hypothesis_id,
        direction="UNSPECIFIED",
        generated_at=GENERATED,
        decision_timestamp=GENERATED,
        expires_at=EXPIRES,
        validity=SignalValidity.VALID,
        provenance=DERIVED,
    )

    assert artifact.decision_timestamp == DECISION
    assert hypothesis.created_at == CREATED
    assert hypothesis.decision_timestamp == DECISION
    assert hypothesis.expires_at == EXPIRES
    assert signal.generated_at == GENERATED
    assert signal.decision_timestamp == GENERATED
    assert signal.expires_at == EXPIRES


def test_traceability_artifacts_are_immutable():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    signal = Signal(
        symbol=hypothesis.symbol,
        hypothesis_id=hypothesis.hypothesis_id,
        direction="UNSPECIFIED",
        generated_at=GENERATED,
        decision_timestamp=GENERATED,
        provenance=DERIVED,
    )

    with pytest.raises(FrozenInstanceError):
        artifact.strategic_artifact_id = "other"

    with pytest.raises(FrozenInstanceError):
        hypothesis.strategic_artifact_id = "other"

    with pytest.raises(FrozenInstanceError):
        signal.hypothesis_id = "other"


def test_traceability_does_not_require_changes_to_analysis_snapshot_identity():
    artifact = make_artifact(snapshot_id="snapshot-preserved")
    assert artifact.snapshot_id == "snapshot-preserved"
    assert artifact.strategic_artifact_id != artifact.snapshot_id
