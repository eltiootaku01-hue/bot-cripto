"""Deterministic virtual backtest over public historical Binance Spot klines.

Only fictitious balances are maintained here. This module never connects to
financial execution or uses private exchange endpoints.
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Callable, Sequence

from .analysis_contracts import ArtifactNature, MarketObservation, Provenance
from .analysis_ema import EMAAlgorithm
from .analysis_engine import AnalysisCalculationConfig, WindowSpecification
from .analysis_rsi import RSIAlgorithm
from .analysis_sma import SMAAlgorithm
from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_intervals import BinanceSpotIntervalUnit, validate_binance_spot_interval
from .binance_spot import BinanceSpotRestConfig
from .market_data import CandleFinality, CandleState, DataCompleteness, DataQuality, MarketData

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
_MAX_CANDLES = 1200
_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS = Decimal("10000")


class BacktestError(ValueError):
    """Invalid configuration, inadmissible history, or simulation failure."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return result


def _decimal_arg(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an exact decimal number") from exc
    if not result.is_finite():
        raise argparse.ArgumentTypeError("must be finite")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a long-only virtual EMA-crossover backtest on public Binance Spot history."
    )
    parser.add_argument("--symbol", required=True, help="Binance Spot symbol, e.g. BTCUSDT")
    parser.add_argument("--interval", required=True, help="Binance kline interval, e.g. 1m")
    parser.add_argument("--candles", type=_positive_int, default=500,
                        help="closed candles to simulate (default: 500; maximum: 1200)")
    parser.add_argument("--fast-ema-period", type=_positive_int, default=12)
    parser.add_argument("--slow-ema-period", type=_positive_int, default=26)
    parser.add_argument("--rsi-period", type=_positive_int, default=14)
    parser.add_argument("--rsi-buy-min", type=_decimal_arg, default=Decimal("50"))
    parser.add_argument("--rsi-sell-at", type=_decimal_arg, default=Decimal("70"))
    parser.add_argument("--initial-quote-balance", type=_decimal_arg, default=Decimal("10000"))
    parser.add_argument("--position-fraction", type=_decimal_arg, default=Decimal("1"))
    parser.add_argument("--fee-rate", type=_decimal_arg, default=Decimal("0.001"))
    parser.add_argument("--slippage-bps", type=_decimal_arg, default=Decimal("5"))
    parser.add_argument("--spread-bps", type=_decimal_arg, default=Decimal("10"),
                        help="assumed full spread in basis points; half is charged at each fill")
    parser.add_argument("--max-entries-per-30d", type=_positive_int, default=5,
                        help="maximum virtual BUY entries in any rolling 30-day window (maximum: 5)")
    return parser


@dataclass(frozen=True)
class BacktestConfig:
    symbol: str
    interval: str
    candles: int = 500
    fast_ema_period: int = 12
    slow_ema_period: int = 26
    rsi_period: int = 14
    rsi_buy_min: Decimal = Decimal("50")
    rsi_sell_at: Decimal = Decimal("70")
    initial_quote_balance: Decimal = Decimal("10000")
    position_fraction: Decimal = Decimal("1")
    fee_rate: Decimal = Decimal("0.001")
    slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("10")
    max_entries_per_30d: int = 5
    min_qty: Decimal | None = None
    quantity_step: Decimal | None = None
    min_notional: Decimal | None = None

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not _SYMBOL_RE.fullmatch(self.symbol):
            raise BacktestError("invalid symbol format; use an uppercase Binance Spot symbol")
        try:
            validate_binance_spot_interval(self.interval)
        except (TypeError, ValueError) as exc:
            raise BacktestError(f"invalid Binance Spot interval: {self.interval}") from exc
        if not 1 <= self.candles <= _MAX_CANDLES:
            raise BacktestError(f"candles must be between 1 and {_MAX_CANDLES}")
        if self.fast_ema_period <= 0 or self.slow_ema_period <= self.fast_ema_period:
            raise BacktestError("EMA periods must satisfy 0 < fast EMA period < slow EMA period")
        if self.rsi_period <= 0:
            raise BacktestError("RSI period must be positive")
        if isinstance(self.max_entries_per_30d, bool) or not 1 <= self.max_entries_per_30d <= 5:
            raise BacktestError("max_entries_per_30d must be between 1 and 5")
        warmup = max(self.slow_ema_period, self.rsi_period + 1)
        if self.candles < warmup + 2:
            raise BacktestError(f"insufficient candles for warm-up and next-bar execution; need at least {warmup + 2}")
        if self.initial_quote_balance <= 0:
            raise BacktestError("initial quote balance must be positive")
        if not (_ZERO < self.position_fraction <= _ONE):
            raise BacktestError("position fraction must be in (0, 1]")
        if not (_ZERO <= self.fee_rate <= Decimal("0.05")):
            raise BacktestError("fee rate must be between 0 and 0.05")
        if not (_ZERO <= self.slippage_bps <= Decimal("1000")):
            raise BacktestError("slippage bps must be between 0 and 1000")
        if not (_ZERO <= self.spread_bps <= Decimal("1000")):
            raise BacktestError("spread bps must be between 0 and 1000")
        if not (_ZERO <= self.rsi_buy_min <= Decimal("100")):
            raise BacktestError("RSI buy threshold must be between 0 and 100")
        if not (_ZERO <= self.rsi_sell_at <= Decimal("100")):
            raise BacktestError("RSI sell threshold must be between 0 and 100")
        for name in ("min_qty", "quantity_step", "min_notional"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value <= _ZERO):
                raise BacktestError(f"{name} must be a positive finite Decimal when supplied")


@dataclass(frozen=True)
class VirtualFill:
    timestamp: datetime
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    quote_balance_after: Decimal
    reason: str
    realized_pnl: Decimal | None = None


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    close: Decimal
    quote_balance: Decimal
    base_position: Decimal
    equity: Decimal
    drawdown: Decimal


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    interval: str
    config: BacktestConfig
    candle_count: int
    first_candle: datetime
    last_candle: datetime
    fills: tuple[VirtualFill, ...]
    equity_curve: tuple[EquityPoint, ...]
    final_quote_balance: Decimal
    final_base_position: Decimal
    final_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    max_drawdown: Decimal
    final_sma: Decimal
    final_fast_ema: Decimal
    final_slow_ema: Decimal
    final_rsi: Decimal
    excluded_final_candles: int
    unknown_marketdata_completeness_count: int
    unknown_finality_count: int
    entry_rejections: tuple[str, ...] = ()


def _next_month(value: datetime) -> datetime:
    year, month = value.year, value.month + 1
    if month == 13:
        year, month = year + 1, 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _expected_next_start(start: datetime, interval: str) -> datetime:
    spec = validate_binance_spot_interval(interval)
    if spec.unit is BinanceSpotIntervalUnit.MONTH:
        result = start
        for _ in range(spec.magnitude):
            result = _next_month(result)
        return result
    if spec.duration is None:
        raise BacktestError(f"interval has no fixed duration: {interval}")
    return start + spec.duration


def _validate_history(items: Sequence[MarketData], interval: str):
    if not items:
        raise BacktestError("historical acquisition returned no candles")
    ordered = list(items)
    try:
        starts = [item.payload.start for item in ordered]
    except AttributeError as exc:
        raise BacktestError("historical response contains an invalid candle payload") from exc
    if starts != sorted(starts):
        raise BacktestError("historical candles are not ordered by start time")
    if len(set(starts)) != len(starts):
        raise BacktestError("duplicate historical candle start time detected")

    accepted: list[MarketData] = []
    excluded = unknown_completeness = unknown_finality = 0
    for index, item in enumerate(ordered):
        candle = item.payload
        last = index == len(ordered) - 1
        if candle.timeframe != interval:
            raise BacktestError(f"candle timeframe mismatch at {candle.start.isoformat()}")
        if candle.candle_state is CandleState.UNKNOWN:
            raise BacktestError(f"unknown candle state at {candle.start.isoformat()}; refusing simulation")
        if candle.completeness is DataCompleteness.UNKNOWN:
            raise BacktestError(f"unknown candle completeness at {candle.start.isoformat()}; refusing simulation")
        if candle.finality is CandleFinality.NOT_FINAL or candle.candle_state is CandleState.OPEN or candle.completeness is DataCompleteness.PARTIAL:
            if last:
                excluded += 1
                continue
            raise BacktestError(f"open, partial, or non-final internal candle at {candle.start.isoformat()}")
        if candle.candle_state is not CandleState.CLOSED or candle.completeness is not DataCompleteness.COMPLETE:
            raise BacktestError(f"candle is not explicitly closed and complete at {candle.start.isoformat()}")
        if item.quality is not DataQuality.VALID:
            raise BacktestError(f"market-data quality is {item.quality.value} at {candle.start.isoformat()}")
        if item.completeness is DataCompleteness.PARTIAL:
            raise BacktestError(f"market-data envelope is invalid or partial at {candle.start.isoformat()}")
        if item.completeness is DataCompleteness.UNKNOWN:
            unknown_completeness += 1
        if candle.finality is CandleFinality.UNKNOWN:
            unknown_finality += 1
        # Historical data often has canonical available_at=None. If it is supplied,
        # never allow it to claim availability after the modeled bar-close decision.
        if item.available_at is not None:
            if item.available_at.tzinfo is None or item.available_at.utcoffset() is None:
                raise BacktestError("available_at must be timezone-aware when present")
            if item.available_at > candle.end + timedelta(milliseconds=1):
                raise BacktestError(f"historical data was not available by simulated bar close at {candle.start.isoformat()}")
        accepted.append(item)

    for previous, current in zip(accepted, accepted[1:]):
        expected = _expected_next_start(previous.payload.start, interval)
        if current.payload.start != expected:
            raise BacktestError(f"historical gap or interval mismatch: expected {expected.isoformat()}, received {current.payload.start.isoformat()}")
    if len(accepted) < 3:
        raise BacktestError("fewer than three closed, complete candles remain")
    return accepted, excluded, unknown_completeness, unknown_finality


def _observation(item: MarketData) -> MarketObservation:
    candle = item.payload
    # Simulation assumption only: a candle close becomes usable at its close boundary.
    # The canonical MarketData.available_at value remains untouched and may be UNKNOWN.
    return MarketObservation(
        symbol=item.instrument.symbol,
        observation_timestamp=candle.end,
        available_timestamp=candle.end,
        observation_type="HISTORICAL_CANDLE_CLOSE",
        values={"close": candle.close},
        provenance=Provenance(
            source=item.source.source_id,
            nature=ArtifactNature.OBSERVED,
            reference=getattr(item, "market_data_id", None),
            metadata={
                "backtest_temporal_model": "bar-close decision; next-bar-open fill",
                "canonical_available_at": item.available_at.isoformat() if item.available_at is not None else "UNKNOWN",
            },
        ),
        venue=item.source.venue,
        observation_id=f"{item.instrument.instrument_id}:{candle.timeframe}:{candle.start.isoformat()}",
    )


def _indicator_config(name: str, symbol: str, interval: str, period: int) -> AnalysisCalculationConfig:
    identity = f"indicator.{name}"
    return AnalysisCalculationConfig(
        analysis_type=identity,
        algorithm_identity=identity,
        algorithm_version="1.0.0",
        effective_configuration={"provider": "binance", "market": "SPOT", "symbol": symbol,
                                 "timeframe": interval, "mode": "HISTORICAL_VIRTUAL_BACKTEST"},
        effective_parameters={"period": period},
        window=WindowSpecification.last_n(period) if name == "sma" else WindowSpecification.all(),
    )


def _indicators(observations: Sequence[MarketObservation], config: BacktestConfig):
    symbol = observations[-1].symbol
    sma_period = config.slow_ema_period
    sma = SMAAlgorithm().compute(
        observations[-sma_period:],
        _indicator_config("sma", symbol, config.interval, sma_period),
    )
    fast = EMAAlgorithm().compute(
        observations, _indicator_config("ema", symbol, config.interval, config.fast_ema_period)
    )
    slow = EMAAlgorithm().compute(
        observations, _indicator_config("ema", symbol, config.interval, config.slow_ema_period)
    )
    rsi = RSIAlgorithm().compute(
        observations, _indicator_config("rsi", symbol, config.interval, config.rsi_period)
    )
    return {"sma": sma["sma"], "fast_ema": fast["ema"], "slow_ema": slow["ema"], "rsi": rsi["rsi"]}


def run_virtual_backtest(
    items: Sequence[MarketData], config: BacktestConfig, *,
    evaluation_start: datetime | None = None, evaluation_end: datetime | None = None,
    entry_admission: Callable[[int, Sequence[MarketObservation]], tuple[bool, str]] | None = None,
) -> BacktestResult:
    """Run virtual accounting with optional warm-up and per-entry admission.

    Evaluation bounds exclude warm-up from metrics but preserve it for indicators.
    Admission applies only to new BUYs; exits are never blocked by entry filters.
    """
    config.validate()
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, config.interval)
    if evaluation_end is not None and evaluation_start is None:
        raise BacktestError("evaluation_start is required when evaluation_end is supplied")
    if evaluation_start is None:
        history = accepted[-config.candles:]
        candles = history
    else:
        history = accepted
        candles = [item for item in history if item.payload.start >= evaluation_start
                   and (evaluation_end is None or item.payload.start < evaluation_end)]
    if len(candles) < config.candles:
        raise BacktestError(f"insufficient evaluation candles: requested {config.candles}, got {len(candles)}")
    if len(candles) != config.candles:
        raise BacktestError(f"evaluation window must contain exactly {config.candles} candles, got {len(candles)}")
    observations = [_observation(item) for item in history]
    index_by_start = {item.payload.start: index for index, item in enumerate(history)}
    quote, position, entry_cost = config.initial_quote_balance, _ZERO, _ZERO
    realized = total_fees = _ZERO
    fills: list[VirtualFill] = []
    curve: list[EquityPoint] = []
    rejections: list[str] = []
    peak = config.initial_quote_balance
    first_signal_index = max(config.slow_ema_period, config.rsi_period + 1)
    pending: tuple[str, str] | None = None

    for eval_index, item in enumerate(candles):
        candle = item.payload
        history_index = index_by_start[candle.start]
        if pending is not None:
            side, reason = pending
            slippage = (config.slippage_bps + config.spread_bps / Decimal("2")) / _BPS
            if side == "BUY" and position == _ZERO:
                fill_price = candle.open * (_ONE + slippage)
                raw_quantity = (quote * config.position_fraction) / (fill_price * (_ONE + config.fee_rate))
                quantity = raw_quantity
                if config.quantity_step is not None:
                    quantity = (raw_quantity / config.quantity_step).to_integral_value(rounding=ROUND_DOWN) * config.quantity_step
                notional = quantity * fill_price
                if quantity <= _ZERO:
                    rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:NON_POSITIVE_QUANTITY")
                elif config.min_qty is not None and quantity < config.min_qty:
                    rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:MIN_QTY quantity={quantity} minimum={config.min_qty}")
                elif config.min_notional is not None and notional < config.min_notional:
                    rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:MIN_NOTIONAL notional={notional} minimum={config.min_notional}")
                else:
                    fee = notional * config.fee_rate
                    cost = notional + fee
                    if cost > quote:
                        rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:COSTS_EXCEED_BALANCE cost={cost} available={quote}")
                    else:
                        quote -= cost
                        position, entry_cost = quantity, cost
                        total_fees += fee
                        fills.append(VirtualFill(candle.start, "BUY", quantity, fill_price, fee, quote, reason))
            elif side == "SELL" and position > _ZERO:
                fill_price = candle.open * (_ONE - slippage)
                notional = position * fill_price
                fee = notional * config.fee_rate
                proceeds = notional - fee
                trade_pnl = proceeds - entry_cost
                quote += proceeds
                realized += trade_pnl
                total_fees += fee
                fills.append(VirtualFill(candle.start, "SELL", position, fill_price, fee, quote, reason, trade_pnl))
                position, entry_cost = _ZERO, _ZERO
            pending = None

        equity = quote + position * candle.close
        peak = max(peak, equity)
        curve.append(EquityPoint(candle.end, candle.close, quote, position, equity,
                                 equity / peak - _ONE if peak > _ZERO else _ZERO))
        if eval_index >= len(candles) - 1 or history_index < first_signal_index:
            continue
        current = _indicators(observations[:history_index + 1], config)
        previous = _indicators(observations[:history_index], config)
        crossed_up = previous["fast_ema"] <= previous["slow_ema"] and current["fast_ema"] > current["slow_ema"]
        crossed_down = previous["fast_ema"] >= previous["slow_ema"] and current["fast_ema"] < current["slow_ema"]
        if position == _ZERO and crossed_up and current["rsi"] >= config.rsi_buy_min:
            cutoff = candle.end - timedelta(days=30)
            recent_entries = sum(1 for fill in fills if fill.side == "BUY" and cutoff < fill.timestamp <= candle.end)
            if recent_entries >= config.max_entries_per_30d:
                continue
            if entry_admission is not None:
                allowed, detail = entry_admission(history_index, observations[:history_index + 1])
                if not allowed:
                    rejections.append(f"{candle.end.isoformat()}:{detail}")
                    continue
                reason = f"EMA/RSI entry passed; {detail}"
            else:
                reason = "fast EMA crossed above slow EMA and RSI filter passed; rolling entry cap passed"
            pending = ("BUY", reason)
        elif position > _ZERO and (crossed_down or current["rsi"] >= config.rsi_sell_at):
            pending = ("SELL", "fast EMA crossed below slow EMA" if crossed_down else "RSI reached sell threshold")

    last_eval_index = index_by_start[candles[-1].payload.start]
    final_values = _indicators(observations[:last_eval_index + 1], config)
    final_equity = curve[-1].equity
    unrealized = position * candles[-1].payload.close - entry_cost if position > _ZERO else _ZERO
    return BacktestResult(
        symbol=candles[0].instrument.symbol, interval=config.interval, config=config,
        candle_count=len(candles), first_candle=candles[0].payload.start,
        last_candle=candles[-1].payload.start, fills=tuple(fills), equity_curve=tuple(curve),
        final_quote_balance=quote, final_base_position=position, final_equity=final_equity,
        realized_pnl=realized, unrealized_pnl=unrealized, total_fees=total_fees,
        max_drawdown=min((point.drawdown for point in curve), default=_ZERO),
        final_sma=final_values["sma"], final_fast_ema=final_values["fast_ema"],
        final_slow_ema=final_values["slow_ema"], final_rsi=final_values["rsi"],
        excluded_final_candles=excluded,
        unknown_marketdata_completeness_count=unknown_completeness,
        unknown_finality_count=unknown_finality, entry_rejections=tuple(rejections),
    )

def _subtract_months(value: datetime, count: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 - count
    year, month_zero = divmod(absolute, 12)
    month = month_zero + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _historical_start(now: datetime, interval: str, candles: int) -> datetime:
    spec = validate_binance_spot_interval(interval)
    count = candles + 2
    if spec.unit is BinanceSpotIntervalUnit.MONTH:
        anchor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return _subtract_months(anchor, count)
    if spec.duration is None:
        raise BacktestError(f"cannot derive a bounded historical range for {interval}")
    return now - spec.duration * count


def _report(result: BacktestResult, requested_symbol: str) -> None:
    cfg = result.config
    return_pct = (result.final_equity / cfg.initial_quote_balance - _ONE) * Decimal("100")
    print("BOT-CRIPTO — VIRTUAL HISTORICAL BACKTEST")
    print("Mode: public Binance Spot history; virtual accounting only")
    print(f"Requested symbol: {requested_symbol}")
    print(f"Canonical symbol: {result.symbol}")
    print(f"Interval: {result.interval}")
    print(f"Candles simulated: {result.candle_count}")
    print(f"Historical range: {result.first_candle.isoformat()} .. {result.last_candle.isoformat()}")
    print("Strategy: EMA crossover with RSI filter; long-only; no leverage")
    print(f"Fast EMA / slow EMA: {cfg.fast_ema_period} / {cfg.slow_ema_period}")
    print(f"Final SMA({cfg.slow_ema_period}): {result.final_sma}")
    print(f"Final fast EMA: {result.final_fast_ema}")
    print(f"Final slow EMA: {result.final_slow_ema}")
    print(f"Final RSI({cfg.rsi_period}): {result.final_rsi}")
    print(f"RSI buy minimum / sell threshold: {cfg.rsi_buy_min} / {cfg.rsi_sell_at}")
    print(f"Initial quote balance: {cfg.initial_quote_balance}")
    print(f"Position fraction: {cfg.position_fraction}")
    print(f"Fee rate per fill: {cfg.fee_rate}")
    print(f"Slippage: {cfg.slippage_bps} bps per fill")
    print(f"Assumed full spread: {cfg.spread_bps} bps (half charged per fill; historical spread is not available)")
    print(f"Maximum entries per rolling 30 days: {cfg.max_entries_per_30d}")
    print(f"Instrument min quantity / step / min notional: {cfg.min_qty} / {cfg.quantity_step} / {cfg.min_notional}")
    print("Quote balance is denominated in the pair quote asset; it is not an ARS balance or an ARS-to-USDT conversion.")
    print("Execution model: signal at candle close; fill at next candle open with configured slippage")
    print("Temporal limitation: canonical historical available_at is UNKNOWN when absent; this simulation uses an explicit bar-close model without rewriting MarketData.available_at.")
    print(f"MarketData completeness UNKNOWN count: {result.unknown_marketdata_completeness_count}")
    print(f"Candle finality UNKNOWN count: {result.unknown_finality_count}")
    print(f"Excluded final open/partial candles: {result.excluded_final_candles}")
    if result.unknown_marketdata_completeness_count or result.unknown_finality_count:
        print("WARNING: envelope completeness/finality contains UNKNOWN values; review the temporal limitation above.")
    print()
    print(f"Virtual fills: {len(result.fills)}")
    for n, fill in enumerate(result.fills, start=1):
        pnl = f"; realized PnL={fill.realized_pnl}" if fill.realized_pnl is not None else ""
        print(f"{n}. {fill.timestamp.isoformat()} {fill.side} qty={fill.quantity} price={fill.price} fee={fill.fee} quote_after={fill.quote_balance_after}{pnl}; reason={fill.reason}")
    print()
    print(f"Final quote balance: {result.final_quote_balance}")
    print(f"Final base position: {result.final_base_position}")
    print(f"Final equity (last close mark): {result.final_equity}")
    print(f"Realized PnL: {result.realized_pnl}")
    print(f"Unrealized PnL: {result.unrealized_pnl}")
    print(f"Total fees: {result.total_fees}")
    print(f"Return: {return_pct}%")
    print(f"Maximum drawdown: {result.max_drawdown * Decimal('100')}%")
    print("Historical simulation is not a guarantee of future profitability.")


def run(args: argparse.Namespace) -> int:
    config = BacktestConfig(
        symbol=args.symbol, interval=args.interval, candles=args.candles,
        fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period,
        rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at,
        initial_quote_balance=args.initial_quote_balance, position_fraction=args.position_fraction,
        fee_rate=args.fee_rate, slippage_bps=args.slippage_bps, spread_bps=args.spread_bps,
        max_entries_per_30d=args.max_entries_per_30d,
    )
    config.validate()
    now = _utc_now()
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    instrument = metadata.resolve_active(config.symbol)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    start = _historical_start(now, config.interval, config.candles)
    request_bound = max(2, (config.candles + 2 + 999) // 1000 + 1)
    items = adapter.fetch_historical_market_data(
        symbol=config.symbol, interval=config.interval,
        start_time=int(start.timestamp() * 1000), end_time=int(now.timestamp() * 1000),
        page_limit=1000, max_requests=request_bound,
    )
    result = run_virtual_backtest(items, config)
    if result.symbol != instrument.symbol:
        raise BacktestError("resolved instrument identity does not match acquired history")
    _report(result, config.symbol)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
