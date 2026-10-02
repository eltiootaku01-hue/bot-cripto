"""Real Binance Spot REST -> canonical MarketData -> SMA E2E validation for FASE 1.31."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot_obrero import binance_instruments as metadata_module
from bot_obrero import binance_spot as spot_module
from bot_obrero.analysis_contracts import ArtifactNature
from bot_obrero.analysis_engine import (
    AnalysisCalculationConfig,
    AnalysisEngine,
    TemporalContractError,
    WindowSpecification,
)
from bot_obrero.analysis_sma import SMAAlgorithm
from bot_obrero.binance_instruments import (
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
)
from bot_obrero.binance_spot import BinanceSpotRestConfig, _datetime_to_ms
from bot_obrero.market_data import to_market_observation


SYMBOL = "BTCUSDT"
INTERVAL = "1m"
CANONICAL_SYMBOL = "BTC/USDT"
INSTRUMENT_ID = "binance:SPOT:BTCUSDT"
PERIOD = 3
ALGORITHM_IDENTITY = "indicator.sma"
ALGORITHM_VERSION = "1.0.0"
HISTORICAL_LOOKBACK_MINUTES = 6
HISTORICAL_END_OFFSET_MINUTES = 2
HISTORICAL_PAGE_LIMIT = 10
HISTORICAL_MAX_REQUESTS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config() -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="indicator.sma",
        algorithm_identity=ALGORITHM_IDENTITY,
        algorithm_version=ALGORITHM_VERSION,
        effective_configuration={
            "provider": "binance",
            "market": "SPOT",
            "symbol": SYMBOL,
            "timeframe": INTERVAL,
        },
        effective_parameters={"period": PERIOD},
        window=WindowSpecification.last_n(PERIOD),
    )


def _assert_aware_utc(value: datetime, field_name: str) -> None:
    assert value.tzinfo is not None and value.utcoffset() == timedelta(0), (
        f"{field_name} must be timezone-aware UTC: {value!r}"
    )


def _run(
    *,
    metadata: BinanceSpotInstrumentMetadata,
    adapter: BinanceMetadataBackedAdapter,
) -> dict:
    metadata_record = metadata.fetch(SYMBOL)
    assert metadata_record.provider_symbol == SYMBOL
    assert metadata_record.market == "SPOT"
    assert metadata_record.venue == "BINANCE"

    instrument = metadata.resolve_active(SYMBOL)
    assert instrument.symbol == CANONICAL_SYMBOL
    assert instrument.instrument_id == INSTRUMENT_ID

    now = _now()
    current_minute = now.replace(second=0, microsecond=0)
    start_time = _datetime_to_ms(
        current_minute - timedelta(minutes=HISTORICAL_LOOKBACK_MINUTES)
    )
    end_time = _datetime_to_ms(
        current_minute - timedelta(minutes=HISTORICAL_END_OFFSET_MINUTES)
    )

    market_data = adapter.fetch_historical_market_data(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time=start_time,
        end_time=end_time,
        page_limit=HISTORICAL_PAGE_LIMIT,
        max_requests=HISTORICAL_MAX_REQUESTS,
    )

    assert len(market_data) >= PERIOD, (
        f"expected at least {PERIOD} real historical candles, got {len(market_data)}"
    )
    assert all(item.instrument.symbol == CANONICAL_SYMBOL for item in market_data)
    assert all(item.instrument.instrument_id == INSTRUMENT_ID for item in market_data)
    assert all(item.source.provider == "binance" for item in market_data)
    assert all(item.source.venue == "BINANCE" for item in market_data)
    assert all(item.source.source_id == "binance-spot-rest" for item in market_data)
    assert all(item.payload.timeframe == INTERVAL for item in market_data)
    assert all(item.payload.start.tzinfo == timezone.utc for item in market_data)
    assert all(item.payload.end.tzinfo == timezone.utc for item in market_data)
    assert all(item.available_at is not None for item in market_data)

    observations = [to_market_observation(item) for item in market_data]
    assert len(observations) == len(market_data)
    assert all(observation.symbol == CANONICAL_SYMBOL for observation in observations)
    assert all(
        observation.values["instrument_id"] == INSTRUMENT_ID
        for observation in observations
    )

    decision_timestamp = _now()
    _assert_aware_utc(decision_timestamp, "decision_timestamp")

    config = _config()
    engine = AnalysisEngine()
    algorithm = SMAAlgorithm()

    result = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=config,
        algorithm=algorithm,
    )

    selected = tuple(
        observation
        for observation in observations[-PERIOD:]
    )
    assert result.observation_ids == tuple(
        observation.observation_id for observation in selected
    )
    assert len(selected) == PERIOD

    for observation in selected:
        assert observation.available_timestamp <= decision_timestamp

    closes = tuple(
        observation.values["close"] for observation in selected
    )
    assert all(isinstance(close, Decimal) for close in closes)
    expected_sma = sum(closes, Decimal("0")) / Decimal(str(PERIOD))

    assert result.symbol == CANONICAL_SYMBOL
    assert result.values["sma"] == expected_sma
    assert isinstance(result.values["sma"], Decimal)
    assert result.values["period"] == PERIOD

    assert result.provenance.nature is ArtifactNature.DERIVED
    assert result.provenance.metadata["algorithm_identity"] == ALGORITHM_IDENTITY
    assert result.provenance.metadata["algorithm_version"] == ALGORITHM_VERSION
    assert result.provenance.metadata["effective_parameters"] == {"period": PERIOD}

    second = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=config,
        algorithm=algorithm,
    )
    assert second.values == result.values
    assert second.observation_ids == result.observation_ids
    assert second.provenance.metadata["algorithm_identity"] == (
        result.provenance.metadata["algorithm_identity"]
    )
    assert second.provenance.metadata["algorithm_version"] == (
        result.provenance.metadata["algorithm_version"]
    )
    assert second.provenance.metadata["effective_parameters"] == (
        result.provenance.metadata["effective_parameters"]
    )

    future_observation = replace(
        selected[0],
        available_timestamp=decision_timestamp + timedelta(seconds=1),
    )
    lookahead_observations = (future_observation, *selected[1:])
    try:
        engine.execute(
            lookahead_observations,
            decision_timestamp=decision_timestamp,
            configuration=config,
            algorithm=algorithm,
        )
    except TemporalContractError as exc:
        assert "look-ahead" in str(exc).lower()
    else:
        raise AssertionError(
            "AnalysisEngine accepted an observation available after decision_timestamp"
        )

    for observation in selected:
        _assert_aware_utc(
            observation.available_timestamp,
            f"available_timestamp[{observation.observation_id}]",
        )
        assert observation.available_timestamp <= decision_timestamp

    return {
        "provider_symbol": SYMBOL,
        "canonical_symbol": result.symbol,
        "instrument_id": INSTRUMENT_ID,
        "observed_candles": len(market_data),
        "selected_observations": selected,
        "decision_timestamp": decision_timestamp,
        "sma": result.values["sma"],
        "expected_sma": expected_sma,
        "result": result,
    }


def main() -> int:
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"),
    )
    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        config=BinanceSpotRestConfig(),
        consumer_handoff_clock=_now,
    )

    evidence = _run(metadata=metadata, adapter=adapter)
    result = evidence["result"]
    selected = evidence["selected_observations"]

    print("FASE 1.31 — REAL BINANCE REST TO SMA E2E")
    print(f"Binance provider symbol: {evidence['provider_symbol']}")
    print(f"Canonical instrument symbol: {evidence['canonical_symbol']}")
    print(f"Instrument ID: {evidence['instrument_id']}")
    print(f"Observed candles: {evidence['observed_candles']}")
    print(
        "Selected observations: "
        + ", ".join(observation.observation_id for observation in selected)
    )
    print(f"Decision timestamp: {evidence['decision_timestamp'].isoformat()}")
    print(f"SMA result: {evidence['sma']}")
    print(f"Expected SMA: {evidence['expected_sma']}")
    print(f"Decimal: {isinstance(evidence['sma'], Decimal)}")
    print("Look-ahead protection: PASS")
    print("Availability contract: available_timestamp <= decision_timestamp")
    print(f"Algorithm identity: {result.provenance.metadata['algorithm_identity']}")
    print(f"Algorithm version: {result.provenance.metadata['algorithm_version']}")
    print(f"Period: {result.values['period']}")
    print(f"Observation IDs: {result.observation_ids}")
    print(f"Provenance nature: {result.provenance.nature.value}")
    print("Determinism: PASS")
    print("RESULT: REAL E2E PASS")
    return 0


_EXTERNAL_ERRORS = (
    metadata_module.ExchangeInfoTransportError,
    metadata_module.ExchangeInfoHTTPError,
    spot_module.BinanceTransportError,
    spot_module.BinanceHTTPError,
    spot_module.BinanceRateLimitError,
)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except _EXTERNAL_ERRORS as exc:
        print(
            f"REAL E2E ENVIRONMENT BLOCKED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    except Exception as exc:
        print(f"REAL E2E IMPLEMENTATION FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
