"""Frozen daily BTC RSI(2) pullback hypothesis with long-term SMA200 regime.

Research only: public Binance Spot candles and public instrument filters; no trading actions.
Parameters were preregistered in issue #139.
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


def _rules():
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    instrument = metadata.resolve_active(SYMBOL)
    payload = metadata._request(SYMBOL)
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], dict):
        raise RuntimeError("FAIL-CLOSED: ExchangeInfo did not return exactly one BTCUSDT record.")
    record = symbols[0]
    if record.get("symbol") != SYMBOL or record.get("status") != "TRADING" or record.get("isSpotTradingAllowed") is not True:
        raise RuntimeError("FAIL-CLOSED: current public metadata does not confirm active BTCUSDT Spot.")
    filters = record.get("filters")
    if not isinstance(filters, list):
        raise RuntimeError("FAIL-CLOSED: BTCUSDT exchange filters missing.")
    lot = next((x for x in filters if isinstance(x, dict) and x.get("filterType") == "LOT_SIZE"), None)
    notional = next((x for x in filters if isinstance(x, dict) and x.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL")), None)
    if not isinstance(lot, dict) or not isinstance(notional, dict):
        raise RuntimeError("FAIL-CLOSED: BTCUSDT LOT_SIZE/minimum notional filter missing.")
    try:
        step = Decimal(lot["stepSize"])
        min_qty = Decimal(lot["minQty"])
        min_notional = Decimal(notional["minNotional"])
    except (KeyError, TypeError, ArithmeticError, ValueError) as exc:
        raise RuntimeError("FAIL-CLOSED: malformed BTCUSDT lot/minimum-notional filter.") from exc
    if min(step, min_qty, min_notional) <= ZERO:
        raise RuntimeError("FAIL-CLOSED: non-positive BTCUSDT exchange filters.")
    selected = {"LOT_SIZE": lot, "MIN_NOTIONAL_OR_NOTIONAL": notional}
    raw = json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "instrument_id": instrument.instrument_id, "step": step, "min_qty": min_qty, "min_notional": min_notional,
        "filters": selected, "filter_sha256": hashlib.sha256(raw).hexdigest(),
        "retrieval_time_utc": datetime.now(UTC).isoformat(),
        "scope": "current public ExchangeInfo; historical filter values not established",
    }


def _fetch(rules):
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    metadata.resolve_active(SYMBOL)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=SYMBOL, interval=INTERVAL,
        start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000),
        page_limit=1000, max_requests=5,
    )
    items = sorted((x for x in acquired if WARMUP_START <= x.payload.start < END), key=lambda x: x.payload.start)
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED: expected {EXPECTED_BARS} contiguous daily bars, got {len(items)}; "
            f"missing={len(set(expected)-set(observed))}; unexpected={len(set(observed)-set(expected))}. "
            "No bars interpolated."
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED: accepted={len(accepted)}, excluded={excluded}; expected {EXPECTED_BARS}.")
    if any(x.instrument.instrument_id != rules["instrument_id"] for x in items):
        raise RuntimeError("FAIL-CLOSED: unexpected BTCUSDT instrument identity.")
    records = [_market_data_record(x) for x in items]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return items, records, hashlib.sha256(canonical).hexdigest(), unknown_completeness, unknown_finality


def _sma(values: list[Decimal], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1:i + 1], ZERO) / Decimal(period)
    return result


def _rsi2(values: list[Decimal]) -> list[Decimal | None]:
    # Exact, frozen definition: simple average of the last two close-to-close gains/losses.
    result: list[Decimal | None] = [None] * len(values)
    for i in range(2, len(values)):
        diffs = (values[i - 1] - values[i - 2], values[i] - values[i - 1])
        avg_gain = sum((max(d, ZERO) for d in diffs), ZERO) / Decimal("2")
        avg_loss = sum((max(-d, ZERO) for d in diffs), ZERO) / Decimal("2")
        if avg_loss == ZERO:
            result[i] = Decimal("100") if avg_gain > ZERO else Decimal("50")
        else:
            rs = avg_gain / avg_loss
            result[i] = Decimal("100") - Decimal("100") / (ONE + rs)
    return result


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
        raise RuntimeError(f"FAIL-CLOSED: BTC B&H violates current lot filters: qty={qty}, notional={notional}.")
    buy_fee = notional * fee
    quote = capital - notional - buy_fee
    price_out = evaluation[-1].payload.close * (ONE - adverse)
    sell_notional = qty * price_out
    sell_fee = sell_notional * fee
    return quote + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(items, rules, signals_entry, signals_exit, capital, cost_name, fee, spread, slip):
    history, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    evaluation = [x for x in history if START <= x.payload.start < END]
    if len(evaluation) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: eval rows={len(evaluation)}, expected={EVAL_BARS}.")
    index_by_start = {x.payload.start: i for i, x in enumerate(history)}
    quote, position, entry_cost = capital, ZERO, ZERO
    total_fees = ZERO
    fills, curve, buy_times = [], [], []
    peak, pending = capital, None

    for n, item in enumerate(evaluation):
        candle = item.payload
        i = index_by_start[candle.start]
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
                        "timestamp_utc": candle.start.isoformat(), "side": "REJECTED_BUY",
                        "quantity_btc": str(qty), "price_usdt": str(price), "fee_usdt": "0",
                        "realized_pnl_usdt": "", "reason": reason,
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
                        "quantity_btc": str(qty), "price_usdt": str(price), "fee_usdt": str(buy_fee),
                        "realized_pnl_usdt": "", "reason": "completed daily close: close > SMA200 and RSI(2) < 10; next open fill",
                    })
            elif pending == "SELL" and position > ZERO:
                price = candle.open * (ONE - adverse)
                notional = position * price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                realized = proceeds - entry_cost
                quote += proceeds
                total_fees += sell_fee
                fills.append({
                    "timestamp_utc": candle.start.isoformat(), "side": "SELL",
                    "quantity_btc": str(position), "price_usdt": str(price), "fee_usdt": str(sell_fee),
                    "realized_pnl_usdt": str(realized), "reason": "completed daily close: close > SMA5; next open fill",
                })
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + position * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "timestamp_utc": candle.end.isoformat(), "equity_usdt": equity, "position_btc": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })
        if n == len(evaluation) - 1:
            continue
        if position == ZERO and signals_entry[i]:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D:
                pending = "BUY"
        elif position > ZERO and signals_exit[i]:
            pending = "SELL"

    if position > ZERO:
        last = evaluation[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = position * price
        exit_fee = notional * fee
        proceeds = notional - exit_fee
        realized = proceeds - entry_cost
        quote += proceeds
        total_fees += exit_fee
        fills.append({
            "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_btc": str(position), "price_usdt": str(price), "fee_usdt": str(exit_fee),
            "realized_pnl_usdt": str(realized), "reason": "TERMINAL_LIQUIDATION at final evaluation close",
        })
        position = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {
            "timestamp_utc": curve[-1]["timestamp_utc"], "equity_usdt": quote, "position_btc": ZERO,
            "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final = quote if position == ZERO else quote + position * evaluation[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [f for f in fills if f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    result = {
        "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final), "net_pnl_usdt": str(final - capital),
        "return_pct": str((final / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(total_fees),
        "time_in_market_pct": str(Decimal(sum(x["position_btc"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")),
        "buy_count": sum(f["side"] == "BUY" for f in fills), "sell_count": len(sells),
        "rejected_buys": sum(f["side"] == "REJECTED_BUY" for f in fills),
        "excluded_history_records": excluded, "market_data_completeness_unknown_count": unknown_completeness,
        "candle_finality_unknown_count": unknown_finality,
    }
    return result, fills, curve


def main():
    output = Path("btc-daily-output")
    output.mkdir(exist_ok=True)
    print("Frozen BTC daily RSI(2) pullback with SMA200 regime; no parameter tuning.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()}); warm-up={WARMUP_BARS} daily bars.")
    print("Virtual Spot only; no exchange orders. Protocol: GitHub issue #139.")
    rules = _rules()
    metadata = {
        "symbol": SYMBOL, "instrument_id": rules["instrument_id"], "step_size": str(rules["step"]),
        "min_qty": str(rules["min_qty"]), "min_notional": str(rules["min_notional"]),
        "filters": rules["filters"], "filter_sha256": rules["filter_sha256"],
        "retrieval_time_utc": rules["retrieval_time_utc"], "scope": rules["scope"],
    }
    items, records, data_sha, unknown_comp, unknown_final = _fetch(rules)
    print(
        f"BTC bars={len(items)} SHA256={data_sha}; step={rules['step']}; minQty={rules['min_qty']}; "
        f"minNotional={rules['min_notional']}; filterSHA={rules['filter_sha256']}; "
        f"completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}"
    )
    closes = [x.payload.close for x in items]
    sma200 = _sma(closes, 200)
    sma5 = _sma(closes, 5)
    rsi2 = _rsi2(closes)
    entries, exits = [False] * len(items), [False] * len(items)
    for i in range(len(items)):
        if sma200[i] is None or sma5[i] is None or rsi2[i] is None:
            continue
        entries[i] = closes[i] > sma200[i] and rsi2[i] < Decimal("10")
        exits[i] = closes[i] > sma5[i]
    eval_items = [x for x in items if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: eval bars={len(eval_items)}, expected={EVAL_BARS}.")
    evidence = {
        "symbol": SYMBOL, "interval": INTERVAL, "evaluation_start": START.isoformat(),
        "evaluation_end_exclusive": END.isoformat(), "warmup_start": WARMUP_START.isoformat(),
        "warmup_bars": WARMUP_BARS, "evaluation_bars": EVAL_BARS,
        "expected_total_bars": EXPECTED_BARS, "data_gaps_interpolated": 0,
        "dataset_sha256": data_sha, "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final, "market_data": records,
        "current_exchange_filters": metadata,
        "strategy": {
            "entry": "completed daily close > SMA200 and simple RSI(2) < 10",
            "exit": "completed daily close > SMA5",
            "fill": "next daily open; terminal liquidation at final evaluation close",
            "rsi_definition": "simple average gains/losses of last two close-to-close changes; RSI=100 if gains>0 and losses=0; RSI=50 when both are zero",
        },
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(s), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, s, sl in SCENARIOS
        ],
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    results, all_fills, all_curves = [], [], []
    for capital in CAPITALS:
        for cost_name, fee, spread, slip in SCENARIOS:
            result, fills, curve = _simulate(items, rules, entries, exits, capital, cost_name, fee, spread, slip)
            bh_final, bh_fees = _buy_hold(items, capital, fee, spread, slip, rules)
            result.update({
                "btc_buy_hold_final_equity_usdt": str(bh_final),
                "btc_buy_hold_net_pnl_usdt": str(bh_final - capital),
                "btc_buy_hold_fees_usdt": str(bh_fees),
                "cash_final_equity_usdt": str(capital), "dataset_sha256": data_sha,
                "filter_sha256": rules["filter_sha256"], "evaluation_bars": EVAL_BARS,
            })
            results.append(result)
            all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **f} for f in fills)
            all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **c} for c in curve)
            print(
                f"RESULT | capital={capital} | {cost_name} | PnL={Decimal(result['net_pnl_usdt']):+.6f} | "
                f"return={Decimal(result['return_pct']):+.3f}% | DD={Decimal(result['max_drawdown_pct']):.3f}% | "
                f"trips={result['completed_round_trips']} W/L={result['wins']}/{result['losses']} | "
                f"fees={Decimal(result['total_fees_usdt']):.4f} | BTC_BH={Decimal(result['btc_buy_hold_net_pnl_usdt']):+.4f}"
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
    gate = len(stress) == 2 and all(
        Decimal(r["net_pnl_usdt"]) > ZERO
        and r["completed_round_trips"] >= 5
        and Decimal(r["max_drawdown_pct"]) >= Decimal("-15")
        and Decimal(r["net_pnl_usdt"]) > Decimal(r["btc_buy_hold_net_pnl_usdt"])
        for r in stress
    )
    report = [
        "# BTC daily RSI(2) pullback — fixed YTD 2026", "",
        "**Disposition:** retrospective research only; NO_TRADE remains in force.", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()})",
        f"- Interval: {INTERVAL}; warm-up: {WARMUP_BARS}; evaluation bars: {EVAL_BARS}",
        f"- Dataset SHA-256: {data_sha}; filter snapshot SHA-256: {rules['filter_sha256']}",
        f"- Current public BTCUSDT filters: step {rules['step']}, minQty {rules['min_qty']}, minNotional {rules['min_notional']}; not asserted historical.",
        "- Frozen entry: daily close > SMA200 and RSI(2) < 10; exit: daily close > SMA5; signals from completed daily candles, next daily open fills; terminal liquidation.",
        "- Costs: BASE 10 bps fee + assumed 10 bps full spread + 5 bps slippage per fill; STRESS_1 10 + 25 + 10; STRESS_2 20 + 50 + 20.", "",
        "| Capital | Cost | Net PnL | Return | Max DD | Cycles | W/L | Fees | BTC B&H PnL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | "
            f"{r['completed_round_trips']} | {r['wins']}/{r['losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
            f"{Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report.extend([
        "", f"**Pre-registered historical gate: {'PASS' if gate else 'FAIL'}.** Requirements: profitable at both capitals in STRESS_2, 5+ closed cycles, drawdown no worse than 15%, and outperform BTC B&H net of matched costs.",
        "", "This is retrospective; the window has been repeatedly inspected in research and is not an independent holdout. A pass only authorizes a separate holdout study, not operation. Point-in-time data availability remains unknown. Fees are modelled and spread/slippage are assumptions.",
        "Protocol: GitHub issue #139; main and TOP1/PRIME unchanged.", "",
    ])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\nPROMOTION_GATE={'PASS' if gate else 'FAIL'}; artifacts written to {output}/.")


if __name__ == "__main__":
    main()
