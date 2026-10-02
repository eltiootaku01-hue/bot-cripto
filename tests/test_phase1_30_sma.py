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
from bot_obrero.analysis_sma import (
    SMA_ALGORITHM_IDENTITY,
    SMA_ALGORITHM_VERSION,
    SMAAlgorithm,
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
    to_market_observation,
    SourceIdentity,
)
from bot_obrero.market_data import MarketDataError

BASE = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
DECISION = BASE + timedelta(minutes=5)
OBSERVED = Provenance("synthetic-feed", ArtifactNature.OBSERVED)


def make_config(
    *,
    window: WindowSpecification,
    period,
    effective_configuration=None,
) -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="indicator.sma",
        algorithm_identity=SMA_ALGORITHM_IDENTITY,
        algorithm_version=SMA_ALGORITHM_VERSION,
        effective_configuration=(
            {} if effective_configuration is None else effective_configuration
        ),
        effective_parameters={} if period is _MISSING else {"period": period},
        window=window,
    )


_MISSING = object()


def make_observation(
    *,
    index: int,
    close,
    timeframe: str = "1m",
    received_offset: int = 1,
    available_at: datetime = DECISION,
) -> MarketObservation:
    timestamp = BASE + timedelta(minutes=index)
    return MarketObservation(
        symbol="BTCUSDT",
        observation_timestamp=timestamp,
        available_timestamp=available_at,
        observation_type=CANDLE_DATA_TYPE,
        values={
            "open": Decimal("999999"),
            "high": Decimal("999999"),
            "low": Decimal("1"),
            "close": close,
            "volume": Decimal("888888"),
            "timeframe": timeframe,
            "received_at": (
                BASE + timedelta(minutes=received_offset)
            ).isoformat(),
        },
        provenance=OBSERVED,
        venue="SYNTHETIC",
        observation_id=f"obs-{index}",
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
        open=Decimal(close) - Decimal("100"),
        high=Decimal(close) + Decimal("100"),
        low=Decimal(close) - Decimal("200"),
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
            symbol="BTCUSDT",
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
    selected_window = (
        WindowSpecification.all() if window is None else window
    )
    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=make_config(
            window=selected_window,
            period=period,
            effective_configuration=effective_configuration,
        ),
        algorithm=SMAAlgorithm() if algorithm is None else algorithm,
    )
    return result


def test_sma_case_100_110_120_period_3():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    result = execute(observations, period=3)

    assert result.values["sma"] == Decimal("110")
    assert result.values["period"] == 3


def test_sma_case_100_10_series_preserves_decimal_precision():
    observations = [
        make_observation(index=0, close=Decimal("100.10")),
        make_observation(index=1, close=Decimal("100.20")),
        make_observation(index=2, close=Decimal("100.30")),
    ]

    result = execute(observations, period=3)

    assert result.values["sma"] == Decimal("100.20")
    assert isinstance(result.values["sma"], Decimal)


def test_sma_case_decimal_non_terminating_result_uses_decimal_context_without_manual_rounding():
    observations = [
        make_observation(index=0, close=Decimal("1.1")),
        make_observation(index=1, close=Decimal("1.2")),
        make_observation(index=2, close=Decimal("1.4")),
    ]

    expected = (Decimal("1.1") + Decimal("1.2") + Decimal("1.4")) / Decimal("3")
    result = execute(observations, period=3)

    assert result.values["sma"] == expected
    assert result.values["sma"].as_tuple() == expected.as_tuple()


@pytest.mark.parametrize(
    ("closes", "period", "expected"),
    [
        (["10"], 1, Decimal("10")),
        (["10", "20"], 2, Decimal("15")),
        (["10", "20", "30"], 3, Decimal("20")),
    ],
)
def test_sma_multiple_exact_periods(closes, period, expected):
    observations = [
        make_observation(index=index, close=Decimal(value))
        for index, value in enumerate(closes)
    ]

    result = execute(observations, period=period)

    assert result.values["sma"] == expected


def test_sma_uses_exact_last_n_window_selected_by_engine():
    observations = [
        make_observation(index=0, close=Decimal("1")),
        make_observation(index=1, close=Decimal("2")),
        make_observation(index=2, close=Decimal("3")),
        make_observation(index=3, close=Decimal("4")),
        make_observation(index=4, close=Decimal("5")),
    ]

    result = execute(
        observations,
        period=3,
        window=WindowSpecification.last_n(3),
    )

    assert result.observation_ids == ("obs-2", "obs-3", "obs-4")
    assert result.values["sma"] == Decimal("4")


def test_sma_uses_only_close_and_ignores_other_ohlcv_values():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    result = execute(observations, period=3)

    assert result.values["sma"] == Decimal("110")


@pytest.mark.parametrize(
    "period",
    [None, 0, -1, True, False, 3.0, "3"],
)
def test_invalid_periods_are_rejected(period):
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    with pytest.raises(AlgorithmConfigurationError, match="period"):
        execute(observations, period=period)


def test_missing_period_is_rejected():
    observations = [
        make_observation(index=0, close=Decimal("100")),
    ]
    configuration = AnalysisCalculationConfig(
        analysis_type="indicator.sma",
        algorithm_identity=SMA_ALGORITHM_IDENTITY,
        algorithm_version=SMA_ALGORITHM_VERSION,
        effective_configuration={},
        effective_parameters={},
        window=WindowSpecification.all(),
    )

    with pytest.raises(AlgorithmConfigurationError, match="requires"):
        AnalysisEngine().execute(
            observations,
            decision_timestamp=DECISION,
            configuration=configuration,
            algorithm=SMAAlgorithm(),
        )


def test_period_greater_than_selected_observations_is_rejected():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    with pytest.raises(AlgorithmConfigurationError, match="exceed"):
        execute(observations, period=4)


def test_period_smaller_than_selected_observations_is_rejected_instead_of_reselecting():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    with pytest.raises(AlgorithmConfigurationError, match="equal"):
        execute(observations, period=2)


@pytest.mark.parametrize(
    "identity,version",
    [
        ("indicator.other", SMA_ALGORITHM_VERSION),
        (SMA_ALGORITHM_IDENTITY, "9.9.9"),
    ],
)
def test_incompatible_algorithm_identity_or_version_is_rejected(identity, version):
    observations = [make_observation(index=0, close=Decimal("100"))]
    configuration = AnalysisCalculationConfig(
        analysis_type="indicator.sma",
        algorithm_identity=identity,
        algorithm_version=version,
        effective_configuration={},
        effective_parameters={"period": 1},
        window=WindowSpecification.all(),
    )

    with pytest.raises(AlgorithmConfigurationError, match="incompatible"):
        AnalysisEngine().execute(
            observations,
            decision_timestamp=DECISION,
            configuration=configuration,
            algorithm=SMAAlgorithm(),
        )


@pytest.mark.parametrize(
    "close",
    [
        1.0,
        True,
        1,
        Decimal("NaN"),
        Decimal("Infinity"),
        "NaN",
        "Infinity",
        "not-a-decimal",
    ],
)
def test_invalid_close_values_are_rejected(close):
    observations = [make_observation(index=0, close=close)]

    with pytest.raises(AlgorithmConfigurationError, match="close"):
        execute(observations, period=1)


def test_missing_close_is_rejected():
    observation = make_observation(index=0, close=Decimal("100"))
    values = dict(observation.values)
    del values["close"]
    observation = MarketObservation(
        symbol=observation.symbol,
        observation_timestamp=observation.observation_timestamp,
        available_timestamp=observation.available_timestamp,
        observation_type=observation.observation_type,
        values=values,
        provenance=observation.provenance,
        venue=observation.venue,
        observation_id=observation.observation_id,
    )

    with pytest.raises(AlgorithmConfigurationError, match="requires"):
        execute([observation], period=1)


def test_exact_decimal_string_close_is_accepted_without_float_conversion():
    result = execute([make_observation(index=0, close="100.20")], period=1)

    assert result.values["sma"] == Decimal("100.20")
    assert isinstance(result.values["sma"], Decimal)


def test_algorithm_exposes_stable_identity_and_version():
    algorithm = SMAAlgorithm()

    assert algorithm.algorithm_identity == "indicator.sma"
    assert algorithm.algorithm_version == "1.0.0"
    assert SMA_ALGORITHM_IDENTITY == "indicator.sma"
    assert SMA_ALGORITHM_VERSION == "1.0.0"


def test_result_provenance_contains_identity_configuration_and_parameters():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    result = execute(
        observations,
        period=3,
        effective_configuration={"price_source": "close"},
    )

    metadata = result.provenance.metadata
    assert metadata["algorithm_identity"] == "indicator.sma"
    assert metadata["algorithm_version"] == "1.0.0"
    assert metadata["effective_configuration"] == {"price_source": "close"}
    assert metadata["effective_parameters"] == {"period": 3}
    assert result.provenance.nature is ArtifactNature.DERIVED


def test_analysis_result_preserves_contract_traceability():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    result = execute(observations, period=3)

    assert result.symbol == "BTCUSDT"
    assert result.observation_ids == ("obs-0", "obs-1", "obs-2")
    assert result.decision_timestamp == DECISION
    assert result.calculated_at >= DECISION
    assert isinstance(result.provenance.metadata["effective_parameters"]["period"], int)


def test_lookahead_is_rejected_by_engine_before_sma_calculates():
    observations = [
        make_observation(
            index=0,
            close=Decimal("100"),
            available_at=DECISION + timedelta(seconds=1),
        )
    ]

    with pytest.raises(TemporalContractError, match="look-ahead"):
        execute(
            observations,
            period=1,
            decision_timestamp=DECISION,
        )


def test_naive_decision_timestamp_is_rejected_by_engine():
    observations = [make_observation(index=0, close=Decimal("100"))]

    with pytest.raises(TemporalContractError, match="timezone-aware"):
        execute(
            observations,
            period=1,
            decision_timestamp=datetime(2026, 10, 2, 12, 5),
        )


def test_canonical_marketdata_reaches_sma_and_analysisresult():
    market_data = [
        make_market_data(0, "100"),
        make_market_data(1, "110"),
        make_market_data(2, "120"),
    ]
    observations = [to_market_observation(item) for item in market_data]

    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=DECISION,
        configuration=make_config(
            window=WindowSpecification.all(),
            period=3,
            effective_configuration={"source": "synthetic"},
        ),
        algorithm=SMAAlgorithm(),
    )

    assert result.values["sma"] == Decimal("110")
    assert result.values["period"] == 3
    assert result.observation_ids == tuple(item.observation_id for item in observations)
    assert result.provenance.metadata["algorithm_identity"] == "indicator.sma"
    assert result.provenance.metadata["algorithm_version"] == "1.0.0"

    assert all(
        item.payload.close == observation.values["close"]
        for item, observation in zip(market_data, observations)
    )


def test_canonical_marketdata_unknown_availability_stops_before_sma():
    market_data = make_market_data(0, "100", available_at=None)

    with pytest.raises(MarketDataError, match="AVAILABLE_AT_UNKNOWN"):
        to_market_observation(market_data)


def test_canonical_marketdata_invalid_quality_stops_before_sma():
    market_data = make_market_data(
        0,
        "100",
        quality=DataQuality.INVALID,
    )

    with pytest.raises(MarketDataError, match="MARKET_DATA_QUALITY_NOT_VALID"):
        to_market_observation(market_data)


def test_sma_does_not_mutate_observations_or_values():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]
    snapshot = tuple(
        (
            item.observation_id,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance.reference,
            dict(item.provenance.metadata),
        )
        for item in observations
    )

    execute(observations, period=3)

    assert snapshot == tuple(
        (
            item.observation_id,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance.reference,
            dict(item.provenance.metadata),
        )
        for item in observations
    )


def test_same_inputs_are_mathematically_deterministic():
    observations = [
        make_observation(index=0, close=Decimal("100")),
        make_observation(index=1, close=Decimal("110")),
        make_observation(index=2, close=Decimal("120")),
    ]

    first = execute(
        observations,
        period=3,
        effective_configuration={"price_source": "close"},
    )
    second = execute(
        observations,
        period=3,
        effective_configuration={"price_source": "close"},
    )

    assert first.values == second.values
    assert first.provenance.metadata == second.provenance.metadata
    assert first.observation_ids == second.observation_ids
    assert first.analysis_id != second.analysis_id


def test_provider_neutral_and_no_float_arithmetic_in_sma_module():
    source = Path("bot_obrero/analysis_sma.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_imports = {
        "bot_obrero.binance_spot",
        "bot_obrero.binance_websocket",
        "bot_obrero.execution",
        "bot_obrero.account",
        "bot_obrero.murphy",
        "numpy",
        "pandas",
        "talib",
    }
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not any(
        imported == forbidden or imported.startswith(f"{forbidden}.")
        for imported in imports
        for forbidden in forbidden_imports
    )

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Constant) and isinstance(node.value, float)
        for node in ast.walk(tree)
    )
    assert "Decimal(" in source


def test_sma_does_not_return_analysisresult_directly():
    observations = [make_observation(index=0, close=Decimal("100"))]

    values = SMAAlgorithm().compute(
        observations,
        make_config(window=WindowSpecification.all(), period=1),
    )

    assert isinstance(values, dict)
    assert not hasattr(values, "analysis_id")


def test_canonical_marketdata_path_uses_decimal_closes():
    market_data = [
        make_market_data(0, "100.10"),
        make_market_data(1, "100.20"),
        make_market_data(2, "100.30"),
    ]
    observations = [to_market_observation(item) for item in market_data]

    assert all(isinstance(item.values["close"], Decimal) for item in observations)

    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=DECISION,
        configuration=make_config(
            window=WindowSpecification.all(),
            period=3,
        ),
        algorithm=SMAAlgorithm(),
    )

    assert result.values["sma"] == Decimal("100.20")
