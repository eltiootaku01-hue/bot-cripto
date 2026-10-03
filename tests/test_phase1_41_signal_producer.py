from dataclasses import FrozenInstanceError
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
    build_hypothesis,
    build_signal,
    build_strategic_artifact,
)


DECISION = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
CREATED = DECISION - timedelta(seconds=1)
GENERATED = DECISION + timedelta(seconds=2)
EXPIRES = DECISION + timedelta(minutes=5)
DERIVED = Provenance(
    source="signal-producer-test",
    nature=ArtifactNature.DERIVED,
    reference="phase1.41-test",
    metadata={"producer": "build_signal"},
)


def make_snapshot(snapshot_id: str = "snapshot-signal-001") -> AnalysisSnapshot:
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


def make_hypothesis(
    artifact: StrategicArtifact,
    *,
    expected_direction: str = "UNSPECIFIED",
    expires_at: datetime | None = EXPIRES,
) -> Hypothesis:
    return build_hypothesis(
        artifact,
        expected_direction=expected_direction,
        expected_horizon="TEST_HORIZON",
        invalidation_conditions=("manual-review",),
        created_at=CREATED,
        provenance=DERIVED,
        expires_at=expires_at,
        status="UNRESOLVED",
    )


def build(
    hypothesis: Hypothesis,
    *,
    generated_at: datetime = GENERATED,
    validity: SignalValidity = SignalValidity.VALID,
    provenance: Provenance = DERIVED,
    evidence=None,
) -> Signal:
    return build_signal(
        hypothesis,
        generated_at=generated_at,
        validity=validity,
        provenance=provenance,
        evidence=evidence,
    )


def logical_fields(signal: Signal) -> tuple:
    return (
        signal.symbol,
        signal.hypothesis_id,
        signal.direction,
        signal.generated_at,
        signal.decision_timestamp,
        signal.provenance,
        signal.evidence,
        signal.expires_at,
        signal.validity,
    )


def test_build_signal_inherits_exact_hypothesis_relationship_fields():
    artifact = make_artifact()
    hypothesis = make_hypothesis(
        artifact,
        expected_direction="UNSPECIFIED",
        expires_at=EXPIRES,
    )

    signal = build(hypothesis)

    assert signal.hypothesis_id == hypothesis.hypothesis_id
    assert signal.symbol == hypothesis.symbol
    assert signal.direction == hypothesis.expected_direction
    assert signal.decision_timestamp == hypothesis.decision_timestamp
    assert signal.expires_at == hypothesis.expires_at


def test_hypothesis_id_cannot_be_overridden_by_the_producer():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    signal = build(hypothesis)

    assert signal.hypothesis_id == hypothesis.hypothesis_id
    assert not hasattr(signal, "alternative_hypothesis_id")


def test_direction_is_passthrough_without_operational_normalization():
    artifact = make_artifact()
    hypothesis = make_hypothesis(
        artifact,
        expected_direction="TEST_DIRECTION",
    )

    signal = build(hypothesis)

    assert signal.direction == "TEST_DIRECTION"


def test_generated_at_is_explicit_and_must_not_precede_decision_timestamp():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    signal = build(
        hypothesis,
        generated_at=GENERATED,
    )
    assert signal.generated_at == GENERATED

    with pytest.raises(ContractError, match="generated_at"):
        build(
            hypothesis,
            generated_at=DECISION - timedelta(microseconds=1),
        )


def test_decision_timestamp_is_inherited_not_generated_from_clock():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    signal = build(hypothesis)

    assert signal.decision_timestamp == hypothesis.decision_timestamp == DECISION


@pytest.mark.parametrize("validity", list(SignalValidity))
def test_signal_validity_values_are_passed_through_and_is_valid_at_respects_contract(validity):
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact, expires_at=EXPIRES)

    signal = build(hypothesis, validity=validity)

    assert signal.validity is validity

    if validity is SignalValidity.VALID:
        assert signal.is_valid_at(DECISION)
        assert signal.is_valid_at(GENERATED)
        assert not signal.is_valid_at(EXPIRES)
    else:
        assert not signal.is_valid_at(GENERATED)


def test_validity_type_is_rejected_when_not_signal_validity():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    with pytest.raises(StrategyContractError, match="SignalValidity"):
        build(hypothesis, validity="VALID")  # type: ignore[arg-type]


def test_expiration_is_inherited_exactly_including_none():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact, expires_at=None)

    signal = build(hypothesis)

    assert signal.expires_at is None


def test_provenance_must_be_derived_and_is_not_relationship_duplication():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    provenance = Provenance(
        source="explicit-signal-producer",
        nature=ArtifactNature.DERIVED,
        reference="external-reference",
        metadata={"kind": "test"},
    )
    signal = build(hypothesis, provenance=provenance)

    assert signal.provenance is provenance
    assert signal.provenance.nature is ArtifactNature.DERIVED
    assert signal.provenance.reference == "external-reference"
    assert "hypothesis_id" not in signal.provenance.metadata
    assert "strategic_artifact_id" not in signal.provenance.metadata
    assert "snapshot_id" not in signal.provenance.metadata
    assert "analysis_id" not in signal.provenance.metadata


def test_non_derived_provenance_is_rejected():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    observed = Provenance(
        source="test",
        nature=ArtifactNature.OBSERVED,
    )

    with pytest.raises(StrategyContractError, match="DERIVED"):
        build(hypothesis, provenance=observed)


def test_wrong_provenance_type_is_rejected():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    with pytest.raises(StrategyContractError, match="Provenance"):
        build(hypothesis, provenance=object())  # type: ignore[arg-type]


def test_evidence_is_optional_and_top_level_immutable():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    source_evidence = {"reason": "manual-review", "score": Decimal("1.25")}

    signal = build(hypothesis, evidence=source_evidence)

    source_evidence["reason"] = "mutated"

    assert signal.evidence["reason"] == "manual-review"
    assert signal.evidence["score"] == Decimal("1.25")

    with pytest.raises(TypeError):
        signal.evidence["new"] = "value"


def test_invalid_evidence_container_is_rejected():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    with pytest.raises(StrategyContractError, match="evidence"):
        build(hypothesis, evidence=["not-a-mapping"])


def test_wrong_input_type_is_rejected():
    with pytest.raises(StrategyContractError, match="Hypothesis"):
        build_signal(
            object(),
            generated_at=GENERATED,
            validity=SignalValidity.VALID,
            provenance=DERIVED,
        )


def test_signal_is_immutable():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    signal = build(hypothesis)

    with pytest.raises(FrozenInstanceError):
        signal.direction = "OTHER"

    with pytest.raises(FrozenInstanceError):
        signal.hypothesis_id = "other"


def test_build_signal_does_not_mutate_hypothesis():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)

    before = (
        hypothesis.symbol,
        hypothesis.supporting_analysis_ids,
        hypothesis.strategic_artifact_id,
        hypothesis.expected_direction,
        hypothesis.expected_horizon,
        hypothesis.invalidation_conditions,
        hypothesis.created_at,
        hypothesis.decision_timestamp,
        hypothesis.provenance,
        hypothesis.hypothesis_id,
        hypothesis.expires_at,
        hypothesis.status,
    )

    build(
        hypothesis,
        evidence={"reason": "unchanged"},
    )

    after = (
        hypothesis.symbol,
        hypothesis.supporting_analysis_ids,
        hypothesis.strategic_artifact_id,
        hypothesis.expected_direction,
        hypothesis.expected_horizon,
        hypothesis.invalidation_conditions,
        hypothesis.created_at,
        hypothesis.decision_timestamp,
        hypothesis.provenance,
        hypothesis.hypothesis_id,
        hypothesis.expires_at,
        hypothesis.status,
    )

    assert after == before


def test_full_chain_is_reconstructible_without_duplicate_relationship_fields():
    snapshot = make_snapshot("snapshot-full-chain")
    artifact = make_artifact(snapshot)
    hypothesis = make_hypothesis(artifact)
    signal = build(hypothesis)

    assert signal.hypothesis_id == hypothesis.hypothesis_id
    assert hypothesis.strategic_artifact_id == artifact.strategic_artifact_id
    assert hypothesis.supporting_analysis_ids == artifact.supporting_analysis_ids
    assert artifact.snapshot_id == snapshot.snapshot_id
    assert hypothesis.supporting_analysis_ids == tuple(
        result.analysis_id for result in snapshot.results
    )

    assert not hasattr(signal, "strategic_artifact_id")
    assert not hasattr(signal, "snapshot_id")
    assert not hasattr(signal, "analysis_id")


def test_signal_has_own_identity_distinct_from_hypothesis_and_artifact():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    signal = build(hypothesis)

    assert signal.signal_id
    assert signal.signal_id != hypothesis.hypothesis_id
    assert signal.signal_id != artifact.strategic_artifact_id
    assert signal.signal_id != artifact.snapshot_id


def test_logical_determinism_excludes_generated_signal_id():
    artifact = make_artifact()
    hypothesis = make_hypothesis(artifact)
    evidence = {"reason": "same"}
    provenance = Provenance(
        source="signal-producer",
        nature=ArtifactNature.DERIVED,
        reference="fixed",
        metadata={"mode": "test"},
    )

    first = build(
        hypothesis,
        generated_at=GENERATED,
        validity=SignalValidity.VALID,
        provenance=provenance,
        evidence=evidence,
    )
    second = build(
        hypothesis,
        generated_at=GENERATED,
        validity=SignalValidity.VALID,
        provenance=provenance,
        evidence=evidence,
    )

    assert first.signal_id != second.signal_id
    assert logical_fields(first) == logical_fields(second)


def test_build_signal_does_not_introduce_operational_vocabulary_or_execution_boundary():
    source = __import__(
        "pathlib",
        fromlist=["Path"],
    ).Path("bot_obrero/strategy_contracts.py").read_text(encoding="utf-8")

    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "BUY" not in source
    assert "SELL" not in source
    assert "OrderIntent" not in source
    assert "RiskEngine" not in source
    assert "ExecutionOrchestrator" not in source
    assert "SignalRegistry" not in source
    assert "signal_manager" not in source


def test_strategy_module_exports_build_signal():
    from bot_obrero.strategy_contracts import build_signal as exported_build_signal

    assert exported_build_signal is build_signal
