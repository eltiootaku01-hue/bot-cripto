"""Frozen BTC-regime ETH trend-family screen; virtual only."""
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
MAX_ENTRIES_PER_30D = 5
MIN_QTY = Decimal("0.0001")
QTY_STEP = Decimal("0.0001")
MIN_NOTIONAL = Decimal("5")
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
STRATEGIES = (
    "BTC_REGIME_DONCHIAN_55_20",
    "BTC_REGIME_SQUEEZE_BREAKOUT_20",
    "BTC_REGIME_EMA50_200",
    "ETH_KELTNER_DIP_BTC200",
)


def _fetch_symbol(symbol: str):
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    metadata.resolve_active(symbol)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=symbol, interval=INTERVAL, start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000), page_limit=1000, max_requests=5,
    )
    items = sorted((x for x in acquired if WARMUP_START <= x.payload.start < END), key=lambda x: x.payload.start)
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED {symbol}: expected {EXPECTED_BARS} contiguous 4h bars, got {len(items)}; "
            f"missing_first={sorted(set(expected)-set(observed))[:12]}; "
            f"unexpected_first={sorted(set(observed)-set(expected))[:12]}; no interpolation."
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED {symbol}: accepted={len(accepted)}, excluded={excluded}.")
    if any(x.instrument.instrument_id != f"binance:SPOT:{symbol}" for x in items):
        raise RuntimeError(f"FAIL-CLOSED {symbol}: instrument identity mismatch.")
    records = [_market_data_record(x) for x in items]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return items, records, hashlib.sha256(canonical).hexdigest(), unknown_completeness, unknown_finality


def _fetch_both():
    eth = _fetch_symbol("ETHUSDT")
    btc = _fetch_symbol("BTCUSDT")
    if [x.payload.start for x in eth[0]] != [x.payload.start for x in btc[0]]:
        raise RuntimeError("FAIL-CLOSED: ETH/BTC timestamps do not align.")
    combined = json.dumps({"BTCUSDT": btc[1], "ETHUSDT": eth[1]}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False).encode()
    return eth, btc, hashlib.sha256(combined).hexdigest()


def _ema(values: list[Decimal], period: int):
    alpha = Decimal("2") / Decimal(period + 1)
    result: list[Decimal | None] = [None] * len(values)
    running = values[0]
    for i, value in enumerate(values):
        if i:
            running = alpha * value + (ONE - alpha) * running
        if i >= period - 1:
            result[i] = running
    return result


def _bollinger_previous(values: list[Decimal], period: int = 20):
    upper: list[Decimal | None] = [None] * len(values)
    width: list[Decimal | None] = [None] * len(values)
    for i in range(period, len(values)):
        window = values[i-period:i]
        mean = sum(window, ZERO) / Decimal(period)
        var = sum(((x - mean) * (x - mean) for x in window), ZERO) / Decimal(period)
        sd = var.sqrt()
        upper[i] = mean + Decimal("2") * sd
        width[i] = Decimal("4") * sd / mean if mean > ZERO else None
    return upper, width


def _atr(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14):
    true_ranges: list[Decimal | None] = [None] * len(closes)
    for i in range(1, len(closes)):
        true_ranges[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    values: list[Decimal | None] = [None] * len(closes)
    for i in range(period, len(closes)):
        window = true_ranges[i-period+1:i+1]
        if all(x is not None for x in window):
            values[i] = sum(window, ZERO) / Decimal(period)
    return values


def _indicators(eth_items, btc_items):
    ec = [x.payload.close for x in eth_items]
    eh = [x.payload.high for x in eth_items]
    el = [x.payload.low for x in eth_items]
    bc = [x.payload.close for x in btc_items]
    bb_upper, bb_width = _bollinger_previous(ec, 20)
    return {
        "eth_closes": ec, "eth_highs": eh, "eth_lows": el, "btc_closes": bc,
        "eth_ema20": _ema(ec, 20), "eth_ema50": _ema(ec, 50), "eth_ema200": _ema(ec, 200),
        "btc_ema200": _ema(bc, 200), "bb_upper": bb_upper, "bb_width": bb_width,
        "eth_atr14": _atr(eh, el, ec, 14),
    }


def _btc_regime(i: int, ind) -> bool:
    ema = ind["btc_ema200"][i]
    return ema is not None and ind["btc_closes"][i] > ema


def _signals(strategy: str, i: int, ind):
    close = ind["eth_closes"][i]
    highs, lows = ind["eth_highs"], ind["eth_lows"]
    btc_ok = _btc_regime(i, ind)
    if strategy == "BTC_REGIME_DONCHIAN_55_20":
        if i < 55 or ind["btc_ema200"][i] is None:
            return False, False, "", ""
        enter = close > max(highs[i-55:i]) and btc_ok
        exit_ = close < min(lows[i-20:i]) or not btc_ok
        return enter, exit_, "close broke prior 55-bar high; BTC close above EMA200", "close below prior 20-bar low or BTC regime lost"
    if strategy == "BTC_REGIME_SQUEEZE_BREAKOUT_20":
        if i < 121 or ind["bb_upper"][i] is None or ind["bb_width"][i-1] is None:
            return False, False, "", ""
        history = [w for w in ind["bb_width"][i-101:i-1] if w is not None]
        if len(history) < 100:
            return False, False, "", ""
        cutoff = sorted(history)[int((len(history)-1) * 0.20)]
        squeeze = ind["bb_width"][i-1] <= cutoff
        enter = close > ind["bb_upper"][i] and squeeze and btc_ok
        exit_ = close < ind["eth_ema20"][i] or not btc_ok
        return enter, exit_, "close broke prior 20-bar Bollinger upper band after prior squeeze; BTC above EMA200", "close below EMA20 or BTC regime lost"
    if strategy == "BTC_REGIME_EMA50_200":
        ema50, ema200 = ind["eth_ema50"][i], ind["eth_ema200"][i]
        if ema50 is None or ema200 is None:
            return False, False, "", ""
        enter = ema50 > ema200 and close > ema50 and btc_ok
        exit_ = close < ema50 or not btc_ok
        return enter, exit_, "ETH close above EMA50 > EMA200; BTC above EMA200", "ETH close below EMA50 or BTC regime lost"
    if strategy == "ETH_KELTNER_DIP_BTC200":
        ema20, ema200 = ind["eth_ema20"][i], ind["eth_ema200"][i]
        atr = ind["eth_atr14"][i]
        btc_ema200 = ind["btc_ema200"][i]
        if ema20 is None or ema200 is None or atr is None or btc_ema200 is None:
            return False, False, "", ""
        enter = close < ema20 - Decimal("1.5") * atr and close > ema200 and btc_ok
        exit_ = close >= ema20 or close < ema200 or close < btc_ema200
        return enter, exit_, "ETH close below EMA20 - 1.5*ATR14 with ETH and BTC above EMA200", "ETH close recovered to EMA20 or ETH/BTC lost EMA200 regime"
    raise ValueError(f"Unknown frozen strategy: {strategy}")


def _adverse(spread: Decimal, slippage: Decimal):
    return (slippage + spread / Decimal("2")) / BPS


def _buy_and_hold(eval_items, capital, fee, spread, slippage):
    adverse = _adverse(spread, slippage)
    price = eval_items[0].payload.open * (ONE + adverse)
    qty = (capital * POSITION_FRACTION / (price * (ONE + fee)) / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP
    if qty < MIN_QTY or qty * price < MIN_NOTIONAL:
        raise RuntimeError("FAIL-CLOSED: ETH buy-and-hold violates lot assumptions.")
    buy_notional = qty * price
    quote = capital - buy_notional - buy_notional * fee
    sell_notional = qty * eval_items[-1].payload.close * (ONE - adverse)
    return quote + sell_notional - sell_notional * fee, buy_notional * fee + sell_notional * fee


def _simulate(eth_items, capital, strategy, cost_name, fee, spread, slippage, ind, hashes):
    by_start = {x.payload.start: i for i, x in enumerate(eth_items)}
    eval_items = [x for x in eth_items if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: got {len(eval_items)} evaluation bars; expected {EVAL_BARS}.")
    quote, position, entry_cost = capital, ZERO, ZERO
    total_fees, pending, peak = ZERO, None, capital
    fills, curve, buy_times = [], [], []
    for eval_idx, item in enumerate(eval_items):
        candle = item.payload
        if pending:
            adverse = _adverse(spread, slippage)
            if pending == "BUY" and position == ZERO:
                fill_price = candle.open * (ONE + adverse)
                qty = (quote * POSITION_FRACTION / (fill_price * (ONE + fee)) / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP
                notional = qty * fill_price
                cutoff = candle.start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= candle.start]
                reject = None
                if qty < MIN_QTY:
                    reject = "MIN_QTY"
                elif notional < MIN_NOTIONAL:
                    reject = "MIN_NOTIONAL"
                elif notional * (ONE + fee) > quote:
                    reject = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_PER_30D:
                    reject = "MAX_ENTRIES_30D"
                if reject:
                    fills.append({"timestamp_utc": candle.start.isoformat(), "side": "REJECTED_BUY",
                                  "quantity_eth": str(qty), "price_usdt": str(fill_price), "fee_usdt": "0",
                                  "realized_pnl_usdt": "", "reason": reject})
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    position = qty
                    total_fees += buy_fee
                    buy_times.append(candle.start)
                    prev_i = by_start[candle.start] - 1
                    reason = _signals(strategy, prev_i, ind)[2] if prev_i >= 0 else "entry signal"
                    fills.append({"timestamp_utc": candle.start.isoformat(), "side": "BUY",
                                  "quantity_eth": str(qty), "price_usdt": str(fill_price), "fee_usdt": str(buy_fee),
                                  "realized_pnl_usdt": "", "reason": reason})
            elif pending == "SELL" and position > ZERO:
                fill_price = candle.open * (ONE - adverse)
                notional = position * fill_price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                realized = proceeds - entry_cost
                quote += proceeds
                total_fees += sell_fee
                fills.append({"timestamp_utc": candle.start.isoformat(), "side": "SELL",
                              "quantity_eth": str(position), "price_usdt": str(fill_price), "fee_usdt": str(sell_fee),
                              "realized_pnl_usdt": str(realized), "reason": "next-open exit"})
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slippage)
        equity = quote + position * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({"strategy": strategy, "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
                      "timestamp_utc": candle.start.isoformat(), "equity_usdt": str(equity),
                      "base_position_eth": str(position),
                      "drawdown_pct": str((equity / peak - ONE) * HUNDRED if peak else ZERO)})
        if eval_idx == len(eval_items) - 1:
            continue
        i = by_start[candle.start]
        enter, exit_, _, _ = _signals(strategy, i, ind)
        if position == ZERO and enter:
            cutoff = candle.end - timedelta(days=30)
            if sum(cutoff < t <= candle.end for t in buy_times) < MAX_ENTRIES_PER_30D:
                pending = "BUY"
        elif position > ZERO and exit_:
            pending = "SELL"

    if position > ZERO:
        last = eval_items[-1].payload
        price = last.close * (ONE - _adverse(spread, slippage))
        notional = position * price
        fee_exit = notional * fee
        proceeds = notional - fee_exit
        realized = proceeds - entry_cost
        quote += proceeds
        total_fees += fee_exit
        fills.append({"timestamp_utc": last.end.isoformat(), "side": "SELL", "quantity_eth": str(position),
                      "price_usdt": str(price), "fee_usdt": str(fee_exit), "realized_pnl_usdt": str(realized),
                      "reason": "terminal liquidation at final close"})
        position = ZERO
        peak = max(peak, Decimal(curve[-1]["equity_usdt"]))
        curve[-1]["equity_usdt"] = str(quote)
        curve[-1]["base_position_eth"] = "0"
        curve[-1]["drawdown_pct"] = str((quote / peak - ONE) * HUNDRED if peak else ZERO)

    final_equity = quote + position * eval_items[-1].payload.close * (ONE - _adverse(spread, slippage)) * (ONE - fee)
    sells = [f for f in fills if f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    bh, bh_fees = _buy_and_hold(eval_items, capital, fee, spread, slippage)
    return {
        "strategy": strategy, "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final_equity), "net_pnl_usdt": str(final_equity - capital),
        "return_pct": str((final_equity / capital - ONE) * HUNDRED),
        "max_drawdown_pct": str(min((Decimal(x["drawdown_pct"]) for x in curve), default=ZERO)),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(total_fees), "buy_count": sum(f["side"] == "BUY" for f in fills),
        "sell_count": len(sells), "buy_hold_final_equity_usdt": str(bh),
        "buy_hold_net_pnl_usdt": str(bh - capital), "buy_hold_fees_usdt": str(bh_fees),
        "cash_final_equity_usdt": str(capital), "dataset_sha256": hashes["combined"],
        "eth_dataset_sha256": hashes["eth"], "btc_dataset_sha256": hashes["btc"],
        "evaluation_bars": EVAL_BARS,
    }, fills, curve


def main():
    out = Path("secondary-output")
    out.mkdir(exist_ok=True)
    print("Frozen BTC-regime ETH family screen; no tuning; virtual only.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()}); 4h bars={EVAL_BARS}")
    print(f"Warm-up: {WARMUP_BARS} bars from {WARMUP_START.isoformat()}.")
    eth, btc, combined_sha = _fetch_both()
    eth_items, eth_records, eth_sha, eth_unknown_comp, eth_unknown_final = eth
    btc_items, btc_records, btc_sha, btc_unknown_comp, btc_unknown_final = btc
    hashes = {"combined": combined_sha, "eth": eth_sha, "btc": btc_sha}
    manifest = {
        "evaluation_start": START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(), "warmup_bars": WARMUP_BARS,
        "interval": INTERVAL, "expected_bars_per_symbol": EXPECTED_BARS, "evaluation_bars": EVAL_BARS,
        "missing_or_interpolated_bars": 0, "dataset_sha256": hashes,
        "completeness_unknown": {"ETHUSDT": eth_unknown_comp, "BTCUSDT": btc_unknown_comp},
        "finality_unknown": {"ETHUSDT": eth_unknown_final, "BTCUSDT": btc_unknown_final},
        "instruments": {"ETHUSDT": eth_records, "BTCUSDT": btc_records},
    }
    Path(out / "dataset-evidence.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("ETH SHA-256:", eth_sha, "| BTC SHA-256:", btc_sha, "| combined SHA-256:", combined_sha)
    print(f"Metadata UNKNOWN: ETH completeness={eth_unknown_comp}/finality={eth_unknown_final}; BTC completeness={btc_unknown_comp}/finality={btc_unknown_final}")
    ind = _indicators(eth_items, btc_items)
    results, all_fills, curves = [], [], []
    for strategy in STRATEGIES:
        for capital in (Decimal("60"), Decimal("100")):
            for cost_name, fee, spread, slippage in SCENARIOS:
                result, fills, curve = _simulate(eth_items, capital, strategy, cost_name, fee, spread, slippage, ind, hashes)
                results.append(result)
                all_fills.extend({"strategy": strategy, "capital_initial_usdt": str(capital), "cost_scenario": cost_name, **f} for f in fills)
                curves.extend(curve)
                print(f"RESULT | {strategy} | capital={capital} | {cost_name} | PnL={Decimal(result['net_pnl_usdt']):+.6f} | "
                      f"return={Decimal(result['return_pct']):+.3f}% | maxDD={Decimal(result['max_drawdown_pct']):.3f}% | "
                      f"cycles={result['completed_round_trips']} W/L={result['wins']}/{result['losses']} | "
                      f"fees={Decimal(result['total_fees_usdt']):.4f} | ETH B&H={Decimal(result['buy_hold_net_pnl_usdt']):+.4f}")
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader(); w.writerows(results)
    fill_fields = list(all_fills[0]) if all_fills else [
        "strategy", "capital_initial_usdt", "cost_scenario", "timestamp_utc", "side",
        "quantity_eth", "price_usdt", "fee_usdt", "realized_pnl_usdt", "reason"]
    with open(out / "fills.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fill_fields)
        w.writeheader(); w.writerows(all_fills)
    with open(out / "equity.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(curves[0]))
        w.writeheader(); w.writerows(curves)

    lines = [
        "# BTC-regime ETH secondary candidate screen — frozen YTD 2026 replay", "",
        "**Status:** research-only. **Project decision remains NO_TRADE.**", "",
        f"- Window: [{START.isoformat()}, {END.isoformat()}); {EVAL_BARS} 4h evaluation bars.",
        f"- Warm-up: {WARMUP_BARS} bars; {EXPECTED_BARS} expected per symbol.",
        f"- ETH SHA-256: {eth_sha}; BTC SHA-256: {btc_sha}; combined SHA-256: {combined_sha}.",
        f"- Completeness UNKNOWN: ETH={eth_unknown_comp}, BTC={btc_unknown_comp}; finality UNKNOWN: ETH={eth_unknown_final}, BTC={btc_unknown_final}.",
        "- Signals use completed 4h candles; fills at next 4h open; final position liquidated at the final close.",
        "- 95% position sizing; max 5 entries in rolling 30d; one position at a time.",
        "- Assumed lot filters: min/step 0.0001 ETH, min notional 5 USDT; not historical rule evidence.",
        "- BASE: 0.10% fee, assumed full spread 10 bps, slippage 5 bps per fill.",
        "- STRESS_1: 0.10% fee, assumed full spread 25 bps, slippage 10 bps per fill.",
        "- STRESS_2: 0.20% fee, assumed full spread 50 bps, slippage 20 bps per fill.", "",
        "| Candidate | Capital | Cost | Net PnL | Return | Max DD | Cycles | W/L | Fees | ETH B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['strategy']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | "
                     f"{Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | "
                     f"{Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
                     f"{r['wins']}/{r['losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
                     f"{Decimal(r['buy_hold_net_pnl_usdt']):+.4f} |")
    lines += ["", "## Preregistered STRESS_2 gate", "",
              "Pass only if both capitals have positive net PnL, each has at least 5 round-trips, and both drawdowns are no worse than -15%. Passing would remain historical research, not operational authorization.", ""]
    for strategy in STRATEGIES:
        rows = [r for r in results if r["strategy"] == strategy and r["cost_scenario"] == "STRESS_2"]
        conditions = {
            "both capitals profitable": len(rows) == 2 and all(Decimal(r["net_pnl_usdt"]) > ZERO for r in rows),
            "at least 5 round-trips for both": len(rows) == 2 and all(r["completed_round_trips"] >= 5 for r in rows),
            "drawdown no worse than -15% for both": len(rows) == 2 and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in rows),
        }
        passed = all(conditions.values())
        lines.append(f"- {strategy}: {'PASS' if passed else 'FAIL'} — " + "; ".join(k + ": " + ("yes" if v else "no") for k, v in conditions.items()))
    lines += ["", "Spread/slippage and lot filters are assumptions, not observed historical bid/ask or reconstructed fills. The inspected window overlaps previous project research and is not a pristine unseen holdout. No post-hoc tuning, PRIME changes, main changes, or PAPER/LIVE promotion are authorized.", ""]
    Path(out / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
