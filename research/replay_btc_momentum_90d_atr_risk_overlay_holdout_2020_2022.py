"""Pre-registered BTC 90d momentum strategy with fixed ATR-based exposure sizing.

Research-only risk overlay to address excessive drawdown seen in issue #142.
No private API, keys, orders, leverage, or shorting.
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
FULL_START = datetime(2020, 1, 1, tzinfo=UTC)
END = datetime(2023, 1, 1, tzinfo=UTC)
BAR = timedelta(days=1)
WARMUP_BARS = 400
WARMUP_START = FULL_START - BAR * WARMUP_BARS
EXPECTED_BARS = WARMUP_BARS + (END - FULL_START).days
ZERO, ONE = Decimal("0"), Decimal("1")
BPS = Decimal("10000")
MAX_POSITION_FRACTION = Decimal("0.95")
TARGET_ANNUAL_ATR_PROXY = Decimal("0.20")
TRADING_DAYS_PER_YEAR = Decimal("365")
MAX_ENTRIES_30D = 5
CAPITALS = (Decimal("60"), Decimal("100"))
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
CANDIDATE = "BTC_MOMENTUM_90D_5PCT_SMA200_ATR14_TARGET20"
WINDOWS = (
    ("FULL_2020_2022", FULL_START, END),
    ("2020", datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
    ("2021", datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
    ("2022", datetime(2022, 1, 1, tzinfo=UTC), END),
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


def _momentum_signals_and_weights(items):
    closes = [x.payload.close for x in items]
    highs = [x.payload.high for x in items]
    lows = [x.payload.low for x in items]
    sma200 = _sma(closes, 200)
    true_ranges = []
    for i, item in enumerate(items):
        high, low = highs[i], lows[i]
        if i == 0:
            true_range = high - low
        else:
            previous_close = closes[i - 1]
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)

    atr14 = [None] * len(items)
    for i in range(13, len(items)):
        atr14[i] = sum(true_ranges[i - 13:i + 1], ZERO) / Decimal("14")

    entry = [False] * len(items)
    exit_ = [False] * len(items)
    weights = [None] * len(items)
    for i in range(200, len(items)):
        momentum = closes[i] / closes[i - 90] - ONE
        prior_momentum = closes[i - 1] / closes[i - 91] - ONE
        entry[i] = momentum > Decimal("0.05") and prior_momentum <= Decimal("0.05") and sma200[i] is not None and closes[i] > sma200[i]
        exit_[i] = momentum <= ZERO or (sma200[i] is not None and closes[i] < sma200[i])
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            atr_fraction = atr14[i] / closes[i]
            denominator = atr_fraction * TRADING_DAYS_PER_YEAR.sqrt()
            if denominator > ZERO:
                weight = min(MAX_POSITION_FRACTION, TARGET_ANNUAL_ATR_PROXY / denominator)
                weights[i] = weight if weight > ZERO else None
    return entry, exit_, weights, atr14, sma200


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
    price_in = evaluation[0].payload.open * (ONE + adverse)
    qty = _qty(capital * MAX_POSITION_FRACTION / (price_in * (ONE + fee)), rules)
    notional = qty * price_in
    if qty < rules["min_qty"] or notional < rules["min_notional"]:
        raise RuntimeError("FAIL-CLOSED: BTC B&H violates current lot/min-notional assumptions.")
    buy_fee = notional * fee
    quote = capital - notional - buy_fee
    price_out = evaluation[-1].payload.close * (ONE - adverse)
    sell_notional = qty * price_out
    sell_fee = sell_notional * fee
    return quote + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(items, rules, entries, exits, weights, atr14, sma200, window, start, end, capital, cost, fee, spread, slip):
    history, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    evaluation = [x for x in history if start <= x.payload.start < end]
    expected = (end - start).days
    if len(evaluation) != expected:
        raise RuntimeError(f"FAIL-CLOSED {window}: rows={len(evaluation)}, expected={expected}.")
    idxmap = {x.payload.start: i for i, x in enumerate(history)}
    quote, quantity, entry_cost = capital, ZERO, ZERO
    fees = ZERO
    fills, curve, buy_times = [], [], []
    peak, pending = capital, None

    for n, item in enumerate(evaluation):
        candle = item.payload
        i = idxmap[candle.start]
        if pending is not None:
            adverse = _adverse(spread, slip)
            if pending[0] == "BUY" and quantity == ZERO:
                position_weight = pending[1]
                if position_weight is None or not ZERO < position_weight <= MAX_POSITION_FRACTION:
                    raise RuntimeError("FAIL-CLOSED: pending position weight is absent or outside allowed range.")
                price = candle.open * (ONE + adverse)
                raw_qty = quote * position_weight / (price * (ONE + fee))
                qty = _qty(raw_qty, rules)
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
                        "candidate": CANDIDATE, "window": window, "timestamp_utc": candle.start.isoformat(),
                        "side": "REJECTED_BUY", "quantity_btc": str(qty), "price_usdt": str(price),
                        "fee_usdt": "0", "realized_pnl_usdt": "", "risk_weight": str(position_weight), "reason": reason,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    quantity = qty
                    fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({
                        "candidate": CANDIDATE, "window": window, "timestamp_utc": candle.start.isoformat(),
                        "side": "BUY", "quantity_btc": str(qty), "price_usdt": str(price),
                        "fee_usdt": str(buy_fee), "realized_pnl_usdt": "", "risk_weight": str(position_weight),
                        "atr14_at_signal": str(atr14[i - 1] if i > 0 else ""),
                        "reason": "90d momentum crosses >+5%, close>SMA200; ATR14 risk weight from completed signal bar; next open fill",
                    })
            elif pending[0] == "SELL" and quantity > ZERO:
                price = candle.open * (ONE - adverse)
                notional = quantity * price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                realized = proceeds - entry_cost
                quote += proceeds
                fees += sell_fee
                fills.append({
                    "candidate": CANDIDATE, "window": window, "timestamp_utc": candle.start.isoformat(),
                    "side": "SELL", "quantity_btc": str(quantity), "price_usdt": str(price),
                    "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(realized), "risk_weight": "",
                    "reason": "90d momentum <= 0 or close<SMA200; next open fill",
                })
                quantity, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + quantity * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "candidate": CANDIDATE, "window": window, "timestamp_utc": candle.end.isoformat(),
            "equity_usdt": equity, "position_btc": quantity, "drawdown": equity / peak - ONE if peak else ZERO,
        })
        if n >= len(evaluation) - 1:
            continue
        if quantity == ZERO and entries[i]:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D and weights[i] is not None:
                pending = ("BUY", weights[i])
        elif quantity > ZERO and exits[i]:
            pending = ("SELL", None)

    if quantity > ZERO:
        last = evaluation[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = quantity * price
        exit_fee = notional * fee
        proceeds = notional - exit_fee
        realized = proceeds - entry_cost
        quote += proceeds
        fees += exit_fee
        fills.append({
            "candidate": CANDIDATE, "window": window, "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_btc": str(quantity), "price_usdt": str(price), "fee_usdt": str(exit_fee),
            "realized_pnl_usdt": str(realized), "risk_weight": "", "reason": "TERMINAL_LIQUIDATION at window close",
        })
        quantity = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {
            "candidate": CANDIDATE, "window": window, "timestamp_utc": curve[-1]["timestamp_utc"],
            "equity_usdt": quote, "position_btc": ZERO, "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final = quote if quantity == ZERO else quote + quantity * evaluation[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [f for f in fills if f["candidate"] == CANDIDATE and f["window"] == window and f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    result = {
        "candidate": CANDIDATE, "window": window, "window_start": start.isoformat(), "window_end_exclusive": end.isoformat(),
        "capital_initial_usdt": str(capital), "cost_scenario": cost,
        "final_equity_usdt": str(final), "net_pnl_usdt": str(final - capital),
        "return_pct": str((final / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(fees),
        "time_in_market_pct": str(Decimal(sum(x["position_btc"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")),
        "buy_count": sum(f["candidate"] == CANDIDATE and f["window"] == window and f["side"] == "BUY" for f in fills),
        "rejected_buys": sum(f["candidate"] == CANDIDATE and f["window"] == window and f["side"] == "REJECTED_BUY" for f in fills),
        "excluded_history_records": excluded, "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
    }
    return result, fills, curve


def main():
    output = Path("atr-risk-overlay-holdout-output")
    output.mkdir(exist_ok=True)
    print("Frozen signal: BTC 90d momentum crosses >+5% with close>SMA200; ATR14 risk sizing.")
    print("Risk size = min(0.95, 0.20 / ((ATR14/close) * sqrt(365))); weight frozen at signal close.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Holdout interval [{FULL_START.isoformat()}, {END.isoformat()}); warmup={WARMUP_BARS} daily bars from {WARMUP_START.isoformat()}.")
    print("Virtual Spot only; protocol issue #144.")
    rules = _rules()
    items, records, data_sha, unknown_comp, unknown_final = _fetch(rules)
    entries, exits, weights, atr14, sma200 = _momentum_signals_and_weights(items)
    print(f"BTC bars={len(items)} SHA256={data_sha}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")
    sample_weights = [w for w in weights if w is not None]
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
        "strategy": {
            "signal": "Buy when 90d momentum first crosses above +5% and close>SMA200; exit when 90d momentum<=0 or close<SMA200.",
            "risk_sizing": "At completed signal bar, weight=min(0.95, 0.20/((ATR14/close)*sqrt(365))); simple 14-bar arithmetic ATR from true range; fixed for the position until exit, no daily rebalance.",
            "target_label": "20% annualized ATR proxy; not guaranteed realized volatility target",
            "min_observed_weight": str(min(sample_weights)) if sample_weights else None,
            "max_observed_weight": str(max(sample_weights)) if sample_weights else None,
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
    for window, start, end in WINDOWS:
        for capital in CAPITALS:
            for cost, fee, spread, slip in SCENARIOS:
                result, fills, curve = _simulate(items, rules, entries, exits, weights, atr14, sma200, window, start, end, capital, cost, fee, spread, slip)
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
                    f"RESULT | {window} | capital={capital} | {cost} | PnL={Decimal(result['net_pnl_usdt']):+.6f} | "
                    f"return={Decimal(result['return_pct']):+.3f}% | DD={Decimal(result['max_drawdown_pct']):.3f}% | "
                    f"trips={result['completed_round_trips']} | weight-pos range=[{evidence['strategy']['min_observed_weight']},{evidence['strategy']['max_observed_weight']}] | "
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
    full_stress = [r for r in results if r["window"] == "FULL_2020_2022" and r["cost_scenario"] == "STRESS_2"]
    yearly_stress = [r for r in results if r["window"] in ("2020", "2021", "2022") and r["cost_scenario"] == "STRESS_2"]
    profitable_years_by_capital = {
        str(capital): sum(
            Decimal(r["net_pnl_usdt"]) > ZERO
            for r in yearly_stress
            if Decimal(r["capital_initial_usdt"]) == capital
        )
        for capital in CAPITALS
    }
    gate = (
        len(full_stress) == 2 and len(yearly_stress) == 6
        and all(Decimal(r["net_pnl_usdt"]) > ZERO and r["completed_round_trips"] >= 5 and Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in full_stress)
        and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in yearly_stress)
        and all(count >= 2 for count in profitable_years_by_capital.values())
    )
    report = [
        "# BTC 90-day momentum + ATR14 risk sizing — historical holdout 2020–2022", "",
        "**Disposition:** historical holdout check only. The strategy configuration was selected using 2023–2026 data; 2020–2022 was frozen as a separate earlier-regime validation before downloading this window. This is not prospective validation.", "",
        f"- Full interval [{FULL_START.isoformat()}, {END.isoformat()}); warm-up {WARMUP_BARS}; observed {len(items)} daily candles; dataset SHA-256 {data_sha}.",
        f"- Current BTCUSDT filter snapshot SHA-256 {rules['sha256']}; LOT_SIZE step {rules['step']}; minQty {rules['min_qty']}; minimum notional {rules['min_notional']}. Historical filters not established.",
        "- Signal unchanged: enter when 90d momentum crosses above +5% and close>SMA200; exit when momentum<=0 or close<SMA200; next-open fills, terminal liquidation per window.",
        "- Risk layer: weight=min(95%, 20%/((ATR14/close)*sqrt(365))), simple 14-bar arithmetic average true range from completed candles; no intratrade rebalance; not a guarantee of 20% realized volatility.",
        "- Costs BASE 10 bps fee/10 bps full assumed spread/5 bps slippage per fill; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions, not historical bookTicker observations.",
        "- Yearly windows each reset capital to cash; 400 pre-window bars are indicator warm-up only.", "",
        "| Window | Capital | Cost | Net PnL | Return | Max DD | Cycles | BTC B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['window']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report.extend(["", f"**Frozen holdout gate: {'PASS' if gate else 'FAIL'}.** Full STRESS_2 positive at both capitals with 5+ cycles; full and each annual drawdown no worse than 15%; positive STRESS_2 PnL in at least 2 of 3 annual slices at both capital levels.", "",
                   "This 2020–2022 historical holdout window was not used by the earlier 2023–2026 screen, but the config was selected after reviewing later data, so it is a retrospective earlier-regime check, not a blind prospective trial. A pass still does not establish future performance or authorize deployment. Market-data point-in-time availability remains unknown; spread/slippage are assumptions.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"PROMOTION_GATE={'PASS' if gate else 'FAIL'}; evidence written to {output}/.")


if __name__ == "__main__":
    main()
