from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    Provenance,
    SignalValidity,
)
from bot_obrero.analysis_snapshot import AnalysisSnapshot
from bot_obrero.strategy_contracts import (
    StrategyCalculationConfig,
    StrategyContractError,
    build_strategic_artifact,
)
from bot_obrero.strategy_runtime import (
    StrategyRuntime,
    StrategyRuntimeState,
)


DECISION = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
NOW = DECISION + timedelta(seconds=5)
EXPIRY = DECISION + timedelta(hours=1)


def make_result(
    *,
    analysis_type: str,
    identity: str,
    version: str,
    analysis_id: str,
) -> AnalysisResult:
    return AnalysisResult(
        symbol="BTC/USDT",
        observation_ids=("obs-1", "obs-2", "obs-3"),
        analysis_type=analysis_type,
        values={"value": Decimal("1.25")},
        calculated_at=NOW,
        decision_timestamp=DECISION,
        provenance=Provenance(
            source=f"analysis:{identity}",
            nature=ArtifactNature.DERIVED,
            reference=analysis_id,
            metadata={
                "analysis_type": analysis_type,
                "algorithm_identity": identity,
                "algorithm_version": version,
            },
        ),
        analysis_id=analysis_id,
    )


def make_snapshot(*, snapshot_id: str = "snapshot-001") -> AnalysisSnapshot:
    results = (
        make_result(
            analysis_type="indicator.ema",
            identity="indicator.ema",
            version="1.0.0",
            analysis_id="analysis-ema",
        ),
        make_result(
            analysis_type="indicator.rsi",
            identity="indicator.rsi",
            version="1.0.0",
            analysis_id="analysis-rsi",
        ),
    )
    return AnalysisSnapshot.from_results(results, snapshot_id=snapshot_id)


def make_config(
    *,
    identity: str = "strategy.test",
    version: str = "1.0.0",
) -> StrategyCalculationConfig:
    return StrategyCalculationConfig(
        strategy_type="test.strategy",
        strategy_identity=identity,
        strategy_version=version,
        effective_configuration={"mode": "deterministic"},
        effective_parameters={"threshold": Decimal("1.0")},
    )


def make_values() -> dict[str, object]:
    return {
        "expected_direction": "LONG",
        "expected_horizon": "1h",
        "invalidation_conditions": ("signal_stale",),
        "expires_at": EXPIRY,
        "hypothesis_status": "UNRESOLVED",
        "signal_evidence": {"rule": "test-rule"},
        "score": Decimal("2.5"),
    }


class ValidStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        return build_strategic_artifact(
            snapshot,
            configuration,
            make_values(),
        )


class WarmupRejectedStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        raise StrategyContractError("insufficient warmup evidence")


class UnexpectedStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        raise RuntimeError("unexpected provider-like failure")


class InvalidOutputStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        return object()


class IncoherentOutputStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        artifact = build_strategic_artifact(
            snapshot,
            configuration,
            make_values(),
        )
        return replace(
            artifact,
            snapshot_id="different-snapshot",
        )


class MissingHypothesisPayloadStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        values = make_values()
        del values["expected_direction"]
        return build_strategic_artifact(snapshot, configuration, values)


class EarlierClockStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(self, snapshot, configuration):
        return build_strategic_artifact(
            snapshot,
            configuration,
            make_values(),
        )


def runtime(*, clock=NOW) -> StrategyRuntime:
    return StrategyRuntime(clock=lambda: clock)


def test_happy_path_completes_lifecycle_and_builds_all_artifacts():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        ValidStrategy(),
    )

    assert result.state is StrategyRuntimeState.COMPLETED
    assert result.lifecycle == (
        StrategyRuntimeState.READY,
        StrategyRuntimeState.EXECUTING,
        StrategyRuntimeState.COMPLETED,
    )
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.strategy_identity == "strategy.test"
    assert result.strategy_version == "1.0.0"
    assert result.strategic_artifact is not None
    assert result.hypothesis is not None
    assert result.signal is not None
    assert result.decision_timestamp == DECISION


def test_strategic_artifact_preserves_snapshot_and_configuration_evidence():
    snapshot = make_snapshot()
    config = make_config()

    result = runtime().execute(snapshot, config, ValidStrategy())

    assert result.strategic_artifact is not None
    artifact = result.strategic_artifact

    assert artifact.snapshot_id == snapshot.snapshot_id
    assert artifact.symbol == snapshot.symbol
    assert artifact.decision_timestamp == snapshot.decision_timestamp
    assert artifact.observation_ids == snapshot.observation_ids
    assert artifact.supporting_analysis_ids == tuple(
        item.analysis_id for item in snapshot.results
    )
    assert artifact.strategy_type == config.strategy_type
    assert artifact.strategy_identity == config.strategy_identity
    assert artifact.strategy_version == config.strategy_version
    assert artifact.effective_configuration == config.effective_configuration
    assert artifact.effective_parameters == config.effective_parameters


def test_hypothesis_inherits_artifact_evidence_and_expiry():
    snapshot = make_snapshot()

    result = runtime().execute(snapshot, make_config(), ValidStrategy())

    assert result.hypothesis is not None
    hypothesis = result.hypothesis

    assert hypothesis.symbol == "BTC/USDT"
    assert hypothesis.supporting_analysis_ids == tuple(
        item.analysis_id for item in snapshot.results
    )
    assert hypothesis.strategic_artifact_id == result.strategic_artifact.strategic_artifact_id
    assert hypothesis.expected_direction == "LONG"
    assert hypothesis.expected_horizon == "1h"
    assert hypothesis.invalidation_conditions == ("signal_stale",)
    assert hypothesis.decision_timestamp == DECISION
    assert hypothesis.expires_at == EXPIRY


def test_signal_inherits_hypothesis_fields_and_is_valid_only_after_success():
    snapshot = make_snapshot()

    result = runtime().execute(snapshot, make_config(), ValidStrategy())

    assert result.signal is not None
    signal = result.signal
    assert result.hypothesis is not None

    assert signal.hypothesis_id == result.hypothesis.hypothesis_id
    assert signal.symbol == result.hypothesis.symbol
    assert signal.direction == result.hypothesis.expected_direction
    assert signal.generated_at == NOW
    assert signal.decision_timestamp == DECISION
    assert signal.expires_at == EXPIRY
    assert signal.validity is SignalValidity.VALID
    assert dict(signal.evidence) == {"rule": "test-rule"}


def test_snapshot_invalid_input_is_rejected_before_execution():
    result = runtime().execute(
        object(),
        make_config(),
        ValidStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert result.lifecycle == (
        StrategyRuntimeState.READY,
        StrategyRuntimeState.REJECTED,
    )
    assert result.strategic_artifact is None


def test_strategy_identity_mismatch_is_rejected():
    snapshot = make_snapshot()
    wrong_identity = type(
        "WrongIdentityStrategy",
        (),
        {
            "strategy_identity": "strategy.other",
            "strategy_version": "1.0.0",
            "compute": ValidStrategy.compute,
        },
    )()

    result = runtime().execute(snapshot, make_config(), wrong_identity)

    assert result.state is StrategyRuntimeState.REJECTED
    assert "strategy_identity" in result.reason


def test_strategy_version_mismatch_is_rejected():
    snapshot = make_snapshot()
    wrong_version = type(
        "WrongVersionStrategy",
        (),
        {
            "strategy_identity": "strategy.test",
            "strategy_version": "9.9.9",
            "compute": ValidStrategy.compute,
        },
    )()

    result = runtime().execute(
        snapshot,
        make_config(version="9.9.9"),
        ValidStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert "strategy_version" in result.reason


def test_strategy_output_type_mismatch_is_rejected():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        InvalidOutputStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert "StrategicArtifact" in result.reason
    assert result.hypothesis is None
    assert result.signal is None


def test_strategy_output_snapshot_mismatch_is_rejected():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        IncoherentOutputStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert "snapshot_id" in result.reason


def test_strategy_specific_warmup_failure_is_fail_closed_as_rejected():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        WarmupRejectedStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert result.lifecycle == (
        StrategyRuntimeState.READY,
        StrategyRuntimeState.EXECUTING,
        StrategyRuntimeState.REJECTED,
    )
    assert "insufficient warmup evidence" in result.reason


def test_unexpected_strategy_failure_is_unknown_not_success():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        UnexpectedStrategy(),
    )

    assert result.state is StrategyRuntimeState.UNKNOWN
    assert result.lifecycle[-1] is StrategyRuntimeState.UNKNOWN
    assert result.strategic_artifact is None
    assert result.hypothesis is None
    assert result.signal is None


def test_missing_explicit_hypothesis_payload_is_rejected():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        MissingHypothesisPayloadStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert "expected_direction" in result.reason


def test_decision_timestamp_never_replaced_by_runtime_clock():
    snapshot = make_snapshot()
    later = DECISION + timedelta(days=10)

    result = StrategyRuntime(clock=lambda: later).execute(
        snapshot,
        make_config(),
        ValidStrategy(),
    )

    assert result.state is StrategyRuntimeState.COMPLETED
    assert result.decision_timestamp == DECISION
    assert result.hypothesis is not None
    assert result.hypothesis.decision_timestamp == DECISION
    assert result.signal is not None
    assert result.signal.decision_timestamp == DECISION
    assert result.signal.generated_at == later


def test_runtime_clock_before_decision_is_rejected_closed():
    snapshot = make_snapshot()
    earlier = DECISION - timedelta(seconds=1)

    result = StrategyRuntime(clock=lambda: earlier).execute(
        snapshot,
        make_config(),
        EarlierClockStrategy(),
    )

    assert result.state is StrategyRuntimeState.REJECTED
    assert "cannot precede" in result.reason


def test_provenance_is_derived_from_snapshot_without_fabricating_source():
    snapshot = make_snapshot()

    result = runtime().execute(
        snapshot,
        make_config(),
        ValidStrategy(),
    )

    assert result.provenance is not None
    assert result.provenance.nature is ArtifactNature.DERIVED
    assert result.provenance.source == "strategy_runtime"
    assert result.provenance.reference == snapshot.snapshot_id
    assert tuple(result.provenance.metadata["supporting_analysis_ids"]) == tuple(
        item.analysis_id for item in snapshot.results
    )

    assert result.hypothesis is not None
    assert result.hypothesis.provenance == result.provenance
    assert result.signal is not None
    assert result.signal.provenance == result.provenance

    upstream = tuple(result.provenance.metadata["upstream_provenance"])
    assert {item["analysis_id"] for item in upstream} == {
        item.analysis_id for item in snapshot.results
    }


def test_same_snapshot_and_configuration_have_same_logical_decision():
    snapshot = make_snapshot()
    config = make_config()

    first = runtime().execute(snapshot, config, ValidStrategy())
    second = runtime().execute(snapshot, config, ValidStrategy())

    assert first.state is second.state is StrategyRuntimeState.COMPLETED
    assert first.strategic_artifact is not None
    assert second.strategic_artifact is not None
    assert first.hypothesis is not None
    assert second.hypothesis is not None
    assert first.signal is not None
    assert second.signal is not None

    assert first.strategic_artifact.snapshot_id == second.strategic_artifact.snapshot_id
    assert first.strategic_artifact.symbol == second.strategic_artifact.symbol
    assert first.strategic_artifact.observation_ids == second.strategic_artifact.observation_ids
    assert first.strategic_artifact.supporting_analysis_ids == second.strategic_artifact.supporting_analysis_ids
    assert first.strategic_artifact.strategy_identity == second.strategic_artifact.strategy_identity
    assert first.strategic_artifact.strategy_version == second.strategic_artifact.strategy_version
    assert first.strategic_artifact.effective_configuration == second.strategic_artifact.effective_configuration
    assert first.strategic_artifact.effective_parameters == second.strategic_artifact.effective_parameters
    assert first.strategic_artifact.values == second.strategic_artifact.values

    assert first.hypothesis.expected_direction == second.hypothesis.expected_direction
    assert first.hypothesis.expected_horizon == second.hypothesis.expected_horizon
    assert first.hypothesis.invalidation_conditions == second.hypothesis.invalidation_conditions
    assert first.hypothesis.decision_timestamp == second.hypothesis.decision_timestamp
    assert first.hypothesis.expires_at == second.hypothesis.expires_at

    assert first.signal.symbol == second.signal.symbol
    assert first.signal.direction == second.signal.direction
    assert first.signal.decision_timestamp == second.signal.decision_timestamp
    assert first.signal.expires_at == second.signal.expires_at
    assert first.signal.validity == second.signal.validity
    assert first.signal.evidence == second.signal.evidence

    assert first.strategic_artifact.strategic_artifact_id != second.strategic_artifact.strategic_artifact_id
    assert first.hypothesis.hypothesis_id != second.hypothesis.hypothesis_id
    assert first.signal.signal_id != second.signal.signal_id


def test_runtime_does_not_mutate_snapshot_or_configuration():
    snapshot = make_snapshot()
    config = make_config()

    snapshot_before = (
        snapshot.snapshot_id,
        snapshot.symbol,
        snapshot.decision_timestamp,
        snapshot.observation_ids,
        tuple(item.analysis_id for item in snapshot.results),
    )
    config_before = (
        config.strategy_type,
        config.strategy_identity,
        config.strategy_version,
        dict(config.effective_configuration),
        dict(config.effective_parameters),
    )

    result = runtime().execute(snapshot, config, ValidStrategy())

    assert result.state is StrategyRuntimeState.COMPLETED
    assert (
        snapshot.snapshot_id,
        snapshot.symbol,
        snapshot.decision_timestamp,
        snapshot.observation_ids,
        tuple(item.analysis_id for item in snapshot.results),
    ) == snapshot_before
    assert (
        config.strategy_type,
        config.strategy_identity,
        config.strategy_version,
        dict(config.effective_configuration),
        dict(config.effective_parameters),
    ) == config_before


def test_successful_runtime_has_no_financial_or_execution_artifacts():
    snapshot = make_snapshot()

    result = runtime().execute(snapshot, make_config(), ValidStrategy())

    assert result.state is StrategyRuntimeState.COMPLETED
    assert not hasattr(result, "risk_decision")
    assert not hasattr(result, "risk_authorization")
    assert not hasattr(result, "trade_proposal")
    assert not hasattr(result, "order_intent")
    assert not hasattr(result, "execution_result")
