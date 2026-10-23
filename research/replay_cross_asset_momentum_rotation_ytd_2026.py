"""Frozen, long-only cross-sectional momentum rotation screen.

Research only: Binance public Spot OHLCV and current public lot filters.
No private endpoint, leverage, margin, PAPER or LIVE order placement.
Parameters are preregistered in GitHub issue #138.
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
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
INTERVAL = "4h"
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
BAR = timedelta(hours=4)
WARMUP_BARS = 600
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
MOMENTUM_LOOKBACK = 180
EMA_PERIOD = 200
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


def _metadata_and_filters(symbol: str):
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    instrument = metadata.resolve_active(symbol)
    # Use the existing bounded/retrying public metadata client to get exchange filters,
    # which its InstrumentIdentity abstraction intentionally does not model.
    payload = metadata._request(symbol)
    records = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise RuntimeError(f"FAIL-CLOSED: ExchangeInfo did not return exactly one {symbol} record.")
    record = records[0]
    if (
        record.get("symbol") != symbol
        or record.get("status") != "TRADING"
        or record.get("isSpotTradingAllowed") is not True
    ):
        raise RuntimeError(f"FAIL-CLOSED: public ExchangeInfo does not confirm active Spot trading for {symbol}.")
    filters = record.get("filters")
    if not isinstance(filters, list):
        raise RuntimeError(f"FAIL-CLOSED: ExchangeInfo filters missing for {symbol}.")
    lot = next((x for x in filters if isinstance(x, dict) and x.get("filterType") == "LOT_SIZE"), None)
    notional = next(
        (x for x in filters if isinstance(x, dict) and x.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL")),
        None,
    )
    if not isinstance(lot, dict) or not isinstance(notional, dict):
        raise RuntimeError(f"FAIL-CLOSED: LOT_SIZE or minimum-notional filter missing for {symbol}.")
    try:
        step = Decimal(lot["stepSize"])
        min_qty = Decimal(lot["minQty"])
        min_notional = Decimal(notional["minNotional"])
    except (KeyError, TypeError, ArithmeticError, ValueError) as exc:
        raise RuntimeError(f"FAIL-CLOSED: invalid lot/notional filter values for {symbol}.") from exc
    if step <= ZERO or min_qty <= ZERO or min_notional <= ZERO:
        raise RuntimeError(f"FAIL-CLOSED: non-positive lot/notional filter for {symbol}.")
    relevant_filters = {"LOT_SIZE": lot, "MIN_NOTIONAL_OR_NOTIONAL": notional}
    canonical = json.dumps(relevant_filters, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    rules = {
        "symbol": symbol, "instrument_id": instrument.instrument_id,
        "step_size": step, "min_qty": min_qty, "min_notional": min_notional,
        "filter_snapshot": relevant_filters, "filter_snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
        "retrieval_time_utc": datetime.now(UTC).isoformat(),
        "metadata_scope": "current public Binance ExchangeInfo; not asserted to be historical filters",
    }
    return rules


def _fetch_symbol(symbol: str, rules):
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    metadata.resolve_active(symbol)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=symbol,
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
            f"FAIL-CLOSED {symbol}: expected {EXPECTED_BARS} contiguous bars, got {len(items)}; "
            f"missing_first={sorted(set(expected) - set(observed))[:8]}; "
            f"unexpected_first={sorted(set(observed) - set(expected))[:8]}. No bars interpolated."
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(
            f"FAIL-CLOSED {symbol}: accepted={len(accepted)}, excluded={excluded}; expected {EXPECTED_BARS} bars."
        )
    if any(x.instrument.instrument_id != rules["instrument_id"] for x in items):
        raise RuntimeError(f"FAIL-CLOSED {symbol}: unexpected instrument identity.")
    records = [_market_data_record(x) for x in items]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return items, records, hashlib.sha256(canonical).hexdigest(), unknown_completeness, unknown_finality


def _ema(values: list[Decimal], period: int) -> list[Decimal | None]:
    alpha = Decimal("2") / Decimal(period + 1)
    running = values[0]
    output: list[Decimal | None] = []
    for i, value in enumerate(values):
        if i:
            running = alpha * value + (ONE - alpha) * running
        output.append(running if i >= period - 1 else None)
    return output


def _targets(data: dict[str, list], indices: list[int]):
    closes = {s: [x.payload.close for x in data[s]] for s in SYMBOLS}
    emas = {s: _ema(closes[s], EMA_PERIOD) for s in SYMBOLS}
    targets: list[str | None] = [None] * len(indices)
    # index order contains both warm-up and evaluation bars. Weekly rebalance decisions
    # occur after the completed bar ending on Monday 00:00 UTC; execution is next bar open.
    for i in indices:
        end_time = data[SYMBOLS[0]][i].payload.end
        if end_time.weekday() != 0 or end_time.hour != 0 or end_time.minute != 0:
            continue
        eligible = []
        for symbol in SYMBOLS:
            if i < MOMENTUM_LOOKBACK or emas[symbol][i] is None:
                continue
            momentum = closes[symbol][i] / closes[symbol][i - MOMENTUM_LOOKBACK] - ONE
            if momentum > ZERO and closes[symbol][i] > emas[symbol][i]:
                eligible.append((momentum, symbol))
        if eligible:
            # Highest momentum wins; symbol name breaks a mathematically exact tie deterministically.
            targets[i] = sorted(eligible, key=lambda pair: (-pair[0], pair[1]))[0][1]
        else:
            targets[i] = None
    return closes, emas, targets


def _adverse(spread_bps: Decimal, slippage_bps: Decimal) -> Decimal:
    return (slippage_bps + spread_bps / Decimal("2")) / BPS


def _rounded_qty(raw_qty: Decimal, rules) -> Decimal:
    step = rules["step_size"]
    return (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step


def _buy_hold(items, capital, fee, spread, slip, rules):
    eval_items = [x for x in items if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: B&H eval bars={len(eval_items)}, expected={EVAL_BARS}.")
    adverse = _adverse(spread, slip)
    buy_price = eval_items[0].payload.open * (ONE + adverse)
    qty = _rounded_qty(capital * FRACTION / (buy_price * (ONE + fee)), rules)
    notional = qty * buy_price
    if qty < rules["min_qty"] or notional < rules["min_notional"]:
        raise RuntimeError(
            f"FAIL-CLOSED: B&H {rules['symbol']} violates current public lot assumptions: qty={qty}, notional={notional}."
        )
    buy_fee = notional * fee
    quote = capital - notional - buy_fee
    sell_price = eval_items[-1].payload.close * (ONE - adverse)
    sell_notional = qty * sell_price
    sell_fee = sell_notional * fee
    return quote + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(data, rules, target_at_index, capital, cost_name, fee, spread, slip):
    indices = {x.payload.start: i for i, x in enumerate(data[SYMBOLS[0]])}
    eval_items = [x for x in data[SYMBOLS[0]] if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: eval bars={len(eval_items)}, expected={EVAL_BARS}.")
    quote = capital
    held_symbol = None
    position = ZERO
    entry_cost = ZERO
    total_fees = ZERO
    pending_flag = False
    pending_target = None
    buy_times = []
    fills, curve = [], []
    peak = capital

    for eval_idx, anchor in enumerate(eval_items):
        start = anchor.payload.start
        i = indices[start]
        candle_by_symbol = {s: data[s][i].payload for s in SYMBOLS}
        if pending_flag:
            target = pending_target
            adverse = _adverse(spread, slip)

            # Sell first if moving to cash or a different asset.
            if held_symbol is not None and held_symbol != target:
                old = held_symbol
                sell_price = candle_by_symbol[old].open * (ONE - adverse)
                notional = position * sell_price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                trade_pnl = proceeds - entry_cost
                quote += proceeds
                total_fees += sell_fee
                fills.append({
                    "timestamp_utc": start.isoformat(), "symbol": old, "side": "SELL",
                    "quantity": str(position), "price_quote": str(sell_price), "fee_quote": str(sell_fee),
                    "realized_pnl_quote": str(trade_pnl), "reason": f"weekly rotation to {target or 'CASH'}",
                })
                held_symbol, position, entry_cost = None, ZERO, ZERO

            if target is not None and held_symbol is None:
                rule = rules[target]
                fill_price = candle_by_symbol[target].open * (ONE + adverse)
                qty = _rounded_qty(quote * FRACTION / (fill_price * (ONE + fee)), rule)
                notional = qty * fill_price
                cutoff = start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= start]
                reason = None
                if qty < rule["min_qty"]:
                    reason = "MIN_QTY"
                elif notional < rule["min_notional"]:
                    reason = "MIN_NOTIONAL"
                elif notional + notional * fee > quote:
                    reason = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_30D:
                    reason = "MAX_ENTRIES_30D"
                if reason:
                    fills.append({
                        "timestamp_utc": start.isoformat(), "symbol": target, "side": "REJECTED_BUY",
                        "quantity": str(qty), "price_quote": str(fill_price), "fee_quote": "0",
                        "realized_pnl_quote": "", "reason": reason,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    position, held_symbol = qty, target
                    total_fees += buy_fee
                    buy_times.append(start)
                    fills.append({
                        "timestamp_utc": start.isoformat(), "symbol": target, "side": "BUY",
                        "quantity": str(qty), "price_quote": str(fill_price), "fee_quote": str(buy_fee),
                        "realized_pnl_quote": "", "reason": "weekly 30d relative-strength + EMA200 signal; next open fill",
                    })
            pending_flag = False
            pending_target = None

        adverse = _adverse(spread, slip)
        if held_symbol is None:
            equity = quote
        else:
            mark = candle_by_symbol[held_symbol].close * (ONE - adverse) * (ONE - fee)
            equity = quote + position * mark
        peak = max(peak, equity)
        curve.append({
            "timestamp_utc": anchor.payload.end.isoformat(), "equity_quote": equity,
            "held_symbol": held_symbol or "CASH", "quantity_held": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })

        # Decide from the just-completed candle only; never submit a new order after the final bar.
        if eval_idx >= len(eval_items) - 1:
            continue
        end_time = anchor.payload.end
        if end_time.weekday() == 0 and end_time.hour == 0 and end_time.minute == 0:
            target = target_at_index[i]
            if target != held_symbol:
                pending_flag = True
                pending_target = target

    if held_symbol is not None:
        last = eval_items[-1].payload
        adverse = _adverse(spread, slip)
        sell_price = last.close * (ONE - adverse)
        notional = position * sell_price
        sell_fee = notional * fee
        proceeds = notional - sell_fee
        trade_pnl = proceeds - entry_cost
        quote += proceeds
        total_fees += sell_fee
        fills.append({
            "timestamp_utc": last.end.isoformat(), "symbol": held_symbol, "side": "SELL",
            "quantity": str(position), "price_quote": str(sell_price), "fee_quote": str(sell_fee),
            "realized_pnl_quote": str(trade_pnl), "reason": "TERMINAL_LIQUIDATION at final evaluation close",
        })
        held_symbol, position = None, ZERO
        peak = max(peak, curve[-1]["equity_quote"])
        curve[-1] = {
            "timestamp_utc": curve[-1]["timestamp_utc"], "equity_quote": quote,
            "held_symbol": "CASH", "quantity_held": ZERO,
            "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final_equity = quote if held_symbol is None else quote + position * (
        candle_by_symbol[held_symbol].close * (ONE - _adverse(spread, slip)) * (ONE - fee)
    )
    sells = [f for f in fills if f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_quote"]) > ZERO for f in sells if f["realized_pnl_quote"])
    losses = sum(Decimal(f["realized_pnl_quote"]) < ZERO for f in sells if f["realized_pnl_quote"])
    result = {
        "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final_equity), "net_pnl_usdt": str(final_equity - capital),
        "return_pct": str((final_equity / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((r["drawdown"] for r in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(total_fees),
        "time_in_market_pct": str(
            Decimal(sum(r["held_symbol"] != "CASH" for r in curve)) / Decimal(len(curve)) * Decimal("100")
        ),
        "buy_count": sum(f["side"] == "BUY" for f in fills),
        "sell_count": len(sells), "rebalance_buy_rejections": sum(f["side"] == "REJECTED_BUY" for f in fills),
    }
    return result, fills, curve


def main():
    output = Path("rotation-output")
    output.mkdir(exist_ok=True)
    print("Frozen cross-sectional crypto rotation: 30d momentum > 0 + price > EMA200; weekly only.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()}); interval={INTERVAL}; warmup={WARMUP_BARS}")
    print("Virtual Spot only; no orders. Protocol: GitHub issue #138.")

    datasets, raw_records, rules, data_hashes = {}, {}, {}, {}
    evidence = {
        "evaluation_start": START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "interval": INTERVAL, "warmup_start": WARMUP_START.isoformat(), "warmup_bars": WARMUP_BARS,
        "evaluation_bars": EVAL_BARS, "expected_bars_per_symbol": EXPECTED_BARS,
        "data_gaps_interpolated": 0,
        "strategy_rule": (
            "Each Monday 00:00 UTC after a completed 4h bar, calculate close/close[-180]-1 and EMA200 "
            "for BTC, ETH, BNB, SOL, XRP. Eligible = momentum > 0 and close > EMA200; hold the eligible asset "
            "with highest momentum or cash if none; fill any change at next 4h open."
        ),
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(s), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, s, sl in SCENARIOS
        ],
        "assets": {},
        "data": {},
    }
    for symbol in SYMBOLS:
        rule = _metadata_and_filters(symbol)
        items, records, data_sha, unknown_comp, unknown_final = _fetch_symbol(symbol, rule)
        datasets[symbol], raw_records[symbol], rules[symbol], data_hashes[symbol] = items, records, rule, data_sha
        evidence["assets"][symbol] = {
            "dataset_sha256": data_sha, "bars": len(items),
            "market_data_completeness_unknown_count": unknown_comp,
            "candle_finality_unknown_count": unknown_final,
            "exchange_filters": {
                "step_size": str(rule["step_size"]), "min_qty": str(rule["min_qty"]),
                "min_notional": str(rule["min_notional"]),
                "filter_snapshot": rule["filter_snapshot"],
                "filter_snapshot_sha256": rule["filter_snapshot_sha256"],
                "retrieval_time_utc": rule["retrieval_time_utc"],
                "metadata_scope": rule["metadata_scope"],
            },
        }
        evidence["data"][symbol] = records
        print(
            f"{symbol} bars={len(items)} SHA256={data_sha}; step={rule['step_size']}; "
            f"minQty={rule['min_qty']}; minNotional={rule['min_notional']}; "
            f"filterSHA={rule['filter_snapshot_sha256']}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}"
        )

    anchor_starts = [x.payload.start for x in datasets[SYMBOLS[0]]]
    for symbol in SYMBOLS[1:]:
        if [x.payload.start for x in datasets[symbol]] != anchor_starts:
            raise RuntimeError(f"FAIL-CLOSED: {symbol} bar grid is not aligned with {SYMBOLS[0]}.")
    valid_indices = [i for i, x in enumerate(datasets[SYMBOLS[0]]) if WARMUP_START <= x.payload.start < END]
    closes, emas, targets = _targets(datasets, valid_indices)
    evidence["data_hashes_sha256"] = data_hashes
    (output / "dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    results, all_fills, all_curves = [], [], []
    eval_items = {s: [x for x in datasets[s] if START <= x.payload.start < END] for s in SYMBOLS}
    for capital in CAPITALS:
        for cost_name, fee, spread, slip in SCENARIOS:
            result, fills, curve = _simulate(datasets, rules, targets, capital, cost_name, fee, spread, slip)
            benchmarks = {}
            for symbol in ("BTCUSDT", "ETHUSDT"):
                final, fees = _buy_hold(datasets[symbol], capital, fee, spread, slip, rules[symbol])
                benchmarks[f"{symbol.lower()}_buy_hold_final_usdt"] = str(final)
                benchmarks[f"{symbol.lower()}_buy_hold_pnl_usdt"] = str(final - capital)
                benchmarks[f"{symbol.lower()}_buy_hold_fees_usdt"] = str(fees)
            result.update({
                **benchmarks,
                "cash_final_equity_usdt": str(capital),
                "dataset_hashes_sha256": json.dumps(data_hashes, sort_keys=True),
            })
            results.append(result)
            all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **fill} for fill in fills)
            all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **row} for row in curve)
            print(
                f"RESULT | capital={capital} | {cost_name} | PnL={Decimal(result['net_pnl_usdt']):+.6f} | "
                f"return={Decimal(result['return_pct']):+.3f}% | DD={Decimal(result['max_drawdown_pct']):.3f}% | "
                f"trips={result['completed_round_trips']} W/L={result['wins']}/{result['losses']} | "
                f"fees={Decimal(result['total_fees_usdt']):.4f} | "
                f"BTC_BH={Decimal(result['btcusdt_buy_hold_pnl_usdt']):+.4f} | "
                f"ETH_BH={Decimal(result['ethusdt_buy_hold_pnl_usdt']):+.4f}"
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
    gate = len(stress) == 2
    gate = gate and all(
        Decimal(r["net_pnl_usdt"]) > ZERO
        and r["completed_round_trips"] >= 5
        and Decimal(r["max_drawdown_pct"]) >= Decimal("-15")
        and Decimal(r["btcusdt_buy_hold_pnl_usdt"]) < Decimal(r["net_pnl_usdt"])
        and Decimal(r["ethusdt_buy_hold_pnl_usdt"]) < Decimal(r["net_pnl_usdt"])
        for r in stress
    )
    report = [
        "# Cross-sectional crypto momentum rotation — fixed YTD 2026", "",
        "**Disposition:** retrospective research only; current project decision remains NO_TRADE.", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()})",
        f"- Bars: {INTERVAL}; warm-up: {WARMUP_BARS}; evaluation bars: {EVAL_BARS}; symbols: {', '.join(SYMBOLS)}",
        "- Rule: each Monday 00:00 UTC, choose the eligible asset with the strongest 30-day momentum if momentum > 0 and close > EMA200; otherwise cash. Only long spot exposure; order at next 4h open.",
        "- 95% sizing; at most one held asset; max 5 entries in rolling 30 days; terminal liquidation at last evaluation close.",
        "- Quantity is rounded using current public Binance LOT_SIZE filters per asset; filter hashes and retrieval timestamps are recorded. These may not equal historical filters.",
        "- Cost assumptions (not historical bookTicker observations): BASE 10 bps fee + 10 bps full spread + 5 bps slippage per fill; STRESS_1 10 + 25 + 10; STRESS_2 20 + 50 + 20.", "",
        "| Capital | Costs | Net PnL | Return | Max DD | Cycles | W/L | Fees | BTC B&H PnL | ETH B&H PnL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | "
            f"{r['completed_round_trips']} | {r['wins']}/{r['losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
            f"{Decimal(r['btcusdt_buy_hold_pnl_usdt']):+.4f} | {Decimal(r['ethusdt_buy_hold_pnl_usdt']):+.4f} |"
        )
    report.extend([
        "", "## Preregistered promotion gate", "",
        f"Historical gate result: {'PASS' if gate else 'FAIL'}. Must be profitable under STRESS_2 at both capital levels, complete at least 5 cycles, drawdown no worse than 15%, and beat cash plus BTC/ETH buy-and-hold.",
        "", "## Limits", "",
        "This is a retrospective search on a window already inspected in prior research, so selection bias remains. A pass only qualifies the rule for a distinct holdout-validation stage; it does not prove forward profitability or authorize PAPER/LIVE. Market-data completeness and point-in-time availability are not established by these candles. Spread/slippage are assumptions.",
        "Protocol: GitHub issue #138. main and TOP1/PRIME are unchanged.", "",
    ])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\nPROMOTION_GATE={'PASS' if gate else 'FAIL'}; evidence written to {output}/.")


if __name__ == "__main__":
    main()
