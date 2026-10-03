from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    MarketObservation,
    Provenance,
)
from bot_obrero.analysis_ema import EMAAlgorithm
from bot_obrero.analysis_engine import (
    AnalysisCalculationConfig,
    AnalysisEngine,
    WindowSpecification,
)
from bot_obrero.analysis_rsi import RSIAlgorithm
from bot_obrero.analysis_sma import SMAAlgorithm
from bot_obrero.analysis_snapshot import AnalysisSnapshot, AnalysisSnapshotError
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
OBSERVATION_IDS = ("obs-1", "obs-2", "obs-3", "obs-4", "obs-5")


def make_result(
    *,
    analysis_type: str,
    algorithm_identity: str,
    algorithm_version: str = "1.0.0",
    symbol: str = "BTC/USDT",
    observation_ids: tuple[str, ...] = OBSERVATION_IDS,
    decision_timestamp: datetime = DECISION,
    calculated_at: datetime | None = None,
    nature: ArtifactNature = ArtifactNature.DERIVED,
) -> AnalysisResult:
    return AnalysisResult(
        symbol=symbol,
        observation_ids=observation_ids,
        analysis_type=analysis_type,
        values={"value": Decimal("1")},
        calculated_at=calculated_at or decision_timestamp + timedelta(seconds=1),
        decision_timestamp=decision_timestamp,
        provenance=Provenance(
            source="analysis-engine",
            nature=nature,
            metadata={
                "algorithm_identity": algorithm_identity,
                "algorithm_version": algorithm_version,
                "effective_configuration": {},
                "effective_parameters": {"period": 5},
                "analysis_type": analysis_type,
            },
        ),
    )


def snapshot_keys(snapshot: AnalysisSnapshot):
    return tuple(
        (
            result.analysis_type,
            result.provenance.metadata["algorithm_identity"],
            result.provenance.metadata["algorithm_version"],
        )
        for result in snapshot.results
    )


def make_market_data(index: int, close: str) -> MarketData:
    start = BASE + timedelta(minutes=index)
    close_value = Decimal(close)
    candle = Candle(
        start=start,
        end=start + timedelta(minutes=1),
        timeframe="1m",
        open=close_value - Decimal("1"),
        high=close_value + Decimal("1"),
        low=close_value - Decimal("2"),
        close=close_value,
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
            base_asset="BTC",
            quote_asset="USDT",
        ),
        source=SourceIdentity(
            source_id="fixture-source",
            provider="synthetic-test",
            venue="SYNTHETIC",
        ),
        data_type=CANDLE_DATA_TYPE,
        observed_at=start + timedelta(seconds=30),
        received_at=start + timedelta(seconds=40),
        available_at=DECISION,
        payload=candle,
        quality=DataQuality.VALID,
        completeness=DataCompleteness.COMPLETE,
        source_sequence=str(index),
    )


def config(
    *,
    analysis_type: str,
    algorithm_identity: str,
    algorithm_version: str,
    period: int,
) -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type=analysis_type,
        algorithm_identity=algorithm_identity,
        algorithm_version=algorithm_version,
        effective_configuration={},
        effective_parameters={"period": period},
        window=WindowSpecification.all(),
    )


def test_snapshot_composes_sma_ema_rsi_with_one_observation_base():
    sma = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    ema = make_result(
        analysis_type="indicator.ema",
        algorithm_identity="indicator.ema",
    )
    rsi = make_result(
        analysis_type="indicator.rsi",
        algorithm_identity="indicator.rsi",
    )

    snapshot = AnalysisSnapshot.from_results([rsi, sma, ema])

    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.decision_timestamp == DECISION
    assert snapshot.observation_ids == OBSERVATION_IDS
    assert len(snapshot.results) == 3
    assert set(snapshot_keys(snapshot)) == {
        ("indicator.sma", "indicator.sma", "1.0.0"),
        ("indicator.ema", "indicator.ema", "1.0.0"),
        ("indicator.rsi", "indicator.rsi", "1.0.0"),
    }


def test_snapshot_is_sorted_by_contract_key_not_input_order():
    sma = make_result(analysis_type="indicator.sma", algorithm_identity="indicator.sma")
    ema = make_result(analysis_type="indicator.ema", algorithm_identity="indicator.ema")
    rsi = make_result(analysis_type="indicator.rsi", algorithm_identity="indicator.rsi")

    first = AnalysisSnapshot.from_results([rsi, sma, ema])
    second = AnalysisSnapshot.from_results([ema, rsi, sma])

    assert snapshot_keys(first) == (
        ("indicator.ema", "indicator.ema", "1.0.0"),
        ("indicator.rsi", "indicator.rsi", "1.0.0"),
        ("indicator.sma", "indicator.sma", "1.0.0"),
    )
    assert snapshot_keys(first) == snapshot_keys(second)


def test_snapshot_rejects_empty_results():
    with pytest.raises(AnalysisSnapshotError, match="at least one"):
        AnalysisSnapshot.from_results([])


def test_snapshot_rejects_non_analysis_result():
    with pytest.raises(AnalysisSnapshotError, match="AnalysisResult"):
        AnalysisSnapshot.from_results([object()])


def test_snapshot_rejects_mixed_symbols():
    btc = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    eth = make_result(
        analysis_type="indicator.ema",
        algorithm_identity="indicator.ema",
        symbol="ETH/USDT",
    )

    with pytest.raises(AnalysisSnapshotError, match="symbols"):
        AnalysisSnapshot.from_results([btc, eth])


def test_snapshot_rejects_mixed_decision_timestamps():
    first = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    second = make_result(
        analysis_type="indicator.ema",
        algorithm_identity="indicator.ema",
        decision_timestamp=DECISION + timedelta(seconds=1),
    )

    with pytest.raises(AnalysisSnapshotError, match="decision_timestamp"):
        AnalysisSnapshot.from_results([first, second])


def test_snapshot_rejects_mixed_observation_ids():
    first = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    second = make_result(
        analysis_type="indicator.ema",
        algorithm_identity="indicator.ema",
        observation_ids=("obs-1", "obs-2", "obs-3", "obs-4"),
    )

    with pytest.raises(AnalysisSnapshotError, match="observation_ids"):
        AnalysisSnapshot.from_results([first, second])


def test_snapshot_rejects_non_derived_result():
    result = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    object.__setattr__(
        result,
        "provenance",
        Provenance(
            source="test",
            nature=ArtifactNature.OBSERVED,
            metadata=dict(result.provenance.metadata),
        ),
    )

    with pytest.raises(AnalysisSnapshotError, match="DERIVED"):
        AnalysisSnapshot.from_results([result])


def test_snapshot_rejects_result_before_decision_timestamp():
    result = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    object.__setattr__(
        result,
        "calculated_at",
        DECISION - timedelta(seconds=1),
    )

    with pytest.raises(AnalysisSnapshotError, match="calculated_at"):
        AnalysisSnapshot.from_results([result])


def test_snapshot_rejects_duplicate_analytical_operation():
    first = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
        observation_ids=OBSERVATION_IDS,
    )
    duplicate = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
        observation_ids=OBSERVATION_IDS,
    )

    with pytest.raises(AnalysisSnapshotError, match="duplicate"):
        AnalysisSnapshot.from_results([first, duplicate])


def test_snapshot_rejects_malformed_provenance_identity():
    result = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    metadata = dict(result.provenance.metadata)
    metadata.pop("algorithm_version")
    object.__setattr__(
        result,
        "provenance",
        Provenance(
            source="test",
            nature=ArtifactNature.DERIVED,
            metadata=metadata,
        ),
    )

    with pytest.raises(AnalysisSnapshotError, match="algorithm_version"):
        AnalysisSnapshot.from_results([result])


def test_snapshot_results_and_observation_ids_are_immutable():
    results = [
        make_result(analysis_type="indicator.rsi", algorithm_identity="indicator.rsi"),
        make_result(analysis_type="indicator.sma", algorithm_identity="indicator.sma"),
    ]

    snapshot = AnalysisSnapshot.from_results(results)
    results.clear()

    assert len(snapshot.results) == 2
    assert isinstance(snapshot.results, tuple)
    assert isinstance(snapshot.observation_ids, tuple)

    with pytest.raises((AttributeError, TypeError)):
        snapshot.results += ()

    with pytest.raises((AttributeError, TypeError)):
        snapshot.observation_ids += ("obs-6",)


def test_snapshot_preserves_result_provenance_without_recalculation():
    result = make_result(
        analysis_type="indicator.ema",
        algorithm_identity="indicator.ema",
    )
    metadata_before = dict(result.provenance.metadata)

    snapshot = AnalysisSnapshot.from_results([result])

    assert snapshot.results[0] is result
    assert dict(snapshot.results[0].provenance.metadata) == metadata_before
    assert snapshot.results[0].values["value"] == Decimal("1")


def test_snapshot_generates_independent_artifact_ids():
    result = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )

    first = AnalysisSnapshot.from_results([result])
    second = AnalysisSnapshot.from_results([result])

    assert first.snapshot_id != second.snapshot_id
    assert first.results[0].analysis_id == result.analysis_id
    assert second.results[0].analysis_id == result.analysis_id


def test_snapshot_accepts_explicit_snapshot_id_without_using_hashing():
    result = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )

    snapshot = AnalysisSnapshot.from_results(
        [result],
        snapshot_id="snapshot-test-001",
    )

    assert snapshot.snapshot_id == "snapshot-test-001"


def test_snapshot_marketdata_to_analysisengine_to_sma_ema_rsi():
    market_data = [
        make_market_data(index, close)
        for index, close in enumerate(["100", "101", "100", "102", "101"])
    ]
    observations = [to_market_observation(item) for item in market_data]

    assert len(observations) == 5
    assert all(item.symbol == "BTC/USDT" for item in observations)

    engine = AnalysisEngine()
    shared_kwargs = {
        "observations": observations,
        "decision_timestamp": DECISION,
    }

    sma_result = engine.execute(
        **shared_kwargs,
        configuration=config(
            analysis_type="indicator.sma",
            algorithm_identity="indicator.sma",
            algorithm_version="1.0.0",
            period=5,
        ),
        algorithm=SMAAlgorithm(),
    )
    ema_result = engine.execute(
        **shared_kwargs,
        configuration=config(
            analysis_type="indicator.ema",
            algorithm_identity="indicator.ema",
            algorithm_version="1.0.0",
            period=3,
        ),
        algorithm=EMAAlgorithm(),
    )
    rsi_result = engine.execute(
        **shared_kwargs,
        configuration=config(
            analysis_type="indicator.rsi",
            algorithm_identity="indicator.rsi",
            algorithm_version="1.0.0",
            period=3,
        ),
        algorithm=RSIAlgorithm(),
    )

    snapshot = AnalysisSnapshot.from_results(
        [rsi_result, sma_result, ema_result]
    )

    observation_ids = tuple(item.observation_id for item in observations)
    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.observation_ids == observation_ids
    assert snapshot.decision_timestamp == DECISION
    assert len(snapshot.results) == 3
    assert snapshot_keys(snapshot) == (
        ("indicator.ema", "indicator.ema", "1.0.0"),
        ("indicator.rsi", "indicator.rsi", "1.0.0"),
        ("indicator.sma", "indicator.sma", "1.0.0"),
    )
    assert {result.provenance.nature for result in snapshot.results} == {
        ArtifactNature.DERIVED
    }


def test_snapshot_is_independent_of_input_list_after_creation():
    result = make_result(
        analysis_type="indicator.ema",
        algorithm_identity="indicator.ema",
    )
    result_list = [result]
    snapshot = AnalysisSnapshot.from_results(result_list)
    result_list.append(
        make_result(
            analysis_type="indicator.rsi",
            algorithm_identity="indicator.rsi",
        )
    )

    assert len(snapshot.results) == 1


def test_provider_neutrality_static_guards():
    module_path = Path("bot_obrero/analysis_snapshot.py")
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

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
    forbidden_float_calls = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                forbidden_float_calls.append(node.lineno)

    for fragment in forbidden_import_fragments:
        assert fragment not in imported_names
    assert not forbidden_float_calls
    assert "hash(" not in source


def test_snapshot_does_not_modify_result_inputs():
    result = make_result(
        analysis_type="indicator.sma",
        algorithm_identity="indicator.sma",
    )
    before = (
        result.symbol,
        result.observation_ids,
        result.analysis_type,
        dict(result.values),
        result.calculated_at,
        result.decision_timestamp,
        dict(result.provenance.metadata),
        result.provenance.nature,
    )

    AnalysisSnapshot.from_results([result])

    after = (
        result.symbol,
        result.observation_ids,
        result.analysis_type,
        dict(result.values),
        result.calculated_at,
        result.decision_timestamp,
        dict(result.provenance.metadata),
        result.provenance.nature,
    )

    assert after == before
