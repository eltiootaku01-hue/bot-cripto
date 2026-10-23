"""Preregistered, fixed-parameter ETH secondary-strategy screen with BTC regime gate.

Research only: public Binance Spot OHLCV, virtual fills, no private endpoint or orders.
Parameters and promotion gate are frozen in GitHub issue #137 before this replay.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
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
SYMBOLS = ("ETHUSDT", "BTCUSDT")
INTERVAL = "4h"
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
BAR = timedelta(hours=4)
WARMUP_BARS = 600
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
FRACTION = Decimal("0.95")
MAX_ENTRIES_30D = 5
MIN_QTY = Decimal("0.0001")
QTY_STEP = Decimal("0.0001")
MIN_NOTIONAL = Decimal("5")
CAPITALS = (Decimal("60"), Decimal("100"))
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
CANDIDATES = (
    "BTC_REGIME_DONCHIAN_55_20",
    "BTC_REGIME_BB_SQUEEZE_BREAKOUT",
    "BTC_REGIME_ETH_EMA50_200",
)


def _fetch_symbol(symbol: str):
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
            f"missing_first={sorted(set(expected) - set(observed))[:12]}; "
            f"unexpected_first={sorted(set(observed) - set(expected))[:12]}. No bars interpolated."
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(
            f"FAIL-CLOSED {symbol}: accepted={len(accepted)}, excluded={excluded}; expected {EXPECTED_BARS} closed bars."
        )
    if any(x.instrument.instrument_id != f"binance:SPOT:{symbol}" for x in items):
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


def _rolling_extreme(values: list[Decimal], period: int, use_max: bool) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    for i in range(period, len(values)):
        window = values[i - period : i]  # excludes current candle: no look-ahead
        output[i] = max(window) if use_max else min(window)
    return output


def _bollinger(closes: list[Decimal], period: int = 20):
    middle: list[Decimal | None] = [None] * len(closes)
    upper: list[Decimal | None] = [None] * len(closes)
    width: list[Decimal | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window, ZERO) / Decimal(period)
        variance = sum(((x - mean) ** 2 for x in window), ZERO) / Decimal(period)
        sd = variance.sqrt()
        if mean <= ZERO:
            continue
        middle[i] = mean
        upper[i] = mean + Decimal("2") * sd
        width[i] = Decimal("4") * sd / mean
    return middle, upper, width


def _signals(eth_items, btc_items):
    eth_close = [x.payload.close for x in eth_items]
    eth_high = [x.payload.high for x in eth_items]
    eth_low = [x.payload.low for x in eth_items]
    btc_close = [x.payload.close for x in btc_items]
    if [x.payload.start for x in eth_items] != [x.payload.start for x in btc_items]:
        raise RuntimeError("FAIL-CLOSED: BTC and ETH bar timestamps are not aligned.")

    ema_eth20 = _ema(eth_close, 20)
    ema_eth50 = _ema(eth_close, 50)
    ema_eth200 = _ema(eth_close, 200)
    ema_btc200 = _ema(btc_close, 200)
    donchian_high55 = _rolling_extreme(eth_high, 55, True)
    donchian_low20 = _rolling_extreme(eth_low, 20, False)
    _, bb_upper, bb_width = _bollinger(eth_close, 20)

    entries = {name: [False] * len(eth_items) for name in CANDIDATES}
    exits = {name: [False] * len(eth_items) for name in CANDIDATES}

    for i in range(1, len(eth_items)):
        c = eth_close[i]
        btc_bull = ema_btc200[i] is not None and btc_close[i] > ema_btc200[i]

        # Candidate 1: low-frequency Donchian breakout, gated by BTC market regime.
        name = "BTC_REGIME_DONCHIAN_55_20"
        if donchian_high55[i] is not None and donchian_low20[i] is not None and ema_btc200[i] is not None:
            entries[name][i] = c > donchian_high55[i] and btc_bull
            exits[name][i] = c < donchian_low20[i] or btc_close[i] < ema_btc200[i]

        # Candidate 2: squeeze uses the previous bar's width vs 120 older widths;
        # every band/percentile excludes the current signal candle.
        name = "BTC_REGIME_BB_SQUEEZE_BREAKOUT"
        prior = i - 1
        hist = [bb_width[j] for j in range(max(19, i - 121), i - 1) if bb_width[j] is not None]
        if (
            bb_upper[prior] is not None
            and bb_width[prior] is not None
            and len(hist) == 120
            and ema_eth50[i] is not None
            and ema_eth20[i] is not None
            and ema_btc200[i] is not None
        ):
            ordered = sorted(hist)
            threshold = ordered[max(0, math.ceil(0.20 * len(ordered)) - 1)]
            squeeze = bb_width[prior] <= threshold
            entries[name][i] = c > bb_upper[prior] and squeeze and c > ema_eth50[i] and btc_bull
            exits[name][i] = c < ema_eth20[i] or btc_close[i] < ema_btc200[i]

        # Candidate 3: slow ETH trend alignment with independent BTC regime gate.
        name = "BTC_REGIME_ETH_EMA50_200"
        if ema_eth50[i] is not None and ema_eth200[i] is not None and ema_btc200[i] is not None:
            entries[name][i] = c > ema_eth50[i] > ema_eth200[i] and btc_bull
            exits[name][i] = c < ema_eth50[i] or ema_eth50[i] <= ema_eth200[i] or btc_close[i] < ema_btc200[i]

    return entries, exits


def _adverse(spread_bps: Decimal, slippage_bps: Decimal) -> Decimal:
    return (slippage_bps + spread_bps / Decimal("2")) / BPS


def _buy_hold(eval_eth, capital, fee, spread, slip):
    adverse = _adverse(spread, slip)
    buy_price = eval_eth[0].payload.open * (ONE + adverse)
    qty = ((capital * FRACTION / (buy_price * (ONE + fee))) / QTY_STEP).to_integral_value(
        rounding=ROUND_DOWN
    ) * QTY_STEP
    buy_notional = qty * buy_price
    if qty < MIN_QTY or buy_notional < MIN_NOTIONAL:
        raise RuntimeError("FAIL-CLOSED: buy-and-hold benchmark violates fixed lot assumptions.")
    buy_fee = buy_notional * fee
    cash = capital - buy_notional - buy_fee
    sell_price = eval_eth[-1].payload.close * (ONE - adverse)
    sell_notional = qty * sell_price
    sell_fee = sell_notional * fee
    return cash + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(candidate, eth_items, entries, exits, capital, cost_name, fee, spread, slip):
    history, excluded, unknown_completeness, unknown_finality = _validate_history(eth_items, INTERVAL)
    eval_items = [x for x in history if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(f"FAIL-CLOSED: evaluation rows={len(eval_items)}, expected={EVAL_BARS}.")
    indexes = {x.payload.start: i for i, x in enumerate(history)}
    quote, position, entry_cost = capital, ZERO, ZERO
    total_fees = ZERO
    fills, curve, buy_times = [], [], []
    peak = capital
    pending = None

    for eval_idx, item in enumerate(eval_items):
        candle = item.payload
        i = indexes[candle.start]
        if pending is not None:
            adverse = _adverse(spread, slip)
            if pending == "BUY" and position == ZERO:
                fill_price = candle.open * (ONE + adverse)
                qty = ((quote * FRACTION / (fill_price * (ONE + fee))) / QTY_STEP).to_integral_value(
                    rounding=ROUND_DOWN
                ) * QTY_STEP
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
                        "candidate": candidate, "timestamp_utc": candle.start.isoformat(),
                        "side": "REJECTED_BUY", "quantity_eth": str(qty),
                        "price_usdt": str(fill_price), "fee_usdt": "0",
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
                        "candidate": candidate, "timestamp_utc": candle.start.isoformat(),
                        "side": "BUY", "quantity_eth": str(qty), "price_usdt": str(fill_price),
                        "fee_usdt": str(buy_fee), "realized_pnl_usdt": "",
                        "reason": f"{candidate}: previous completed 4h candle signal; next open fill",
                    })
            elif pending == "SELL" and position > ZERO:
                fill_price = candle.open * (ONE - adverse)
                notional = position * fill_price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                pnl = proceeds - entry_cost
                quote += proceeds
                total_fees += sell_fee
                fills.append({
                    "candidate": candidate, "timestamp_utc": candle.start.isoformat(),
                    "side": "SELL", "quantity_eth": str(position), "price_usdt": str(fill_price),
                    "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(pnl),
                    "reason": f"{candidate}: previous completed 4h candle exit; next open fill",
                })
                position, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + position * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "candidate": candidate, "timestamp_utc": candle.end.isoformat(),
            "equity_usdt": equity, "base_position_eth": position,
            "drawdown": equity / peak - ONE if peak else ZERO,
        })

        if eval_idx >= len(eval_items) - 1:
            continue
        if entries[candidate][i] and position == ZERO:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D:
                pending = "BUY"
        elif exits[candidate][i] and position > ZERO:
            pending = "SELL"

    if position > ZERO:
        last = eval_items[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = position * price
        exit_fee = notional * fee
        proceeds = notional - exit_fee
        pnl = proceeds - entry_cost
        quote += proceeds
        total_fees += exit_fee
        fills.append({
            "candidate": candidate, "timestamp_utc": last.end.isoformat(),
            "side": "SELL", "quantity_eth": str(position), "price_usdt": str(price),
            "fee_usdt": str(exit_fee), "realized_pnl_usdt": str(pnl),
            "reason": "TERMINAL_LIQUIDATION at final evaluation close",
        })
        position = ZERO
        if curve:
            peak = max(peak, curve[-1]["equity_usdt"])
            curve[-1] = {
                "candidate": candidate, "timestamp_utc": curve[-1]["timestamp_utc"],
                "equity_usdt": quote, "base_position_eth": ZERO,
                "drawdown": quote / peak - ONE if peak else ZERO,
            }

    final_equity = quote + position * eval_items[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [x for x in fills if x["side"] == "SELL"]
    wins = sum(Decimal(x["realized_pnl_usdt"]) > ZERO for x in sells if x["realized_pnl_usdt"])
    losses = sum(Decimal(x["realized_pnl_usdt"]) < ZERO for x in sells if x["realized_pnl_usdt"])
    result = {
        "candidate": candidate, "capital_initial_usdt": str(capital), "cost_scenario": cost_name,
        "final_equity_usdt": str(final_equity), "net_pnl_usdt": str(final_equity - capital),
        "return_pct": str((final_equity / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(total_fees),
        "time_in_market_pct": str(
            Decimal(sum(x["base_position_eth"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")
        ),
        "buy_count": sum(x["side"] == "BUY" for x in fills), "sell_count": len(sells),
        "excluded_history_records": excluded, "market_data_completeness_unknown_count": unknown_completeness,
        "candle_finality_unknown_count": unknown_finality,
    }
    return result, fills, curve


def main():
    output = Path("secondary-screen-output")
    output.mkdir(exist_ok=True)
    print("Preregistered research screen: three frozen BTC-regime ETH candidates; no retuning.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()})")
    print(f"Warm-up: {WARMUP_BARS} closed 4h bars before evaluation; expected {EXPECTED_BARS} bars per symbol.")
    print("Virtual only; no exchange orders. Promotion gate is recorded in GitHub issue #137.")

    datasets = {}
    evidence = {
        "evaluation_start": START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "interval": INTERVAL, "warmup_start": WARMUP_START.isoformat(),
        "warmup_bars": WARMUP_BARS, "evaluation_bars": EVAL_BARS,
        "expected_bars_per_symbol": EXPECTED_BARS, "data_gaps_interpolated": 0,
        "assets": {}, "strategy_rules": {
            "BTC_REGIME_DONCHIAN_55_20": "ETH close > highest high of previous 55 bars and BTC close > BTC EMA200 to buy; ETH close < lowest low of previous 20 bars or BTC close < EMA200 to exit.",
            "BTC_REGIME_BB_SQUEEZE_BREAKOUT": "ETH close > upper band calculated from preceding 20 closes; preceding band width <= 20th-percentile nearest-rank of the prior 120 widths; ETH close > EMA50; BTC close > EMA200. Exit ETH close < EMA20 or BTC close < EMA200.",
            "BTC_REGIME_ETH_EMA50_200": "Buy only when ETH close > EMA50 > EMA200 and BTC close > EMA200; exit if ETH close < EMA50, ETH EMA50 <= EMA200, or BTC close < EMA200.",
        },
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(s), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, s, sl in SCENARIOS
        ],
        "data": {},
    }
    for symbol in SYMBOLS:
        items, records, data_sha, unknown_comp, unknown_final = _fetch_symbol(symbol)
        datasets[symbol] = items
        evidence["assets"][symbol] = {
            "dataset_sha256": data_sha, "bars": len(items),
            "market_data_completeness_unknown_count": unknown_comp,
            "candle_finality_unknown_count": unknown_final,
            "instrument_id": f"binance:SPOT:{symbol}",
        }
        evidence["data"][symbol] = records
        print(f"{symbol} dataset SHA-256: {data_sha}; bars={len(items)}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")

    eth_items, btc_items = datasets["ETHUSDT"], datasets["BTCUSDT"]
    if [x.payload.start for x in eth_items] != [x.payload.start for x in btc_items]:
        raise RuntimeError("FAIL-CLOSED: BTC/ETH bar grids differ.")
    entries, exits = _signals(eth_items, btc_items)
    (output / "dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    eval_eth = [x for x in eth_items if START <= x.payload.start < END]
    results, all_fills, all_curves = [], [], []
    for candidate in CANDIDATES:
        print(f"\nCANDIDATE: {candidate}")
        for capital in CAPITALS:
            for cost_name, fee, spread, slip in SCENARIOS:
                result, fills, curve = _simulate(
                    candidate, eth_items, entries, exits, capital, cost_name, fee, spread, slip
                )
                bh_final, bh_fees = _buy_hold(eval_eth, capital, fee, spread, slip)
                result.update({
                    "buy_hold_final_equity_usdt": str(bh_final),
                    "buy_hold_net_pnl_usdt": str(bh_final - capital),
                    "buy_hold_fees_usdt": str(bh_fees),
                    "cash_final_equity_usdt": str(capital),
                    "dataset_eth_sha256": evidence["assets"]["ETHUSDT"]["dataset_sha256"],
                    "dataset_btc_sha256": evidence["assets"]["BTCUSDT"]["dataset_sha256"],
                    "evaluation_bars": EVAL_BARS,
                })
                results.append(result)
                all_fills.extend({
                    "capital_initial_usdt": str(capital), "cost_scenario": cost_name, **fill
                } for fill in fills)
                all_curves.extend({
                    "capital_initial_usdt": str(capital), "cost_scenario": cost_name, **row
                } for row in curve)
                print(
                    f"RESULT | {candidate} | capital={capital} | {cost_name} | "
                    f"PnL={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                    f"DD={Decimal(result['max_drawdown_pct']):.3f}% | trips={result['completed_round_trips']} | "
                    f"W/L={result['wins']}/{result['losses']} | fees={Decimal(result['total_fees_usdt']):.4f} | "
                    f"ETH_BH={Decimal(result['buy_hold_net_pnl_usdt']):+.4f}"
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
        profitable_both = len(subset) == 2 and all(Decimal(r["net_pnl_usdt"]) > ZERO for r in subset)
        enough_trades = len(subset) == 2 and all(r["completed_round_trips"] >= 5 for r in subset)
        drawdown_gate = len(subset) == 2 and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in subset)
        gates[candidate] = {
            "profitable_under_stress2_at_60_and_100": profitable_both,
            "at_least_5_round_trips_at_both_capitals": enough_trades,
            "max_drawdown_no_worse_than_15pct_at_both_capitals": drawdown_gate,
            "passes_preregistered_historical_gate": profitable_both and enough_trades and drawdown_gate,
        }

    report = [
        "# ETH/BTC-regime secondary screen — fixed YTD 2026", "",
        "**Disposition:** research only; keep NO_TRADE. No LIVE or PAPER execution.", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()})",
        f"- Interval: {INTERVAL}; warm-up: {WARMUP_BARS} bars per symbol; evaluation bars: {EVAL_BARS}",
        f"- ETHUSDT data SHA-256: {evidence['assets']['ETHUSDT']['dataset_sha256']}",
        f"- BTCUSDT data SHA-256: {evidence['assets']['BTCUSDT']['dataset_sha256']}",
        "- All indicator inputs use completed bars. Order fill model: next 4h open; terminal liquidation at last evaluation close.",
        "- 95% sizing; one open position; at most 5 buys in rolling 30 days; min qty/step 0.0001 ETH and minimum notional 5 USDT are fixed assumptions.",
        "- BASE: fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/fill.",
        "- STRESS_1: fee 10 bps/fill, assumed full spread 25 bps, slippage 10 bps/fill.",
        "- STRESS_2: fee 20 bps/fill, assumed full spread 50 bps, slippage 20 bps/fill.",
        "- These spread/slippage/lot constraints are assumptions; not historical bookTicker fills. Market-data completeness and candle finality are reported unknown by the source model.", "",
        "| Candidate | Capital | Cost | Net PnL | Return | Max DD | Cycles | W/L | Fees | ETH B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['candidate']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | "
            f"{Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{r['wins']}/{r['losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
            f"{Decimal(r['buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report.extend([
        "", "## Pre-registered promotion gate", "",
        "The candidate must be net profitable in STRESS_2 at both 60 and 100 USDT, complete at least 5 round trips at each capital, and have maximum drawdown no worse than 15% at each capital.",
        "",
    ])
    for candidate, gate in gates.items():
        report.append(
            f"- **{candidate}: {'PASS' if gate['passes_preregistered_historical_gate'] else 'FAIL'}** — "
            f"Stress-2 positive both capitals: {gate['profitable_under_stress2_at_60_and_100']}; "
            f"5+ trips both: {gate['at_least_5_round_trips_at_both_capitals']}; "
            f"drawdown gate: {gate['max_drawdown_no_worse_than_15pct_at_both_capitals']}."
        )
    report += [
        "", "## Interpretation / limitations", "",
        "This is a retrospective fixed-window screen across several preregistered hypotheses, not a clean prospective test. A pass would only qualify a candidate for a separate holdout-validation stage, not operational deployment. A fail is not retuned in this branch.",
        "The model uses public OHLCV candles; it does not prove point-in-time availability for each historical decision, actual historical spread/slippage, queue/fill realism, or production reconciliation. No interpolated candles are accepted.",
        "Reference protocol: GitHub issue #137. main remains untouched; project status remains NO_TRADE.", "",
    ]
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\nPREREGISTERED GATES:")
    for candidate, gate in gates.items():
        print(f"{candidate}: {'PASS' if gate['passes_preregistered_historical_gate'] else 'FAIL'} :: {json.dumps(gate, sort_keys=True)}")
    print(f"Evidence written to {output}/ (dataset evidence, summary, fills, equity, report).")


if __name__ == "__main__":
    main()
