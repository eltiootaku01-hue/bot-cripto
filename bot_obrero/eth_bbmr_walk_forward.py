"""Clock-driven, research-only replay of the frozen ETH BBMR tactic.

The market-data referee acquires true historical candles. On each simulated tick,
the decision trace receives only the history prefix visible at that time. No
exchange order endpoints, private keys, margin, leverage, or real funds are used.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Sequence

from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_spot import BinanceSpotRestConfig
from .virtual_backtest import (
    BacktestConfig,
    BacktestError,
    BacktestResult,
    EquityPoint,
    VirtualFill,
    _configuration_record,
    _evidence_hashes,
    _expected_next_start,
    _indicator_series,
    _market_data_record,
    _net_liquidation_value,
    _observation,
    _validate_history,
    run_virtual_backtest,
)

STRATEGY = "eth-bbmr-4h-research"
EMA_RSI_STRATEGY = "ema-rsi"
DONCHIAN_STRATEGY = "donchian-20-10-research"
FROZEN_STRATEGIES = (STRATEGY, EMA_RSI_STRATEGY, DONCHIAN_STRATEGY)
STRATEGY_DESCRIPTIONS = {
    STRATEGY: "Bollinger z48 entry <-0.75 with close > EMA180; exit z>2.0 or close <= EMA180",
    EMA_RSI_STRATEGY: "EMA12/26 crossover; enter only when RSI14 >= 50; exit crossover down or RSI14 >= 70",
    DONCHIAN_STRATEGY: "Enter close > prior 20-bar high; exit close < prior 10-bar low; current bar excluded from both windows",
}
SYMBOL = "ETHUSDT"
INTERVAL = "4h"
EMA_PERIOD = 180
POSITION_FRACTION = Decimal("0.95")
MIN_QTY = Decimal("0.0001")
QTY_STEP = Decimal("0.0001")
MIN_NOTIONAL = Decimal("5")
MAX_ENTRIES_PER_30D = 5
BPS = Decimal("10000")
ZERO = Decimal("0")
ONE = Decimal("1")
COST_SCENARIOS = {
    "BASE": (Decimal("0.001"), Decimal("10"), Decimal("5")),
    "STRESS_1": (Decimal("0.001"), Decimal("25"), Decimal("10")),
    "STRESS_2": (Decimal("0.002"), Decimal("50"), Decimal("20")),
}


def parse_aware_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and require an explicit UTC offset."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601 with timezone, e.g. 2026-10-01T00:00:00+00:00") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def validate_start_boundary(start: datetime) -> None:
    """Require a 4h Binance kline boundary to keep the virtual clock unambiguous."""
    if (
        start.minute != 0 or start.second != 0 or start.microsecond != 0
        or start.hour % 4 != 0
    ):
        raise BacktestError("simulation start must align to a 4h Binance UTC candle boundary")


def _strategy_action(
    *,
    zscore: Decimal | None,
    close: Decimal,
    ema: Decimal | None,
    position: Decimal,
    recent_entries: int,
    warmup_ready: bool,
) -> str:
    """Pure one-tick decision function; receives no future bars or future prices."""
    if not warmup_ready or zscore is None or ema is None:
        return "WAIT_WARMUP"
    buy_signal = zscore < Decimal("-0.75") and close > ema
    sell_signal = zscore > Decimal("2.0") or close <= ema
    if position <= ZERO:
        if buy_signal:
            return "BLOCK_ENTRY_CAP" if recent_entries >= MAX_ENTRIES_PER_30D else "BUY_NEXT_OPEN"
        return "WAIT_NO_SIGNAL"
    return "SELL_NEXT_OPEN" if sell_signal else "HOLD_POSITION"


def _signal_flags(
    config: BacktestConfig,
    current: dict[str, Decimal | None] | None,
    previous: dict[str, Decimal | None] | None,
    candle,
    visible_items,
) -> tuple[bool, bool, str, str]:
    """Frozen, causal signal rules; all channel windows exclude the current candle."""
    if current is None or previous is None:
        return False, False, "indicator warm-up incomplete", "indicator warm-up incomplete"

    if config.strategy == STRATEGY:
        zscore = current.get("zscore")
        slow_ema = current.get("slow_ema")
        buy = zscore is not None and slow_ema is not None and zscore < Decimal("-0.75") and candle.close > slow_ema
        sell = zscore is not None and slow_ema is not None and (
            zscore > Decimal("2.0") or candle.close <= slow_ema
        )
        entry_reason = "BBMR z-score below -0.75 while close is above EMA180"
        exit_reason = (
            "BBMR z-score above 2.0"
            if zscore is not None and zscore > Decimal("2.0")
            else "close at/below EMA180 trend gate"
        )
        return buy, sell, entry_reason, exit_reason

    if config.strategy == DONCHIAN_STRATEGY:
        previous_twenty = visible_items[-21:-1]
        previous_ten = visible_items[-11:-1]
        if len(previous_twenty) != 20 or len(previous_ten) != 10:
            return False, False, "Donchian 20-bar warm-up incomplete", "Donchian 10-bar warm-up incomplete"
        upper = max(item.payload.high for item in previous_twenty)
        lower = min(item.payload.low for item in previous_ten)
        buy = candle.close > upper
        sell = candle.close < lower
        entry_reason = "Donchian 20-bar high breakout (current candle excluded)"
        exit_reason = "Donchian 10-bar low breakdown (current candle excluded)"
        return buy, sell, entry_reason, exit_reason

    crossed_up = previous["fast_ema"] <= previous["slow_ema"] and current["fast_ema"] > current["slow_ema"]
    crossed_down = previous["fast_ema"] >= previous["slow_ema"] and current["fast_ema"] < current["slow_ema"]
    buy = crossed_up and current["rsi"] >= config.rsi_buy_min
    sell = crossed_down or current["rsi"] >= config.rsi_sell_at
    return (
        buy,
        sell,
        "EMA12/26 bullish crossover with RSI14 >= 50",
        "EMA12/26 bearish crossover" if crossed_down else "RSI14 >= 70",
    )


def _decide_action(
    *,
    config: BacktestConfig,
    current: dict[str, Decimal | None] | None,
    previous: dict[str, Decimal | None] | None,
    candle,
    visible_items,
    position: Decimal,
    recent_entries: int,
    warmup_ready: bool,
) -> str:
    if not warmup_ready:
        return "WAIT_WARMUP"
    buy, sell, _, _ = _signal_flags(config, current, previous, candle, visible_items)
    if position <= ZERO:
        if buy:
            return "BLOCK_ENTRY_CAP" if recent_entries >= config.max_entries_per_30d else "BUY_NEXT_OPEN"
        return "WAIT_NO_SIGNAL"
    return "SELL_NEXT_OPEN" if sell else "HOLD_POSITION"


def _config(capital: Decimal, costs: tuple[Decimal, Decimal, Decimal], candles: int, strategy: str = STRATEGY) -> BacktestConfig:
    fee, spread, slippage = costs
    slow_ema_period = EMA_PERIOD if strategy == STRATEGY else 26
    config = BacktestConfig(
        symbol=SYMBOL,
        interval=INTERVAL,
        strategy=strategy,
        candles=candles,
        fast_ema_period=12,
        slow_ema_period=slow_ema_period,
        rsi_period=14,
        rsi_buy_min=Decimal("50"),
        rsi_sell_at=Decimal("70"),
        initial_quote_balance=capital,
        position_fraction=POSITION_FRACTION,
        fee_rate=fee,
        spread_bps=spread,
        slippage_bps=slippage,
        max_entries_per_30d=MAX_ENTRIES_PER_30D,
        min_qty=MIN_QTY,
        quantity_step=QTY_STEP,
        min_notional=MIN_NOTIONAL,
        liquidate_at_end=True,
    )
    config.validate()
    return config



def _run_prefix_only(
    *,
    all_items,
    eval_items,
    config: BacktestConfig,
) -> BacktestResult:
    """Execute the account loop one candle at a time; each decision receives a prefix only."""
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(all_items, config.interval)
    index_by_start = {item.payload.start: i for i, item in enumerate(accepted)}
    if len(eval_items) != config.candles:
        raise BacktestError("prefix-only replay evaluation count does not match frozen configuration")
    quote, position, entry_cost = config.initial_quote_balance, ZERO, ZERO
    realized = total_fees = ZERO
    fills: list[VirtualFill] = []
    curve: list[EquityPoint] = []
    rejections: list[str] = []
    peak = config.initial_quote_balance
    pending: tuple[str, str] | None = None
    first_signal_index = max(config.slow_ema_period, config.rsi_period + 1)
    last_values = None

    for eval_index, item in enumerate(eval_items):
        candle = item.payload
        history_index = index_by_start[candle.start]
        if pending is not None:
            side, reason = pending
            slippage = (config.slippage_bps + config.spread_bps / Decimal("2")) / BPS
            if side == "BUY" and position == ZERO:
                fill_price = candle.open * (ONE + slippage)
                raw_quantity = (quote * config.position_fraction) / (fill_price * (ONE + config.fee_rate))
                quantity = raw_quantity
                if config.quantity_step is not None:
                    quantity = (raw_quantity / config.quantity_step).to_integral_value(rounding=ROUND_DOWN) * config.quantity_step
                notional = quantity * fill_price
                if quantity <= ZERO:
                    rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:NON_POSITIVE_QUANTITY")
                elif config.min_qty is not None and quantity < config.min_qty:
                    rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:MIN_QTY quantity={quantity} minimum={config.min_qty}")
                elif config.max_qty is not None and quantity > config.max_qty:
                    rejections.append(f"{candle.start.isoformat()}:ORDER_REJECTED:MAX_QTY quantity={quantity} maximum={config.max_qty}")
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
            elif side == "SELL" and position > ZERO:
                fill_price = candle.open * (ONE - slippage)
                notional = position * fill_price
                fee = notional * config.fee_rate
                proceeds = notional - fee
                trade_pnl = proceeds - entry_cost
                quote += proceeds
                realized += trade_pnl
                total_fees += fee
                fills.append(VirtualFill(candle.start, "SELL", position, fill_price, fee, quote, reason, trade_pnl))
                position, entry_cost = ZERO, ZERO
            pending = None

        equity = quote + _net_liquidation_value(position, candle.close, config)
        peak = max(peak, equity)
        curve.append(EquityPoint(
            timestamp=candle.end,
            close=candle.close,
            quote_balance=quote,
            base_position=position,
            equity=equity,
            drawdown=equity / peak - ONE if peak > ZERO else ZERO,
        ))

        # Only release the prefix ending at the just-closed candle to the strategy.
        visible_items = accepted[: history_index + 1]
        visible_observations = [_observation(value) for value in visible_items]
        visible_indicators = _indicator_series(visible_observations, config)
        current = visible_indicators[-1]
        previous = visible_indicators[-2] if len(visible_indicators) > 1 else None
        last_values = current
        if eval_index >= len(eval_items) - 1 or history_index < first_signal_index:
            continue
        if current is None or previous is None:
            raise BacktestError("prefix-only indicator history is insufficient at a simulated decision")
        zscore = current.get("zscore")
        buy_signal = (
            zscore is not None
            and zscore < Decimal("-0.75")
            and candle.close > current["slow_ema"]
        )
        sell_signal = (
            zscore is not None
            and (zscore > Decimal("2.0") or candle.close <= current["slow_ema"])
        )
        buy_signal, sell_signal, entry_reason, exit_reason = _signal_flags(
            config, current, previous, candle, visible_items
        )
        if position == ZERO and buy_signal:
            cutoff = candle.end - timedelta(days=30)
            recent_entries = sum(
                1 for fill in fills
                if fill.side == "BUY" and cutoff < fill.timestamp <= candle.end
            )
            if recent_entries >= config.max_entries_per_30d:
                continue
            pending = ("BUY", entry_reason + "; rolling entry cap passed")
        elif position > ZERO and sell_signal:
            pending = ("SELL", exit_reason)

    if last_values is None:
        raise BacktestError("no prefix-only indicators were calculated")
    if config.liquidate_at_end and position > ZERO:
        last_candle = eval_items[-1].payload
        terminal_slippage = (config.slippage_bps + config.spread_bps / Decimal("2")) / BPS
        fill_price = last_candle.close * (ONE - terminal_slippage)
        if fill_price <= ZERO:
            raise BacktestError("terminal liquidation price is non-positive")
        notional = position * fill_price
        fee = notional * config.fee_rate
        proceeds = notional - fee
        trade_pnl = proceeds - entry_cost
        quote += proceeds
        realized += trade_pnl
        total_fees += fee
        fills.append(VirtualFill(
            last_candle.end, "SELL", position, fill_price, fee, quote,
            "TERMINAL_LIQUIDATION", trade_pnl,
        ))
        position, entry_cost = ZERO, ZERO
        previous_point = curve[-1]
        peak = max(peak, previous_point.equity)
        curve[-1] = EquityPoint(
            timestamp=previous_point.timestamp,
            close=previous_point.close,
            quote_balance=quote,
            base_position=ZERO,
            equity=quote,
            drawdown=quote / peak - ONE if peak > ZERO else ZERO,
        )

    final_equity = curve[-1].equity
    unrealized = _net_liquidation_value(position, eval_items[-1].payload.close, config) - entry_cost if position > ZERO else ZERO
    last_eval = eval_items[-1].payload.start
    final_history_index = index_by_start[last_eval]
    # The prefix-only final indicator series is authoritative; recompute from a
    # prefix ending at the last evaluated candle, excluding any referee-only suffix.
    final_prefix = accepted[: final_history_index + 1]
    final_values = _indicator_series([_observation(value) for value in final_prefix], config)[-1]
    if final_values is None:
        raise BacktestError("indicator history is insufficient at the final simulation tick")
    return BacktestResult(
        symbol=eval_items[0].instrument.symbol,
        interval=config.interval,
        config=config,
        candle_count=len(eval_items),
        first_candle=eval_items[0].payload.start,
        last_candle=eval_items[-1].payload.start,
        fills=tuple(fills),
        equity_curve=tuple(curve),
        final_quote_balance=quote,
        final_base_position=position,
        final_equity=final_equity,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_fees=total_fees,
        max_drawdown=min((point.drawdown for point in curve), default=ZERO),
        final_sma=final_values["sma"],
        final_fast_ema=final_values["fast_ema"],
        final_slow_ema=final_values["slow_ema"],
        final_rsi=final_values["rsi"],
        excluded_final_candles=excluded,
        unknown_marketdata_completeness_count=unknown_completeness,
        unknown_finality_count=unknown_finality,
        entry_rejections=tuple(rejections),
        final_bollinger_zscore=final_values.get("zscore"),
    )


def _build_timeline(
    *,
    all_items,
    eval_items,
    config: BacktestConfig,
    result,
) -> list[dict[str, object]]:
    """Reconstruct each decision using a prefix-only input at every virtual tick."""
    index_by_start = {item.payload.start: i for i, item in enumerate(all_items)}
    fills_by_time: dict[datetime, list] = {}
    for fill in result.fills:
        fills_by_time.setdefault(fill.timestamp, []).append(fill)
    rejected_by_time: dict[datetime, list[str]] = {}
    for reason in result.entry_rejections:
        timestamp_text, marker, detail = reason.partition(":ORDER_REJECTED")
        if not marker:
            # Non-order admission rejections have a separate schema and do not
            # correspond to an exchange-like simulated open fill.
            continue
        try:
            timestamp = datetime.fromisoformat(timestamp_text)
        except ValueError:
            continue
        rejected_by_time.setdefault(timestamp, []).append(marker + detail)

    position = ZERO
    quote = config.initial_quote_balance
    peak = config.initial_quote_balance
    visible_start = all_items[0].payload.start
    timeline: list[dict[str, object]] = []

    for eval_index, item in enumerate(eval_items):
        candle = item.payload
        history_index = index_by_start[candle.start]
        visible_items = all_items[: history_index + 1]
        # Crucial anti-lookahead boundary: indicator calculation receives only
        # candles already visible at this simulated close.
        visible_observations = [_observation(value) for value in visible_items]
        prefix_indicators = _indicator_series(visible_observations, config)
        values = prefix_indicators[-1]

        open_fills = fills_by_time.get(candle.start, [])
        executed_at_open: list[dict[str, str]] = []
        for fill in open_fills:
            quote = fill.quote_balance_after
            position = fill.quantity if fill.side == "BUY" else ZERO
            executed_at_open.append({
                "side": fill.side,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "fee": str(fill.fee),
                "reason": fill.reason,
                "realized_pnl": str(fill.realized_pnl) if fill.realized_pnl is not None else "",
            })

        terminal_fills = [
            fill for fill in fills_by_time.get(candle.end, [])
            if fill.reason == "TERMINAL_LIQUIDATION"
        ]
        terminal_events: list[dict[str, str]] = []
        for fill in terminal_fills:
            quote = fill.quote_balance_after
            position = ZERO
            terminal_events.append({
                "side": fill.side,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "fee": str(fill.fee),
                "reason": fill.reason,
                "realized_pnl": str(fill.realized_pnl) if fill.realized_pnl is not None else "",
            })

        recent_cutoff = candle.end - timedelta(days=30)
        recent_buys = sum(
            1 for fill in result.fills
            if fill.side == "BUY" and recent_cutoff < fill.timestamp <= candle.end
        )
        warmup_ready = values is not None and history_index >= max(config.slow_ema_period, config.rsi_period + 1)
        zscore = values.get("zscore") if values is not None else None
        ema = values.get("slow_ema") if values is not None else None
        previous_values = prefix_indicators[-2] if len(prefix_indicators) >= 2 else None
        if eval_index == len(eval_items) - 1:
            action = "END_OF_REPLAY"
        else:
            action = _decide_action(
                config=config,
                current=values,
                previous=previous_values,
                candle=candle,
                visible_items=visible_items,
                position=position,
                recent_entries=recent_buys,
                warmup_ready=warmup_ready,
            )

        previous_action = timeline[-1]["decision"] if timeline else ""
        prior_signal = previous_action if previous_action in ("BUY_NEXT_OPEN", "SELL_NEXT_OPEN") else ""
        open_status = "NO_PENDING_ORDER"
        if prior_signal:
            expected_side = prior_signal.split("_")[0]
            if any(fill.side == expected_side for fill in open_fills):
                open_status = "FILLED_AT_THIS_OPEN"
            elif candle.start in rejected_by_time:
                open_status = "REJECTED_AT_THIS_OPEN"
            else:
                raise BacktestError(
                    f"previous {prior_signal} decision had neither a fill nor a recorded rejection at next open"
                )

        equity = quote + _net_liquidation_value(position, candle.close, config)
        peak = max(peak, equity)
        drawdown = equity / peak - ONE if peak > ZERO else ZERO
        timeline.append({
            "simulated_decision_time_utc": (candle.end + timedelta(milliseconds=1)).isoformat(),
            "candle_start_utc": candle.start.isoformat(),
            "candle_close_time_utc": candle.end.isoformat(),
            "visible_history_start_utc": visible_start.isoformat(),
            "visible_history_end_utc": candle.end.isoformat(),
            "visible_candle_count_including_warmup": len(visible_items),
            "future_candles_passed_to_decision": 0,
            "ohlc": {
                "open": str(candle.open),
                "high": str(candle.high),
                "low": str(candle.low),
                "close": str(candle.close),
            },
            "strategy_id": config.strategy,
            "bollinger_zscore_48": str(zscore) if zscore is not None else None,
            "ema180": str(ema) if ema is not None else None,
            "fast_ema": str(values.get("fast_ema")) if values and values.get("fast_ema") is not None else None,
            "slow_ema": str(values.get("slow_ema")) if values and values.get("slow_ema") is not None else None,
            "rsi14": str(values.get("rsi")) if values and values.get("rsi") is not None else None,
            "donchian_previous_20_high": str(max(value.payload.high for value in visible_items[-21:-1])) if config.strategy == DONCHIAN_STRATEGY and len(visible_items) >= 21 else None,
            "donchian_previous_10_low": str(min(value.payload.low for value in visible_items[-11:-1])) if config.strategy == DONCHIAN_STRATEGY and len(visible_items) >= 11 else None,
            "position_quantity_after_open_execution": str(position),
            "quote_balance_after_open_execution": str(quote),
            "decision": action,
            "previous_close_signal_execution_status": open_status,
            "executions_at_open": executed_at_open,
            "terminal_executions_at_close": terminal_events,
            "entry_cap_count_visible_at_decision": recent_buys,
            "equity_after_this_candle_close": str(equity),
            "drawdown_at_this_tick": str(drawdown),
        })
    return timeline


def _buy_and_hold_benchmark(eval_items, config: BacktestConfig) -> dict[str, str]:
    """ETH buy-and-hold entered at the earliest next-open time available to the strategy."""
    if len(eval_items) < 2:
        return {"status": "BLOCKED", "reason": "fewer than two evaluation candles"}
    adverse = (config.slippage_bps + config.spread_bps / Decimal("2")) / BPS
    buy_price = eval_items[1].payload.open * (ONE + adverse)
    if buy_price <= ZERO:
        return {"status": "BLOCKED", "reason": "non-positive modeled buy price"}
    quantity = (config.initial_quote_balance * config.position_fraction) / (
        buy_price * (ONE + config.fee_rate)
    )
    quantity = (quantity / config.quantity_step).to_integral_value(rounding=ROUND_DOWN) * config.quantity_step
    notional = quantity * buy_price
    if quantity < config.min_qty or notional < config.min_notional:
        return {"status": "BLOCKED", "reason": "fixed quantity filters reject benchmark entry"}
    buy_fee = notional * config.fee_rate
    cash = config.initial_quote_balance - notional - buy_fee
    final_close = eval_items[-1].payload.close
    sell_price = final_close * (ONE - adverse)
    if sell_price <= ZERO:
        return {"status": "BLOCKED", "reason": "non-positive modeled liquidation price"}
    sell_notional = quantity * sell_price
    sell_fee = sell_notional * config.fee_rate
    final_cash = cash + sell_notional - sell_fee
    equity_curve = [config.initial_quote_balance]
    for index, item in enumerate(eval_items):
        if index == 0:
            equity_curve.append(config.initial_quote_balance)
        else:
            mark = item.payload.close * (ONE - adverse)
            mark_notional = quantity * mark
            equity_curve.append(cash + mark_notional - mark_notional * config.fee_rate)
    peak = config.initial_quote_balance
    max_drawdown = ZERO
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > ZERO:
            max_drawdown = min(max_drawdown, equity / peak - ONE)
    pnl = final_cash - config.initial_quote_balance
    ret = pnl / config.initial_quote_balance * Decimal("100")
    return {
        "status": "OK",
        "final_equity": str(final_cash),
        "net_pnl": str(pnl),
        "return_pct": str(ret),
        "max_drawdown_pct": str(max_drawdown * Decimal("100")),
        "entry_time_utc": eval_items[1].payload.start.isoformat(),
        "exit_time_utc": eval_items[-1].payload.end.isoformat(),
        "quantity": str(quantity),
    }


def _cash_benchmark(capital: Decimal) -> dict[str, str]:
    return {
        "status": "OK",
        "final_equity": str(capital),
        "net_pnl": "0",
        "return_pct": "0",
        "max_drawdown_pct": "0",
    }


def _scenario_summary(name: str, capital: Decimal, config: BacktestConfig, result, benchmark) -> dict[str, object]:
    sells = [fill for fill in result.fills if fill.side == "SELL"]
    wins = sum(1 for fill in sells if fill.realized_pnl is not None and fill.realized_pnl > ZERO)
    losses = sum(1 for fill in sells if fill.realized_pnl is not None and fill.realized_pnl < ZERO)
    pnl = result.final_equity - capital
    strategy_return = pnl / capital * Decimal("100")
    buy_hold_return = Decimal(benchmark["return_pct"]) if benchmark.get("status") == "OK" else None
    cash_benchmark = _cash_benchmark(capital)
    return {
        "scenario": name,
        "strategy_id": config.strategy,
        "strategy_definition": STRATEGY_DESCRIPTIONS[config.strategy],
        "initial_usdt": str(capital),
        "final_equity_usdt": str(result.final_equity),
        "net_pnl_usdt": str(pnl),
        "return_pct": str(strategy_return),
        "max_drawdown_pct": str(result.max_drawdown * Decimal("100")),
        "outperformance_vs_buy_hold_percentage_points": str(strategy_return - buy_hold_return) if buy_hold_return is not None else "UNKNOWN",
        "outperformance_vs_cash_percentage_points": str(strategy_return),
        "buy_fills": sum(1 for fill in result.fills if fill.side == "BUY"),
        "sell_fills_including_terminal_liquidation": len(sells),
        "winning_sell_cycles": wins,
        "losing_sell_cycles": losses,
        "total_fees_usdt": str(result.total_fees),
        "final_base_position": str(result.final_base_position),
        "buy_and_hold_same_cost_model": benchmark,
        "cash_no_trade_benchmark": cash_benchmark,
        "strategy": config.strategy,
        "decision": "NO_TRADE",
    }


def _write_scenario_files(
    *,
    output_dir: Path,
    capital: Decimal,
    scenario_name: str,
    config: BacktestConfig,
    result,
    timeline: list[dict[str, object]],
    summary: dict[str, object],
) -> None:
    name = f"{config.strategy}-{int(capital)}-{scenario_name.lower()}"
    (output_dir / f"timeline-{name}.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in timeline),
        encoding="utf-8",
    )
    (output_dir / f"report-{name}.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "config": _configuration_record(config),
                "fills": [
                    {
                        "timestamp": fill.timestamp.isoformat(),
                        "side": fill.side,
                        "quantity": str(fill.quantity),
                        "price": str(fill.price),
                        "fee": str(fill.fee),
                        "quote_balance_after": str(fill.quote_balance_after),
                        "reason": fill.reason,
                        "realized_pnl": str(fill.realized_pnl) if fill.realized_pnl is not None else None,
                    }
                    for fill in result.fills
                ],
                "entry_rejections": list(result.entry_rejections),
                "market_data_unknown_finality_count": result.unknown_finality_count,
                "market_data_unknown_completeness_count": result.unknown_marketdata_completeness_count,
                "candle_count": result.candle_count,
                "range_start": result.first_candle.isoformat(),
                "range_end": result.last_candle.isoformat(),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clock-driven, historical ETH BBMR 4h walk-forward replay; virtual funds only."
    )
    parser.add_argument(
        "--start",
        type=parse_aware_datetime,
        default=parse_aware_datetime("2026-10-01T00:00:00+00:00"),
        help="simulated clock start as an ISO-8601 timezone-aware timestamp (default 2026-10-01 00:00 UTC)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evidence/eth-bbmr-walk-forward"),
        help="directory for data bundle, per-scenario reports and chronological decision logs",
    )
    args = parser.parse_args(argv)
    start = args.start
    try:
        validate_start_boundary(start)
        capture_time = datetime.now(timezone.utc)
        if capture_time <= start:
            raise BacktestError("capture time must be after the simulated start")
        history_start = start - timedelta(hours=4 * 200)
        start_ms = int(history_start.timestamp() * 1000)
        end_ms = int(capture_time.timestamp() * 1000)

        metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
        instrument = metadata.resolve_active(SYMBOL)
        adapter = BinanceMetadataBackedAdapter(metadata=metadata)
        acquired = adapter.fetch_historical_market_data(
            symbol=SYMBOL,
            interval=INTERVAL,
            start_time=start_ms,
            end_time=end_ms,
            page_limit=1000,
            max_requests=5,
        )
        accepted, excluded_partial, unknown_completeness, unknown_finality = _validate_history(acquired, INTERVAL)
        # The market-data referee only releases fully closed bars to the simulated bot.
        closed = [item for item in accepted if item.payload.end < capture_time]
        eval_items = [item for item in closed if item.payload.start >= start]
        if not eval_items:
            raise BacktestError("no fully closed 4h candles available after the simulation start")
        if eval_items[0].payload.start != start:
            raise BacktestError(
                f"first evaluation candle must start exactly at {start.isoformat()}, received {eval_items[0].payload.start.isoformat()}"
            )
        eval_end = _expected_next_start(eval_items[-1].payload.start, INTERVAL)
        all_items = [item for item in closed if item.payload.start < eval_end]
        if len(all_items) <= EMA_PERIOD:
            raise BacktestError("insufficient visible warm-up candles before the simulation start")
        if any(item.instrument.instrument_id != instrument.instrument_id for item in all_items):
            raise BacktestError("historical candle instrument identity differs from current active Binance Spot instrument")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        summaries: list[dict[str, object]] = []
        dataset_sha = ""
        config_sha = ""
        evidence_records = []
        for strategy in FROZEN_STRATEGIES:
            for capital in (Decimal("60"), Decimal("100")):
                for scenario_name, costs in COST_SCENARIOS.items():
                    config = _config(capital, costs, len(eval_items), strategy=strategy)
                    batch_reference = run_virtual_backtest(
                        all_items,
                        config,
                        evaluation_start=start,
                        evaluation_end=eval_end,
                    )
                    result = _run_prefix_only(
                        all_items=all_items,
                        eval_items=eval_items,
                        config=config,
                    )
                    if result != batch_reference:
                        raise BacktestError(
                            f"prefix-only {strategy} execution diverged from the existing virtual accounting engine; "
                            "refusing to publish an unverified economic result"
                        )
                    data_hash, effective_config_hash, records = _evidence_hashes(all_items, config)
                    if not dataset_sha:
                        dataset_sha = data_hash
                        evidence_records = records
                    elif dataset_sha != data_hash:
                        raise BacktestError("dataset hash changed across scenarios; refusing non-identical market-data comparisons")
                    if strategy == STRATEGY and capital == Decimal("60") and scenario_name == "BASE":
                        config_sha = effective_config_hash
                    timeline = _build_timeline(
                        all_items=all_items,
                        eval_items=eval_items,
                        config=config,
                        result=result,
                    )
                    benchmark = _buy_and_hold_benchmark(eval_items, config)
                    summary = _scenario_summary(scenario_name, capital, config, result, benchmark)
                    summary["effective_configuration_sha256"] = effective_config_hash
                    summaries.append(summary)
                    _write_scenario_files(
                        output_dir=args.output_dir,
                        capital=capital,
                        scenario_name=scenario_name,
                        config=config,
                        result=result,
                        timeline=timeline,
                        summary=summary,
                    )
                    print(
                        "RESULT "
                        f"strategy={strategy} capital={capital} scenario={scenario_name} "
                        f"start={start.isoformat()} end_exclusive={eval_end.isoformat()} "
                        f"candles={len(eval_items)} "
                        f"final={result.final_equity} pnl={summary['net_pnl_usdt']} "
                        f"return_pct={summary['return_pct']} max_dd_pct={summary['max_drawdown_pct']} "
                        f"buys={summary['buy_fills']} sells={summary['sell_fills_including_terminal_liquidation']} "
                        f"wins={summary['winning_sell_cycles']} losses={summary['losing_sell_cycles']} "
                        f"buy_hold_final={benchmark.get('final_equity', benchmark.get('status'))} "
                        f"buy_hold_return_pct={benchmark.get('return_pct', 'UNKNOWN')} "
                        f"cash_final={capital} cash_return_pct=0"
                    )

        bundle = {
            "schema_version": 1,
            "acquired_at": capture_time.isoformat(),
            "simulation_start": start.isoformat(),
            "evaluation_start": start.isoformat(),
            "evaluation_end_exclusive": eval_end.isoformat(),
            "simulated_clock_mode": "PREFIX_ONLY_DECISION_PER_CLOSED_4H_CANDLE",
            "future_candles_passed_to_decision": 0,
            "strategy": STRATEGY,
            "frozen_strategies": [{"id": strategy_id, "definition": STRATEGY_DESCRIPTIONS[strategy_id]} for strategy_id in FROZEN_STRATEGIES],
            "decision": "NO_TRADE",
            "dataset_sha256": dataset_sha,
            "configuration_sha256": config_sha,
            "configuration": _configuration_record(_config(Decimal("60"), COST_SCENARIOS["BASE"], len(eval_items))),
            "data": evidence_records,
        }
        (args.output_dir / "market-data-bundle.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        index = {
            "research_only": True,
            "operational_decision": "NO_TRADE",
            "strategies": [{"id": strategy_id, "definition": STRATEGY_DESCRIPTIONS[strategy_id]} for strategy_id in FROZEN_STRATEGIES],
            "strategy": STRATEGY,
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "simulated_clock_start_utc": start.isoformat(),
            "capture_time_utc": capture_time.isoformat(),
            "evaluation_start_utc": start.isoformat(),
            "evaluation_end_exclusive_utc": eval_end.isoformat(),
            "evaluation_candles": len(eval_items),
            "warmup_candles": len(all_items) - len(eval_items),
            "dataset_sha256": dataset_sha,
            "capture_configuration_sha256": config_sha,
            "future_candles_passed_to_decision": 0,
            "known_market_data_boundaries": {
                "source": "public Binance Spot Klines",
                "spread_slippage": "assumptions, not observed historical book quotes",
                "lot_filters": "fixed assumptions; historical rule timeline not captured",
                "available_at": "UNKNOWN for canonical historical Klines; strategy uses completed-bar close then next-open fill",
                "candle_finality": "UNKNOWN where source metadata cannot prove point-in-time finality",
                "fill_model": "next 4h open with configured fee, half-spread and slippage; terminal liquidation at last close",
            },
            "raw_acquired_count": len(acquired),
            "closed_valid_count": len(all_items),
            "excluded_partial_count": excluded_partial,
            "unknown_completeness_count": unknown_completeness,
            "unknown_finality_count": unknown_finality,
            "scenarios": summaries,
        }
        (args.output_dir / "RESULT_INDEX.json").write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("WALK_FORWARD_SUMMARY " + json.dumps(index, sort_keys=True))
        print(f"Dataset SHA-256: {dataset_sha}")
        print(f"Effective configuration SHA-256: {config_sha}")
        print("Strategy input contract: prefix-only; future_candles_passed_to_decision=0")
        print("Frozen tactics compared: " + ", ".join(FROZEN_STRATEGIES))
        print("Benchmarks: ETH buy-and-hold under same modeled costs; cash no-trade at 0% nominal return")
        print("Research only. This historical window was previously screened; no independent holdout is established.")
        print("Decision: NO_TRADE")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
