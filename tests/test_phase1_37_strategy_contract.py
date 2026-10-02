from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import AnalysisResult, ArtifactNature, Provenance, Hypothesis, Signal
from bot_obrero.analysis_snapshot import AnalysisSnapshot
from bot_obrero.execution import OrderIntent
from bot_obrero.strategy_contracts import (
    StrategyCalculationConfig,
    StrategyContractError,
    StrategicArtifact,
    Strategy,
    build_strategic_artifact,
)


DECISION = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
OBSERVATION_IDS = ("obs-1", "obs-2")


def make_analysis_result(
    *,
    analysis_type: str,
    algorithm_identity: str,
    algorithm_version: str,
) -> AnalysisResult:
    return AnalysisResult(
        symbol="BTC/USDT",
        observation_ids=OBSERVATION_IDS,
        analysis_type=analysis_type,
        values={"value": Decimal("1.25")},
        calculated_at=DECISION + timedelta(seconds=1),
        decision_timestamp=DECISION,
        provenance=Provenance(
            source="analysis-engine",
            nature=ArtifactNature.DERIVED,
            metadata={
                "analysis_type": analysis_type,
                "algorithm_identity": algorithm_identity,
                "algorithm_version": algorithm_version,
            },
        ),
    )


def make_snapshot(snapshot_id: str = "snapshot-fixed") -> AnalysisSnapshot:
    results = (
        make_analysis_result(
            analysis_type="indicator.ema",
            algorithm_identity="indicator.ema",
            algorithm_version="1.0.0",
        ),
        make_analysis_result(
            analysis_type="indicator.rsi",
            algorithm_identity="indicator.rsi",
            algorithm_version="1.0.0",
        ),
    )
    return AnalysisSnapshot.from_results(results, snapshot_id=snapshot_id)


def make_config(
    *,
    strategy_type="neutral-test",
    strategy_identity="strategy.test",
    strategy_version="1.0.0",
    effective_configuration=None,
    effective_parameters=None,
) -> StrategyCalculationConfig:
    return StrategyCalculationConfig(
        strategy_type=strategy_type,
        strategy_identity=strategy_identity,
        strategy_version=strategy_version,
        effective_configuration=(
            {"source": "fixed"} if effective_configuration is None else effective_configuration
        ),
        effective_parameters=(
            {"window": "all"} if effective_parameters is None else effective_parameters
        ),
    )


class DeterministicStrategy:
    strategy_identity = "strategy.test"
    strategy_version = "1.0.0"

    def compute(
        self,
        snapshot: AnalysisSnapshot,
        configuration: StrategyCalculationConfig,
    ) -> StrategicArtifact:
        return build_strategic_artifact(
            snapshot,
            configuration,
            values={
                "analysis_count": len(snapshot.results),
                "symbol": snapshot.symbol,
            },
        )


def test_valid_snapshot_and_config_produce_neutral_artifact():
    snapshot = make_snapshot()
    configuration = make_config()

    artifact = DeterministicStrategy().compute(snapshot, configuration)

    assert isinstance(artifact, StrategicArtifact)
    assert artifact.snapshot_id == snapshot.snapshot_id
    assert artifact.symbol == snapshot.symbol
    assert artifact.decision_timestamp == snapshot.decision_timestamp
    assert artifact.observation_ids == snapshot.observation_ids
    assert artifact.strategy_identity == "strategy.test"
    assert artifact.strategy_version == "1.0.0"
    assert artifact.effective_configuration["source"] == "fixed"
    assert artifact.effective_parameters["window"] == "all"
    assert artifact.values["analysis_count"] == 2


def test_strategy_contract_accepts_only_analysis_snapshot_and_config():
    snapshot = make_snapshot()
    configuration = make_config()

    with pytest.raises(StrategyContractError, match="snapshot"):
        build_strategic_artifact(object(), configuration, values={})

    with pytest.raises(StrategyContractError, match="configuration"):
        build_strategic_artifact(snapshot, object())


def test_configuration_is_immutable_and_freezes_nested_values():
    nested = {"thresholds": [Decimal("1.0"), Decimal("2.0")]}
    source_configuration = {"nested": nested}
    source_parameters = {"period": 5}

    configuration = make_config(
        effective_configuration=source_configuration,
        effective_parameters=source_parameters,
    )

    nested["thresholds"].append(Decimal("3.0"))
    source_parameters["period"] = 99

    assert configuration.effective_configuration["nested"]["thresholds"] == (
        Decimal("1.0"),
        Decimal("2.0"),
    )
    assert configuration.effective_parameters["period"] == 5

    with pytest.raises(TypeError):
        configuration.effective_configuration["new"] = "value"

    with pytest.raises(FrozenInstanceError):
        configuration.strategy_version = "2.0.0"


def test_malformed_configuration_is_rejected():
    with pytest.raises(StrategyContractError):
        StrategyCalculationConfig(
            strategy_type="neutral-test",
            strategy_identity="strategy.test",
            strategy_version="1.0.0",
            effective_configuration={},
            effective_parameters=None,
        )

    with pytest.raises(StrategyContractError, match="strategy_type"):
        make_config(strategy_type="")

    with pytest.raises(StrategyContractError, match="float"):
        make_config(effective_parameters={"period": 5.0})

    with pytest.raises(StrategyContractError, match="keys"):
        make_config(effective_parameters={1: "invalid"})


def test_strategy_output_is_immutable_and_does_not_mutate_snapshot():
    snapshot = make_snapshot()
    snapshot_before = (
        snapshot.snapshot_id,
        snapshot.symbol,
        snapshot.decision_timestamp,
        snapshot.observation_ids,
        snapshot.results,
    )
    configuration = make_config()

    artifact = DeterministicStrategy().compute(snapshot, configuration)

    with pytest.raises(FrozenInstanceError):
        artifact.symbol = "ETH/USDT"

    with pytest.raises(TypeError):
        artifact.values["new"] = Decimal("4")

    assert (
        snapshot.snapshot_id,
        snapshot.symbol,
        snapshot.decision_timestamp,
        snapshot.observation_ids,
        snapshot.results,
    ) == snapshot_before


def test_traceability_is_preserved_without_numeric_only_projection():
    snapshot = make_snapshot(snapshot_id="traceable-snapshot")
    configuration = make_config(
        effective_configuration={"mode": "test"},
        effective_parameters={"period": 5},
    )

    artifact = build_strategic_artifact(
        snapshot,
        configuration,
        values={"score": Decimal("12.5")},
    )

    assert artifact.snapshot_id == "traceable-snapshot"
    assert artifact.decision_timestamp == DECISION
    assert artifact.observation_ids == OBSERVATION_IDS
    assert artifact.strategy_identity == "strategy.test"
    assert artifact.strategy_version == "1.0.0"
    assert artifact.effective_configuration == {"mode": "test"}
    assert artifact.effective_parameters == {"period": 5}
    assert artifact.values["score"] == Decimal("12.5")


def test_deterministic_strategy_returns_same_logical_output_for_same_input():
    snapshot = make_snapshot(snapshot_id="deterministic-snapshot")
    configuration = make_config()

    first = DeterministicStrategy().compute(snapshot, configuration)
    second = DeterministicStrategy().compute(snapshot, configuration)

    assert first == second
    assert first.snapshot_id == second.snapshot_id
    assert first.decision_timestamp == second.decision_timestamp
    assert first.observation_ids == second.observation_ids
    assert first.values == second.values


def test_decision_timestamp_is_taken_from_snapshot_not_current_clock():
    snapshot = make_snapshot(snapshot_id="fixed-time")
    configuration = make_config()

    artifact = DeterministicStrategy().compute(snapshot, configuration)

    assert artifact.decision_timestamp == DECISION
    assert "datetime.now" not in Path(
        "bot_obrero/strategy_contracts.py"
    ).read_text(encoding="utf-8")


def test_strategy_output_is_not_an_execution_order_or_signal():
    snapshot = make_snapshot()
    configuration = make_config()

    artifact = DeterministicStrategy().compute(snapshot, configuration)

    assert not isinstance(artifact, OrderIntent)
    assert not isinstance(artifact, Hypothesis)
    assert not isinstance(artifact, Signal)


def test_strategy_protocol_is_provider_neutral_and_minimal():
    assert "strategy_identity" in Strategy.__annotations__
    assert "strategy_version" in Strategy.__annotations__
    assert callable(getattr(Strategy, "compute", None))


def test_strategy_contract_has_no_provider_or_execution_imports():
    module_path = Path("bot_obrero/strategy_contracts.py")
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_fragments = (
        "binance",
        "websocket",
        "requests",
        "http",
        "rest",
        "execution",
        "account",
        "risk",
    )
    imported_names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.append((node.module or "").lower())

    for fragment in forbidden_fragments:
        assert all(fragment not in imported for imported in imported_names)

    assert "OrderIntent" not in source
    assert "MarketData" not in source
    assert "MarketObservation" not in source
    assert "current clock" not in source.lower()


@pytest.mark.parametrize(
    "field_name",
    ["strategy_type", "strategy_identity", "strategy_version"],
)
def test_required_strategy_identity_fields_cannot_be_empty(field_name):
    kwargs = {
        "strategy_type": "neutral-test",
        "strategy_identity": "strategy.test",
        "strategy_version": "1.0.0",
        "effective_configuration": {},
        "effective_parameters": {},
    }
    kwargs[field_name] = ""

    with pytest.raises(StrategyContractError, match=field_name):
        StrategyCalculationConfig(**kwargs)


def test_configuration_rejects_mutable_custom_objects():
    class MutableValue:
        pass

    with pytest.raises(StrategyContractError, match="unsupported or mutable"):
        make_config(effective_parameters={"custom": MutableValue()})
