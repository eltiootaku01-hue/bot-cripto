"""Fixed-window virtual replay for a frozen ETH 168h momentum secondary hypothesis.

Research-only. Public Binance Spot candles only; fails closed on data gaps.
No private endpoint, paper execution, or real orders are involved.
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
INTERVAL = "1h"
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
BAR = timedelta(hours=1)
MOMENTUM_LOOKBACK = 168
TREND_EMA_PERIOD = 100
WARMUP_BARS = 200
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
MOMENTUM_ENTRY = Decimal("0.01")
MOMENTUM_EXIT = Decimal("0")
ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
FRACTION = Decimal("0.95")
MAX_ENTRIES_30D = 5
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
        max_requests=10,
    )
    items = sorted(
        (x for x in acquired if WARMUP_START <= x.payload.start < END),
        key=lambda x: x.payload.start,
    )
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED: expected {EXPECTED_BARS} contiguous hourly bars, got {len(items)}; "
            f"missing_first={sorted(set(expected) - set(observed))[:12]}; "
            f"unexpected_first={sorted(set(observed) - set(expected))[:12]}. "
            "No bars were interpolated."
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


def _buy_hold(eval_items, capital, fee, spread, slip):
    adverse = _adverse(spread, slip)
    buy_price = eval_items[0].payload.open * (ONE + adverse)
    raw_qty = capital * FRACTION / (buy_price * (ONE + fee))
    qty = (raw_qty / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP
    buy_notional = qty * buy_price
    if qty < MIN_QTY or buy_notional < MIN_NOTIONAL:
        raise RuntimeError("FAIL-CLOSED: buy-and-hold benchmark violates fixed lot assumptions.")
    buy_fee = buy_notional * fee
    cash = capital - buy_notional - buy_fee
    sell_price = eval_items[-1].payload.close * (ONE - adverse)
    sell_notional = qty * sell_price
    sell_fee = sell_notional * fee
    return cash + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(items, capital, cost_name, fee, spread, slip):
    history, _, _, _ = _validate_history(items, INTERVAL)
    eval_items = [x for x in history if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: evaluation rows={len(eval_items)}, expected={EVAL_BARS}.")
    closes = [x.payload.close for x in history]
    alpha = Decimal("2") / Decimal(TREND_EMA_PERIOD + 1)
    ema = []
    running = closes[0]  # pandas ewm(span=100, adjust=False) recursive seed semantics
    for i, close in enumerate(closes):
        if i:
            running = alpha * close + (ONE - alpha) * running
        ema.append(running if i >= TREND_EMA_PERIOD - 1 else None)
    index_by_start = {x.payload.start: i for i, x in enumerate(history)}

    quote, position, entry_cost = capital, ZERO, ZERO
    total_fees = ZERO
    fills, curve, buy_times = [], [], []
    peak = capital
    pending = None

    for eval_idx, item in enumerate(eval_items):
        candle = item.payload
        if pending is not None:
            adverse = _adverse(spread, slip)
            if pending == "BUY" and position == ZERO:
                fill_price = candle.open * (ONE + adverse)
                raw_qty = quote * FRACTION / (fill_price * (ONE + fee))
                qty = (raw_qty / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP
                notional = qty * fill_price
                cutoff = candle.start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= candle.start]
                reason = None
                if qty < MIN_QTY:
                    reason = "MIN_QTY"
                elif notional < MIN_NOTIONAL:
                    reason = "MIN_NOTIONAL"
                elif notional + notional * fee > quote:
                    reason = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_30D:
                    reason = "MAX_ENTRIES_30D"
                if reason:
                    fills.append({
                        "timestamp_utc": candle.start.isoformat(), "side": "REJECTED_BUY",
                        "quantity_eth": str(qty), "price_usdt": str(fill_price),
                        "fee_usdt": "0", "realized_pnl_usdt": "", "reason": reason,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    position = qty
                    total_fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({
                        "timestamp_utc": candle.start.isoformat(), "side": "BUY",
                        "quantity_eth": str(qty), "price_usdt": str(fill_price),
                        "fee_usdt": str(buy_fee), "realized_pnl_usdt": "",
                        "reason": "168h momentum > +1% and close > EMA100; next-hour open fill",
                    })
            elif pending == "SELL" and position > ZERO:
                fill_price = candle.open * (ONE - adverse)
                notional = position * fill_price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                trade_pnl = proceeds - entry_cost
                quote += proceeds
                total_fees += sell_fee
                fills.append({
                    "timestamp_utc": candle.start.isoformat(), "side": "SELL",
                    "quantity_eth": str(position), "price_usdt": str(fill_price),
                    "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(trade_pnl),
                    "reason": "168h momentum <= 0% or close < EMA100; next-hour open fill",
                })
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + position * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "timestamp_utc": candle.end.isoformat(), "equity_usdt": equity,
            "base_position_eth": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })

        if eval_idx >= len(eval_items) - 1:
            continue
        i = index_by_start[candle.start]
        if i < max(MOMENTUM_LOOKBACK, TREND_EMA_PERIOD - 1) or ema[i] is None:
            continue
        momentum = closes[i] / closes[i - MOMENTUM_LOOKBACK] - ONE
        entry_signal = momentum > MOMENTUM_ENTRY and candle.close > ema[i]
        exit_signal = momentum <= MOMENTUM_EXIT or candle.close < ema[i]
        if position == ZERO and entry_signal:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D:
                pending = "BUY"
        elif position > ZERO and exit_signal:
            pending = "SELL"

    if position > ZERO:
        last = eval_items[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = position * price
        fee_exit = notional * fee
        proceeds = notional - fee_exit
        pnl = proceeds - entry_cost
        quote += proceeds
        total_fees += fee_exit
        fills.append({
            "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_eth": str(position), "price_usdt": str(price),
            "fee_usdt": str(fee_exit), "realized_pnl_usdt": str(pnl),
            "reason": "TERMINAL_LIQUIDATION at final evaluation close",
        })
        position = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {
            "timestamp_utc": curve[-1]["timestamp_utc"], "equity_usdt": quote,
            "base_position_eth": ZERO,
            "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final_equity = quote + position * eval_items[-1].payload.close * (ONE - _adverse(spread, slip)) * (ONE - fee)
    sells = [x for x in fills if x["side"] == "SELL"]
    wins = sum(Decimal(x["realized_pnl_usdt"]) > ZERO for x in sells if x["realized_pnl_usdt"])
    losses = sum(Decimal(x["realized_pnl_usdt"]) < ZERO for x in sells if x["realized_pnl_usdt"])
    return {
        "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final_equity), "net_pnl_usdt": str(final_equity - capital),
        "return_pct": str((final_equity / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(total_fees),
        "time_in_market_pct": str(Decimal(sum(x["base_position_eth"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")),
        "buy_count": sum(x["side"] == "BUY" for x in fills), "sell_count": len(sells),
    }, fills


def main():
    Path("secondary-output").mkdir(exist_ok=True)
    print("Frozen candidate: ETH_TSMOM_168H_EMA100; no parameter tuning.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()})")
    print(f"Warm-up UTC: [{WARMUP_START.isoformat()}, {START.isoformat()}); {WARMUP_BARS} hourly bars")
    print(f"Expected bars: {EXPECTED_BARS} total; {EVAL_BARS} evaluation bars")
    print("Rule: 168h momentum > +1% and close > EMA100 to buy; momentum <= 0% or close < EMA100 to sell.")
    print("Virtual only; no exchange orders.")

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
            bh_final, bh_fees = _buy_hold(eval_items, capital, fee, spread, slip)
            result.update({
                "buy_hold_final_equity_usdt": str(bh_final),
                "buy_hold_net_pnl_usdt": str(bh_final - capital),
                "buy_hold_fees_usdt": str(bh_fees),
                "cash_final_equity_usdt": str(capital),
                "dataset_sha256": data_sha, "evaluation_bars": EVAL_BARS,
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
        "# Secondary candidate: ETH 168h momentum + EMA100 — YTD 2026", "",
        "**Role:** secondary research candidate only. **Project decision: NO_TRADE.**", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()})",
        f"- Warm-up: {WARMUP_BARS} hourly bars; evaluation bars: {EVAL_BARS}",
        f"- Dataset SHA-256: {data_sha}",
        "- Rule frozen before replay: momentum close/close[-168]-1 > +1% and close > EMA100 to enter; momentum <= 0% or close < EMA100 to exit.",
        "- Signal from completed bars; fill at next hourly open; remaining position liquidated at final close.",
        "- Sizing 95%; one position; max 5 buy entries in rolling 30 days; fixed lot assumptions 0.0001 ETH / 5 USDT.",
        "- BASE: fee 0.10% per fill; assumed full spread 10 bps; slippage 5 bps per fill.",
        "- STRESS_1: fee 0.10% per fill; assumed full spread 25 bps; slippage 10 bps per fill.",
        "- STRESS_2: fee 0.20% per fill; assumed full spread 50 bps; slippage 20 bps per fill.", "",
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
        "", "Spread/slippage and lot filters are assumptions, not reconstructed historical bookTicker executions.",
        "Historical available_at and point-in-time finality remain unknown; the dataset was seen in earlier research.",
        "All trades are virtual; no PAPER/LIVE actions; do not infer future profitability.", "",
    ]
    Path("secondary-output/report.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
