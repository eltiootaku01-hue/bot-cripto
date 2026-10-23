"""Fixed-window virtual replay for a frozen secondary Donchian 20/10 hypothesis.

Research-only. This script consumes public Binance Spot 4h candles and keeps
all accounting virtual. It fails closed on gaps or malformed bars.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from bot_obrero.binance_instruments import (
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
)
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record, _validate_history

UTC = timezone.utc
SYMBOL = "ETHUSDT"
INTERVAL = "4h"
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)  # exclusive; includes bars through Oct 21 20:00 UTC
BAR = timedelta(hours=4)
ENTRY_WINDOW = 20
EXIT_WINDOW = 10
WARMUP_BARS = max(ENTRY_WINDOW, EXIT_WINDOW)
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
POSITION_FRACTION = Decimal("0.95")
MAX_ENTRIES_PER_30D = 5
MIN_QTY = Decimal("0.0001")
QTY_STEP = Decimal("0.0001")
MIN_NOTIONAL = Decimal("5")
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)


def _fetch():
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    metadata.resolve_active(SYMBOL)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000),
        page_limit=1000,
        max_requests=5,
    )
    items = sorted(
        (x for x in acquired if WARMUP_START <= x.payload.start < END),
        key=lambda x: x.payload.start,
    )
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        missing = sorted(set(expected) - set(observed))[:12]
        unexpected = sorted(set(observed) - set(expected))[:12]
        raise RuntimeError(
            f"FAIL-CLOSED: expected {EXPECTED_BARS} contiguous 4h bars, got {len(items)}; "
            f"missing_first={missing}; unexpected_first={unexpected}. No bars were interpolated."
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(
            f"FAIL-CLOSED: accepted={len(accepted)}, excluded={excluded}; expected {EXPECTED_BARS} closed bars."
        )
    if any(x.instrument.instrument_id != "binance:SPOT:ETHUSDT" for x in items):
        raise RuntimeError("FAIL-CLOSED: unexpected instrument identity.")
    records = [_market_data_record(x) for x in items]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    dataset_sha = hashlib.sha256(canonical).hexdigest()
    return items, records, dataset_sha, unknown_completeness, unknown_finality


def _adverse(spread_bps: Decimal, slippage_bps: Decimal) -> Decimal:
    return (slippage_bps + spread_bps / Decimal("2")) / BPS


def _benchmark_buy_hold(eval_items, capital, fee, spread_bps, slippage_bps):
    adverse = _adverse(spread_bps, slippage_bps)
    buy_price = eval_items[0].payload.open * (ONE + adverse)
    raw_qty = capital * POSITION_FRACTION / (buy_price * (ONE + fee))
    qty = (raw_qty / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP
    buy_notional = qty * buy_price
    if qty < MIN_QTY or buy_notional < MIN_NOTIONAL:
        raise RuntimeError("FAIL-CLOSED: buy-and-hold benchmark violates lot assumptions.")
    buy_fee = buy_notional * fee
    residual_cash = capital - buy_notional - buy_fee
    sell_price = eval_items[-1].payload.close * (ONE - adverse)
    sell_notional = qty * sell_price
    sell_fee = sell_notional * fee
    return residual_cash + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(items, capital, cost_name, fee, spread_bps, slippage_bps):
    accepted, _, _, _ = _validate_history(items, INTERVAL)
    history = accepted
    eval_items = [x for x in history if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: evaluation rows={len(eval_items)}; expected={EVAL_BARS}.")
    index_by_start = {x.payload.start: i for i, x in enumerate(history)}
    quote = capital
    position = ZERO
    entry_cost = ZERO
    total_fees = ZERO
    realized_pnl = ZERO
    fills = []
    equity_curve = []
    peak = capital
    pending = None
    buy_times = []

    for eval_idx, item in enumerate(eval_items):
        candle = item.payload
        if pending is not None:
            side = pending
            adverse = _adverse(spread_bps, slippage_bps)
            if side == "BUY" and position == ZERO:
                fill_price = candle.open * (ONE + adverse)
                raw_qty = quote * POSITION_FRACTION / (fill_price * (ONE + fee))
                qty = (raw_qty / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP
                notional = qty * fill_price
                cutoff = candle.start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= candle.start]
                reject = None
                if qty < MIN_QTY:
                    reject = "MIN_QTY"
                elif notional < MIN_NOTIONAL:
                    reject = "MIN_NOTIONAL"
                elif notional + notional * fee > quote:
                    reject = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_PER_30D:
                    reject = "MAX_ENTRIES_30D"
                if reject is not None:
                    fills.append({
                        "timestamp_utc": candle.start.isoformat(), "side": "REJECTED_BUY",
                        "quantity_eth": str(qty), "price_usdt": str(fill_price),
                        "fee_usdt": "0", "realized_pnl_usdt": "",
                        "reason": reject,
                    })
                else:
                    buy_fee = notional * fee
                    cost = notional + buy_fee
                    quote -= cost
                    position, entry_cost = qty, cost
                    total_fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({
                        "timestamp_utc": candle.start.isoformat(), "side": "BUY",
                        "quantity_eth": str(qty), "price_usdt": str(fill_price),
                        "fee_usdt": str(buy_fee), "realized_pnl_usdt": "",
                        "reason": "close > previous 20-bar high channel; next 4h open fill",
                    })
            elif side == "SELL" and position > ZERO:
                fill_price = candle.open * (ONE - adverse)
                notional = position * fill_price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                trade_pnl = proceeds - entry_cost
                quote += proceeds
                realized_pnl += trade_pnl
                total_fees += sell_fee
                fills.append({
                    "timestamp_utc": candle.start.isoformat(), "side": "SELL",
                    "quantity_eth": str(position), "price_usdt": str(fill_price),
                    "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(trade_pnl),
                    "reason": "close < previous 10-bar low channel; next 4h open fill",
                })
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread_bps, slippage_bps)
        liquidation_mark = position * candle.close * (ONE - adverse) * (ONE - fee)
        equity = quote + liquidation_mark
        peak = max(peak, equity)
        equity_curve.append({
            "timestamp_utc": candle.end.isoformat(),
            "equity_usdt": equity,
            "base_position_eth": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })

        if eval_idx >= len(eval_items) - 1:
            continue
        i = index_by_start[candle.start]
        # Exclude the current candle from both channels. Signals use only the completed prefix.
        previous = history[i - ENTRY_WINDOW:i]
        previous_exit = history[i - EXIT_WINDOW:i]
        if len(previous) < ENTRY_WINDOW or len(previous_exit) < EXIT_WINDOW:
            continue
        upper = max(x.payload.high for x in previous)
        lower = min(x.payload.low for x in previous_exit)
        if position == ZERO and candle.close > upper:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_PER_30D:
                pending = "BUY"
        elif position > ZERO and candle.close < lower:
            pending = "SELL"

    # Liquidate any remaining position at the last evaluation close, charging modeled exit costs.
    if position > ZERO:
        last = eval_items[-1].payload
        adverse = _adverse(spread_bps, slippage_bps)
        price = last.close * (ONE - adverse)
        notional = position * price
        fee_exit = notional * fee
        proceeds = notional - fee_exit
        trade_pnl = proceeds - entry_cost
        quote += proceeds
        realized_pnl += trade_pnl
        total_fees += fee_exit
        fills.append({
            "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_eth": str(position), "price_usdt": str(price),
            "fee_usdt": str(fee_exit), "realized_pnl_usdt": str(trade_pnl),
            "reason": "TERMINAL_LIQUIDATION at final evaluation close",
        })
        position = ZERO
        if equity_curve:
            peak = max(peak, equity_curve[-1]["equity_usdt"])
            equity_curve[-1] = {
                "timestamp_utc": equity_curve[-1]["timestamp_utc"],
                "equity_usdt": quote, "base_position_eth": ZERO,
                "drawdown": quote / peak - ONE if peak else ZERO,
            }

    final_equity = quote + position * eval_items[-1].payload.close * (ONE - _adverse(spread_bps, slippage_bps)) * (ONE - fee)
    sells = [f for f in fills if f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    max_dd = min((p["drawdown"] for p in equity_curve), default=ZERO)
    held_bars = sum(1 for p in equity_curve if p["base_position_eth"] > ZERO)
    return {
        "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final_equity), "net_pnl_usdt": str(final_equity - capital),
        "return_pct": str((final_equity / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(max_dd * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(total_fees),
        "time_in_market_pct": str(Decimal(held_bars) / Decimal(len(equity_curve)) * Decimal("100")),
        "buy_count": sum(1 for f in fills if f["side"] == "BUY"),
        "sell_count": len(sells),
    }, fills


def main():
    Path("secondary-output").mkdir(exist_ok=True)
    print("Frozen candidate: DONCHIAN_BREAKOUT_20_10; no parameter search")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()})")
    print(f"Warm-up UTC: [{WARMUP_START.isoformat()}, {START.isoformat()}); {WARMUP_BARS} completed 4h bars")
    print(f"Expected bars: {EXPECTED_BARS} total; {EVAL_BARS} evaluation bars")
    print("Virtual only; public Binance Spot data; no orders or private API.")

    items, records, data_sha, unknown_comp, unknown_final = _fetch()
    eval_items = [x for x in items if START <= x.payload.start < END]
    evidence = {
        "symbol": SYMBOL, "interval": INTERVAL,
        "evaluation_start": START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(), "warmup_bars": WARMUP_BARS,
        "evaluation_bars": EVAL_BARS, "expected_bars": EXPECTED_BARS,
        "dataset_sha256": data_sha, "missing_or_interpolated_bars": 0,
        "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final, "data": records,
    }
    Path("secondary-output/dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Dataset SHA-256:", data_sha)
    print("Data time range:", items[0].payload.start.isoformat(), "..", items[-1].payload.start.isoformat())
    print(f"Completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")

    results, all_fills = [], []
    for capital in (Decimal("60"), Decimal("100")):
        for name, fee, spread, slip in SCENARIOS:
            result, fills = _simulate(items, capital, name, fee, spread, slip)
            bh_final, bh_fees = _benchmark_buy_hold(eval_items, capital, fee, spread, slip)
            result.update({
                "buy_hold_final_equity_usdt": str(bh_final),
                "buy_hold_net_pnl_usdt": str(bh_final - capital),
                "buy_hold_fees_usdt": str(bh_fees),
                "cash_final_equity_usdt": str(capital),
                "dataset_sha256": data_sha,
                "evaluation_bars": EVAL_BARS,
            })
            results.append(result)
            for fill in fills:
                all_fills.append({"capital_initial_usdt": str(capital), "cost_scenario": name, **fill})
            print(
                f"RESULT | capital={capital} | {name} | final={Decimal(result['final_equity_usdt']):.8f} | "
                f"net_pnl={Decimal(result['net_pnl_usdt']):+.8f} | return_pct={result['return_pct']} | "
                f"max_dd_pct={result['max_drawdown_pct']} | trips={result['completed_round_trips']} "
                f"W/L={result['wins']}/{result['losses']} | fees={result['total_fees_usdt']} | "
                f"buy_hold_pnl={Decimal(result['buy_hold_net_pnl_usdt']):+.8f}"
            )

    with open("secondary-output/summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    fill_fields = list(all_fills[0]) if all_fills else [
        "capital_initial_usdt", "cost_scenario", "timestamp_utc", "side",
        "quantity_eth", "price_usdt", "fee_usdt", "realized_pnl_usdt", "reason",
    ]
    with open("secondary-output/fills.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fill_fields)
        w.writeheader()
        w.writerows(all_fills)

    report = [
        "# Secondary candidate: ETH Donchian 20/10 — YTD 2026", "",
        "**Role:** secondary research candidate only. **Project decision: NO_TRADE.**", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()})",
        f"- Warm-up: {WARMUP_BARS} 4h bars; evaluation bars: {EVAL_BARS}",
        f"- Dataset SHA-256: {data_sha}",
        "- Rule: close above previous 20-bar highest high to enter; close below previous 10-bar lowest low to exit.",
        "- Channel excludes current bar; completed-candle signal; next-bar open fill; terminal liquidation at final close.",
        "- Position sizing 95%; one position; maximum five buy entries per rolling 30 days; minimum/step 0.0001 ETH and 5 USDT notional (assumptions).",
        "- BASE: fee 0.10% per fill, assumed full spread 10 bps, slippage 5 bps per fill.",
        "- STRESS_1: fee 0.10% per fill, assumed full spread 25 bps, slippage 10 bps per fill.",
        "- STRESS_2: fee 0.20% per fill, assumed full spread 50 bps, slippage 20 bps per fill.", "",
        "| Capital | Cost | Final equity | Net PnL | Return | Max DD | Cycles | W/L | Fees | B&H PnL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['final_equity_usdt']):.4f} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | "
            f"{r['completed_round_trips']} | {r['wins']}/{r['losses']} | "
            f"{Decimal(r['total_fees_usdt']):.4f} | {Decimal(r['buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report += [
        "", "Spread/slippage and lot filters are assumptions; no historical bookTicker execution was reconstructed.",
        "Historical available_at and point-in-time finality remain unknown. This is not a holdout untouched by earlier research.",
        "All trades are virtual; no PAPER/LIVE actions; do not infer future profitability.", "",
    ]
    Path("secondary-output/report.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
