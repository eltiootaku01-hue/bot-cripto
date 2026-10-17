"""Fail-closed market-data → Signal → optional PAPER TradeProposal pipeline.

This is an analysis/proposal producer, not an order executor. It only consumes
canonical 4h candles with explicit availability, contiguous chronology, closed
and complete candle state, and a FINAL latest candle. A caller must separately
authorize any later PAPER execution through the existing financial runtime.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping

from .analysis_contracts import (
    ArtifactNature,
    MarketObservation,
    Provenance,
    Signal,
)
from .analysis_engine import (
    AnalysisCalculationConfig,
    AnalysisEngine,
    WindowSpecification,
)
from .analysis_ema import EMAAlgorithm, EMA_ALGORITHM_IDENTITY, EMA_ALGORITHM_VERSION
from .analysis_rsi import RSIAlgorithm, RSI_ALGORITHM_IDENTITY, RSI_ALGORITHM_VERSION
from .analysis_snapshot import AnalysisSnapshot
from .binance_intervals import validate_binance_spot_interval
from .market_data import (
    CANDLE_DATA_TYPE,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    MarketData,
)
from .signal_trade_proposal_bridge import (
    PaperProposalPolicy,
    SignalTradeProposalBridgeError,
    build_paper_trade_proposal,
)
from .strategy_contracts import (
    StrategicArtifact,
    StrategyCalculationConfig,
    build_strategic_artifact,
)
from .strategy_runtime import (
    StrategyRuntime,
    StrategyRuntimeResult,
    StrategyRuntimeState,
)
from .trade_proposal import TradeProposal

STRATEGY_IDENTITY = "strategy.ema_rsi_crossover"
STRATEGY_VERSION = "1.0.0"
TIMEFRAME = "4h"
BAR_DURATION = timedelta(hours=4)
CANDLE_CLOSE_EPSILON = timedelta(milliseconds=1)
DEFAULT_MAX_CANDLE_AGE = timedelta(hours=4, minutes=5)


class MarketSignalPipelineError(ValueError):
    """A market-data or strategy prerequisite is not admissible."""


class MarketSignalPipelineStatus(str, Enum):
    BLOCKED = "BLOCKED"
    NO_TRADE = "NO_TRADE"
    SIGNAL_READY = "SIGNAL_READY"
    PROPOSAL_READY = "PROPOSAL_READY"


@dataclass(frozen=True)
class EmaRsiCrossoverConfig:
    """Frozen technical-rule parameters; these are not a claim of economic edge."""

    fast_ema_period: int = 12
    slow_ema_period: int = 26
    rsi_period: int = 14
    rsi_buy_min: Decimal = Decimal("50")
    expiry_hours: int = 4

    def __post_init__(self) -> None:
        periods = (
            ("fast_ema_period", self.fast_ema_period),
            ("slow_ema_period", self.slow_ema_period),
            ("rsi_period", self.rsi_period),
        )
        for name, value in periods:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise MarketSignalPipelineError(f"{name.upper()}_INVALID")
        if self.fast_ema_period >= self.slow_ema_period:
            raise MarketSignalPipelineError("FAST_EMA_MUST_BE_LOWER_THAN_SLOW_EMA")
        if type(self.rsi_buy_min) is not Decimal or not self.rsi_buy_min.is_finite():
            raise MarketSignalPipelineError("RSI_BUY_MIN_MUST_BE_FINITE_DECIMAL")
        if not Decimal("0") <= self.rsi_buy_min <= Decimal("100"):
            raise MarketSignalPipelineError("RSI_BUY_MIN_OUT_OF_RANGE")
        if isinstance(self.expiry_hours, bool) or not isinstance(self.expiry_hours, int) or self.expiry_hours < 1:
            raise MarketSignalPipelineError("SIGNAL_EXPIRY_HOURS_INVALID")

    @property
    def minimum_observations(self) -> int:
        # The prior candle must contain a complete slow-EMA window; RSI needs
        # at least one more sample than its period.
        return max(self.slow_ema_period + 1, self.rsi_period + 1, 3)


@dataclass(frozen=True)
class MarketSignalPipelineResult:
    status: MarketSignalPipelineStatus
    reason: str
    decision_timestamp: datetime | None
    market_data_ids: tuple[str, ...] = ()
    excluded_market_data_ids: tuple[str, ...] = ()
    analysis_snapshot: AnalysisSnapshot | None = None
    strategy_runtime_result: StrategyRuntimeResult | None = None
    signal: Signal | None = None
    trade_proposal: TradeProposal | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketSignalPipelineError(f"{field_name.upper()}_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _finality_basis(item: MarketData) -> str:
    """Explain whether FINAL comes from Binance's explicit flag or local time."""
    if item.source.source_id == "binance-spot-websocket":
        return "BINANCE_WEBSOCKET_KLINE_X_FLAG"
    if item.source.source_id == "binance-spot-rest":
        return "REST_RECEIVE_TIME_AFTER_CANDLE_CLOSE"
    return "UNKNOWN"


def _closed_observation(item: MarketData) -> MarketObservation:
    candle = item.payload
    finality_basis = _finality_basis(item)
    return MarketObservation(
        symbol=item.instrument.symbol,
        observation_timestamp=candle.end,
        available_timestamp=item.available_at,
        observation_type="BINANCE_SPOT_CLOSED_CANDLE",
        values={
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "timeframe": candle.timeframe,
            "candle_start": candle.start.isoformat(),
            "candle_end": candle.end.isoformat(),
            "candle_state": candle.candle_state.value,
            "candle_completeness": candle.completeness.value,
            "finality": candle.finality.value,
            "candle_finality_basis": finality_basis,
            "market_data_completeness": item.completeness.value,
            "market_data_id": item.market_data_id,
            "source_id": item.source.source_id,
            "source_provider": item.source.provider,
            "source_venue": item.source.venue or "UNKNOWN",
        },
        provenance=Provenance(
            source=item.source.source_id,
            nature=ArtifactNature.OBSERVED,
            reference=item.market_data_id,
            metadata={
                "market_data_id": item.market_data_id,
                "source_id": item.source.source_id,
                "provider": item.source.provider,
                "venue": item.source.venue or "UNKNOWN",
                "available_at": item.available_at.isoformat(),
                "candle_finality": candle.finality.value,
                "candle_finality_basis": finality_basis,
                "candle_state": candle.candle_state.value,
            },
        ),
        venue=item.source.venue,
        observation_id=f"{item.market_data_id}:{candle.end.isoformat()}",
    )


def _indicator_config(
    *,
    symbol: str,
    period: int,
    identity: str,
    version: str,
    timeframe: str,
    operation: str,
) -> AnalysisCalculationConfig:
    return AnalysisCalculationConfig(
        analysis_type=identity,
        algorithm_identity=identity,
        algorithm_version=version,
        effective_configuration={
            "provider": "binance",
            "market": "SPOT",
            "symbol": symbol,
            "timeframe": timeframe,
            "operation": operation,
        },
        effective_parameters={"period": period},
        window=WindowSpecification.all(),
    )


class EmaRsiCrossoverAlgorithm:
    """Composite analysis invoking the project's canonical EMA and Wilder-RSI."""

    algorithm_identity = "analysis.ema_rsi_crossover"
    algorithm_version = "1.0.0"

    def compute(
        self,
        observations: Sequence[MarketObservation],
        configuration: AnalysisCalculationConfig,
    ) -> Mapping[str, Any]:
        if len(observations) < 2:
            raise MarketSignalPipelineError("INSUFFICIENT_OBSERVATIONS_FOR_CROSSOVER")
        params = configuration.effective_parameters
        fast_period = params.get("fast_ema_period")
        slow_period = params.get("slow_ema_period")
        rsi_period = params.get("rsi_period")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (fast_period, slow_period, rsi_period)):
            raise MarketSignalPipelineError("INDICATOR_PERIODS_MUST_BE_EXPLICIT_INTEGERS")

        symbol = observations[-1].symbol
        timeframe = str(configuration.effective_configuration["timeframe"])
        ema = EMAAlgorithm()
        rsi = RSIAlgorithm()
        fast_config = _indicator_config(
            symbol=symbol, period=fast_period, identity=EMA_ALGORITHM_IDENTITY,
            version=EMA_ALGORITHM_VERSION, timeframe=timeframe, operation="current_and_previous",
        )
        slow_config = _indicator_config(
            symbol=symbol, period=slow_period, identity=EMA_ALGORITHM_IDENTITY,
            version=EMA_ALGORITHM_VERSION, timeframe=timeframe, operation="current_and_previous",
        )
        rsi_config = _indicator_config(
            symbol=symbol, period=rsi_period, identity=RSI_ALGORITHM_IDENTITY,
            version=RSI_ALGORITHM_VERSION, timeframe=timeframe, operation="current",
        )
        previous = tuple(observations[:-1])
        return {
            "previous_fast_ema": ema.compute(previous, fast_config)["ema"],
            "current_fast_ema": ema.compute(observations, fast_config)["ema"],
            "previous_slow_ema": ema.compute(previous, slow_config)["ema"],
            "current_slow_ema": ema.compute(observations, slow_config)["ema"],
            "current_rsi": rsi.compute(observations, rsi_config)["rsi"],
            "latest_candle_finality_basis": observations[-1].values.get("candle_finality_basis", "UNKNOWN"),
            "candle_finality_bases": tuple(
                item.values.get("candle_finality_basis", "UNKNOWN")
                for item in observations
            ),
            "fast_ema_period": fast_period,
            "slow_ema_period": slow_period,
            "rsi_period": rsi_period,
        }


class EmaRsiCrossoverStrategy:
    """Versioned strategy transformer. It proposes a direction or NO_TRADE only."""

    strategy_identity = STRATEGY_IDENTITY
    strategy_version = STRATEGY_VERSION

    def compute(
        self,
        snapshot: AnalysisSnapshot,
        configuration: StrategyCalculationConfig,
    ) -> StrategicArtifact:
        if len(snapshot.results) != 1:
            raise MarketSignalPipelineError("COMPOSITE_ANALYSIS_RESULT_REQUIRED")
        analysis = snapshot.results[0]
        values = analysis.values
        previous_fast = values["previous_fast_ema"]
        current_fast = values["current_fast_ema"]
        previous_slow = values["previous_slow_ema"]
        current_slow = values["current_slow_ema"]
        current_rsi = values["current_rsi"]
        minimum_rsi = configuration.effective_parameters["rsi_buy_min"]

        crossed_up = previous_fast <= previous_slow and current_fast > current_slow
        crossed_down = previous_fast >= previous_slow and current_fast < current_slow
        if crossed_up and current_rsi >= minimum_rsi:
            direction = "UP"
            reason = "EMA_BULLISH_CROSS_WITH_RSI_CONFIRMATION"
        elif crossed_down:
            direction = "DOWN"
            reason = "EMA_BEARISH_CROSS"
        else:
            direction = "NO_TRADE"
            reason = "NO_CONFIRMED_EMA_RSI_SETUP"

        expires_at = snapshot.decision_timestamp + timedelta(
            hours=configuration.effective_parameters["expiry_hours"]
        )
        return build_strategic_artifact(
            snapshot,
            configuration,
            {
                "expected_direction": direction,
                "expected_horizon": TIMEFRAME,
                "invalidation_conditions": (
                    "signal_expired",
                    "market_data_stale",
                    "unknown_market_evidence",
                    "risk_rejected",
                ),
                "expires_at": expires_at,
                "hypothesis_status": "UNRESOLVED",
                "signal_evidence": {
                    "rule": reason,
                    "timeframe": TIMEFRAME,
                    "decision_timestamp": snapshot.decision_timestamp.isoformat(),
                    "latest_observation_id": snapshot.observation_ids[-1],
                    "observation_count": len(snapshot.observation_ids),
                    "previous_fast_ema": previous_fast,
                    "current_fast_ema": current_fast,
                    "previous_slow_ema": previous_slow,
                    "current_slow_ema": current_slow,
                    "current_rsi": current_rsi,
                    "rsi_buy_min": minimum_rsi,
                    "latest_candle_finality_basis": values.get("latest_candle_finality_basis", "UNKNOWN"),
                    "candle_finality_bases": values.get("candle_finality_bases", ()),
                },
            },
        )


def _blocked(
    reason: str,
    *,
    decision_timestamp: datetime | None,
    market_data_ids: Sequence[str] = (),
    excluded_market_data_ids: Sequence[str] = (),
    snapshot: AnalysisSnapshot | None = None,
    strategy_result: StrategyRuntimeResult | None = None,
    signal: Signal | None = None,
) -> MarketSignalPipelineResult:
    return MarketSignalPipelineResult(
        status=MarketSignalPipelineStatus.BLOCKED,
        reason=reason,
        decision_timestamp=decision_timestamp,
        market_data_ids=tuple(market_data_ids),
        excluded_market_data_ids=tuple(excluded_market_data_ids),
        analysis_snapshot=snapshot,
        strategy_runtime_result=strategy_result,
        signal=signal,
    )


def _validate_market_data(
    items: Sequence[MarketData],
    *,
    decision_timestamp: datetime,
    config: EmaRsiCrossoverConfig,
    expected_symbol: str | None,
    max_candle_age: timedelta,
) -> tuple[MarketObservation, ...]:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise MarketSignalPipelineError("MARKET_DATA_MUST_BE_A_SEQUENCE")
    if not items:
        raise MarketSignalPipelineError("MARKET_DATA_EMPTY")
    if len(items) < config.minimum_observations:
        raise MarketSignalPipelineError(
            f"INSUFFICIENT_CLOSED_CANDLES:{len(items)}<{config.minimum_observations}"
        )
    if max_candle_age <= timedelta(0):
        raise MarketSignalPipelineError("MAX_CANDLE_AGE_MUST_BE_POSITIVE")

    symbol = expected_symbol or (items[0].instrument.symbol if isinstance(items[0], MarketData) else None)
    if not isinstance(symbol, str) or not symbol.strip():
        raise MarketSignalPipelineError("EXPECTED_SYMBOL_REQUIRED_OR_UNRESOLVABLE")

    observations: list[MarketObservation] = []
    previous_item: MarketData | None = None
    seen: set[tuple[str, str, datetime]] = set()

    for index, item in enumerate(items):
        if not isinstance(item, MarketData):
            raise MarketSignalPipelineError(f"MARKET_DATA_ITEM_TYPE_INVALID:{index}")
        candle = item.payload
        if item.data_type != CANDLE_DATA_TYPE:
            raise MarketSignalPipelineError(f"MARKET_DATA_TYPE_INVALID:{index}")
        if item.instrument.symbol != symbol or item.instrument.market != "SPOT":
            raise MarketSignalPipelineError(f"INSTRUMENT_IDENTITY_MISMATCH:{index}")
        if (
            item.source.provider != "binance"
            or item.source.venue != "BINANCE"
            or item.source.source_id not in {"binance-spot-rest", "binance-spot-websocket"}
        ):
            raise MarketSignalPipelineError(
                f"MARKET_DATA_SOURCE_UNSUPPORTED:{index}:{item.source.source_id}"
            )
        if item.quality is not DataQuality.VALID:
            raise MarketSignalPipelineError(f"MARKET_DATA_QUALITY_NOT_VALID:{index}")
        if candle.timeframe != TIMEFRAME:
            raise MarketSignalPipelineError(f"TIMEFRAME_MISMATCH:{index}:{candle.timeframe}")
        if candle.candle_state is not CandleState.CLOSED:
            raise MarketSignalPipelineError(f"CANDLE_NOT_CLOSED:{index}")
        if candle.completeness is not DataCompleteness.COMPLETE:
            raise MarketSignalPipelineError(f"CANDLE_NOT_COMPLETE:{index}")
        if candle.finality is CandleFinality.NOT_FINAL:
            raise MarketSignalPipelineError(f"CANDLE_NOT_FINAL:{index}")
        if item.available_at is None:
            raise MarketSignalPipelineError(f"AVAILABLE_AT_UNKNOWN:{item.market_data_id}")
        available_at = _require_aware(item.available_at, "available_at")
        received_at = _require_aware(item.received_at, "received_at")
        candle_end = _require_aware(candle.end, "candle_end")
        _require_aware(candle.start, "candle_start")
        if available_at < received_at:
            raise MarketSignalPipelineError(f"AVAILABILITY_PRECEDES_RECEIPT:{item.market_data_id}")
        if candle_end > available_at:
            raise MarketSignalPipelineError(f"CANDLE_CLOSE_NOT_AVAILABLE:{item.market_data_id}")
        if available_at > decision_timestamp:
            raise MarketSignalPipelineError(f"MARKET_DATA_NOT_YET_AVAILABLE:{item.market_data_id}")
        if candle_end > decision_timestamp:
            raise MarketSignalPipelineError(f"CANDLE_CLOSE_AFTER_DECISION:{item.market_data_id}")

        if candle.end - candle.start != BAR_DURATION - CANDLE_CLOSE_EPSILON:
            raise MarketSignalPipelineError(f"CANDLE_DURATION_INVALID:{item.market_data_id}")
        identity = item.candle_identity
        if identity in seen:
            raise MarketSignalPipelineError(f"DUPLICATE_CANDLE:{item.market_data_id}")
        seen.add(identity)

        if previous_item is not None:
            expected_start = previous_item.payload.end + CANDLE_CLOSE_EPSILON
            if candle.start != expected_start:
                raise MarketSignalPipelineError(
                    f"CANDLE_SEQUENCE_GAP_OR_ORDER_ERROR:{previous_item.market_data_id}->{item.market_data_id}"
                )
            if candle.end <= previous_item.payload.end:
                raise MarketSignalPipelineError("CANDLES_NOT_STRICTLY_CHRONOLOGICAL")
        previous_item = item
        observations.append(_closed_observation(item))

    last = items[-1]
    if last.payload.finality is not CandleFinality.FINAL:
        raise MarketSignalPipelineError("LATEST_CANDLE_FINALITY_NOT_PROVEN")
    age = decision_timestamp - last.payload.end
    if age < timedelta(0) or age > max_candle_age:
        raise MarketSignalPipelineError(f"LATEST_CANDLE_STALE:{age}")
    # Validate each event/availability pair through the canonical temporal primitive.
    for item in items:
        try:
            item.evidence_at(decision_timestamp)
        except (TypeError, ValueError) as exc:
            raise MarketSignalPipelineError(
                f"TEMPORAL_EVIDENCE_REJECTED:{item.market_data_id}:{exc}"
            ) from exc
    return tuple(observations)


def generate_market_signal(
    items: Sequence[MarketData],
    *,
    decision_timestamp: datetime,
    config: EmaRsiCrossoverConfig = EmaRsiCrossoverConfig(),
    expected_symbol: str | None = None,
    max_candle_age: timedelta = DEFAULT_MAX_CANDLE_AGE,
    proposal_policy: PaperProposalPolicy | None = None,
) -> MarketSignalPipelineResult:
    """Generate a canonical Signal and, only under explicit policy, a TradeProposal.

    This function has no provider, order adapter, persistence, or submission call.
    """
    ids = tuple(item.market_data_id for item in items if isinstance(item, MarketData))
    try:
        decision = _require_aware(decision_timestamp, "decision_timestamp")
    except MarketSignalPipelineError as exc:
        return _blocked(str(exc), decision_timestamp=None, market_data_ids=ids)
    try:
        observations = _validate_market_data(
            items,
            decision_timestamp=decision,
            config=config,
            expected_symbol=expected_symbol,
            max_candle_age=max_candle_age,
        )
        effective_symbol = expected_symbol or items[0].instrument.symbol
        effective_config = {
            "provider": "binance",
            "market": "SPOT",
            "symbol": effective_symbol,
            "timeframe": TIMEFRAME,
            "mode": "PAPER_SIGNAL_PRODUCTION",
            "strategy_identity": STRATEGY_IDENTITY,
            "strategy_version": STRATEGY_VERSION,
        }
        parameters = {
            "fast_ema_period": config.fast_ema_period,
            "slow_ema_period": config.slow_ema_period,
            "rsi_period": config.rsi_period,
            "rsi_buy_min": config.rsi_buy_min,
            "expiry_hours": config.expiry_hours,
        }
        analysis_config = AnalysisCalculationConfig(
            analysis_type=EmaRsiCrossoverAlgorithm.algorithm_identity,
            algorithm_identity=EmaRsiCrossoverAlgorithm.algorithm_identity,
            algorithm_version=EmaRsiCrossoverAlgorithm.algorithm_version,
            effective_configuration=effective_config,
            effective_parameters=parameters,
            window=WindowSpecification.all(),
        )
        result = AnalysisEngine().execute(
            observations,
            decision_timestamp=decision,
            configuration=analysis_config,
            algorithm=EmaRsiCrossoverAlgorithm(),
        )
        snapshot = AnalysisSnapshot.from_results((result,))
        strategy_config = StrategyCalculationConfig(
            strategy_type="TECHNICAL_CROSSOVER",
            strategy_identity=STRATEGY_IDENTITY,
            strategy_version=STRATEGY_VERSION,
            effective_configuration=effective_config,
            effective_parameters=parameters,
        )
        runtime_result = StrategyRuntime(clock=lambda: decision).execute(
            snapshot,
            strategy_config,
            EmaRsiCrossoverStrategy(),
        )
        if runtime_result.state is not StrategyRuntimeState.COMPLETED or runtime_result.signal is None:
            return _blocked(
                f"STRATEGY_RUNTIME_{runtime_result.state.value}:{runtime_result.reason}",
                decision_timestamp=decision,
                market_data_ids=ids,
                snapshot=snapshot,
                strategy_result=runtime_result,
            )

        direction = runtime_result.signal.direction
        if direction == "NO_TRADE":
            return MarketSignalPipelineResult(
                status=MarketSignalPipelineStatus.NO_TRADE,
                reason=str(runtime_result.signal.evidence.get("rule", "NO_CONFIRMED_EMA_RSI_SETUP")),
                decision_timestamp=decision,
                market_data_ids=ids,
                analysis_snapshot=snapshot,
                strategy_runtime_result=runtime_result,
            )
        if direction not in {"UP", "DOWN"}:
            return _blocked(
                f"UNSUPPORTED_SIGNAL_DIRECTION:{direction}",
                decision_timestamp=decision,
                market_data_ids=ids,
                snapshot=snapshot,
                strategy_result=runtime_result,
                signal=runtime_result.signal,
            )

        if proposal_policy is None:
            return MarketSignalPipelineResult(
                status=MarketSignalPipelineStatus.SIGNAL_READY,
                reason="VALID_SIGNAL_BUILT_NO_PROPOSAL_POLICY_SUPPLIED",
                decision_timestamp=decision,
                market_data_ids=ids,
                analysis_snapshot=snapshot,
                strategy_runtime_result=runtime_result,
                signal=runtime_result.signal,
            )
        try:
            proposal = build_paper_trade_proposal(
                signal=runtime_result.signal,
                policy=proposal_policy,
                decision_timestamp=decision,
            )
        except SignalTradeProposalBridgeError as exc:
            return _blocked(
                f"PAPER_PROPOSAL_REJECTED:{exc}",
                decision_timestamp=decision,
                market_data_ids=ids,
                snapshot=snapshot,
                strategy_result=runtime_result,
                signal=runtime_result.signal,
            )
        return MarketSignalPipelineResult(
            status=MarketSignalPipelineStatus.PROPOSAL_READY,
            reason="PAPER_TRADE_PROPOSAL_BUILT_NOT_SUBMITTED",
            decision_timestamp=decision,
            market_data_ids=ids,
            analysis_snapshot=snapshot,
            strategy_runtime_result=runtime_result,
            signal=runtime_result.signal,
            trade_proposal=proposal,
        )
    except (MarketSignalPipelineError, ValueError, TypeError, KeyError) as exc:
        return _blocked(
            f"MARKET_SIGNAL_PIPELINE_REJECTED:{exc}",
            decision_timestamp=decision,
            market_data_ids=ids,
        )
    except Exception as exc:
        # Strategy/provider failures are never converted to a tradable result.
        return _blocked(
            f"MARKET_SIGNAL_PIPELINE_UNKNOWN:{type(exc).__name__}:{exc}",
            decision_timestamp=decision,
            market_data_ids=ids,
        )


def generate_market_signal_from_rest_adapter(
    adapter: Any,
    *,
    provider_symbol: str,
    clock: Callable[[], datetime] = _utc_now,
    limit: int = 100,
    config: EmaRsiCrossoverConfig = EmaRsiCrossoverConfig(),
    expected_symbol: str | None = None,
    max_candle_age: timedelta = DEFAULT_MAX_CANDLE_AGE,
    proposal_policy: PaperProposalPolicy | None = None,
) -> MarketSignalPipelineResult:
    """Acquire a bounded public REST candle page, then build a point-in-time signal.

    The adapter must be configured with an explicit consumer_handoff_clock.
    A missing availability timestamp stays UNKNOWN and blocks signal production.
    """
    try:
        if not hasattr(adapter, "fetch_market_data") or not callable(adapter.fetch_market_data):
            raise MarketSignalPipelineError("MARKET_DATA_ADAPTER_REQUIRED")
        if not isinstance(provider_symbol, str) or not provider_symbol.strip():
            raise MarketSignalPipelineError("PROVIDER_SYMBOL_REQUIRED")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise MarketSignalPipelineError("CANDLE_LIMIT_OUT_OF_RANGE")
        decision_items = list(
            adapter.fetch_market_data(symbol=provider_symbol, interval=TIMEFRAME, limit=limit)
        )
        decision = _require_aware(clock(), "decision_timestamp")
    except Exception as exc:
        return _blocked(
            f"PUBLIC_CANDLE_ACQUISITION_FAILED:{type(exc).__name__}:{exc}",
            decision_timestamp=None,
        )

    excluded_ids: list[str] = []
    # The REST endpoint naturally includes an in-progress trailing candle.
    # Only that confirmed incomplete suffix is removed; internal gaps are never skipped.
    while decision_items:
        last = decision_items[-1]
        if not isinstance(last, MarketData):
            break
        candle = last.payload
        if candle.candle_state is CandleState.OPEN and candle.completeness is DataCompleteness.PARTIAL:
            excluded_ids.append(last.market_data_id)
            decision_items.pop()
            continue
        break

    result = generate_market_signal(
        decision_items,
        decision_timestamp=decision,
        config=config,
        expected_symbol=expected_symbol,
        max_candle_age=max_candle_age,
        proposal_policy=proposal_policy,
    )
    if excluded_ids:
        return MarketSignalPipelineResult(
            status=result.status,
            reason=result.reason,
            decision_timestamp=result.decision_timestamp,
            market_data_ids=result.market_data_ids,
            excluded_market_data_ids=tuple(reversed(excluded_ids)),
            analysis_snapshot=result.analysis_snapshot,
            strategy_runtime_result=result.strategy_runtime_result,
            signal=result.signal,
            trade_proposal=result.trade_proposal,
        )
    return result


__all__ = [
    "DEFAULT_MAX_CANDLE_AGE",
    "EmaRsiCrossoverConfig",
    "EmaRsiCrossoverStrategy",
    "MarketSignalPipelineError",
    "MarketSignalPipelineResult",
    "MarketSignalPipelineStatus",
    "STRATEGY_IDENTITY",
    "STRATEGY_VERSION",
    "TIMEFRAME",
    "generate_market_signal",
    "generate_market_signal_from_rest_adapter",
]
