"""Real Binance Spot REST -> canonical MarketData -> EMA/RSI E2E validation for FASE 1.34."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot_obrero import binance_instruments as metadata_module
from bot_obrero import binance_spot as spot_module
from bot_obrero.analysis_contracts import ArtifactNature
from bot_obrero.analysis_ema import EMAAlgorithm
from bot_obrero.analysis_engine import (
    AnalysisCalculationConfig,
    AnalysisEngine,
    TemporalContractError,
    WindowSpecification,
)
from bot_obrero.analysis_rsi import RSIAlgorithm
from bot_obrero.binance_instruments import (
    BinanceMetadataBackedAdapter,
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
)
from bot_obrero.binance_spot import BinanceSpotRestConfig, _datetime_to_ms
from bot_obrero.market_data import to_market_observation


SYMBOL = "BTCUSDT"
INTERVAL = "1m"
CANONICAL_SYMBOL = "BTC/USDT"
INSTRUMENT_ID = "binance:SPOT:BTCUSDT"

PERIOD = 5
EMA_IDENTITY = "indicator.ema"
EMA_VERSION = "1.0.0"
RSI_IDENTITY = "indicator.rsi"
RSI_VERSION = "1.0.0"

HISTORICAL_LOOKBACK_MINUTES = 15
HISTORICAL_END_OFFSET_MINUTES = 2
HISTORICAL_PAGE_LIMIT = 100
HISTORICAL_MAX_REQUESTS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _assert_aware_utc(value: datetime, field_name: str) -> None:
    assert value.tzinfo is not None and value.utcoffset() == timedelta(0), (
        f"{field_name} must be timezone-aware UTC: {value!r}"
    )


def _ema_config() -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="indicator.ema",
        algorithm_identity=EMA_IDENTITY,
        algorithm_version=EMA_VERSION,
        effective_configuration={
            "provider": "binance",
            "market": "SPOT",
            "symbol": SYMBOL,
            "timeframe": INTERVAL,
        },
        effective_parameters={"period": PERIOD},
        window=WindowSpecification.all(),
    )


def _rsi_config() -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type="indicator.rsi",
        algorithm_identity=RSI_IDENTITY,
        algorithm_version=RSI_VERSION,
        effective_configuration={
            "provider": "binance",
            "market": "SPOT",
            "symbol": SYMBOL,
            "timeframe": INTERVAL,
        },
        effective_parameters={"period": PERIOD},
        window=WindowSpecification.all(),
    )


def _expected_ema(closes: tuple[Decimal, ...], period: int) -> Decimal:
    """Independent EMA expected-value calculation; never calls EMAAlgorithm."""
    alpha = Decimal("2") / Decimal(period + 1)
    ema = sum(closes[:period], Decimal("0")) / Decimal(period)
    for close in closes[period:]:
        ema = alpha * close + (Decimal("1") - alpha) * ema
    return ema


def _expected_rsi(closes: tuple[Decimal, ...], period: int) -> Decimal:
    """Independent Wilder RSI expected-value calculation; never calls RSIAlgorithm."""
    previous_close = closes[0]
    gain_total = Decimal("0")
    loss_total = Decimal("0")
    average_gain: Decimal | None = None
    average_loss: Decimal | None = None
    rsi: Decimal | None = None

    for index in range(1, len(closes)):
        close = closes[index]
        change = close - previous_close
        gain = change if change > Decimal("0") else Decimal("0")
        loss = -change if change < Decimal("0") else Decimal("0")

        if index <= period:
            gain_total += gain
            loss_total += loss
            if index == period:
                average_gain = gain_total / Decimal(period)
                average_loss = loss_total / Decimal(period)
        else:
            assert average_gain is not None
            assert average_loss is not None
            average_gain = (
                (average_gain * Decimal(period - 1)) + gain
            ) / Decimal(period)
            average_loss = (
                (average_loss * Decimal(period - 1)) + loss
            ) / Decimal(period)

        if index >= period:
            assert average_gain is not None
            assert average_loss is not None
            if average_loss == Decimal("0"):
                if average_gain > Decimal("0"):
                    rsi = Decimal("100")
                elif average_gain == Decimal("0"):
                    rsi = Decimal("50")
                else:
                    raise AssertionError("average_gain cannot be negative")
            elif average_gain == Decimal("0"):
                rsi = Decimal("0")
            else:
                rs = average_gain / average_loss
                rsi = Decimal("100") - (
                    Decimal("100") / (Decimal("1") + rs)
                )

        previous_close = close

    assert rsi is not None
    return rsi


def _snapshot(observations) -> tuple:
    return tuple(
        (
            item.observation_id,
            item.observation_timestamp,
            item.available_timestamp,
            dict(item.values),
            item.provenance,
        )
        for item in observations
    )


def _assert_result_provenance(result, *, identity: str, version: str) -> None:
    assert result.provenance.nature is ArtifactNature.DERIVED
    assert result.provenance.metadata["algorithm_identity"] == identity
    assert result.provenance.metadata["algorithm_version"] == version
    assert result.provenance.metadata["effective_parameters"] == {"period": PERIOD}


def _run(metadata: BinanceSpotInstrumentMetadata, adapter: BinanceMetadataBackedAdapter) -> dict:
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

    assert len(market_data) >= 10, (
        f"expected at least 10 real historical candles, got {len(market_data)}"
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

    for observation in observations:
        _assert_aware_utc(
            observation.available_timestamp,
            f"available_timestamp[{observation.observation_id}]",
        )
        assert observation.available_timestamp <= decision_timestamp

    selected = tuple(observations)
    closes = tuple(observation.values["close"] for observation in selected)
    assert len(closes) == len(selected)
    assert all(isinstance(close, Decimal) for close in closes)

    expected_ema = _expected_ema(closes, PERIOD)
    expected_rsi = _expected_rsi(closes, PERIOD)

    engine = AnalysisEngine()
    ema_config = _ema_config()
    rsi_config = _rsi_config()

    before_snapshot = _snapshot(observations)

    ema_result = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=ema_config,
        algorithm=EMAAlgorithm(),
    )
    ema_second = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=ema_config,
        algorithm=EMAAlgorithm(),
    )

    assert ema_result.symbol == CANONICAL_SYMBOL
    assert ema_result.observation_ids == tuple(
        observation.observation_id for observation in selected
    )
    assert ema_result.values["ema"] == expected_ema
    assert isinstance(ema_result.values["ema"], Decimal)
    assert ema_result.values["period"] == PERIOD
    _assert_result_provenance(
        ema_result,
        identity=EMA_IDENTITY,
        version=EMA_VERSION,
    )
    assert ema_second.values == ema_result.values
    assert ema_second.observation_ids == ema_result.observation_ids
    assert ema_second.provenance.metadata == ema_result.provenance.metadata

    rsi_result = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=rsi_config,
        algorithm=RSIAlgorithm(),
    )
    rsi_second = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=rsi_config,
        algorithm=RSIAlgorithm(),
    )

    assert rsi_result.symbol == CANONICAL_SYMBOL
    assert rsi_result.observation_ids == tuple(
        observation.observation_id for observation in selected
    )
    assert rsi_result.values["rsi"] == expected_rsi
    assert isinstance(rsi_result.values["rsi"], Decimal)
    assert rsi_result.values["period"] == PERIOD
    _assert_result_provenance(
        rsi_result,
        identity=RSI_IDENTITY,
        version=RSI_VERSION,
    )
    assert rsi_second.values == rsi_result.values
    assert rsi_second.observation_ids == rsi_result.observation_ids
    assert rsi_second.provenance.metadata == rsi_result.provenance.metadata

    assert _snapshot(observations) == before_snapshot

    ema_after_rsi = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=ema_config,
        algorithm=EMAAlgorithm(),
    )
    rsi_after_ema = engine.execute(
        observations,
        decision_timestamp=decision_timestamp,
        configuration=rsi_config,
        algorithm=RSIAlgorithm(),
    )
    assert ema_after_rsi.values == ema_result.values
    assert rsi_after_ema.values == rsi_result.values

    future_observation = replace(
        selected[0],
        available_timestamp=decision_timestamp + timedelta(seconds=1),
    )
    lookahead_observations = (future_observation, *selected[1:])

    for configuration, algorithm, label in (
        (ema_config, EMAAlgorithm(), "EMA"),
        (rsi_config, RSIAlgorithm(), "RSI"),
    ):
        try:
            engine.execute(
                lookahead_observations,
                decision_timestamp=decision_timestamp,
                configuration=configuration,
                algorithm=algorithm,
            )
        except TemporalContractError as exc:
            assert "look-ahead" in str(exc).lower(), (
                f"{label} rejected for an unexpected temporal reason: {exc}"
            )
        else:
            raise AssertionError(
                f"AnalysisEngine accepted a look-ahead observation for {label}"
            )

    assert _snapshot(observations) == before_snapshot

    return {
        "provider_symbol": SYMBOL,
        "canonical_symbol": CANONICAL_SYMBOL,
        "instrument_id": INSTRUMENT_ID,
        "observed_candles": len(market_data),
        "selected": selected,
        "decision_timestamp": decision_timestamp,
        "expected_ema": expected_ema,
        "expected_rsi": expected_rsi,
        "ema_result": ema_result,
        "rsi_result": rsi_result,
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
    ema = evidence["ema_result"]
    rsi = evidence["rsi_result"]
    selected = evidence["selected"]

    print("FASE 1.34 — REAL BINANCE REST TO EMA + RSI E2E")
    print(f"Binance provider symbol: {evidence['provider_symbol']}")
    print(f"Canonical instrument symbol: {evidence['canonical_symbol']}")
    print(f"Instrument ID: {evidence['instrument_id']}")
    print(f"Timeframe: {INTERVAL}")
    print(f"Observed candles: {evidence['observed_candles']}")
    print(
        "Observation IDs: "
        + ", ".join(observation.observation_id for observation in selected)
    )
    print(f"Decision timestamp: {evidence['decision_timestamp'].isoformat()}")

    print(f"EMA algorithm identity: {ema.provenance.metadata['algorithm_identity']}")
    print(f"EMA version: {ema.provenance.metadata['algorithm_version']}")
    print(f"EMA period: {ema.values['period']}")
    print(f"EMA result: {ema.values['ema']}")
    print(f"EMA expected: {evidence['expected_ema']}")
    print(f"EMA Decimal: {isinstance(ema.values['ema'], Decimal)}")
    print(f"EMA observation IDs: {ema.observation_ids}")
    print("EMA provenance nature: DERIVED")
    print("EMA determinism: PASS")

    print(f"RSI algorithm identity: {rsi.provenance.metadata['algorithm_identity']}")
    print(f"RSI version: {rsi.provenance.metadata['algorithm_version']}")
    print(f"RSI period: {rsi.values['period']}")
    print(f"RSI result: {rsi.values['rsi']}")
    print(f"RSI expected: {evidence['expected_rsi']}")
    print(f"RSI Decimal: {isinstance(rsi.values['rsi'], Decimal)}")
    print(f"RSI observation IDs: {rsi.observation_ids}")
    print("RSI provenance nature: DERIVED")
    print("RSI determinism: PASS")

    print("Look-ahead EMA: PASS")
    print("Look-ahead RSI: PASS")
    print("Availability contract: available_timestamp <= decision_timestamp")
    print("Canonical path: Binance REST -> MarketData -> MarketObservation -> AnalysisEngine -> indicators -> AnalysisResult")
    print("No mutation / indicator independence: PASS")
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
        print(
            f"REAL E2E IMPLEMENTATION FAILURE: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
