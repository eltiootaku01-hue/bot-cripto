from datetime import datetime, timedelta, timezone
import ast
import importlib
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, MarketObservation, Provenance
from bot_obrero.analysis_engine import (
    AnalysisCalculationConfig,
    AnalysisEngine,
    AlgorithmConfigurationError,
    AlgorithmExecutionError,
    InputContractError,
    TemporalContractError,
    WindowContractError,
    WindowSpecification,
)

T = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
OBSERVED = Provenance("market-feed", ArtifactNature.OBSERVED)
DERIVED = Provenance("derived", ArtifactNature.DERIVED)


def make_observation(
    *,
    offset: int = 0,
    available_offset: int = 0,
    observation_id: str | None = None,
    symbol: str = "BTCUSDT",
    venue: str | None = "BINANCE",
    provenance: Provenance = OBSERVED,
    close: str = "100",
    timeframe: str = "1m",
) -> MarketObservation:
    obs_id = f"obs-{offset}" if observation_id is None else observation_id
    return MarketObservation(
        symbol=symbol,
        observation_timestamp=T + timedelta(minutes=offset),
        available_timestamp=T + timedelta(minutes=available_offset),
        observation_type="CANDLE",
        values={
            "close": close,
            "timeframe": timeframe,
            "received_at": (T + timedelta(minutes=available_offset)).isoformat(),
        },
        provenance=provenance,
        venue=venue,
        observation_id=obs_id,
    )


def make_config(
    *,
    window: WindowSpecification | None = None,
    effective_configuration=None,
    effective_parameters=None,
) -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="test-sum",
        algorithm_identity="test.sum",
        algorithm_version="1.0.0",
        effective_configuration={} if effective_configuration is None else effective_configuration,
        effective_parameters={} if effective_parameters is None else effective_parameters,
        window=WindowSpecification.all() if window is None else window,
    )


class SumAlgorithm:
    def __init__(self) -> None:
        self.received: tuple[MarketObservation, ...] | None = None

    def compute(self, observations, configuration):
        self.received = tuple(observations)
        return {
            "sum_close": sum(int(item.values["close"]) for item in observations),
            "count": len(observations),
        }


def run(
    observations,
    *,
    decision_timestamp=T + timedelta(minutes=3),
    window=None,
    algorithm=None,
    configuration=None,
):
    algo = SumAlgorithm() if algorithm is None else algorithm
    config = make_config(window=window) if configuration is None else configuration
    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=config,
        algorithm=algo,
    )
    return result, algo


def test_empty_observations_are_rejected():
    with pytest.raises(InputContractError, match="must not be empty"):
        run([])


@pytest.mark.parametrize("bad", [None, {"not": "observations"}, "abc"])
def test_invalid_observation_collection_is_rejected(bad):
    with pytest.raises(InputContractError):
        run(bad)


def test_each_element_must_be_market_observation():
    with pytest.raises(InputContractError, match="MarketObservation"):
        run([object()])


def test_empty_observation_id_is_rejected():
    observation = make_observation(observation_id="")
    with pytest.raises(InputContractError, match="observation_id"):
        run([observation])


def test_duplicate_observation_ids_are_rejected():
    observations = [
        make_observation(offset=0, observation_id="dup"),
        make_observation(offset=1, observation_id="dup"),
    ]
    with pytest.raises(InputContractError, match="duplicate"):
        run(observations)


def test_non_observed_provenance_is_rejected():
    with pytest.raises(InputContractError, match="OBSERVED"):
        run([make_observation(provenance=DERIVED)])


def test_mixed_symbols_are_rejected():
    observations = [
        make_observation(offset=0),
        make_observation(offset=1, symbol="ETHUSDT"),
    ]
    with pytest.raises(InputContractError, match="mixed symbols"):
        run(observations)


def test_mixed_venues_are_rejected():
    observations = [
        make_observation(offset=0, venue="BINANCE"),
        make_observation(offset=1, venue="BYBIT"),
    ]
    with pytest.raises(InputContractError, match="mixed venues"):
        run(observations)


def test_none_venue_is_consistent():
    observations = [
        make_observation(offset=0, venue=None),
        make_observation(offset=1, venue=None),
    ]
    result, _ = run(observations)
    assert result.values["sum_close"] == 200


def test_none_venue_and_concrete_venue_are_rejected():
    observations = [
        make_observation(offset=0, venue=None),
        make_observation(offset=1, venue="BINANCE"),
    ]
    with pytest.raises(InputContractError, match="mixed venues"):
        run(observations)


def test_correct_order_is_accepted():
    result, algorithm = run(
        [make_observation(offset=0), make_observation(offset=1)]
    )
    assert algorithm.received is not None
    assert result.values["count"] == 2


def test_out_of_order_input_is_rejected():
    observations = [
        make_observation(offset=1),
        make_observation(offset=0),
    ]
    with pytest.raises(InputContractError, match="strictly ascending"):
        run(observations)


def test_equal_observation_timestamps_use_available_timestamp_as_tiebreaker():
    observations = [
        make_observation(offset=0, available_offset=0, observation_id="a"),
        make_observation(offset=0, available_offset=1, observation_id="b"),
    ]
    result, _ = run(observations)
    assert result.values["count"] == 2


def test_equal_timestamps_and_availability_use_observation_id_as_tiebreaker():
    observations = [
        make_observation(offset=0, available_offset=0, observation_id="a"),
        make_observation(offset=0, available_offset=0, observation_id="b"),
    ]
    result, _ = run(observations)
    assert result.values["count"] == 2


def test_reversed_final_observation_id_tiebreaker_is_rejected():
    observations = [
        make_observation(offset=0, available_offset=0, observation_id="b"),
        make_observation(offset=0, available_offset=0, observation_id="a"),
    ]
    with pytest.raises(InputContractError, match="strictly ascending"):
        run(observations)


def test_all_observations_window_is_used():
    result, algorithm = run([make_observation(offset=i) for i in range(3)])
    assert result.observation_ids == ("obs-0", "obs-1", "obs-2")
    assert tuple(item.observation_id for item in algorithm.received) == result.observation_ids


@pytest.mark.parametrize("count", [0, -1])
def test_last_n_requires_positive_count(count):
    with pytest.raises(WindowContractError, match="count > 0"):
        WindowSpecification.last_n(count)


def test_last_n_rejects_count_larger_than_available():
    with pytest.raises(WindowContractError, match="too large"):
        run(
            [make_observation(offset=i) for i in range(2)],
            window=WindowSpecification.last_n(3),
        )


def test_last_n_selects_last_items_without_fetching_more():
    result, algorithm = run(
        [make_observation(offset=i) for i in range(3)],
        window=WindowSpecification.last_n(2),
    )
    assert result.observation_ids == ("obs-1", "obs-2")
    assert tuple(item.observation_id for item in algorithm.received) == ("obs-1", "obs-2")


def test_last_n_can_exclude_unavailable_observations():
    observations = [
        make_observation(offset=0, available_offset=10),
        make_observation(offset=1, available_offset=2),
        make_observation(offset=2, available_offset=2),
    ]
    result, _ = run(
        observations,
        decision_timestamp=T + timedelta(minutes=3),
        window=WindowSpecification.last_n(2),
    )
    assert result.observation_ids == ("obs-1", "obs-2")


def test_time_range_is_start_inclusive_and_end_exclusive():
    observations = [
        make_observation(offset=0),
        make_observation(offset=1),
        make_observation(offset=2),
    ]
    window = WindowSpecification.time_range(
        T + timedelta(minutes=1),
        T + timedelta(minutes=2),
    )
    result, _ = run(observations, window=window)
    assert result.observation_ids == ("obs-1",)


def test_time_range_uses_observation_timestamp_not_received_at():
    observations = [
        make_observation(offset=0, available_offset=10),
        make_observation(offset=1, available_offset=10),
    ]
    window = WindowSpecification.time_range(
        T,
        T + timedelta(minutes=1, seconds=30),
    )
    result, _ = run(
        observations,
        decision_timestamp=T + timedelta(minutes=11),
        window=window,
    )
    assert result.observation_ids == ("obs-0", "obs-1")


def test_empty_time_range_selection_is_rejected():
    window = WindowSpecification.time_range(
        T + timedelta(hours=1),
        T + timedelta(hours=2),
    )
    with pytest.raises(WindowContractError, match="selected no observations"):
        run([make_observation(offset=0)], window=window)


def test_explicit_selection_preserves_requested_order_when_valid():
    observations = [make_observation(offset=i) for i in range(3)]
    window = WindowSpecification.explicit(("obs-0", "obs-2"))
    result, algorithm = run(observations, window=window)
    assert result.observation_ids == ("obs-0", "obs-2")
    assert tuple(item.observation_id for item in algorithm.received) == ("obs-0", "obs-2")


def test_explicit_selection_unknown_id_is_rejected():
    with pytest.raises(WindowContractError, match="unknown observation_id"):
        run(
            [make_observation(offset=0)],
            window=WindowSpecification.explicit(("does-not-exist",)),
        )


def test_explicit_selection_out_of_order_is_rejected():
    observations = [make_observation(offset=i) for i in range(3)]
    window = WindowSpecification.explicit(("obs-2", "obs-0"))
    with pytest.raises(InputContractError, match="strictly ascending"):
        run(observations, window=window)


def test_explicit_selection_duplicate_is_rejected():
    with pytest.raises(WindowContractError, match="unique"):
        WindowSpecification.explicit(("obs-0", "obs-0"))


def test_multiple_timeframes_are_structurally_accepted_without_resampling():
    observations = [
        make_observation(offset=0, timeframe="1m"),
        make_observation(offset=1, timeframe="5m"),
    ]
    result, algorithm = run(observations)
    assert result.observation_ids == ("obs-0", "obs-1")
    assert tuple(item.values["timeframe"] for item in algorithm.received) == ("1m", "5m")


def test_decision_timestamp_must_be_timezone_aware():
    with pytest.raises(TemporalContractError, match="timezone-aware"):
        run([make_observation()], decision_timestamp=datetime(2026, 10, 2, 12, 3))


def test_all_available_at_decision_timestamp_are_accepted():
    result, _ = run(
        [
            make_observation(offset=0, available_offset=3),
            make_observation(offset=1, available_offset=3),
        ],
        decision_timestamp=T + timedelta(minutes=3),
    )
    assert result.observation_ids == ("obs-0", "obs-1")


def test_available_at_equal_to_decision_is_accepted():
    result, _ = run(
        [make_observation(available_offset=3)],
        decision_timestamp=T + timedelta(minutes=3),
    )
    assert result.observation_ids == ("obs-0",)


def test_available_after_decision_is_rejected():
    with pytest.raises(TemporalContractError, match="look-ahead"):
        run(
            [make_observation(offset=0, available_offset=5)],
            decision_timestamp=T + timedelta(minutes=3),
        )


def test_received_at_cannot_substitute_for_availability():
    observation = make_observation(offset=0, available_offset=5)
    assert observation.values["received_at"] < (T + timedelta(minutes=3)).isoformat()
    with pytest.raises(TemporalContractError, match="look-ahead"):
        run([observation], decision_timestamp=T + timedelta(minutes=3))


def test_decision_timestamp_is_preserved():
    decision = T + timedelta(minutes=3)
    result, _ = run([make_observation()], decision_timestamp=decision)
    assert result.decision_timestamp == decision


def test_result_has_exact_selected_observation_ids_and_derived_provenance():
    result, _ = run(
        [make_observation(offset=i) for i in range(3)],
        window=WindowSpecification.last_n(2),
    )
    assert result.observation_ids == ("obs-1", "obs-2")
    assert result.provenance.nature is ArtifactNature.DERIVED
    assert result.provenance.metadata["algorithm_identity"] == "test.sum"
    assert result.provenance.metadata["algorithm_version"] == "1.0.0"
    assert result.provenance.metadata["analysis_type"] == "test-sum"


def test_result_contains_effective_configuration_and_parameters():
    result, _ = run(
        [make_observation()],
        configuration=make_config(
            effective_configuration={"mode": "close"},
            effective_parameters={"period": 14},
        ),
    )
    assert result.provenance.metadata["effective_configuration"] == {"mode": "close"}
    assert result.provenance.metadata["effective_parameters"] == {"period": 14}


def test_explicit_empty_and_omitted_configuration_are_distinct():
    with pytest.raises(AlgorithmConfigurationError, match="explicit empty mapping"):
        AnalysisCalculationConfig(
            analysis_type="test",
            algorithm_identity="test.sum",
            algorithm_version="1.0.0",
            effective_configuration=None,
            effective_parameters={},
            window=WindowSpecification.all(),
        )


def test_calculated_at_meets_contract():
    decision = T + timedelta(hours=1)
    result, _ = run([make_observation()], decision_timestamp=decision)
    assert result.calculated_at >= decision
    assert result.calculated_at.tzinfo is not None


def test_algorithm_receives_only_selected_observations():
    algorithm = SumAlgorithm()
    run(
        [make_observation(offset=i) for i in range(3)],
        window=WindowSpecification.last_n(2),
        algorithm=algorithm,
    )
    assert algorithm.received is not None
    assert tuple(item.observation_id for item in algorithm.received) == ("obs-1", "obs-2")


def test_algorithm_cannot_return_analysis_result():
    class BadAlgorithm:
        def compute(self, observations, configuration):
            from bot_obrero.analysis_contracts import AnalysisResult

            return AnalysisResult(
                symbol="BTCUSDT",
                observation_ids=("obs-0",),
                analysis_type="bad",
                values={},
                calculated_at=T,
                decision_timestamp=T,
                provenance=Provenance("x", ArtifactNature.DERIVED),
            )

    with pytest.raises(AlgorithmExecutionError, match="derived values"):
        run([make_observation()], algorithm=BadAlgorithm())


def test_algorithm_return_type_is_enforced():
    class BadAlgorithm:
        def compute(self, observations, configuration):
            return 42

    with pytest.raises(AlgorithmExecutionError, match="mapping"):
        run([make_observation()], algorithm=BadAlgorithm())


def test_algorithm_failure_is_provider_neutral():
    class BadAlgorithm:
        def compute(self, observations, configuration):
            raise RuntimeError("boom")

    with pytest.raises(AlgorithmExecutionError, match="algorithm execution failed"):
        run([make_observation()], algorithm=BadAlgorithm())


def test_input_observations_are_not_mutated():
    observations = [make_observation(offset=i) for i in range(2)]
    snapshot = tuple(
        (
            item.observation_id,
            item.symbol,
            item.venue,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance.nature,
            dict(item.provenance.metadata),
        )
        for item in observations
    )
    run(observations)
    assert snapshot == tuple(
        (
            item.observation_id,
            item.symbol,
            item.venue,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance.nature,
            dict(item.provenance.metadata),
        )
        for item in observations
    )


def test_calculation_metadata_is_semantically_deterministic():
    observations = [make_observation(offset=i) for i in range(3)]
    first, _ = run(
        observations,
        configuration=make_config(
            effective_configuration={"mode": "close"},
            effective_parameters={"period": 2},
        ),
    )
    second, _ = run(
        observations,
        configuration=make_config(
            effective_configuration={"mode": "close"},
            effective_parameters={"period": 2},
        ),
    )
    assert first.values == second.values
    assert first.observation_ids == second.observation_ids
    assert first.provenance.metadata == second.provenance.metadata
    assert first.analysis_id != second.analysis_id


def test_engine_has_no_provider_or_execution_imports():
    module = importlib.import_module("bot_obrero.analysis_engine")
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    forbidden = {
        "bot_obrero.binance_spot",
        "bot_obrero.binance_websocket",
        "bot_obrero.execution",
        "bot_obrero.account",
        "bot_obrero.murphy",
    }
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    assert not any(name in forbidden for name in imports)


def test_multi_timeframe_is_not_resampled_or_aligned():
    first = make_observation(offset=0, timeframe="1m")
    second = make_observation(offset=1, timeframe="5m")
    result, algorithm = run([first, second])
    assert tuple(item.values["timeframe"] for item in algorithm.received) == ("1m", "5m")
    assert result.observation_ids == ("obs-0", "obs-1")