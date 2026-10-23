"""Fixed-parameter annual robustness replay for BTC daily SMA50/SMA200.

Retrospective research only. No private endpoints, keys, PAPER/LIVE orders, or parameter tuning.
Protocol is preregistered in GitHub issue #141.
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


def _signals(items):
    closes = [x.payload.close for x in items]
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    entries, exits = [False] * len(items), [False] * len(items)
    for i in range(len(items)):
        if sma50[i] is None or sma200[i] is None:
            continue
        entries[i] = closes[i] > sma200[i] and sma50[i] > sma200[i]
        exits[i] = closes[i] < sma50[i] or closes[i] < sma200[i]
    return entries, exits


def _adverse(spread_bps, slippage_bps):
    return (slippage_bps + spread_bps / Decimal("2")) / BPS


def _qty(raw, rules):
    return (raw / rules["step"]).to_integral_value(rounding=ROUND_DOWN) * rules["step"]


def _simulate(items, rules, entries, exits, window_name, window_start, window_end, capital, cost_name, fee, spread, slip):
    history, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    evaluation = [x for x in history if window_start <= x.payload.start < window_end]
    expected = (window_end - window_start).days
    if len(evaluation) != expected:
        raise RuntimeError(f"FAIL-CLOSED {window_name}: eval rows={len(evaluation)}, expected={expected}.")
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
                        "window": window_name, "timestamp_utc": candle.start.isoformat(), "side": "REJECTED_BUY",
                        "quantity_btc": str(qty), "price_usdt": str(price), "fee_usdt": "0",
                        "realized_pnl_usdt": "", "reason": reason,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    position = qty
                    fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({
                        "window": window_name, "timestamp_utc": candle.start.isoformat(), "side": "BUY",
                        "quantity_btc": str(qty), "price_usdt": str(price), "fee_usdt": str(buy_fee),
                        "realized_pnl_usdt": "", "reason": "completed daily close: close>SMA200 and SMA50>SMA200; next open fill",
                    })
            elif pending == "SELL" and position > ZERO:
                price = candle.open * (ONE - adverse)
                notional = position * price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                realized = proceeds - entry_cost
                quote += proceeds
                fees += sell_fee
                fills.append({
                    "window": window_name, "timestamp_utc": candle.start.isoformat(), "side": "SELL",
                    "quantity_btc": str(position), "price_usdt": str(price), "fee_usdt": str(sell_fee),
                    "realized_pnl_usdt": str(realized), "reason": "completed daily close below SMA50 or SMA200; next open fill",
                })
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + position * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "window": window_name, "timestamp_utc": candle.end.isoformat(),
            "equity_usdt": equity, "position_btc": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })
        if n >= len(evaluation) - 1:
            continue
        if position == ZERO and entries[i]:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D:
                pending = "BUY"
        elif position > ZERO and exits[i]:
            pending = "SELL"

    if position > ZERO:
        last = evaluation[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = position * price
        exit_fee = notional * fee
        proceeds = notional - exit_fee
        realized = proceeds - entry_cost
        quote += proceeds
        fees += exit_fee
        fills.append({
            "window": window_name, "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_btc": str(position), "price_usdt": str(price), "fee_usdt": str(exit_fee),
            "realized_pnl_usdt": str(realized), "reason": "TERMINAL_LIQUIDATION at window final close",
        })
        position = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {
            "window": window_name, "timestamp_utc": curve[-1]["timestamp_utc"],
            "equity_usdt": quote, "position_btc": ZERO, "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final = quote if position == ZERO else quote + position * evaluation[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [f for f in fills if f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    result = {
        "window": window_name, "window_start": window_start.isoformat(), "window_end_exclusive": window_end.isoformat(),
        "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
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


def _buy_hold(items, rules, window_start, window_end, capital, fee, spread, slip):
    evaluation = [x for x in items if window_start <= x.payload.start < window_end]
    if len(evaluation) != (window_end - window_start).days:
        raise RuntimeError("FAIL-CLOSED: B&H window candle count mismatch.")
    adverse = _adverse(spread, slip)
    entry_price = evaluation[0].payload.open * (ONE + adverse)
    qty = _qty(capital * FRACTION / (entry_price * (ONE + fee)), rules)
    notional = qty * entry_price
    if qty < rules["min_qty"] or notional < rules["min_notional"]:
        raise RuntimeError(f"FAIL-CLOSED: B&H violates current filter assumptions: qty={qty}; notional={notional}.")
    buy_fee = notional * fee
    cash = capital - notional - buy_fee
    exit_price = evaluation[-1].payload.close * (ONE - adverse)
    out = qty * exit_price
    sell_fee = out * fee
    return cash + out - sell_fee, buy_fee + sell_fee


def main():
    output = Path("annual-robustness-output")
    output.mkdir(exist_ok=True)
    print("Fixed BTC daily SMA50/SMA200; yearly slices reset capital; no parameter changes.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Full evaluation UTC: [{FULL_START.isoformat()}, {END.isoformat()}); warmup={WARMUP_BARS} bars starting {WARMUP_START.isoformat()}")
    print("Virtual only; protocol issue #141.")
    rules = _rules()
    items, records, data_sha, unknown_comp, unknown_final = _fetch(rules)
    entries, exits = _signals(items)
    print(f"BTC bars={len(items)} SHA256={data_sha}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")
    evidence = {
        "symbol": SYMBOL, "interval": INTERVAL, "full_evaluation_start": FULL_START.isoformat(),
        "evaluation_end_exclusive": END.isoformat(), "warmup_start": WARMUP_START.isoformat(),
        "warmup_bars": WARMUP_BARS, "expected_bars": EXPECTED_BARS, "observed_bars": len(items),
        "data_gaps_interpolated": 0, "dataset_sha256": data_sha,
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot": rules["snapshot"],
            "sha256": rules["sha256"], "retrieved_at_utc": rules["retrieved_at"], "scope": rules["scope"],
        },
        "strategy": "Enter on completed daily close > SMA200 and SMA50 > SMA200. Exit on completed daily close < SMA50 or close < SMA200. Fill next daily open; terminal liquidation per window. Same rule across all windows.",
        "windows": [{"name": n, "start": s.isoformat(), "end_exclusive": e.isoformat()} for n, s, e in WINDOWS],
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(sp), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, sp, sl in SCENARIOS
        ],
        "data": records,
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for window_name, start, end in WINDOWS:
        for capital in CAPITALS:
            for cost_name, fee, spread, slip in SCENARIOS:
                result, fills, curve = _simulate(items, rules, entries, exits, window_name, start, end, capital, cost_name, fee, spread, slip)
                bh_final, bh_fees = _buy_hold(items, rules, start, end, capital, fee, spread, slip)
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
                    f"RESULT | {window_name} | capital={capital} | {cost_name} | PnL={Decimal(result['net_pnl_usdt']):+.6f} | "
                    f"return={Decimal(result['return_pct']):+.3f}% | DD={Decimal(result['max_drawdown_pct']):.3f}% | "
                    f"trips={result['completed_round_trips']} | BTC_BH={Decimal(result['btc_buy_hold_net_pnl_usdt']):+.4f}"
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
    gate = (
        len(full_stress) == 2 and len(ytd_stress) == 2 and len(yearly_stress) == 8
        and all(Decimal(r["net_pnl_usdt"]) > ZERO and r["completed_round_trips"] >= 5 for r in full_stress)
        and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in yearly_stress)
        and all(Decimal(r["net_pnl_usdt"]) > ZERO and Decimal(r["net_pnl_usdt"]) > Decimal(r["btc_buy_hold_net_pnl_usdt"]) for r in ytd_stress)
    )
    report = [
        "# BTC daily SMA50/SMA200 — annual fixed-window robustness 2023–2026", "",
        "**Disposition:** retrospective robustness check; not a blind holdout and not an operational approval.", "",
        f"- Requested full interval: [{FULL_START.isoformat()}, {END.isoformat()}); warm-up: {WARMUP_BARS} daily bars.",
        f"- Actual candles: {len(items)}; dataset SHA-256: {data_sha}.",
        f"- Current public filters: step {rules['step']}, minQty {rules['min_qty']}, minNotional {rules['min_notional']}; filter snapshot SHA-256 {rules['sha256']}. Historical equality not asserted.",
        "- Fixed rule: enter close > SMA200 and SMA50 > SMA200; exit close < SMA50 or close < SMA200; completed daily close signals, next-open fills; terminal liquidation at each window end.",
        "- Each annual window resets capital to 60 / 100 USDT, and uses historical candles preceding that window only for indicator warm-up, never for trades.",
        "- Cost assumptions: BASE fee 10 bps/fill, assumed full spread 10 bps and slippage 5 bps; STRESS_1 10/25/10; STRESS_2 20/50/20. Not reconstructed bookTicker fills.", "",
        "| Window | Capital | Cost | Net PnL | Return | Max DD | Cycles | BTC B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['window']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | "
            f"{Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report.extend([
        "", f"**Preregistered robustness gate: {'PASS' if gate else 'FAIL'}.** Full-period STRESS_2 PnL positive at both capital levels with 5+ cycles; all annual STRESS_2 drawdowns no worse than 15%; and 2026 YTD STRESS_2 positive and better than cash and BTC B&H.",
        "", "Selection bias warning: the rule was selected for follow-up after seeing its YTD 2026 results. The cross-year screen is therefore useful robustness evidence but not a blind holdout. A pass only qualifies for a truly untouched forward/holdout validation. Point-in-time data availability remains unknown; spread/slippage are assumptions.", "",
    ])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"ROBUSTNESS_GATE={'PASS' if gate else 'FAIL'}; evidence written to {output}/.")


if __name__ == "__main__":
    main()
