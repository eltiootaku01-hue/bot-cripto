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
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
WARMUP_BARS = 300
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
ZERO, ONE, HUNDRED, BPS = Decimal("0"), Decimal("1"), Decimal("100"), Decimal("10000")
POSITION_FRACTION = Decimal("0.95")
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
        end_time=int(END.timestamp() * 1000), page_limit=1000, max_requests=5,
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
        if eval_idx < EVAL_BARS - 1 and eval_idx >= REBALANCE_BARS - 1 and (eval_idx - (REBALANCE_BARS - 1)) % REBALANCE_BARS == 0:
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
    out = Path("rotation-output")
    out.mkdir(exist_ok=True)
    print("Frozen weekly BTC/ETH relative-strength rotation; no parameter tuning; virtual only.")
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
    results, all_fills, all_curves = [], [], []
    for capital in (Decimal("60"), Decimal("100")):
        for name, fee, spread, slippage in SCENARIOS:
            result, fills, curve = _simulate(items, capital, name, fee, spread, slippage, closes, opens, emas, hashes)
            results.append(result)
            all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": name, **f} for f in fills)
            all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": name, **c} for c in curve)
            print(
                f"RESULT | capital={capital} | {name} | final={Decimal(result['final_equity_usdt']):.6f} | "
                f"net_pnl={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                f"maxDD={Decimal(result['max_drawdown_pct']):.3f}% | closures={result['completed_closures']} "
                f"W/L={result['realized_wins']}/{result['realized_losses']} | fees={Decimal(result['total_fees_usdt']):.4f} | "
                f"BTC_B&H={Decimal(result['btc_usdt_buy_hold_pnl']):+.4f} | ETH_B&H={Decimal(result['eth_usdt_buy_hold_pnl']):+.4f}"
            )
    for fname, data in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with open(out / fname, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]) if data else [])
            writer.writeheader()
            writer.writerows(data)
    lines = [
        "# BTC/ETH weekly relative-strength rotation — fixed YTD 2026 replay", "",
        "**Status:** research only. **Decision: NO_TRADE.**", "",
        f"- Window: [{START.isoformat()}, {END.isoformat()}); {EVAL_BARS} evaluation 4h bars and {WARMUP_BARS} warm-up bars.",
        f"- BTC SHA-256: {hashes['btc_dataset_sha256']}; ETH SHA-256: {hashes['eth_dataset_sha256']}; combined: {combined_sha}.",
        f"- Data metadata: {json.dumps(meta, sort_keys=True)}.",
        "- Rule: every 42 bars, choose the asset with the stronger trailing 180-bar return only if its 30-day momentum is positive and the asset close is above its own EMA200; otherwise cash.",
        "- Decision at candle close; apply target at next 4h open; terminal liquidation at final close.",
        "- 95% sizing; fixed assumed lot steps BTC 0.00001, ETH 0.0001, minimum notional 5 USDT; not historical filter evidence.",
        "- BASE: fee 10 bps, assumed full spread 10 bps, slippage 5 bps per fill.",
        "- STRESS_1: fee 10 bps, assumed full spread 25 bps, slippage 10 bps.",
        "- STRESS_2: fee 20 bps, assumed full spread 50 bps, slippage 20 bps.", "",
        "| Capital | Cost | Net PnL | Return | Max DD | Closures | W/L | Fees | BTC B&H PnL | ETH B&H PnL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | "
            f"{r['completed_closures']} | {r['realized_wins']}/{r['realized_losses']} | "
            f"{Decimal(r['total_fees_usdt']):.4f} | {Decimal(r['btc_usdt_buy_hold_pnl']):+.4f} | "
            f"{Decimal(r['eth_usdt_buy_hold_pnl']):+.4f} |"
        )
    lines += ["", "## Preregistered screening gate", "",
              "STRESS_2 must have positive net PnL for both capital levels, >=5 position closures, drawdown no worse than -15%, and beat cash. Passing means historical eligibility only, not operational promotion.", ""]
    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    passed = (len(stress) == 2
              and all(Decimal(r["net_pnl_usdt"]) > ZERO for r in stress)
              and all(r["completed_closures"] >= 5 for r in stress)
              and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in stress))
    lines.append("**Gate: " + ("PASS" if passed else "FAIL") + "**")
    lines += ["", "Spread/slippage and order quantity filters are assumptions, not reconstructed historical executions. The YTD sample has been inspected by this project and is not a clean unseen holdout. No post-hoc tuning and no PAPER/LIVE authorization.", ""]
    Path(out / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
