"""Preregistered screen of three BTC daily trend-following families.

Research only: public Binance Spot candles, virtual fills only, no private endpoints or orders.
Rule set / promotion gate frozen in GitHub issue #140 before this replay.
"""
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
SYMBOL = "BTCUSDT"
INTERVAL = "1d"
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
BAR = timedelta(days=1)
WARMUP_BARS = 400
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
FRACTION = Decimal("0.95")
MAX_ENTRIES_30D = 5
CAPITALS = (Decimal("60"), Decimal("100"))
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
CANDIDATES = ("BTC_DAILY_SMA50_SMA200", "BTC_DAILY_MACD12_26_9_SMA200", "BTC_DAILY_DONCHIAN20_10_SMA200")


def _rules():
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    instrument = metadata.resolve_active(SYMBOL)
    payload = metadata._request(SYMBOL)
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], dict):
        raise RuntimeError("FAIL-CLOSED: ExchangeInfo did not return exactly one BTCUSDT record.")
    record = symbols[0]
    if record.get("symbol") != SYMBOL or record.get("status") != "TRADING" or record.get("isSpotTradingAllowed") is not True:
        raise RuntimeError("FAIL-CLOSED: ExchangeInfo does not confirm active BTCUSDT Spot.")
    filters = record.get("filters")
    if not isinstance(filters, list):
        raise RuntimeError("FAIL-CLOSED: BTCUSDT filters missing.")
    lot = next((f for f in filters if isinstance(f, dict) and f.get("filterType") == "LOT_SIZE"), None)
    notion = next((f for f in filters if isinstance(f, dict) and f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL")), None)
    if not isinstance(lot, dict) or not isinstance(notion, dict):
        raise RuntimeError("FAIL-CLOSED: BTCUSDT LOT_SIZE/minimum-notional missing.")
    try:
        step, min_qty, min_notional = Decimal(lot["stepSize"]), Decimal(lot["minQty"]), Decimal(notion["minNotional"])
    except (KeyError, TypeError, ArithmeticError, ValueError) as exc:
        raise RuntimeError("FAIL-CLOSED: malformed BTCUSDT filters.") from exc
    if min(step, min_qty, min_notional) <= ZERO:
        raise RuntimeError("FAIL-CLOSED: nonpositive BTCUSDT filter.")
    snapshot = {"LOT_SIZE": lot, "MIN_NOTIONAL_OR_NOTIONAL": notion}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {
        "instrument_id": instrument.instrument_id, "step": step, "min_qty": min_qty,
        "min_notional": min_notional, "snapshot": snapshot, "sha256": digest,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "scope": "current public Binance ExchangeInfo; historical filters not established",
    }


def _fetch(rules):
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    metadata.resolve_active(SYMBOL)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=SYMBOL, interval=INTERVAL,
        start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000), page_limit=1000, max_requests=5,
    )
    items = sorted((x for x in acquired if WARMUP_START <= x.payload.start < END), key=lambda x: x.payload.start)
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED: expected {EXPECTED_BARS} contiguous daily bars, got {len(items)}; "
            f"missing={len(set(expected)-set(observed))}; unexpected={len(set(observed)-set(expected))}. No interpolation."
        )
    accepted, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED: accepted={len(accepted)}, excluded={excluded}, expected={EXPECTED_BARS}.")
    if any(x.instrument.instrument_id != rules["instrument_id"] for x in items):
        raise RuntimeError("FAIL-CLOSED: unexpected BTCUSDT instrument identity.")
    records = [_market_data_record(x) for x in items]
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return items, records, digest, unknown_comp, unknown_final


def _sma(values, period):
    output = [None] * len(values)
    for i in range(period - 1, len(values)):
        output[i] = sum(values[i - period + 1:i + 1], ZERO) / Decimal(period)
    return output


def _ema(values, period):
    alpha = Decimal("2") / Decimal(period + 1)
    running = values[0]
    output = []
    for i, value in enumerate(values):
        if i:
            running = alpha * value + (ONE - alpha) * running
        output.append(running)
    return output


def _rolling_extreme(values, period, is_max):
    output = [None] * len(values)
    for i in range(period, len(values)):
        window = values[i - period:i]  # strictly prior bars; excludes current bar
        output[i] = max(window) if is_max else min(window)
    return output


def _build_signals(items):
    closes = [x.payload.close for x in items]
    highs = [x.payload.high for x in items]
    lows = [x.payload.low for x in items]
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    sma5 = _sma(closes, 5)
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    macd = [ema12[i] - ema26[i] for i in range(len(closes))]
    signal = _ema(macd, 9)
    high20, low10 = _rolling_extreme(highs, 20, True), _rolling_extreme(lows, 10, False)
    entry = {name: [False] * len(items) for name in CANDIDATES}
    exit_ = {name: [False] * len(items) for name in CANDIDATES}
    for i in range(1, len(items)):
        if sma200[i] is None:
            continue
        # A: trend-following regime alignment; exit on loss of medium trend.
        name = CANDIDATES[0]
        entry[name][i] = closes[i] > sma200[i] and sma50[i] is not None and sma50[i] > sma200[i]
        exit_[name][i] = (sma50[i] is not None and closes[i] < sma50[i]) or closes[i] < sma200[i]

        # B: classic MACD bullish/bearish crossover, gated by the slow regime.
        name = CANDIDATES[1]
        bull_cross = macd[i - 1] <= signal[i - 1] and macd[i] > signal[i]
        bear_cross = macd[i - 1] >= signal[i - 1] and macd[i] < signal[i]
        entry[name][i] = bull_cross and closes[i] > sma200[i]
        exit_[name][i] = bear_cross or closes[i] < sma200[i]

        # C: daily channel breakout using only preceding bars.
        name = CANDIDATES[2]
        if high20[i] is not None and low10[i] is not None:
            entry[name][i] = closes[i] > high20[i] and closes[i] > sma200[i]
            exit_[name][i] = closes[i] < low10[i] or closes[i] < sma200[i]
    return entry, exit_


def _adverse(spread_bps, slippage_bps):
    return (slippage_bps + spread_bps / Decimal("2")) / BPS


def _qty(raw, rules):
    return (raw / rules["step"]).to_integral_value(rounding=ROUND_DOWN) * rules["step"]


def _buy_hold(items, capital, fee, spread, slip, rules):
    evaluation = [x for x in items if START <= x.payload.start < END]
    if len(evaluation) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: B&H rows={len(evaluation)}, expected={EVAL_BARS}.")
    adverse = _adverse(spread, slip)
    price_in = evaluation[0].payload.open * (ONE + adverse)
    qty = _qty(capital * FRACTION / (price_in * (ONE + fee)), rules)
    notional = qty * price_in
    if qty < rules["min_qty"] or notional < rules["min_notional"]:
        raise RuntimeError(f"FAIL-CLOSED: BTC B&H violates lot filters: qty={qty}, notional={notional}.")
    buy_fee = notional * fee
    quote = capital - notional - buy_fee
    price_out = evaluation[-1].payload.close * (ONE - adverse)
    sell_notional = qty * price_out
    sell_fee = sell_notional * fee
    return quote + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(candidate, items, rules, entry, exit_, capital, cost_name, fee, spread, slip):
    history, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    evaluation = [x for x in history if START <= x.payload.start < END]
    if len(evaluation) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: eval rows={len(evaluation)}, expected={EVAL_BARS}.")
    index = {x.payload.start: i for i, x in enumerate(history)}
    quote, position, entry_cost = capital, ZERO, ZERO
    fees = ZERO
    fills, curve, buy_times = [], [], []
    peak, pending = capital, None

    for n, item in enumerate(evaluation):
        candle = item.payload
        i = index[candle.start]
        if pending is not None:
            adverse = _adverse(spread, slip)
            if pending == "BUY" and position == ZERO:
                price = candle.open * (ONE + adverse)
                qty = _qty(quote * FRACTION / (price * (ONE + fee)), rules)
                notional = qty * price
                cutoff = candle.start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= candle.start]
                reason = None
                if qty < rules["min_qty"]:
                    reason = "MIN_QTY"
                elif notional < rules["min_notional"]:
                    reason = "MIN_NOTIONAL"
                elif notional * (ONE + fee) > quote:
                    reason = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_30D:
                    reason = "MAX_ENTRIES_30D"
                if reason:
                    fills.append({
                        "candidate": candidate, "timestamp_utc": candle.start.isoformat(),
                        "side": "REJECTED_BUY", "quantity_btc": str(qty), "price_usdt": str(price),
                        "fee_usdt": "0", "realized_pnl_usdt": "", "reason": reason,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    position = qty
                    fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({
                        "candidate": candidate, "timestamp_utc": candle.start.isoformat(),
                        "side": "BUY", "quantity_btc": str(qty), "price_usdt": str(price),
                        "fee_usdt": str(buy_fee), "realized_pnl_usdt": "",
                        "reason": f"{candidate} completed daily signal; next daily open fill",
                    })
            elif pending == "SELL" and position > ZERO:
                price = candle.open * (ONE - adverse)
                notional = position * price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                pnl = proceeds - entry_cost
                quote += proceeds
                fees += sell_fee
                fills.append({
                    "candidate": candidate, "timestamp_utc": candle.start.isoformat(),
                    "side": "SELL", "quantity_btc": str(position), "price_usdt": str(price),
                    "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(pnl), "reason": "exit signal; next daily open fill",
                })
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + position * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "candidate": candidate, "timestamp_utc": candle.end.isoformat(),
            "equity_usdt": equity, "position_btc": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })
        if n >= len(evaluation) - 1:
            continue
        if position == ZERO and entry[candidate][i]:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D:
                pending = "BUY"
        elif position > ZERO and exit_[candidate][i]:
            pending = "SELL"

    if position > ZERO:
        last = evaluation[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = position * price
        exit_fee = notional * fee
        proceeds = notional - exit_fee
        pnl = proceeds - entry_cost
        quote += proceeds
        fees += exit_fee
        fills.append({
            "candidate": candidate, "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_btc": str(position), "price_usdt": str(price), "fee_usdt": str(exit_fee),
            "realized_pnl_usdt": str(pnl), "reason": "TERMINAL_LIQUIDATION at last evaluation close",
        })
        position = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {
            "candidate": candidate, "timestamp_utc": curve[-1]["timestamp_utc"],
            "equity_usdt": quote, "position_btc": ZERO,
            "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final = quote if position == ZERO else quote + position * evaluation[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [f for f in fills if f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    result = {
        "candidate": candidate, "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final), "net_pnl_usdt": str(final - capital),
        "return_pct": str((final / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(fees),
        "time_in_market_pct": str(Decimal(sum(x["position_btc"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")),
        "buy_count": sum(f["side"] == "BUY" for f in fills), "sell_count": len(sells),
        "rejected_buys": sum(f["side"] == "REJECTED_BUY" for f in fills),
        "excluded_history_records": excluded, "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
    }
    return result, fills, curve


def main():
    output = Path("btc-trend-families-output")
    output.mkdir(exist_ok=True)
    print("Frozen BTC daily trend-family screen; no parameter tuning after result.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()}); warmup={WARMUP_BARS} daily bars")
    print("Virtual Spot only; no exchange orders. Protocol: GitHub issue #140.")
    rules = _rules()
    items, records, data_sha, unknown_comp, unknown_final = _fetch(rules)
    print(f"BTC bars={len(items)} SHA256={data_sha}; step={rules['step']}; minQty={rules['min_qty']}; minNotional={rules['min_notional']}; filterSHA={rules['sha256']}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")
    entry, exit_ = _build_signals(items)
    evidence = {
        "symbol": SYMBOL, "interval": INTERVAL, "evaluation_start": START.isoformat(),
        "evaluation_end_exclusive": END.isoformat(), "warmup_start": WARMUP_START.isoformat(),
        "warmup_bars": WARMUP_BARS, "evaluation_bars": EVAL_BARS, "expected_bars": EXPECTED_BARS,
        "data_gaps_interpolated": 0, "dataset_sha256": data_sha, "data": records,
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot": rules["snapshot"],
            "sha256": rules["sha256"], "retrieved_at_utc": rules["retrieved_at"], "scope": rules["scope"],
        },
        "candidates": {
            CANDIDATES[0]: "Enter completed daily close > SMA200 and SMA50 > SMA200; exit close < SMA50 or close < SMA200.",
            CANDIDATES[1]: "MACD(EMA12-EMA26) crosses above EMA9 signal with close > SMA200 to enter; bearish cross or close < SMA200 to exit.",
            CANDIDATES[2]: "Enter close above max high of preceding 20 completed daily bars and close > SMA200; exit close below min low of preceding 10 completed bars or close < SMA200.",
        },
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(s), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, s, sl in SCENARIOS
        ],
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for candidate in CANDIDATES:
        print(f"\nCANDIDATE {candidate}")
        for capital in CAPITALS:
            for cost_name, fee, spread, slip in SCENARIOS:
                result, fills, curve = _simulate(candidate, items, rules, entry, exit_, capital, cost_name, fee, spread, slip)
                bh_final, bh_fees = _buy_hold(items, capital, fee, spread, slip, rules)
                result.update({
                    "btc_buy_hold_final_equity_usdt": str(bh_final),
                    "btc_buy_hold_net_pnl_usdt": str(bh_final - capital),
                    "btc_buy_hold_fees_usdt": str(bh_fees),
                    "cash_final_equity_usdt": str(capital), "dataset_sha256": data_sha,
                    "filter_sha256": rules["sha256"],
                })
                results.append(result)
                all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **f} for f in fills)
                all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **c} for c in curve)
                print(
                    f"RESULT | {candidate} | capital={capital} | {cost_name} | "
                    f"PnL={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                    f"DD={Decimal(result['max_drawdown_pct']):.3f}% | trips={result['completed_round_trips']} "
                    f"W/L={result['wins']}/{result['losses']} | fees={Decimal(result['total_fees_usdt']):.4f} | "
                    f"BTC_BH={Decimal(result['btc_buy_hold_net_pnl_usdt']):+.4f}"
                )
    for filename, rows in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with (output / filename).open("w", newline="", encoding="utf-8") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            else:
                f.write("\n")
    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    gates = {}
    for candidate in CANDIDATES:
        subset = [r for r in stress if r["candidate"] == candidate]
        gates[candidate] = len(subset) == 2 and all(
            Decimal(r["net_pnl_usdt"]) > ZERO
            and r["completed_round_trips"] >= 5
            and Decimal(r["max_drawdown_pct"]) >= Decimal("-15")
            and Decimal(r["net_pnl_usdt"]) > Decimal(r["btc_buy_hold_net_pnl_usdt"])
            for r in subset
        )
    report = [
        "# BTC daily trend families — fixed YTD 2026", "",
        "**Disposition:** retrospective research only; NO_TRADE remains in force.", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()}); interval {INTERVAL}; warm-up {WARMUP_BARS}; evaluation bars {EVAL_BARS}.",
        f"- Dataset SHA-256: {data_sha}; current lot filter snapshot SHA-256: {rules['sha256']}.",
        f"- Current public LOT_SIZE step {rules['step']}; minQty {rules['min_qty']}; minNotional {rules['min_notional']}. Historical filter equivalence not asserted.",
        "- Signals use completed daily candles; fills at next daily open; terminal liquidation at final evaluation close; 95% of cash; max 5 buy entries per rolling 30 days.",
        "- BASE: fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/fill; STRESS_1: 10/25/10; STRESS_2: 20/50/20. Spread and slippage are assumptions, not historical bookTicker observations.", "",
        "| Candidate | Capital | Cost | Net PnL | Return | Max DD | Cycles | W/L | Fees | BTC B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['candidate']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | "
            f"{Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | {r['wins']}/{r['losses']} | "
            f"{Decimal(r['total_fees_usdt']):.4f} | {Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report += ["", "## Pre-registered gate", ""]
    for candidate, passed in gates.items():
        report.append(f"- **{candidate}: {'PASS' if passed else 'FAIL'}** under STRESS_2 at both capital levels.")
    report += [
        "", "The gate requires positive PnL at 60 and 100 USDT under STRESS_2, at least 5 round trips, max DD no worse than 15%, and outperformance of cash and BTC buy-and-hold. Any PASS qualifies only for independent holdout study, not deployment.",
        "This YTD window is already in-sample from repeated research. Point-in-time data availability is UNKNOWN; assumed spreads/slippage are not reconstructed historical executions.", "",
    ]
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\nPROMOTION GATES:", json.dumps(gates, sort_keys=True))
    print(f"Evidence written to {output}/.")


if __name__ == "__main__":
    main()
