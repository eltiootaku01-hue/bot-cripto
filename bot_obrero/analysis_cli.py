"""Small read-only Binance Spot CLI for SMA, EMA and RSI analysis.

This module never accesses private exchange endpoints or financial execution.
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from .analysis_ema import EMAAlgorithm
from .analysis_engine import AnalysisCalculationConfig, AnalysisEngine, WindowSpecification
from .analysis_rsi import RSIAlgorithm
from .analysis_sma import SMAAlgorithm
from .availability import AvailabilityEvidence
from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_intervals import BinanceSpotIntervalUnit, validate_binance_spot_interval
from .binance_spot import BinanceSpotRestConfig
from .market_data import (
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    MarketData,
    to_market_observation,
)
from .market_data_consumption import (
    MarketDataConsumptionPolicy,
    MarketDataConsumptionStatus,
    evaluate_market_data_consumption,
)

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
_MAX_CANDLES = 999


class AnalysisCLIError(ValueError):
    """Input, acquisition or market-data admissibility failure."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only SMA/EMA/RSI analysis using public Binance Spot data."
    )
    parser.add_argument("--symbol", required=True, help="Binance Spot symbol, e.g. BTCUSDT")
    parser.add_argument("--interval", required=True, help="Binance kline interval, e.g. 1m")
    parser.add_argument("--candles", type=_positive_int, default=100,
                        help="closed candles to analyze (default: 100; maximum: 999)")
    parser.add_argument("--sma-period", type=_positive_int, default=20)
    parser.add_argument("--ema-period", type=_positive_int, default=20)
    parser.add_argument("--rsi-period", type=_positive_int, default=14)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not _SYMBOL_RE.fullmatch(args.symbol):
        raise AnalysisCLIError("invalid symbol format; use an uppercase Binance Spot symbol")
    if args.candles > _MAX_CANDLES:
        raise AnalysisCLIError(f"--candles must be at most {_MAX_CANDLES}; one extra slot is reserved")
    try:
        validate_binance_spot_interval(args.interval)
    except (TypeError, ValueError) as exc:
        raise AnalysisCLIError(f"invalid Binance Spot interval: {args.interval}") from exc
    if args.candles < args.rsi_period + 1:
        raise AnalysisCLIError("--candles must exceed --rsi-period to calculate RSI")
    if args.candles < args.sma_period:
        raise AnalysisCLIError("--candles must be at least --sma-period")
    if args.candles < args.ema_period:
        raise AnalysisCLIError("--candles must be at least --ema-period")


def _next_month(value: datetime) -> datetime:
    year, month = value.year, value.month + 1
    if month == 13:
        year, month = year + 1, 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _expected_next_start(start: datetime, interval: str) -> datetime:
    """Return the next candle start using the canonical Binance interval contract."""
    try:
        specification = validate_binance_spot_interval(interval)
    except (TypeError, ValueError) as exc:
        raise AnalysisCLIError(
            f"unsupported interval for continuity validation: {interval}"
        ) from exc
    if specification.unit is BinanceSpotIntervalUnit.MONTH:
        result = start
        for _ in range(specification.magnitude):
            result = _next_month(result)
        return result
    if specification.duration is None:
        raise AnalysisCLIError(
            f"interval has no fixed duration for continuity validation: {interval}"
        )
    return start + specification.duration


def _closed_complete_items(items: Sequence[MarketData], interval: str) -> tuple[list[MarketData], int]:
    if not items:
        raise AnalysisCLIError("Binance returned no market data")

    ordered = list(items)
    starts = [item.payload.start for item in ordered]
    if starts != sorted(starts):
        raise AnalysisCLIError("candles are not ordered by start time")
    if len(set(starts)) != len(starts):
        raise AnalysisCLIError("duplicate candle start time detected")

    excluded = 0
    admissible: list[MarketData] = []
    for index, item in enumerate(ordered):
        candle = item.payload
        is_last = index == len(ordered) - 1

        # UNKNOWN is not evidence that a candle is open or partial. Fail closed
        # before considering the final-candle exclusion rule.
        unknown_fields = []
        if candle.candle_state is CandleState.UNKNOWN:
            unknown_fields.append("candle state")
        if candle.completeness is DataCompleteness.UNKNOWN:
            unknown_fields.append("candle completeness")
        if item.completeness is DataCompleteness.UNKNOWN:
            unknown_fields.append("MarketData completeness")
        if candle.finality is CandleFinality.UNKNOWN:
            unknown_fields.append("candle finality")
        if unknown_fields:
            raise AnalysisCLIError(
                f"unknown {'/'.join(unknown_fields)} at {candle.start.isoformat()}; "
                "admissibility cannot be confirmed"
            )

        explicit_open_or_partial = (
            candle.candle_state is CandleState.OPEN
            or candle.completeness is DataCompleteness.PARTIAL
            or item.completeness is DataCompleteness.PARTIAL
            or candle.finality is CandleFinality.NOT_FINAL
        )
        if is_last and explicit_open_or_partial:
            excluded += 1
            continue

        if candle.candle_state is not CandleState.CLOSED:
            raise AnalysisCLIError(f"inadmissible internal candle state at {candle.start.isoformat()}")
        if candle.completeness is not DataCompleteness.COMPLETE:
            raise AnalysisCLIError(f"incomplete internal candle at {candle.start.isoformat()}")
        if item.completeness is not DataCompleteness.COMPLETE:
            raise AnalysisCLIError(f"incomplete MarketData at {candle.start.isoformat()}")
        if item.quality is not DataQuality.VALID:
            raise AnalysisCLIError(f"market-data quality is {item.quality.value} at {candle.start.isoformat()}")
        if candle.finality is CandleFinality.NOT_FINAL:
            raise AnalysisCLIError(f"non-final internal candle at {candle.start.isoformat()}")
        admissible.append(item)

    for previous, current in zip(admissible, admissible[1:]):
        expected = _expected_next_start(previous.payload.start, interval)
        if current.payload.start != expected:
            raise AnalysisCLIError(
                f"candle gap or interval mismatch: expected {expected.isoformat()}, "
                f"received {current.payload.start.isoformat()}"
            )
    return admissible, excluded


def _build_config(indicator: str, symbol: str, interval: str, period: int) -> AnalysisCalculationConfig:
    identity = f"indicator.{indicator}"
    window = WindowSpecification.last_n(period) if indicator == "sma" else WindowSpecification.all()
    return AnalysisCalculationConfig(
        analysis_type=identity,
        algorithm_identity=identity,
        algorithm_version="1.0.0",
        effective_configuration={
            "provider": "binance",
            "market": "SPOT",
            "symbol": symbol,
            "timeframe": interval,
        },
        effective_parameters={"period": period},
        window=window,
    )


def _analyze(items: Sequence[MarketData], *, decision_timestamp: datetime, symbol: str,
             interval: str, sma_period: int, ema_period: int, rsi_period: int):
    observations = []
    for item in items:
        decision = evaluate_market_data_consumption(
            item,
            decision_timestamp=decision_timestamp,
            policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        )
        if decision.status is not MarketDataConsumptionStatus.ACCEPTED:
            reason = decision.reason or decision.status.value
            raise AnalysisCLIError(
                f"market data rejected by temporal policy ({reason}) at "
                f"{item.payload.start.isoformat()}"
            )
        try:
            observations.append(to_market_observation(item))
        except (TypeError, ValueError) as exc:
            raise AnalysisCLIError(f"cannot promote market data to observation: {exc}") from exc

    engine = AnalysisEngine()
    specs = (
        ("sma", sma_period, SMAAlgorithm()),
        ("ema", ema_period, EMAAlgorithm()),
        ("rsi", rsi_period, RSIAlgorithm()),
    )
    results = []
    for name, period, algorithm in specs:
        config = _build_config(name, symbol, interval, period)
        try:
            result = engine.execute(
                observations,
                decision_timestamp=decision_timestamp,
                configuration=config,
                algorithm=algorithm,
            )
        except (ValueError, TypeError) as exc:
            raise AnalysisCLIError(f"{name.upper()} calculation failed: {exc}") from exc
        results.append((name, period, result))
    return results, observations


def _print_report(args: argparse.Namespace, requested_symbol: str, instrument, received: int,
                  admitted: Sequence[MarketData], excluded: int, decision_timestamp: datetime,
                  results, observations) -> None:
    print("BINANCE SPOT — READ-ONLY MARKET ANALYSIS")
    print(f"Requested symbol: {requested_symbol}")
    print(f"Canonical symbol: {instrument.symbol}")
    print(f"Instrument ID: {instrument.instrument_id}")
    print(f"Market: {instrument.market} / {instrument.instrument_type.value}")
    print(f"Interval: {args.interval}")
    print(f"Decision timestamp (UTC): {decision_timestamp.isoformat()}")
    print(f"Candles requested (closed target): {args.candles}")
    print(f"MarketData received: {received}")
    print(f"Admissible closed candles: {len(admitted)}")
    print(f"Excluded final candle(s): {excluded}")
    if excluded:
        print("WARNING: final open/partial/non-final candle excluded.")
    finality_values = sorted({item.payload.finality.value for item in admitted})
    quality_values = sorted({item.quality.value for item in admitted})
    completeness_values = sorted({item.completeness.value for item in admitted})
    print(f"Quality: {', '.join(quality_values) or 'UNKNOWN'}")
    print(f"Completeness: {', '.join(completeness_values) or 'UNKNOWN'}")
    print(f"Finality: {', '.join(finality_values) or 'UNKNOWN'}")
    print(f"Source: {admitted[-1].source.source_id} / {admitted[-1].source.provider} / {admitted[-1].source.venue}")
    print()
    for name, period, result in results:
        print(f"{name.upper()}:")
        for key, value in result.values.items():
            if isinstance(value, Decimal):
                print(f"  {key}: {value}")
        print(f"  algorithm: {result.provenance.metadata.get('algorithm_identity', 'UNKNOWN')}")
        print(f"  version: {result.provenance.metadata.get('algorithm_version', 'UNKNOWN')}")
        print(f"  period: {period}")
        print(f"  observations used: {len(result.observation_ids)}")
        print(f"  observation IDs: {', '.join(result.observation_ids)}")
        print(f"  source: {result.provenance.source}")
        print(f"  decision timestamp: {result.decision_timestamp.isoformat()}")
        print()


def run(args: argparse.Namespace) -> int:
    _validate_args(args)
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    instrument = metadata.resolve_active(args.symbol)
    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        consumer_handoff_clock=_utc_now,
    )
    received_items = adapter.fetch_market_data(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.candles + 1,
    )
    decision_timestamp = _utc_now()
    admissible, excluded = _closed_complete_items(received_items, args.interval)
    if len(admissible) < args.candles:
        raise AnalysisCLIError(
            f"insufficient admissible closed candles: need {args.candles}, got {len(admissible)}"
        )
    selected = admissible[-args.candles:]
    results, observations = _analyze(
        selected,
        decision_timestamp=decision_timestamp,
        symbol=instrument.symbol,
        interval=args.interval,
        sma_period=args.sma_period,
        ema_period=args.ema_period,
        rsi_period=args.rsi_period,
    )
    _print_report(
        args, args.symbol, instrument, len(received_items), selected, excluded,
        decision_timestamp, results, observations,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
