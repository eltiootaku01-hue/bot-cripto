from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, MarketObservation, Provenance
from bot_obrero.analysis_engine import (
    AlgorithmConfigurationError,
    AnalysisCalculationConfig,
    AnalysisEngine,
    TemporalContractError,
    WindowSpecification,
)
from bot_obrero.analysis_rsi import (
    RSI_ALGORITHM_IDENTITY,
    RSI_ALGORITHM_VERSION,
    RSIAlgorithm,
)
from bot_obrero.market_data import (
    CANDLE_DATA_TYPE,
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    MarketData,
    SourceIdentity,
    to_market_observation,
)


BASE = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
DECISION = BASE + timedelta(minutes=10)
OBSERVED = Provenance("synthetic-feed", ArtifactNature.OBSERVED)
_MISSING = object()


def make_config(
    *,
    window: WindowSpecification,
    period,
    effective_configuration=None,
    identity=RSI_ALGORITHM_IDENTITY,
    version=RSI_ALGORITHM_VERSION,
) -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="indicator.rsi",
        algorithm_identity=identity,
        algorithm_version=version,
        effective_configuration=(
            {} if effective_configuration is None else effective_configuration
        ),
        effective_parameters={} if period is _MISSING else {"period": period},
        window=window,
    )


def make_observation(
    *,
    index: int,
    close,
    available_at: datetime = DECISION,
    observation_id: str | None = None,
) -> MarketObservation:
    timestamp = BASE + timedelta(minutes=index)
    return MarketObservation(
        symbol="BTC/USDT",
        observation_timestamp=timestamp,
        available_timestamp=available_at,
        observation_type=CANDLE_DATA_TYPE,
        values={
            "open": Decimal("0"),
            "high": Decimal("999999"),
            "low": Decimal("1"),
            "close": close,
            "volume": Decimal("10"),
            "timeframe": "1m",
        },
        provenance=OBSERVED,
        venue="SYNTHETIC",
        observation_id=observation_id or f"obs-{index}",
    )


def make_market_data(
    index: int,
    close: str,
    *,
    available_at: datetime = DECISION,
    quality: DataQuality = DataQuality.VALID,
) -> MarketData:
    start = BASE + timedelta(minutes=index)
    candle = Candle(
        start=start,
        end=start + timedelta(minutes=1),
        timeframe="1m",
        open=Decimal(close) - Decimal("1"),
        high=Decimal(close) + Decimal("1"),
        low=Decimal(close) - Decimal("2"),
        close=Decimal(close),
        volume=Decimal("10"),
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.FINAL,
    )
    return MarketData(
        market_data_id=f"md-{index}",
        instrument=InstrumentIdentity(
            instrument_id="instrument-btcusdt-test",
            symbol="BTC/USDT",
            market="SYNTHETIC",
        ),
        source=SourceIdentity(
            source_id="fixture-source",
            provider="synthetic-test",
            venue="SYNTHETIC",
        ),
        data_type=CANDLE_DATA_TYPE,
        observed_at=start + timedelta(seconds=30),
        received_at=start + timedelta(seconds=40),
        available_at=available_at,
        payload=candle,
        quality=quality,
        completeness=DataCompleteness.COMPLETE,
        source_sequence=str(index),
    )


def execute(
    observations,
    *,
    period,
    window=None,
    decision_timestamp=DECISION,
    algorithm=None,
    effective_configuration=None,
):
    selected_window = WindowSpecification.all() if window is None else window
    return AnalysisEngine().execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=make_config(
            window=selected_window,
            period=period,
            effective_configuration=effective_configuration,
        ),
        algorithm=RSIAlgorithm() if algorithm is None else algorithm,
    )


def test_rsi_canonical_wilder_example_1_2_3_2_2_period_3():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "2", "2"])
    ]

    result = execute(observations, period=3)

    seed_gain = (Decimal("1") + Decimal("1") + Decimal("0")) / Decimal("3")
    seed_loss = (Decimal("0") + Decimal("0") + Decimal("1")) / Decimal("3")
    average_gain = ((seed_gain * Decimal("2")) + Decimal("0")) / Decimal("3")
    average_loss = ((seed_loss * Decimal("2")) + Decimal("0")) / Decimal("3")
    rs = average_gain / average_loss
    expected = Decimal("100") - (
        Decimal("100") / (Decimal("1") + rs)
    )

    assert result.values["rsi"] == expected
    assert result.values["period"] == 3
    assert isinstance(result.values["rsi"], Decimal)


def test_rsi_period_one_works_with_two_observations():
    result = execute(
        [
            make_observation(index=0, close=Decimal("10")),
            make_observation(index=1, close=Decimal("15")),
        ],
        period=1,
    )

    assert result.values["rsi"] == Decimal("100")


@pytest.mark.parametrize("period", [4, 5])
def test_rsi_requires_period_plus_one_observations(period):
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["10", "11", "12", "13"])
    ]

    with pytest.raises(AlgorithmConfigurationError, match="less than"):
        execute(observations, period=period)


def test_rsi_only_gains_returns_100():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "4", "5"])
    ]

    result = execute(observations, period=3)

    assert result.values["rsi"] == Decimal("100")


def test_rsi_only_losses_returns_0():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["5", "4", "3", "2", "1"])
    ]

    result = execute(observations, period=3)

    assert result.values["rsi"] == Decimal("0")


def test_rsi_no_movement_returns_explicit_50_policy():
    observations = [
        make_observation(index=index, close=Decimal("5"))
        for index in range(5)
    ]

    result = execute(observations, period=3)

    assert result.values["rsi"] == Decimal("50")


def test_rsi_accepts_exact_decimal_string_close():
    observations = [
        make_observation(index=0, close="10.00"),
        make_observation(index=1, close="12.00"),
        make_observation(index=2, close="11.00"),
    ]

    result = execute(observations, period=2)

    assert isinstance(result.values["rsi"], Decimal)


@pytest.mark.parametrize(
    "close",
    [
        1.0,
        1,
        True,
        Decimal("NaN"),
        Decimal("Infinity"),
        "NaN",
        "Infinity",
        "not-a-decimal",
    ],
)
def test_invalid_close_values_are_rejected(close):
    observations = [make_observation(index=0, close=close), make_observation(index=1, close=Decimal("10"))]

    with pytest.raises(AlgorithmConfigurationError, match="close"):
        execute(observations, period=1)


def test_missing_close_is_rejected():
    observation = make_observation(index=0, close=Decimal("10"))
    values = dict(observation.values)
    del values["close"]
    missing_close = MarketObservation(
        symbol=observation.symbol,
        observation_timestamp=observation.observation_timestamp,
        available_timestamp=observation.available_timestamp,
        observation_type=observation.observation_type,
        values=values,
        provenance=observation.provenance,
        venue=observation.venue,
        observation_id=observation.observation_id,
    )

    second = make_observation(index=1, close=Decimal("11"))
    with pytest.raises(AlgorithmConfigurationError, match="requires"):
        execute([missing_close, second], period=1)


@pytest.mark.parametrize(
    "period",
    [None, 0, -1, True, False, 3.0, "3"],
)
def test_invalid_period_values_are_rejected(period):
    observations = [
        make_observation(index=0, close=Decimal("10")),
        make_observation(index=1, close=Decimal("11")),
        make_observation(index=2, close=Decimal("12")),
        make_observation(index=3, close=Decimal("13")),
    ]

    with pytest.raises(AlgorithmConfigurationError, match="period"):
        execute(observations, period=period)


def test_missing_period_is_rejected():
    observations = [
        make_observation(index=0, close=Decimal("10")),
        make_observation(index=1, close=Decimal("11")),
    ]
    configuration = make_config(window=WindowSpecification.all(), period=_MISSING)

    with pytest.raises(AlgorithmConfigurationError, match="requires"):
        AnalysisEngine().execute(
            observations,
            decision_timestamp=DECISION,
            configuration=configuration,
            algorithm=RSIAlgorithm(),
        )


@pytest.mark.parametrize(
    ("identity", "version"),
    [
        ("indicator.other", RSI_ALGORITHM_VERSION),
        (RSI_ALGORITHM_IDENTITY, "9.9.9"),
    ],
)
def test_incompatible_identity_or_version_is_rejected(identity, version):
    observations = [
        make_observation(index=0, close=Decimal("10")),
        make_observation(index=1, close=Decimal("11")),
    ]
    configuration = make_config(
        window=WindowSpecification.all(),
        period=1,
        identity=identity,
        version=version,
    )

    with pytest.raises(AlgorithmConfigurationError, match="incompatible"):
        AnalysisEngine().execute(
            observations,
            decision_timestamp=DECISION,
            configuration=configuration,
            algorithm=RSIAlgorithm(),
        )


def test_algorithm_exposes_stable_identity_and_version():
    algorithm = RSIAlgorithm()

    assert algorithm.algorithm_identity == "indicator.rsi"
    assert algorithm.algorithm_version == "1.0.0"
    assert RSI_ALGORITHM_IDENTITY == "indicator.rsi"
    assert RSI_ALGORITHM_VERSION == "1.0.0"


def test_all_window_consumes_entire_engine_selection():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "2", "2"])
    ]

    result = execute(
        observations,
        period=3,
        window=WindowSpecification.all(),
    )

    assert result.observation_ids == tuple(item.observation_id for item in observations)
    assert result.values["rsi"] == Decimal("66.66666666666666666666666666666667")


def test_last_n_window_is_selected_by_engine_then_consumed_exactly():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "4", "2"])
    ]

    result = execute(
        observations,
        period=3,
        window=WindowSpecification.last_n(4),
    )

    assert result.observation_ids == ("obs-1", "obs-2", "obs-3", "obs-4")
    assert result.values["rsi"] == Decimal("50")


def test_explicit_window_preserves_engine_selection():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "4", "2"])
    ]
    explicit_ids = ("obs-1", "obs-2", "obs-3", "obs-4")

    result = execute(
        observations,
        period=3,
        window=WindowSpecification.explicit(explicit_ids),
    )

    assert result.observation_ids == explicit_ids
    assert result.values["rsi"] == Decimal("50")


def test_lookahead_is_rejected_by_engine_before_rsi():
    observations = [
        make_observation(
            index=0,
            close=Decimal("10"),
            available_at=DECISION + timedelta(seconds=1),
        ),
        make_observation(index=1, close=Decimal("11")),
    ]

    with pytest.raises(TemporalContractError, match="look-ahead"):
        execute(observations, period=1, decision_timestamp=DECISION)


def test_naive_decision_timestamp_is_rejected_by_engine():
    observations = [
        make_observation(index=0, close=Decimal("10")),
        make_observation(index=1, close=Decimal("11")),
    ]

    with pytest.raises(TemporalContractError, match="timezone-aware"):
        execute(
            observations,
            period=1,
            decision_timestamp=datetime(2026, 10, 2, 12, 10),
        )


def test_canonical_marketdata_reaches_rsi_through_real_conversion():
    market_data = [
        make_market_data(index, value)
        for index, value in enumerate(["1", "2", "3", "2", "2"])
    ]
    observations = [to_market_observation(item) for item in market_data]

    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=DECISION,
        configuration=make_config(
            window=WindowSpecification.all(),
            period=3,
            effective_configuration={"source": "synthetic-market-data"},
        ),
        algorithm=RSIAlgorithm(),
    )

    assert result.values["rsi"] == Decimal("66.66666666666666666666666666666667")
    assert result.values["period"] == 3
    assert result.observation_ids == tuple(item.observation_id for item in observations)
    assert result.provenance.metadata["algorithm_identity"] == RSI_ALGORITHM_IDENTITY
    assert result.provenance.metadata["algorithm_version"] == RSI_ALGORITHM_VERSION
    assert all(
        item.payload.close == observation.values["close"]
        for item, observation in zip(market_data, observations)
    )


def test_rsi_does_not_mutate_observations_values_or_provenance():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "2", "2"])
    ]
    snapshot = tuple(
        (
            item.observation_id,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance,
        )
        for item in observations
    )

    result = execute(observations, period=3)

    after = tuple(
        (
            item.observation_id,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance,
        )
        for item in observations
    )

    assert result.values["rsi"] == Decimal("66.66666666666666666666666666666667")
    assert after == snapshot


def test_rsi_is_deterministic_with_same_inputs_and_configuration():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "2", "2"])
    ]

    first = execute(observations, period=3)
    second = execute(observations, period=3)

    assert second.values == first.values
    assert second.observation_ids == first.observation_ids
    assert second.provenance.nature is first.provenance.nature
    assert second.provenance.metadata == first.provenance.metadata
    assert first.analysis_id != second.analysis_id


def test_result_provenance_contains_identity_version_and_period():
    observations = [
        make_observation(index=0, close=Decimal("10")),
        make_observation(index=1, close=Decimal("12")),
        make_observation(index=2, close=Decimal("11")),
    ]

    result = execute(
        observations,
        period=2,
        effective_configuration={"price_source": "close"},
    )

    metadata = result.provenance.metadata
    assert metadata["algorithm_identity"] == RSI_ALGORITHM_IDENTITY
    assert metadata["algorithm_version"] == RSI_ALGORITHM_VERSION
    assert metadata["effective_parameters"] == {"period": 2}
    assert metadata["effective_configuration"] == {"price_source": "close"}
    assert result.provenance.nature is ArtifactNature.DERIVED


def test_traceability_preserves_exact_engine_selected_ids():
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(["1", "2", "3", "2", "2"])
    ]
    expected_ids = ("obs-1", "obs-2", "obs-3", "obs-4")

    result = execute(
        observations,
        period=3,
        window=WindowSpecification.explicit(expected_ids),
    )

    assert result.observation_ids == expected_ids
    assert result.decision_timestamp == DECISION
    assert result.calculated_at >= DECISION


def test_empty_sequence_is_rejected():
    with pytest.raises(AlgorithmConfigurationError, match="must not be empty"):
        RSIAlgorithm().compute(
            (),
            make_config(window=WindowSpecification.all(), period=1),
        )


def test_non_observation_element_is_rejected():
    with pytest.raises(AlgorithmConfigurationError, match="MarketObservation"):
        RSIAlgorithm().compute(
            [object()],
            make_config(window=WindowSpecification.all(), period=1),
        )


def test_provider_neutrality_static_import_float_and_slice_guards():
    module_path = Path("bot_obrero/analysis_rsi.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    forbidden_import_fragments = (
        "bot_obrero.binance_spot",
        "bot_obrero.binance_websocket",
        "bot_obrero.execution",
        "bot_obrero.account",
        "numpy",
        "pandas",
        "talib",
    )
    imported_names = []
    float_calls = []
    float_literals = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                float_calls.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            float_literals.append(node.lineno)

    for fragment in forbidden_import_fragments:
        assert fragment not in imported_names
    assert not float_calls
    assert not float_literals

    source = module_path.read_text(encoding="utf-8")
    assert RSI_ALGORITHM_IDENTITY in source
    assert RSI_ALGORITHM_VERSION in source
    assert "100" in source
    assert "50" in source
    assert "period + 1" not in source


def test_module_has_no_slice_reselection_or_sorting():
    module_path = Path("bot_obrero/analysis_rsi.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    slices = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
    ]
    assert slices == []

    source = module_path.read_text(encoding="utf-8")
    assert ".sort(" not in source
    assert "sorted(" not in source


def test_prior_indicator_surfaces_remain_present():
    assert Path("bot_obrero/analysis_sma.py").exists()
    assert Path("tests/test_phase1_30_sma.py").exists()
    assert Path("bot_obrero/analysis_ema.py").exists()
    assert Path("tests/test_phase1_32_ema.py").exists()
