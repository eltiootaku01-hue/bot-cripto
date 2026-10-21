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


def _config(capital: Decimal, costs: tuple[Decimal, Decimal, Decimal], candles: int) -> BacktestConfig:
    fee, spread, slippage = costs
    config = BacktestConfig(
        symbol=SYMBOL,
        interval=INTERVAL,
        strategy=STRATEGY,
        candles=candles,
        slow_ema_period=EMA_PERIOD,
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
        timestamp_text, _, detail = reason.partition(":")
        try:
            timestamp = datetime.fromisoformat(timestamp_text)
        except ValueError:
            continue
        rejected_by_time.setdefault(timestamp, []).append(detail)

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
        if eval_index == len(eval_items) - 1:
            action = "END_OF_REPLAY"
        else:
            action = _strategy_action(
                zscore=zscore,
                close=candle.close,
                ema=ema,
                position=position,
                recent_entries=recent_buys,
                warmup_ready=warmup_ready,
            )

        pending = action in ("BUY_NEXT_OPEN", "SELL_NEXT_OPEN")
        previous_action = timeline[-1]["decision"] if timeline else ""
        if pending and previous_action in ("BUY_NEXT_OPEN", "SELL_NEXT_OPEN"):
            raise BacktestError("overlapping virtual order decisions detected")
        prior_signal = previous_action if previous_action in ("BUY_NEXT_OPEN", "SELL_NEXT_OPEN") else ""
        open_status = "NO_PENDING_ORDER"
        if prior_signal:
            expected_side = prior_signal.split("_")[0]
            if any(fill.side == expected_side for fill in open_fills):
                open_status = "FILLED_AT_THIS_OPEN"
            elif candle.start in rejected_by_time:
                open_status = "REJECTED_AT_THIS_OPEN"
            else:
                open_status = "NO_FILL_OBSERVED_AT_THIS_OPEN"

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
            "bollinger_zscore_48": str(zscore) if zscore is not None else None,
            "ema180": str(ema) if ema is not None else None,
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


def _scenario_summary(name: str, capital: Decimal, config: BacktestConfig, result, benchmark) -> dict[str, object]:
    sells = [fill for fill in result.fills if fill.side == "SELL"]
    wins = sum(1 for fill in sells if fill.realized_pnl is not None and fill.realized_pnl > ZERO)
    losses = sum(1 for fill in sells if fill.realized_pnl is not None and fill.realized_pnl < ZERO)
    pnl = result.final_equity - capital
    return {
        "scenario": name,
        "initial_usdt": str(capital),
        "final_equity_usdt": str(result.final_equity),
        "net_pnl_usdt": str(pnl),
        "return_pct": str(pnl / capital * Decimal("100")),
        "max_drawdown_pct": str(result.max_drawdown * Decimal("100")),
        "buy_fills": sum(1 for fill in result.fills if fill.side == "BUY"),
        "sell_fills_including_terminal_liquidation": len(sells),
        "winning_sell_cycles": wins,
        "losing_sell_cycles": losses,
        "total_fees_usdt": str(result.total_fees),
        "final_base_position": str(result.final_base_position),
        "buy_and_hold_same_cost_model": benchmark,
        "strategy": STRATEGY,
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
    name = f"{int(capital)}-{scenario_name.lower()}"
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
        for capital in (Decimal("60"), Decimal("100")):
            for scenario_name, costs in COST_SCENARIOS.items():
                config = _config(capital, costs, len(eval_items))
                result = run_virtual_backtest(
                    all_items,
                    config,
                    evaluation_start=start,
                    evaluation_end=eval_end,
                )
                data_hash, effective_config_hash, records = _evidence_hashes(all_items, config)
                if not dataset_sha:
                    dataset_sha = data_hash
                    evidence_records = records
                elif dataset_sha != data_hash:
                    raise BacktestError("dataset hash changed across scenarios; refusing non-identical market-data comparisons")
                if capital == Decimal("60") and scenario_name == "BASE":
                    config_sha = effective_config_hash
                timeline = _build_timeline(
                    all_items=all_items,
                    eval_items=eval_items,
                    config=config,
                    result=result,
                )
                benchmark = _buy_and_hold_benchmark(eval_items, config)
                summary = _scenario_summary(scenario_name, capital, config, result, benchmark)
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
                    f"capital={capital} scenario={scenario_name} "
                    f"start={start.isoformat()} end_exclusive={eval_end.isoformat()} "
                    f"candles={len(eval_items)} "
                    f"final={result.final_equity} pnl={summary['net_pnl_usdt']} "
                    f"return_pct={summary['return_pct']} max_dd_pct={summary['max_drawdown_pct']} "
                    f"buys={summary['buy_fills']} sells={summary['sell_fills_including_terminal_liquidation']} "
                    f"wins={summary['winning_sell_cycles']} losses={summary['losing_sell_cycles']} "
                    f"buy_hold_final={benchmark.get('final_equity', benchmark.get('status'))} "
                    f"buy_hold_return_pct={benchmark.get('return_pct', 'UNKNOWN')}"
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
        print("Research only. This historical window was previously screened; no independent holdout is established.")
        print("Decision: NO_TRADE")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
