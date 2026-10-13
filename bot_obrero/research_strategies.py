"""Research-only strategy signal contracts and frozen candidate rules.

Strategies receive an immutable history ending at the decision candle. No future
bar is available through StrategyContext. This module has no financial-runtime
imports and maintains no real-account state.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, Sequence

from .analysis_ema import EMAAlgorithm
from .analysis_engine import AnalysisCalculationConfig, WindowSpecification


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class StrategySignal:
    action: SignalAction
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValueError("every research signal must have a readable reason")


@dataclass(frozen=True)
class StrategyContext:
    """Immutable point-in-time inputs; history ends at the current closed bar."""
    history: Sequence[Any]
    in_position: bool
    current_values: dict[str, Decimal]
    previous_values: dict[str, Decimal]
    timestamp: Any
    entries_in_rolling_window: int
    interval: str


class ResearchStrategy(Protocol):
    name: str
    warmup_bars: int

    def decide(self, context: StrategyContext) -> StrategySignal: ...


class EmaRsiStrategy:
    """Compatibility adapter for the legacy EMA/RSI entry and exit rules."""
    name = "EMA/RSI"
    requires_ema_rsi_indicators = True

    def __init__(self, fast_period: int = 12, slow_period: int = 26,
                 rsi_buy_min: Decimal = Decimal("50"),
                 rsi_sell_at: Decimal = Decimal("70"), rsi_period: int = 14) -> None:
        if fast_period <= 0 or slow_period <= fast_period:
            raise ValueError("EMA periods must satisfy 0 < fast < slow")
        self.fast_period, self.slow_period = fast_period, slow_period
        self.rsi_buy_min, self.rsi_sell_at = rsi_buy_min, rsi_sell_at
        if rsi_period <= 0:
            raise ValueError("RSI period must be positive")
        self.rsi_period = rsi_period
        self.warmup_bars = max(slow_period, rsi_period + 1)

    def decide(self, context: StrategyContext) -> StrategySignal:
        current, previous = context.current_values, context.previous_values
        up = previous["fast_ema"] <= previous["slow_ema"] and current["fast_ema"] > current["slow_ema"]
        down = previous["fast_ema"] >= previous["slow_ema"] and current["fast_ema"] < current["slow_ema"]
        if not context.in_position and up and current["rsi"] >= self.rsi_buy_min:
            return StrategySignal(SignalAction.BUY,
                "fast EMA crossed above slow EMA and RSI filter passed; rolling entry cap passed")
        if context.in_position and (down or current["rsi"] >= self.rsi_sell_at):
            return StrategySignal(SignalAction.SELL,
                "fast EMA crossed below slow EMA" if down else "RSI reached sell threshold")
        return StrategySignal(SignalAction.HOLD, "EMA/RSI entry or exit conditions not met")


class DonchianBreakoutStrategy:
    """Long-only breakout: 20 prior highs in, 10 prior lows out; current bar excluded."""
    name = "DONCHIAN_20_10"
    warmup_bars = 20

    def decide(self, context: StrategyContext) -> StrategySignal:
        bars = context.history
        if len(bars) < self.warmup_bars:
            return StrategySignal(SignalAction.HOLD, "DONCHIAN_WARMUP_INSUFFICIENT")
        current_close = bars[-1].values["close"]
        previous20 = bars[-21:-1]
        previous10 = bars[-11:-1]
        if not context.in_position and current_close > max(b.values["high"] for b in previous20):
            return StrategySignal(SignalAction.BUY, "close broke above prior 20-bar high; current bar excluded")
        if context.in_position and current_close < min(b.values["low"] for b in previous10):
            return StrategySignal(SignalAction.SELL, "close broke below prior 10-bar low; current bar excluded")
        return StrategySignal(SignalAction.HOLD, "Donchian breakout/exit condition not met")


class MeanReversionStrategy:
    """Frozen 20-bar population-z-score entry with EMA-200 trend filter."""
    name = "MEAN_REVERSION_20_EMA200"
    warmup_bars = 199

    def decide(self, context: StrategyContext) -> StrategySignal:
        bars = context.history
        if len(bars) < self.warmup_bars + 1:
            return StrategySignal(SignalAction.HOLD, "MEAN_REVERSION_WARMUP_INSUFFICIENT")
        previous_closes = [bar.values["close"] for bar in bars[-21:-1]]
        mean = sum(previous_closes, Decimal("0")) / Decimal(20)
        variance = sum(((price - mean) ** 2 for price in previous_closes), Decimal("0")) / Decimal(20)
        if variance <= 0:
            close = bars[-1].values["close"]
            if context.in_position:
                return StrategySignal(SignalAction.SELL, "zero variance collapses the 3σ stop to the prior mean")
            return StrategySignal(SignalAction.HOLD, "MEAN_REVERSION_ZERO_VARIANCE; no entry")
        deviation = variance.sqrt()
        close = bars[-1].values["close"]
        symbol = bars[-1].symbol
        cfg = AnalysisCalculationConfig(
            analysis_type="indicator.ema", algorithm_identity="indicator.ema",
            algorithm_version="1.0.0",
            effective_configuration={"provider": "historical", "symbol": symbol,
                                     "timeframe": context.interval, "mode": "RESEARCH"},
            effective_parameters={"period": 200}, window=WindowSpecification.all(),
        )
        ema_result = EMAAlgorithm().compute(list(bars), cfg)
        ema200 = ema_result["ema"]
        if not context.in_position and close <= mean - Decimal(2) * deviation and close > ema200:
            return StrategySignal(SignalAction.BUY,
                "close <= prior 20-bar mean - 2 population deviations and close > EMA200")
        if context.in_position and (close >= mean or close <= mean - Decimal(3) * deviation):
            return StrategySignal(SignalAction.SELL,
                "close returned to prior mean" if close >= mean else "close <= prior mean - 3 population deviations")
        return StrategySignal(SignalAction.HOLD, "mean-reversion entry/exit condition not met")
