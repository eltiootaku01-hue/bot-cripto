"""Shared-cash, single-position virtual portfolio for Product V2 research.

This is a historical research ledger only. It never connects to execution
runtimes or private exchange endpoints. All monetary arithmetic uses Decimal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Sequence, Any

from .analysis_ema import EMAAlgorithm
from .analysis_engine import AnalysisCalculationConfig, WindowSpecification
from .virtual_backtest import BacktestError, _expected_next_start, _observation, _validate_history

D = Decimal
ZERO, ONE = D("0"), D("1")


@dataclass(frozen=True)
class PortfolioConfig:
    initial_cash: Decimal = D("1000")
    fee_rate: Decimal = D("0.001")
    spread_bps: Decimal = D("10")
    slippage_bps: Decimal = D("5")
    momentum_bars: int = 42
    ema_period: int = 50

    def validate(self) -> None:
        if not self.initial_cash.is_finite() or self.initial_cash <= ZERO:
            raise BacktestError("portfolio initial cash must be positive and finite")
        if not ZERO <= self.fee_rate <= D("0.05"):
            raise BacktestError("portfolio fee rate outside supported range")
        if not ZERO <= self.spread_bps <= D("1000") or not ZERO <= self.slippage_bps <= D("1000"):
            raise BacktestError("portfolio spread/slippage outside supported range")
        if self.momentum_bars != 42 or self.ema_period != 50:
            raise BacktestError("Product V2-R5 portfolio periods are frozen at momentum=42 and EMA=50")


@dataclass(frozen=True)
class PortfolioFill:
    timestamp: datetime
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    cash_after: Decimal
    reason: str
    realized_pnl: Decimal | None = None


@dataclass(frozen=True)
class PortfolioEquityPoint:
    timestamp: datetime
    cash: Decimal
    symbol: str | None
    quantity: Decimal
    mark_price: Decimal | None
    equity: Decimal
    drawdown: Decimal


@dataclass(frozen=True)
class PortfolioDecision:
    timestamp: datetime
    selected_symbol: str | None
    momentum: Decimal | None
    reason: str


@dataclass(frozen=True)
class SharedPortfolioResult:
    initial_cash: Decimal
    final_cash: Decimal
    final_symbol: str | None
    final_quantity: Decimal
    final_entry_cost: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    total_fees: Decimal
    max_drawdown: Decimal
    fills: tuple[PortfolioFill, ...]
    equity_curve: tuple[PortfolioEquityPoint, ...]
    decisions: tuple[PortfolioDecision, ...]
    rejected_transitions: tuple[str, ...]
    first_timestamp: datetime
    last_timestamp: datetime


def _ema50(items: Sequence[Any], symbol: str) -> Decimal:
    observations = [_observation(item) for item in items]
    config = AnalysisCalculationConfig(
        analysis_type="indicator.ema", algorithm_identity="indicator.ema",
        algorithm_version="1.0.0",
        effective_configuration={"provider": "binance", "market": "SPOT", "symbol": symbol,
                                 "timeframe": "4h", "mode": "SHARED_PORTFOLIO_RESEARCH"},
        effective_parameters={"period": 50}, window=WindowSpecification.all(),
    )
    return EMAAlgorithm().compute(observations, config)["ema"]


def run_shared_portfolio(histories: Mapping[str, Sequence[Any]],
                         config: PortfolioConfig = PortfolioConfig(), *,
                         evaluation_start: datetime | None = None) -> SharedPortfolioResult:
    """Run daily UTC rebalance with one shared USDT cash ledger and at most one long.

    A decision is made only on the inclusive 23:59:59.999 UTC close of a 4h bar;
    any transition is executed at the next common candle open. The universe is a
    fixed exploratory panel unless caller supplies historically reconstructed
    eligibility evidence (not currently provided by this interface).
    """
    config.validate()
    if len(histories) < 2:
        raise BacktestError("shared portfolio requires at least two symbols")
    accepted: dict[str, list[Any]] = {}
    for symbol, items in histories.items():
        if not symbol or not items:
            raise BacktestError(f"empty historical series for {symbol!r}")
        series, _, _, _ = _validate_history(items, "4h")
        accepted[symbol] = series

    start_sets = [set(item.payload.start for item in series) for series in accepted.values()]
    common_starts = sorted(set.intersection(*start_sets))
    if len(common_starts) < max(config.momentum_bars + 2, config.ema_period + 2):
        raise BacktestError("insufficient synchronized 4h history for momentum/EMA warm-up")
    # Do not interpolate or silently bridge a missing common timestamp.
    for previous, current in zip(common_starts, common_starts[1:]):
        if _expected_next_start(previous, "4h") != current:
            raise BacktestError("cross-asset history has missing or misaligned common candles")

    by_symbol = {
        symbol: {item.payload.start: item for item in series}
        for symbol, series in accepted.items()
    }
    cash = config.initial_cash
    held_symbol: str | None = None
    quantity = ZERO
    entry_cost = ZERO
    fees_total = ZERO
    peak = config.initial_cash
    fills: list[PortfolioFill] = []
    curve: list[PortfolioEquityPoint] = []
    decisions: list[PortfolioDecision] = []
    rejected: list[str] = []
    pending_target: tuple[str | None, str, datetime] | None = None
    evaluation_index = 0
    if evaluation_start is not None:
        if evaluation_start.tzinfo is None or evaluation_start.utcoffset() is None:
            raise BacktestError("portfolio evaluation_start must be timezone-aware")
        eligible = [i for i, start in enumerate(common_starts) if start >= evaluation_start]
        if not eligible:
            raise BacktestError("portfolio evaluation_start is outside available history")
        evaluation_index = eligible[0]
    all_symbols = tuple(sorted(accepted))
    series_cache = {symbol: [by_symbol[symbol][start] for start in common_starts] for symbol in all_symbols}

    for index, start in enumerate(common_starts):
        if index < evaluation_index:
            continue
        current_items = {symbol: by_symbol[symbol][start] for symbol in all_symbols}
        # Apply only a decision made at the previous bar close, at this next open.
        if pending_target is not None:
            target, reason, decision_at = pending_target
            pending_target = None
            if target != held_symbol:
                if held_symbol is not None and quantity > ZERO:
                    old = current_items.get(held_symbol)
                    if old is None:
                        rejected.append(f"{start.isoformat()}:SELL_REJECTED:NO_COMMON_OPEN:{held_symbol}")
                    else:
                        slip = (config.spread_bps / D("2") + config.slippage_bps) / D("10000")
                        price = old.payload.open * (ONE - slip)
                        notional = quantity * price
                        fee = notional * config.fee_rate
                        proceeds = notional - fee
                        realized = proceeds - entry_cost
                        cash += proceeds
                        fees_total += fee
                        fills.append(PortfolioFill(start, held_symbol, "SELL", quantity, price, fee,
                                                   cash, f"rebalance after {decision_at.isoformat()}: {reason}", realized))
                        held_symbol, quantity, entry_cost = None, ZERO, ZERO
                if target is not None and held_symbol is None:
                    selected = current_items.get(target)
                    if selected is None:
                        rejected.append(f"{start.isoformat()}:BUY_REJECTED:NO_COMMON_OPEN:{target}")
                    else:
                        slip = (config.spread_bps / D("2") + config.slippage_bps) / D("10000")
                        price = selected.payload.open * (ONE + slip)
                        raw_qty = cash / (price * (ONE + config.fee_rate))
                        qty = raw_qty
                        notional = qty * price
                        fee = notional * config.fee_rate
                        total = notional + fee
                        # Guard against Decimal context rounding at an all-cash buy.
                        adjustments = 0
                        while total > cash and qty > ZERO and adjustments < 4:
                            qty = qty.next_minus()
                            notional = qty * price
                            fee = notional * config.fee_rate
                            total = notional + fee
                            adjustments += 1
                        if qty <= ZERO or total > cash:
                            rejected.append(f"{start.isoformat()}:BUY_REJECTED:INSUFFICIENT_CASH:{target}")
                        else:
                            cash -= total
                            held_symbol, quantity, entry_cost = target, qty, total
                            fees_total += fee
                            fills.append(PortfolioFill(start, target, "BUY", qty, price, fee, cash,
                                                       f"rebalance after {decision_at.isoformat()}: {reason}"))

        # Equity is marked only from the common closed candle.
        marks = {symbol: item.payload.close for symbol, item in current_items.items()}
        equity = cash + (quantity * marks[held_symbol] if held_symbol is not None else ZERO)
        peak = max(peak, equity)
        curve.append(PortfolioEquityPoint(current_items[held_symbol].payload.end if held_symbol else
                                          next(iter(current_items.values())).payload.end,
                                          cash, held_symbol, quantity,
                                          marks.get(held_symbol) if held_symbol else None,
                                          equity, equity / peak - ONE if peak > ZERO else ZERO))

        end = next(iter(current_items.values())).payload.end.astimezone(timezone.utc)
        # Binance inclusive close convention: daily decision is the 20:00–23:59:59.999 candle.
        if end.hour != 23 or end.minute != 59 or end.second != 59 or end.microsecond != 999000:
            continue
        if index < max(config.momentum_bars, config.ema_period):
            decisions.append(PortfolioDecision(end, None, None, "WARMUP_INSUFFICIENT"))
            continue
        ranked: list[tuple[Decimal, str]] = []
        for symbol in all_symbols:
            series = series_cache[symbol][:index + 1]
            close = series[-1].payload.close
            reference = series[-1 - config.momentum_bars].payload.close
            if reference <= ZERO:
                continue
            momentum = close / reference - ONE
            ema = _ema50(series, symbol)
            if momentum > ZERO and close > ema:
                ranked.append((momentum, symbol))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        target = ranked[0][1] if ranked else None
        momentum = ranked[0][0] if ranked else None
        reason = "highest strictly positive 42-bar momentum with close above EMA50" if target else "NO_ASSET_QUALIFIES; target cash"
        decisions.append(PortfolioDecision(end, target, momentum, reason))
        if target != held_symbol:
            pending_target = (target, reason, end)

    if not curve:
        raise BacktestError("portfolio produced no equity observations")
    final_point = curve[-1]
    return SharedPortfolioResult(
        initial_cash=config.initial_cash, final_cash=cash, final_symbol=held_symbol,
        final_quantity=quantity, final_entry_cost=entry_cost if held_symbol is not None else ZERO,
        final_equity=final_point.equity,
        net_pnl=final_point.equity - config.initial_cash, total_fees=fees_total,
        max_drawdown=min((point.drawdown for point in curve), default=ZERO),
        fills=tuple(fills), equity_curve=tuple(curve), decisions=tuple(decisions),
        rejected_transitions=tuple(rejected), first_timestamp=curve[0].timestamp,
        last_timestamp=curve[-1].timestamp,
    )
