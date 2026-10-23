"""Fixed-window replay for the frozen ETH BBMR EMA720 research profile.

Research-only: fetches public Binance Spot candles, never submits an order.
The workflow runs this against Jan 1 through Oct 21, 2026 and saves hash-bound
data, summary rows, and virtual fills as a GitHub Actions artifact.
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
from bot_obrero.virtual_backtest import (
    BacktestConfig,
    _market_data_record,
    _validate_history,
    run_virtual_backtest,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)  # exclusive
WARMUP_START = START - timedelta(hours=720)
SYMBOL = "ETHUSDT"
INTERVAL = "1h"
EVAL_BARS = int((END - START).total_seconds() // 3600)
WARMUP_BARS = 720
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
FILTERS = {
    "min_qty": Decimal("0.0001"),
    "quantity_step": Decimal("0.0001"),
    "min_notional": Decimal("5"),
}
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)


def acquire_data():
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    metadata.resolve_active(SYMBOL)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=SYMBOL,
        interval=INTERVAL,
        start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000),
        page_limit=1000,
        max_requests=12,
    )
    # Binance endTime is inclusive; retain exactly the requested open-time range.
    items = sorted(
        (x for x in acquired if WARMUP_START <= x.payload.start < END),
        key=lambda x: x.payload.start,
    )
    expected_starts = [
        WARMUP_START + timedelta(hours=i) for i in range(EXPECTED_BARS)
    ]
    observed_starts = [x.payload.start for x in items]
    if observed_starts != expected_starts:
        observed, expected = set(observed_starts), set(expected_starts)
        raise RuntimeError(
            f"FAIL-CLOSED: expected {EXPECTED_BARS} contiguous hourly bars, "
            f"got {len(items)}; missing_first={sorted(expected - observed)[:10]}; "
            f"unexpected_first={sorted(observed - expected)[:10]}. "
            "No rows were interpolated."
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(
        items, INTERVAL
    )
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(
            f"FAIL-CLOSED: accepted={len(accepted)}, excluded={excluded}; "
            f"expected {EXPECTED_BARS} closed, complete bars."
        )
    if any(x.instrument.instrument_id != "binance:SPOT:ETHUSDT" for x in items):
        raise RuntimeError("FAIL-CLOSED: unexpected instrument identity.")
    records = [_market_data_record(x) for x in items]
    canonical = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    data_sha = hashlib.sha256(canonical).hexdigest()
    return items, records, data_sha, unknown_completeness, unknown_finality


def buy_hold(eval_items, capital, fee, spread_bps, slippage_bps):
    """One buy at first evaluation open, liquidation at final close, same modeled costs."""
    adverse = (slippage_bps + spread_bps / Decimal("2")) / Decimal("10000")
    buy_price = eval_items[0].payload.open * (Decimal("1") + adverse)
    raw_qty = (capital * Decimal("0.95")) / (buy_price * (Decimal("1") + fee))
    qty = (
        (raw_qty / FILTERS["quantity_step"]).to_integral_value(rounding=ROUND_DOWN)
        * FILTERS["quantity_step"]
    )
    notional = qty * buy_price
    if qty < FILTERS["min_qty"] or notional < FILTERS["min_notional"]:
        raise RuntimeError("FAIL-CLOSED: buy-and-hold benchmark violates fixed lot filters.")
    buy_fee = notional * fee
    quote = capital - notional - buy_fee
    sell_price = eval_items[-1].payload.close * (Decimal("1") - adverse)
    sell_gross = qty * sell_price
    sell_fee = sell_gross * fee
    return quote + sell_gross - sell_fee, buy_fee + sell_fee


def main():
    Path("historical-output").mkdir(exist_ok=True)
    print("Protocol: frozen eth-bbmr-top1; no parameter tuning.")
    print("Research HEAD:", subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()})")
    print(f"Warm-up: {WARMUP_START.isoformat()} through Dec 31, 2025; {WARMUP_BARS} bars.")
    print(f"Bars expected: {EXPECTED_BARS} total, including {EVAL_BARS} evaluation bars.")
    print("Virtual only; public data; no private endpoint, no orders.")

    items, records, data_sha, unknown_comp, unknown_final = acquire_data()
    eval_items = [x for x in items if START <= x.payload.start < END]
    if len(eval_items) != EVAL_BARS:
        raise RuntimeError(
            f"FAIL-CLOSED: evaluation rows={len(eval_items)}, expected={EVAL_BARS}."
        )
    print("Data SHA-256:", data_sha)
    print("Observed first/last:", items[0].payload.start.isoformat(),
          "..", items[-1].payload.start.isoformat())
    print(f"Completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}")

    evidence = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "evaluation_start": START.isoformat(),
        "evaluation_end_exclusive": END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(),
        "warmup_bars": WARMUP_BARS,
        "evaluation_bars": EVAL_BARS,
        "expected_bars": EXPECTED_BARS,
        "dataset_sha256": data_sha,
        "missing_or_interpolated_bars": 0,
        "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
        "data": records,
    }
    Path("historical-output/dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    results, fills = [], []
    for capital in (Decimal("60"), Decimal("100")):
        for name, fee, spread, slip in SCENARIOS:
            cfg = BacktestConfig(
                symbol=SYMBOL,
                interval=INTERVAL,
                strategy="eth-bbmr-top1",
                candles=EVAL_BARS,
                slow_ema_period=720,
                initial_quote_balance=capital,
                position_fraction=Decimal("0.95"),
                fee_rate=fee,
                spread_bps=spread,
                slippage_bps=slip,
                max_entries_per_30d=5,
                liquidate_at_end=True,
                **FILTERS,
            )
            result = run_virtual_backtest(
                items, cfg, evaluation_start=START, evaluation_end=END
            )
            sells = [f for f in result.fills if f.side == "SELL"]
            wins = sum(f.realized_pnl is not None and f.realized_pnl > 0 for f in sells)
            losses = sum(f.realized_pnl is not None and f.realized_pnl < 0 for f in sells)
            flats = len(sells) - wins - losses
            bh_final, bh_fees = buy_hold(eval_items, capital, fee, spread, slip)
            final = result.final_equity
            pnl = final - capital
            in_market = sum(p.base_position > 0 for p in result.equity_curve)
            row = {
                "capital_initial_usdt": str(capital),
                "cost_scenario": name,
                "final_equity_usdt": str(final),
                "net_pnl_usdt": str(pnl),
                "return_pct": str((final / capital - Decimal("1")) * Decimal("100")),
                "max_drawdown_pct": str(result.max_drawdown * Decimal("100")),
                "completed_round_trips": len(sells),
                "wins": int(wins), "losses": int(losses), "flat_exits": int(flats),
                "total_fees_usdt": str(result.total_fees),
                "time_in_market_pct": str(
                    Decimal(in_market) / Decimal(len(result.equity_curve)) * Decimal("100")
                ),
                "buy_hold_final_equity_usdt": str(bh_final),
                "buy_hold_net_pnl_usdt": str(bh_final - capital),
                "buy_hold_fees_usdt": str(bh_fees),
                "cash_final_equity_usdt": str(capital),
                "dataset_sha256": data_sha,
                "evaluation_bars": EVAL_BARS,
            }
            results.append(row)
            for fill in result.fills:
                fills.append({
                    "capital_initial_usdt": str(capital), "cost_scenario": name,
                    "timestamp_utc": fill.timestamp.isoformat(), "side": fill.side,
                    "quantity_eth": str(fill.quantity), "price_usdt": str(fill.price),
                    "fee_usdt": str(fill.fee),
                    "quote_balance_after_usdt": str(fill.quote_balance_after),
                    "realized_pnl_usdt": "" if fill.realized_pnl is None else str(fill.realized_pnl),
                    "reason": fill.reason,
                })
            print(
                f"RESULT | capital={capital} | {name} | final={final:.8f} | "
                f"net_pnl={pnl:.8f} | return_pct={row['return_pct']} | "
                f"max_dd_pct={row['max_drawdown_pct']} | trips={len(sells)} "
                f"W/L/flat={wins}/{losses}/{flats} | fees={result.total_fees:.8f} | "
                f"time_in_market_pct={row['time_in_market_pct']} | "
                f"buy_hold_pnl={bh_final - capital:.8f}"
            )

    with open("historical-output/summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    fill_fields = list(fills[0]) if fills else [
        "capital_initial_usdt", "cost_scenario", "timestamp_utc", "side",
        "quantity_eth", "price_usdt", "fee_usdt", "quote_balance_after_usdt",
        "realized_pnl_usdt", "reason",
    ]
    with open("historical-output/fills.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fill_fields)
        writer.writeheader()
        writer.writerows(fills)

    report = [
        "# ETH BBMR EMA720 — YTD 2026 historical replay", "",
        "Decision: NO_TRADE. Research only; historical returns do not guarantee future profitability.", "",
        f"- Evaluation UTC: [{START.isoformat()}, {END.isoformat()})",
        f"- Candles: {EVAL_BARS} hourly evaluation bars; {WARMUP_BARS} warm-up bars before Jan 1",
        f"- Dataset SHA-256: {data_sha}",
        "- Missing/interpolated bars: 0; workflow fails closed if any hourly gap exists",
        f"- MarketData completeness UNKNOWN: {unknown_comp}; candle finality UNKNOWN: {unknown_final}",
        "- Strategy: ETHUSDT Spot 1h; z-score 168 closes; enter z < -2.5 and close > EMA720; exit z > +1.0 or close <= EMA720",
        "- Signal at closed candle; fill at next open; liquidate at final close with modeled costs",
        "- Size 95%; max 5 new buys in a rolling 30-day window; lot assumptions quantity step/minimum 0.0001 ETH, min notional 5 USDT",
        "- BASE: fee 0.10% per fill; assumed full spread 10 bps; slippage 5 bps per fill",
        "- STRESS_1: fee 0.10% per fill; assumed full spread 25 bps; slippage 10 bps per fill",
        "- STRESS_2: fee 0.20% per fill; assumed full spread 50 bps; slippage 20 bps per fill", "",
        "| Capital | Cost | Final equity | Net PnL | Return | Max DD | Round-trips | W/L | Fees | B&H net PnL |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        report.append(
            f"| {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['final_equity_usdt']):.4f} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | "
            f"{r['completed_round_trips']} | {r['wins']}/{r['losses']} | "
            f"{Decimal(r['total_fees_usdt']):.4f} | {Decimal(r['buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report += [
        "", "Spread/slippage and lot filters are assumptions, not archived historical bid/ask or historical exchange filters.",
        "Historical available_at/finality are not sufficient to prove point-in-time availability.",
        "Virtual replay only. No PAPER/LIVE execution.", "",
    ]
    Path("historical-output/report.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
