"""Frozen BTC/ETH weekly relative-strength rotation; cash allowed; virtual only."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record, _validate_history

UTC = timezone.utc
INTERVAL = "4h"
BAR = timedelta(hours=4)
START = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
WARMUP_BARS = 300
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
ZERO, ONE, HUNDRED, BPS = Decimal("0"), Decimal("1"), Decimal("100"), Decimal("10000")
POSITION_FRACTION = Decimal("0.50")
REBALANCE_BARS = 42
MOMENTUM_LOOKBACK = 180
EMA_PERIOD = 200
ASSETS = ("BTCUSDT", "ETHUSDT")
LOT = {
    "BTCUSDT": (Decimal("0.00001"), Decimal("0.00001"), Decimal("5")),
    "ETHUSDT": (Decimal("0.0001"), Decimal("0.0001"), Decimal("5")),
}
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)


def _fetch(symbol: str):
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    metadata.resolve_active(symbol)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=symbol, interval=INTERVAL,
        start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000), page_limit=1000, max_requests=25,
    )
    items = sorted(
        (x for x in acquired if WARMUP_START <= x.payload.start < END),
        key=lambda x: x.payload.start,
    )
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED {symbol}: expected {EXPECTED_BARS} contiguous bars, got {len(items)}; "
            f"missing={sorted(set(expected)-set(observed))[:10]}; "
            f"unexpected={sorted(set(observed)-set(expected))[:10]}"
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED {symbol}: accepted={len(accepted)}, excluded={excluded}")
    if any(x.instrument.instrument_id != f"binance:SPOT:{symbol}" for x in items):
        raise RuntimeError(f"FAIL-CLOSED {symbol}: unexpected instrument identity")
    records = [_market_data_record(x) for x in items]
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return items, records, hashlib.sha256(blob).hexdigest(), unknown_completeness, unknown_finality


def _ema(values: list[Decimal], period: int):
    alpha = Decimal("2") / Decimal(period + 1)
    output: list[Decimal | None] = [None] * len(values)
    value = values[0]
    for i, close in enumerate(values):
        if i:
            value = alpha * close + (ONE - alpha) * value
        if i >= period - 1:
            output[i] = value
    return output


def _adverse(spread: Decimal, slippage: Decimal):
    return (slippage + spread / Decimal("2")) / BPS


def _floor_step(value: Decimal, step: Decimal):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _buy_hold(items, capital: Decimal, asset: str, fee: Decimal, spread: Decimal, slippage: Decimal):
    qty_step, min_qty, min_notional = LOT[asset]
    adverse = _adverse(spread, slippage)
    entry_price = items[0].payload.open * (ONE + adverse)
    qty = _floor_step(capital * POSITION_FRACTION / (entry_price * (ONE + fee)), qty_step)
    notional = qty * entry_price
    if qty < min_qty or notional < min_notional or notional * (ONE + fee) > capital:
        raise RuntimeError(f"FAIL-CLOSED: {asset} buy-and-hold violates fixed lot assumptions")
    buy_fee = notional * fee
    cash = capital - notional - buy_fee
    exit_notional = qty * items[-1].payload.close * (ONE - adverse)
    sell_fee = exit_notional * fee
    return cash + exit_notional - sell_fee, buy_fee + sell_fee


def _desired_target(i: int, closes, emas):
    eligible = []
    for asset in ASSETS:
        price = closes[asset][i]
        ema = emas[asset][i]
        if ema is None or i < MOMENTUM_LOOKBACK:
            continue
        momentum = price / closes[asset][i - MOMENTUM_LOOKBACK] - ONE
        if momentum > ZERO and price > ema:
            eligible.append((momentum, asset))
    if not eligible:
        return None, "neither BTC nor ETH has positive 30d momentum above its own EMA200"
    eligible.sort(key=lambda x: (x[0], x[1]))
    momentum, asset = eligible[-1]
    return asset, f"{asset} selected: trailing 30d momentum {momentum * HUNDRED:.4f}% > 0 and close > own EMA200"


def _simulate(items, capital, scenario, fee, spread, slippage, closes, opens, emas, hashes):
    index_by_start = {x.payload.start: i for i, x in enumerate(items["BTCUSDT"])}
    eval_items = {
        asset: [x for x in items[asset] if START <= x.payload.start < END]
        for asset in ASSETS
    }
    if any(len(eval_items[a]) != EVAL_BARS for a in ASSETS):
        raise RuntimeError("FAIL-CLOSED: evaluation row count mismatch")
    if [x.payload.start for x in eval_items["BTCUSDT"]] != [x.payload.start for x in eval_items["ETHUSDT"]]:
        raise RuntimeError("FAIL-CLOSED: BTC and ETH evaluation timestamps are not aligned")

    quote = capital
    holding = None
    quantity = ZERO
    entry_cost = ZERO
    total_fees = ZERO
    peak = capital
    pending_target = None
    pending_set = False
    fills, curve = [], []
    realized_pnls = []
    weekly_decisions = 0

    for eval_idx in range(EVAL_BARS):
        timestamp = eval_items["BTCUSDT"][eval_idx].payload.start
        i = index_by_start[timestamp]

        if pending_set:
            target = pending_target
            pending_set = False
            if target != holding:
                # Sell current holding at this next candle's open, paying modeled spread/slippage and fee.
                if holding is not None and quantity > ZERO:
                    adverse = _adverse(spread, slippage)
                    sell_price = opens[holding][i] * (ONE - adverse)
                    notional = quantity * sell_price
                    sell_fee = notional * fee
                    proceeds = notional - sell_fee
                    realized = proceeds - entry_cost
                    realized_pnls.append(realized)
                    quote += proceeds
                    total_fees += sell_fee
                    fills.append({
                        "timestamp_utc": timestamp.isoformat(), "side": "SELL", "asset": holding,
                        "quantity": str(quantity), "price_usdt": str(sell_price), "fee_usdt": str(sell_fee),
                        "realized_pnl_usdt": str(realized), "reason": "scheduled next-open rebalance",
                    })
                    holding, quantity, entry_cost = None, ZERO, ZERO

                if target is not None:
                    adverse = _adverse(spread, slippage)
                    buy_price = opens[target][i] * (ONE + adverse)
                    step, min_qty, min_notional = LOT[target]
                    buy_qty = _floor_step(quote * POSITION_FRACTION / (buy_price * (ONE + fee)), step)
                    notional = buy_qty * buy_price
                    rejection = None
                    if buy_qty < min_qty:
                        rejection = "MIN_QTY"
                    elif notional < min_notional:
                        rejection = "MIN_NOTIONAL"
                    elif notional * (ONE + fee) > quote:
                        rejection = "COSTS_EXCEED_BALANCE"
                    if rejection:
                        fills.append({
                            "timestamp_utc": timestamp.isoformat(), "side": "REJECTED_BUY", "asset": target,
                            "quantity": str(buy_qty), "price_usdt": str(buy_price), "fee_usdt": "0",
                            "realized_pnl_usdt": "", "reason": rejection,
                        })
                    else:
                        buy_fee = notional * fee
                        entry_cost = notional + buy_fee
                        quote -= entry_cost
                        total_fees += buy_fee
                        holding, quantity = target, buy_qty
                        fills.append({
                            "timestamp_utc": timestamp.isoformat(), "side": "BUY", "asset": target,
                            "quantity": str(buy_qty), "price_usdt": str(buy_price), "fee_usdt": str(buy_fee),
                            "realized_pnl_usdt": "", "reason": "weekly relative-strength target",
                        })

        # Conservatively mark holdings at estimated immediate liquidation value.
        if holding is None:
            equity = quote
        else:
            adverse = _adverse(spread, slippage)
            mark = closes[holding][i] * (ONE - adverse)
            equity = quote + quantity * mark * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "timestamp_utc": eval_items["BTCUSDT"][eval_idx].payload.end.isoformat(),
            "equity_usdt": str(equity), "holding": holding or "CASH",
            "drawdown_pct": str((equity / peak - ONE) * HUNDRED if peak else ZERO),
        })

        # Schedule a new target after observing the current completed candle's close.
        decision_time = eval_items["BTCUSDT"][eval_idx].payload.end
        if eval_idx < EVAL_BARS - 1 and decision_time.weekday() == 0 and decision_time.hour == 0:
            target, reason = _desired_target(i, closes, emas)
            weekly_decisions += 1
            if target != holding:
                pending_target = target
                pending_set = True
                fills.append({
                    "timestamp_utc": eval_items["BTCUSDT"][eval_idx].payload.end.isoformat(),
                    "side": "SIGNAL", "asset": target or "CASH", "quantity": "", "price_usdt": "",
                    "fee_usdt": "0", "realized_pnl_usdt": "", "reason": reason,
                })

    # Liquidate any remaining position at the final completed close.
    if holding is not None and quantity > ZERO:
        last_i = index_by_start[eval_items["BTCUSDT"][-1].payload.start]
        adverse = _adverse(spread, slippage)
        sell_price = closes[holding][last_i] * (ONE - adverse)
        notional = quantity * sell_price
        sell_fee = notional * fee
        proceeds = notional - sell_fee
        realized = proceeds - entry_cost
        realized_pnls.append(realized)
        quote += proceeds
        total_fees += sell_fee
        fills.append({
            "timestamp_utc": eval_items["BTCUSDT"][-1].payload.end.isoformat(), "side": "SELL", "asset": holding,
            "quantity": str(quantity), "price_usdt": str(sell_price), "fee_usdt": str(sell_fee),
            "realized_pnl_usdt": str(realized), "reason": "terminal liquidation",
        })
        holding, quantity = None, ZERO
        curve[-1]["equity_usdt"] = str(quote)
        curve[-1]["holding"] = "CASH"
        peak = max(peak, quote)
        curve[-1]["drawdown_pct"] = str((quote / peak - ONE) * HUNDRED if peak else ZERO)

    final_equity = quote
    completed_closures = sum(f["side"] == "SELL" for f in fills)
    wins = sum(pnl > ZERO for pnl in realized_pnls)
    losses = sum(pnl < ZERO for pnl in realized_pnls)
    bh_results = {}
    for asset in ASSETS:
        bh, fees = _buy_hold(eval_items[asset], capital, asset, fee, spread, slippage)
        name = asset.lower().replace("usdt", "_usdt")
        bh_results[f"{name}_buy_hold_final"] = str(bh)
        bh_results[f"{name}_buy_hold_pnl"] = str(bh - capital)
        bh_results[f"{name}_buy_hold_fees"] = str(fees)
    return {
        "capital_initial_usdt": str(capital), "cost_scenario": scenario,
        "final_equity_usdt": str(final_equity), "net_pnl_usdt": str(final_equity - capital),
        "return_pct": str((final_equity / capital - ONE) * HUNDRED),
        "max_drawdown_pct": str(min((Decimal(x["drawdown_pct"]) for x in curve), default=ZERO)),
        "completed_closures": completed_closures, "realized_wins": int(wins), "realized_losses": int(losses),
        "total_fees_usdt": str(total_fees), "weekly_decisions": weekly_decisions,
        "cash_final_equity_usdt": str(capital), **bh_results, **hashes, "evaluation_bars": EVAL_BARS,
    }, fills, curve


def main():
    global START, END, EVAL_BARS
    out = Path("rotation-50-output")
    out.mkdir(exist_ok=True)
    print("Frozen BTC/ETH weekly relative-strength rotation, 50% allocation, multiple fixed periods; virtual only.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Window UTC: [{START.isoformat()}, {END.isoformat()}); evaluation bars={EVAL_BARS}; warm-up={WARMUP_BARS}")
    records, hashes, meta = {}, {}, {}
    items = {}
    for asset in ASSETS:
        it, rec, sha, unknown_comp, unknown_final = _fetch(asset)
        items[asset], records[asset], hashes[asset] = it, rec, sha
        meta[asset] = {"completeness_unknown": unknown_comp, "finality_unknown": unknown_final}
        print(f"{asset} SHA-256={sha}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")
    timestamps = {asset: [x.payload.start for x in items[asset]] for asset in ASSETS}
    if timestamps["BTCUSDT"] != timestamps["ETHUSDT"]:
        raise RuntimeError("FAIL-CLOSED: BTC and ETH candle timestamps do not match")
    combined = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    combined_sha = hashlib.sha256(combined).hexdigest()
    manifest = {
        "evaluation_start": START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(), "warmup_bars": WARMUP_BARS,
        "interval": INTERVAL, "expected_bars_per_asset": EXPECTED_BARS,
        "evaluation_bars": EVAL_BARS, "asset_dataset_sha256": hashes,
        "combined_dataset_sha256": combined_sha, "market_data_metadata": meta,
        "data": records,
    }
    Path(out / "dataset-evidence.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    closes = {asset: [x.payload.close for x in items[asset]] for asset in ASSETS}
    opens = {asset: [x.payload.open for x in items[asset]] for asset in ASSETS}
    emas = {asset: _ema(closes[asset], EMA_PERIOD) for asset in ASSETS}
    hashes = {"btc_dataset_sha256": hashes["BTCUSDT"], "eth_dataset_sha256": hashes["ETHUSDT"], "combined_dataset_sha256": combined_sha}
    WINDOWS = (
        ("FULL_2020_2026", START, END),
        ("HOLDOUT_2020_2022", datetime(2020, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
        ("RECENT_2023_2026", datetime(2023, 1, 1, tzinfo=UTC), END),
        ("2020", datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
        ("2021", datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
        ("2022", datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
        ("2023", datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
        ("2024", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
        ("2025", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026_YTD", datetime(2026, 1, 1, tzinfo=UTC), END),
    )
    full_start, full_end = START, END
    full_bars = EVAL_BARS
    results, all_fills, all_curves = [], [], []
    for window_name, window_start, window_end in WINDOWS:
        START, END = window_start, window_end
        EVAL_BARS = int((END - START) / BAR)
        for capital in (Decimal("60"), Decimal("100")):
            for cost_name, fee, spread, slippage in SCENARIOS:
                result, fills, curve = _simulate(items, capital, cost_name, fee, spread, slippage, closes, opens, emas, hashes)
                result["window"] = window_name
                results.append(result)
                all_fills.extend({"window": window_name, "capital_initial_usdt": str(capital), "cost_scenario": cost_name, **f} for f in fills)
                all_curves.extend({"window": window_name, "capital_initial_usdt": str(capital), "cost_scenario": cost_name, **c} for c in curve)
                print(
                    f"RESULT | {window_name} | capital={capital} | {cost_name} | final={Decimal(result['final_equity_usdt']):.6f} | "
                    f"net_pnl={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                    f"maxDD={Decimal(result['max_drawdown_pct']):.3f}% | closures={result['completed_closures']} "
                    f"W/L={result['realized_wins']}/{result['realized_losses']} | fees={Decimal(result['total_fees_usdt']):.4f} | "
                    f"BTC_B&H={Decimal(result['btc_usdt_buy_hold_pnl']):+.4f} | ETH_B&H={Decimal(result['eth_usdt_buy_hold_pnl']):+.4f}"
                )
    START, END, EVAL_BARS = full_start, full_end, full_bars
    for fname, data in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with open(out / fname, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]) if data else [])
            writer.writeheader()
            writer.writerows(data)
    lines = [
        "# BTC/ETH weekly relative-strength rotation — 50% allocation, fixed multi-regime replay", "",
        "**Status:** research only. **Decision: NO_TRADE.**", "",
        f"- Full window: [{START.isoformat()}, {END.isoformat()}); {EVAL_BARS} evaluation 4h bars and {WARMUP_BARS} warm-up bars.",
        f"- BTC SHA-256: {hashes['btc_dataset_sha256']}; ETH SHA-256: {hashes['eth_dataset_sha256']}; combined: {combined_sha}.",
        f"- Data metadata: {json.dumps(meta, sort_keys=True)}.",
        "- Rule: every Monday 00:00 UTC, rank BTC/ETH by trailing 180-bar return; choose the strongest only if its momentum >0 and close > its own EMA200; otherwise cash.",
        "- Signals use completed candles, fills at the next 4h open; each evaluation window resets capital and liquidates terminal positions.",
        "- Position allocation: 50% of available quote balance per entry; current lot steps/minimum notional are assumptions, not historical filter proof.",
        "- BASE: fee 10 bps, assumed full spread 10 bps, slippage 5 bps per fill.",
        "- STRESS_1: fee 10 bps, assumed full spread 25 bps, slippage 10 bps.",
        "- STRESS_2: fee 20 bps, assumed full spread 50 bps, slippage 20 bps.", "",
        "| Window | Capital | Cost | Net PnL | Return | Max DD | Closures | W/L | Fees | BTC B&H PnL | ETH B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['window']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | "
            f"{r['completed_closures']} | {r['realized_wins']}/{r['realized_losses']} | "
            f"{Decimal(r['total_fees_usdt']):.4f} | {Decimal(r['btc_usdt_buy_hold_pnl']):+.4f} | "
            f"{Decimal(r['eth_usdt_buy_hold_pnl']):+.4f} |"
        )
    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    by_window = {name: [r for r in stress if r["window"] == name] for name, _, _ in WINDOWS}
    required_blocks = ("FULL_2020_2026", "HOLDOUT_2020_2022", "RECENT_2023_2026")
    block_pass = {}
    for name in required_blocks:
        rows = by_window[name]
        block_pass[name] = len(rows) == 2 and all(
            Decimal(r["net_pnl_usdt"]) > ZERO and r["completed_closures"] >= 5
            and Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in rows
        )
    annual_names = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    annual_rows = [r for name in annual_names for r in by_window[name]]
    annual_dd_pass = len(annual_rows) == len(annual_names) * 2 and all(
        Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in annual_rows
    )
    bear_rows = by_window["2022"]
    bear_pass = len(bear_rows) == 2 and all(Decimal(r["net_pnl_usdt"]) >= ZERO for r in bear_rows)
    profitable_years = {
        cap: sum(Decimal(r["net_pnl_usdt"]) > ZERO for r in annual_rows if r["capital_initial_usdt"] == cap)
        for cap in ("60", "100")
    }
    years_pass = all(v >= 4 for v in profitable_years.values())
    ytd_rows = by_window["2026_YTD"]
    ytd_pass = len(ytd_rows) == 2 and all(
        Decimal(r["net_pnl_usdt"]) > ZERO
        and Decimal(r["net_pnl_usdt"]) > Decimal(r["btc_usdt_buy_hold_pnl"])
        and Decimal(r["net_pnl_usdt"]) > Decimal(r["eth_usdt_buy_hold_pnl"])
        for r in ytd_rows
    )
    gate = all(block_pass.values()) and annual_dd_pass and bear_pass and years_pass and ytd_pass
    lines += ["", "## Preregistered STRESS_2 gate", "",
              "Required: positive PnL at both capitals in full/HOLDOUT/RECENT; at least 5 closures and max drawdown no worse than -15% in each block; annual/YTD drawdown no worse than -15%; 2022 nonnegative; at least 4 profitable annual/YTD slices per capital; 2026 YTD positive and better than both BTC and ETH buy-and-hold.", ""]
    lines.append("Block gates: " + json.dumps(block_pass, sort_keys=True))
    lines.append("Annual drawdown gate: " + str(annual_dd_pass))
    lines.append("2022 nonnegative gate: " + str(bear_pass))
    lines.append("Profitable annual/YTD windows by capital: " + json.dumps(profitable_years, sort_keys=True))
    lines.append("Four profitable annual/YTD windows per capital: " + str(years_pass))
    lines.append("2026 YTD beats both buy-and-hold benchmarks: " + str(ytd_pass))
    lines.append("**Overall preregistered gate: " + ("PASS" if gate else "FAIL") + "**")
    lines += ["", "Costs/lots and fills are modeled assumptions; market completeness/point-in-time history is not proven. The 2026 YTD source results were previously inspected, so this is retrospective research, not a blind prospective test. A pass only qualifies for future shadow validation, never PAPER/LIVE authorization. Main and PRIME remain untouched.", ""]
    Path(out / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()