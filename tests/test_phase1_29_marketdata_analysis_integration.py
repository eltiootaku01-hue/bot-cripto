from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.analysis_contracts import ArtifactNature
from bot_obrero.analysis_engine import (
    AnalysisCalculationConfig,
    AnalysisEngine,
    TemporalContractError,
    WindowSpecification,
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
    MarketDataError,
    SourceIdentity,
    to_market_observation,
)

BASE = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
DECISION = BASE + timedelta(minutes=3)
UNSET = object()


class CountAndSumClose:
    """Deterministic test-only algorithm for integration coverage."""

    def __init__(self) -> None:
        self.received = None

    def compute(self, observations, configuration):
        self.received = tuple(observations)
        return {
            "count": len(observations),
            "sum_close": sum(
                (observation.values["close"] for observation in observations),
                Decimal("0"),
            ),
        }


def make_market_data(
    index: int,
    *,
    close: str | None = None,
    timeframe: str = "1m",
    available_offset: int = 3,
    received_offset: int | None = None,
    available_at: datetime | None | object = UNSET,
    quality: DataQuality = DataQuality.VALID,
    market_data_id: str | None = None,
) -> MarketData:
    start = BASE + timedelta(minutes=index)
    end = start + timedelta(minutes=1 if timeframe == "1m" else 5)
    received = (
        BASE + timedelta(minutes=received_offset)
        if received_offset is not None
        else start + timedelta(seconds=10)
    )
    availability = (
        BASE + timedelta(minutes=available_offset)
        if available_at is UNSET
        else available_at
    )
    close_value = close if close is not None else str(100 + index)

    candle = Candle(
        start=start,
        end=end,
        timeframe=timeframe,
        open=Decimal(close_value) - Decimal("1"),
        high=Decimal(close_value) + Decimal("1"),
        low=Decimal(close_value) - Decimal("2"),
        close=Decimal(close_value),
        volume=Decimal("10"),
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.FINAL,
    )
    return MarketData(
        market_data_id=market_data_id or f"md-{index}",
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
        received_at=received,
        available_at=availability,
        payload=candle,
        quality=quality,
        completeness=DataCompleteness.COMPLETE,
        source_sequence=str(index),
    )


def make_config(window: WindowSpecification) -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="phase1.29.integration",
        algorithm_identity="test.count-and-sum-close",
        algorithm_version="1.0.0",
        effective_configuration={"source": "synthetic"},
        effective_parameters={"purpose": "integration-test"},
        window=window,
    )


def execute_market_data(market_data_items, *, window=None, decision_timestamp=DECISION):
    observations = [to_market_observation(item) for item in market_data_items]
    algorithm = CountAndSumClose()
    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=make_config(
            WindowSpecification.all() if window is None else window
        ),
        algorithm=algorithm,
    )
    return market_data_items, observations, result, algorithm


def test_canonical_market_data_reaches_analysis_result_through_real_conversion():
    market_data = make_market_data(0, market_data_id="md-canonical-0", close="100")

    _, observations, result, algorithm = execute_market_data([market_data])

    observation = observations[0]
    assert observation.symbol == market_data.instrument.symbol
    assert observation.observation_timestamp == market_data.observed_at
    assert observation.available_timestamp == market_data.available_at
    assert observation.values["close"] == Decimal("100")
    assert observation.values["market_data_id"] == market_data.market_data_id
    assert observation.values["instrument_id"] == market_data.instrument_id
    assert observation.values["source_id"] == market_data.source_id
    assert observation.values["received_at"] == market_data.received_at.isoformat()

    assert observation.provenance.nature is ArtifactNature.OBSERVED
    assert observation.provenance.source == market_data.source_id
    assert observation.provenance.reference == market_data.market_data_id
    assert observation.provenance.metadata["instrument_id"] == market_data.instrument_id
    assert observation.provenance.metadata["provider"] == market_data.source.provider
    assert observation.provenance.metadata["venue"] == market_data.source.venue
    assert observation.provenance.metadata["quality"] == market_data.quality.value
    assert observation.venue == market_data.source.venue
    assert observation.observation_id
    assert algorithm.received == tuple(observations)

    assert result.observation_ids == (observation.observation_id,)
    assert result.symbol == market_data.instrument.symbol
    assert result.values == {"count": 1, "sum_close": Decimal("100")}
    assert result.decision_timestamp == DECISION
    assert result.provenance.nature is ArtifactNature.DERIVED
    assert result.provenance.metadata["algorithm_identity"] == "test.count-and-sum-close"
    assert result.provenance.metadata["algorithm_version"] == "1.0.0"
    assert result.provenance.metadata["effective_configuration"] == {"source": "synthetic"}
    assert result.provenance.metadata["effective_parameters"] == {
        "purpose": "integration-test"
    }


def test_three_canonical_market_data_items_preserve_order_and_identity_trace():
    market_data_items = [
        make_market_data(0, market_data_id="md-0", close="100"),
        make_market_data(1, market_data_id="md-1", close="101"),
        make_market_data(2, market_data_id="md-2", close="102"),
    ]

    _, observations, result, algorithm = execute_market_data(market_data_items)

    assert [item.observation_timestamp for item in observations] == sorted(
        item.observation_timestamp for item in observations
    )
    assert len({item.observation_id for item in observations}) == 3

    assert [
        item.values["market_data_id"] for item in observations
    ] == ["md-0", "md-1", "md-2"]
    assert [
        item.provenance.reference for item in observations
    ] == ["md-0", "md-1", "md-2"]

    assert result.observation_ids == tuple(item.observation_id for item in observations)
    assert algorithm.received == tuple(observations)
    assert result.values == {"count": 3, "sum_close": Decimal("303")}

    # The canonical conversion generates a distinct observation identity.
    assert all(
        observation.observation_id != market_data.market_data_id
        for market_data, observation in zip(market_data_items, observations)
    )


def test_availability_is_explicit_and_available_at_equal_to_decision_is_accepted():
    market_data = make_market_data(
        0,
        available_at=DECISION,
        received_offset=1,
    )

    _, observations, result, _ = execute_market_data([market_data])

    assert observations[0].available_timestamp == DECISION
    assert observations[0].values["received_at"] < DECISION.isoformat()
    assert result.decision_timestamp == DECISION


def test_available_at_after_decision_is_rejected_even_when_received_at_is_earlier():
    market_data = make_market_data(
        0,
        available_at=BASE + timedelta(minutes=5),
        received_offset=2,
    )
    observation = to_market_observation(market_data)

    assert observation.values["received_at"] < DECISION.isoformat()
    with pytest.raises(TemporalContractError, match="look-ahead"):
        AnalysisEngine().execute(
            [observation],
            decision_timestamp=DECISION,
            configuration=make_config(WindowSpecification.all()),
            algorithm=CountAndSumClose(),
        )


def test_available_at_unknown_blocks_market_data_promotion_before_analysis():
    market_data = make_market_data(0, available_at=None)

    with pytest.raises(MarketDataError, match="AVAILABLE_AT_UNKNOWN"):
        to_market_observation(market_data)


def test_invalid_quality_blocks_market_data_promotion_before_analysis():
    market_data = make_market_data(0, quality=DataQuality.INVALID)

    with pytest.raises(MarketDataError, match="MARKET_DATA_QUALITY_NOT_VALID"):
        to_market_observation(market_data)


@pytest.mark.parametrize(
    ("window", "expected_indexes", "expected_sum"),
    [
        (WindowSpecification.all(), (0, 1, 2), Decimal("303")),
        (WindowSpecification.last_n(2), (1, 2), Decimal("203")),
        (
            WindowSpecification.time_range(
                BASE + timedelta(minutes=1),
                BASE + timedelta(minutes=3),
            ),
            (1, 2),
            Decimal("203"),
        ),
    ],
)
def test_windows_operate_on_observations_created_from_market_data(
    window,
    expected_indexes,
    expected_sum,
):
    market_data_items = [
        make_market_data(0),
        make_market_data(1),
        make_market_data(2),
    ]
    observations = [to_market_observation(item) for item in market_data_items]

    # Stabilize only the expectation surface: use the real generated IDs.
    real_ids = tuple(item.observation_id for item in observations)
    expected_ids = tuple(real_ids[index] for index in expected_indexes)

    algorithm = CountAndSumClose()
    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=DECISION,
        configuration=make_config(window),
        algorithm=algorithm,
    )

    assert result.observation_ids == expected_ids
    assert tuple(item.observation_id for item in algorithm.received) == expected_ids
    assert result.values["sum_close"] == expected_sum


def test_multitimeframe_market_data_passes_without_resampling_or_alignment():
    market_data_items = [
        make_market_data(0, timeframe="1m", close="100"),
        make_market_data(1, timeframe="5m", close="200"),
    ]
    observations = [to_market_observation(item) for item in market_data_items]
    algorithm = CountAndSumClose()

    result = AnalysisEngine().execute(
        observations,
        decision_timestamp=DECISION,
        configuration=make_config(WindowSpecification.all()),
        algorithm=algorithm,
    )

    assert algorithm.received is not None
    assert tuple(item.values["timeframe"] for item in algorithm.received) == ("1m", "5m")
    assert tuple(item.values["close"] for item in algorithm.received) == (
        Decimal("100"),
        Decimal("200"),
    )
    assert result.observation_ids == tuple(item.observation_id for item in observations)
    assert result.values == {"count": 2, "sum_close": Decimal("300")}


def test_market_data_are_not_mutated_by_conversion_or_analysis():
    market_data_items = [
        make_market_data(0, close="100"),
        make_market_data(1, close="101"),
        make_market_data(2, close="102"),
    ]
    before = tuple(item.to_dict() for item in market_data_items)

    execute_market_data(market_data_items)

    after = tuple(item.to_dict() for item in market_data_items)
    assert after == before
