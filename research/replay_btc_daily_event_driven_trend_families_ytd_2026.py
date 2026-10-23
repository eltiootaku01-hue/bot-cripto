"""Frozen screen of three event-driven BTC daily trend strategies.

Research only: public Binance Spot OHLCV, virtual execution, no private API or orders.
Parameters and promotion gate frozen in GitHub issue #142.
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
FULL_START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2026, 10, 22, tzinfo=UTC)
BAR = timedelta(days=1)
WARMUP_BARS = 400
WARMUP_START = FULL_START - BAR * WARMUP_BARS
EXPECTED_BARS = WARMUP_BARS + (END - FULL_START).days
ZERO, ONE = Decimal("0"), Decimal("1")
BPS = Decimal("10000")
FRACTION = Decimal("0.95")
MAX_ENTRIES_30D = 5
CAPITALS = (Decimal("60"), Decimal("100"))
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
CANDIDATES = (
    "BTC_GOLDEN_CROSS_EVENT_50_200",
    "BTC_DONCHIAN_100_50_SMA200",
    "BTC_MOMENTUM_90D_5PCT_SMA200",
)
WINDOWS = (
    ("FULL_2023_2026_YTD", FULL_START, END),
    ("2023", datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
    ("2024", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    ("2025", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ("2026_YTD", datetime(2026, 1, 1, tzinfo=UTC), END),
)


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
        raise RuntimeError("FAIL-CLOSED: nonpositive BTCUSDT filters.")
    snapshot = {"LOT_SIZE": lot, "MIN_NOTIONAL_OR_NOTIONAL": notion}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {
        "instrument_id": instrument.instrument_id, "step": step, "min_qty": min_qty, "min_notional": min_notional,
        "snapshot": snapshot, "sha256": digest, "retrieved_at": datetime.now(UTC).isoformat(),
        "scope": "current public ExchangeInfo; historical filters not established",
    }


def _fetch(rules):
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    metadata.resolve_active(SYMBOL)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=SYMBOL, interval=INTERVAL, start_time=int(WARMUP_START.timestamp() * 1000),
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
        raise RuntimeError("FAIL-CLOSED: unexpected BTCUSDT identity.")
    records = [_market_data_record(x) for x in items]
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return items, records, digest, unknown_comp, unknown_final


def _sma(values, period):
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1:i + 1], ZERO) / Decimal(period)
    return result


def _rolling(values, period, is_max):
    result = [None] * len(values)
    for i in range(period, len(values)):
        prior = values[i - period:i]
        result[i] = max(prior) if is_max else min(prior)
    return result


def _signals(items):
    close = [x.payload.close for x in items]
    high = [x.payload.high for x in items]
    low = [x.payload.low for x in items]
    sma50, sma200 = _sma(close, 50), _sma(close, 200)
    high100, low50 = _rolling(high, 100, True), _rolling(low, 50, False)
    entries = {c: [False] * len(items) for c in CANDIDATES}
    exits = {c: [False] * len(items) for c in CANDIDATES}

    for i in range(1, len(items)):
        if sma50[i] is not None and sma200[i] is not None and sma50[i - 1] is not None and sma200[i - 1] is not None:
            name = CANDIDATES[0]
            entries[name][i] = sma50[i - 1] <= sma200[i - 1] and sma50[i] > sma200[i] and close[i] > sma200[i]
            exits[name][i] = (sma50[i - 1] >= sma200[i - 1] and sma50[i] < sma200[i]) or close[i] < sma200[i]
            name = CANDIDATES[1]
            if high100[i] is not None and low50[i] is not None:
                entries[name][i] = close[i] > high100[i] and close[i] > sma200[i]
                exits[name][i] = close[i] < low50[i] or close[i] < sma200[i]
            name = CANDIDATES[2]
            if i >= 90:
                momentum = close[i] / close[i - 90] - ONE
                prior_momentum = close[i - 1] / close[i - 91] - ONE
                entries[name][i] = momentum > Decimal("0.05") and close[i] > sma200[i] and prior_momentum <= Decimal("0.05")
                exits[name][i] = momentum <= ZERO or close[i] < sma200[i]
    return entries, exits


def _adverse(spread, slip):
    return (slip + spread / Decimal("2")) / BPS


def _qty(raw, rules):
    return (raw / rules["step"]).to_integral_value(rounding=ROUND_DOWN) * rules["step"]


def _buy_hold(items, rules, start, end, capital, fee, spread, slip):
    evaluation = [x for x in items if start <= x.payload.start < end]
    expected = (end - start).days
    if len(evaluation) != expected:
        raise RuntimeError(f"FAIL-CLOSED B&H {start.isoformat()}: rows={len(evaluation)}, expected={expected}.")
    adverse = _adverse(spread, slip)
    p_in = evaluation[0].payload.open * (ONE + adverse)
    qty = _qty(capital * FRACTION / (p_in * (ONE + fee)), rules)
    notional = qty * p_in
    if qty < rules["min_qty"] or notional < rules["min_notional"]:
        raise RuntimeError("FAIL-CLOSED: BTC B&H violates current lot/min-notional assumptions.")
    buy_fee = notional * fee
    cash = capital - notional - buy_fee
    p_out = evaluation[-1].payload.close * (ONE - adverse)
    out = qty * p_out
    sell_fee = out * fee
    return cash + out - sell_fee, buy_fee + sell_fee


def _simulate(candidate, items, rules, entries, exits, window, start, end, capital, cost, fee, spread, slip):
    history, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    evaluation = [x for x in history if start <= x.payload.start < end]
    expected = (end - start).days
    if len(evaluation) != expected:
        raise RuntimeError(f"FAIL-CLOSED {window}: rows={len(evaluation)}, expected={expected}.")
    idxmap = {x.payload.start: i for i, x in enumerate(history)}
    quote, qty, entry_cost = capital, ZERO, ZERO
    fees = ZERO
    fills, curve, buy_times = [], [], []
    peak, pending = capital, None
    for n, item in enumerate(evaluation):
        candle = item.payload
        i = idxmap[candle.start]
        if pending is not None:
            adverse = _adverse(spread, slip)
            if pending == "BUY" and qty == ZERO:
                price = candle.open * (ONE + adverse)
                quantity = _qty(quote * FRACTION / (price * (ONE + fee)), rules)
                notional = quantity * price
                cutoff = candle.start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= candle.start]
                reason = None
                if quantity < rules["min_qty"]:
                    reason = "MIN_QTY"
                elif notional < rules["min_notional"]:
                    reason = "MIN_NOTIONAL"
                elif notional * (ONE + fee) > quote:
                    reason = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_30D:
                    reason = "MAX_ENTRIES_30D"
                if reason:
                    fills.append({"candidate": candidate, "window": window, "timestamp_utc": candle.start.isoformat(),
                                  "side": "REJECTED_BUY", "quantity_btc": str(quantity), "price_usdt": str(price),
                                  "fee_usdt": "0", "realized_pnl_usdt": "", "reason": reason})
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    qty = quantity
                    fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({"candidate": candidate, "window": window, "timestamp_utc": candle.start.isoformat(),
                                  "side": "BUY", "quantity_btc": str(qty), "price_usdt": str(price),
                                  "fee_usdt": str(buy_fee), "realized_pnl_usdt": "", "reason": "completed daily signal; next open fill"})
            elif pending == "SELL" and qty > ZERO:
                price = candle.open * (ONE - adverse)
                notional = qty * price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                pnl = proceeds - entry_cost
                quote += proceeds
                fees += sell_fee
                fills.append({"candidate": candidate, "window": window, "timestamp_utc": candle.start.isoformat(),
                              "side": "SELL", "quantity_btc": str(qty), "price_usdt": str(price),
                              "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(pnl), "reason": "exit signal; next open fill"})
                qty, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + qty * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({"candidate": candidate, "window": window, "timestamp_utc": candle.end.isoformat(),
                      "equity_usdt": equity, "position_btc": qty, "drawdown": equity / peak - ONE if peak else ZERO})
        if n >= len(evaluation) - 1:
            continue
        if qty == ZERO and entries[candidate][i]:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D:
                pending = "BUY"
        elif qty > ZERO and exits[candidate][i]:
            pending = "SELL"

    if qty > ZERO:
        last = evaluation[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = qty * price
        fee_exit = notional * fee
        proceeds = notional - fee_exit
        pnl = proceeds - entry_cost
        quote += proceeds
        fees += fee_exit
        fills.append({"candidate": candidate, "window": window, "timestamp_utc": last.end.isoformat(), "side": "SELL",
                      "quantity_btc": str(qty), "price_usdt": str(price), "fee_usdt": str(fee_exit),
                      "realized_pnl_usdt": str(pnl), "reason": "TERMINAL_LIQUIDATION at window close"})
        qty = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {"candidate": candidate, "window": window, "timestamp_utc": curve[-1]["timestamp_utc"],
                     "equity_usdt": quote, "position_btc": ZERO, "drawdown": quote / peak - ONE if peak else ZERO}

    final = quote if qty == ZERO else quote + qty * evaluation[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [f for f in fills if f["candidate"] == candidate and f["window"] == window and f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    result = {
        "candidate": candidate, "window": window, "window_start": start.isoformat(), "window_end_exclusive": end.isoformat(),
        "capital_initial_usdt": str(capital), "cost_scenario": cost,
        "final_equity_usdt": str(final), "net_pnl_usdt": str(final - capital),
        "return_pct": str((final / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(fees),
        "time_in_market_pct": str(Decimal(sum(x["position_btc"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")),
        "buy_count": sum(f["candidate"] == candidate and f["window"] == window and f["side"] == "BUY" for f in fills),
        "rejected_buys": sum(f["candidate"] == candidate and f["window"] == window and f["side"] == "REJECTED_BUY" for f in fills),
        "excluded_history_records": excluded, "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
    }
    return result, fills, curve


def main():
    output = Path("event-driven-output")
    output.mkdir(exist_ok=True)
    print("Frozen event-driven BTC screen: golden-cross events, long channel breakout, 90d momentum.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Full window [{FULL_START.isoformat()}, {END.isoformat()}); warmup {WARMUP_BARS} bars from {WARMUP_START.isoformat()}.")
    print("Virtual Spot only; protocol issue #142.")
    rules = _rules()
    items, records, data_sha, unknown_comp, unknown_final = _fetch(rules)
    entry, exit_ = _signals(items)
    print(f"BTC bars={len(items)} SHA256={data_sha}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")
    evidence = {
        "symbol": SYMBOL, "interval": INTERVAL, "full_evaluation_start": FULL_START.isoformat(),
        "evaluation_end_exclusive": END.isoformat(), "warmup_start": WARMUP_START.isoformat(),
        "warmup_bars": WARMUP_BARS, "expected_bars": EXPECTED_BARS, "observed_bars": len(items),
        "data_gaps_interpolated": 0, "dataset_sha256": data_sha,
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]), "min_notional": str(rules["min_notional"]),
            "snapshot": rules["snapshot"], "sha256": rules["sha256"], "retrieved_at_utc": rules["retrieved_at"], "scope": rules["scope"],
        },
        "candidate_rules": {
            CANDIDATES[0]: "Enter only on SMA50 crossing from <= to > SMA200 while close>SMA200; exit on reverse crossover or close<SMA200.",
            CANDIDATES[1]: "Enter close above highest high of prior 100 completed daily bars and close>SMA200; exit below lowest low of prior 50 bars or close<SMA200.",
            CANDIDATES[2]: "Enter when 90d momentum first crosses above +5% and close>SMA200; exit when 90d momentum<=0 or close<SMA200.",
        },
        "windows": [{"name": n, "start": s.isoformat(), "end_exclusive": e.isoformat()} for n, s, e in WINDOWS],
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(sp), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, sp, sl in SCENARIOS
        ],
        "data": records,
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for candidate in CANDIDATES:
        for window, start, end in WINDOWS:
            for capital in CAPITALS:
                for cost, fee, spread, slip in SCENARIOS:
                    result, fills, curve = _simulate(candidate, items, rules, entry, exit_, window, start, end, capital, cost, fee, spread, slip)
                    bh, bh_fees = _buy_hold(items, rules, start, end, capital, fee, spread, slip)
                    result.update({
                        "btc_buy_hold_final_equity_usdt": str(bh),
                        "btc_buy_hold_net_pnl_usdt": str(bh - capital),
                        "btc_buy_hold_fees_usdt": str(bh_fees),
                        "cash_final_equity_usdt": str(capital), "dataset_sha256": data_sha,
                        "filter_sha256": rules["sha256"],
                    })
                    results.append(result)
                    all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **f} for f in fills)
                    all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **c} for c in curve)
                    print(
                        f"RESULT | {candidate} | {window} | capital={capital} | {cost} | "
                        f"PnL={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                        f"DD={Decimal(result['max_drawdown_pct']):.3f}% | trips={result['completed_round_trips']} | "
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

    full_stress = [r for r in results if r["window"] == "FULL_2023_2026_YTD" and r["cost_scenario"] == "STRESS_2"]
    ytd_stress = [r for r in results if r["window"] == "2026_YTD" and r["cost_scenario"] == "STRESS_2"]
    yearly_stress = [r for r in results if r["window"] in ("2023", "2024", "2025", "2026_YTD") and r["cost_scenario"] == "STRESS_2"]
    gates = {}
    for candidate in CANDIDATES:
        full = [r for r in full_stress if r["candidate"] == candidate]
        ytd = [r for r in ytd_stress if r["candidate"] == candidate]
        years = [r for r in yearly_stress if r["candidate"] == candidate]
        gates[candidate] = (
            len(full) == 2 and len(ytd) == 2 and len(years) == 8
            and all(Decimal(r["net_pnl_usdt"]) > ZERO and r["completed_round_trips"] >= 5 for r in full)
            and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in years)
            and all(Decimal(r["net_pnl_usdt"]) > ZERO and Decimal(r["net_pnl_usdt"]) > Decimal(r["btc_buy_hold_net_pnl_usdt"]) for r in ytd)
        )

    report = [
        "# Event-driven BTC trend families — fixed annual windows", "",
        "**Disposition:** retrospective research only; NO_TRADE remains in force.", "",
        f"- Full interval [{FULL_START.isoformat()}, {END.isoformat()}); warm-up {WARMUP_BARS} bars; observed {len(items)} candles.",
        f"- Dataset SHA-256 {data_sha}; current filter snapshot SHA-256 {rules['sha256']}; completeness UNKNOWN {unknown_comp}; finality UNKNOWN {unknown_final}.",
        f"- Current public filters: step {rules['step']}, minQty {rules['min_qty']}, minNotional {rules['min_notional']}; historical identity not established.",
        "- Cost assumptions BASE 10 bps fee/10 bps spread/5 bps slippage, STRESS_1 10/25/10, STRESS_2 20/50/20. Spread/slippage are not historical bookTicker records.",
        "- Every annual slice resets equity and starts from cash; only completed-candle signals within that evaluation slice can open trades; next-open fills and terminal liquidation.", "",
        "| Candidate | Window | Capital | Cost | Net PnL | Return | DD | Cycles | BTC B&H PnL |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['candidate']} | {r['window']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | "
            f"{Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report.extend(["", "## Frozen gate", ""])
    for candidate, passed in gates.items():
        report.append(f"- **{candidate}: {'PASS' if passed else 'FAIL'}**")
    report.extend([
        "", "Gate: full-period STRESS_2 positive for both capitals and at least 5 completed round trips; every annual/YTD stress2 drawdown no worse than 15%; 2026 YTD stress2 positive and better than BTC B&H. PASS only qualifies for an untouched forward/holdout test.",
        "", "This remains retrospective and selection-biased research. Point-in-time data availability is UNKNOWN; costs are assumed. No strategy is operationally approved.", "",
    ])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("PROMOTION_GATES:", json.dumps(gates, sort_keys=True))
    print(f"Evidence written to {output}/.")


if __name__ == "__main__":
    main()
