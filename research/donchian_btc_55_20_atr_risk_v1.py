"""Frozen BTC Donchian 55-day breakout / 20-day low exit replay; research only."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_DONCHIAN_55_20_ATR14_TARGET20"
PROTOCOL = "BTC_DONCHIAN_55_20_ATR14_V1"
ENTRY_LOOKBACK = 55
EXIT_LOOKBACK = 20
ZERO, ONE = Decimal("0"), Decimal("1")
ENTRY_SIGNALS: list[bool] = []

def donchian_signals_and_weights(items: Sequence[Any]):
    """Close-confirmed channel signals; each channel reference excludes the current candle."""
    closes = [row.payload.close for row in items]
    highs = [row.payload.high for row in items]
    lows = [row.payload.low for row in items]
    count = len(items)

    true_ranges = []
    for i in range(count):
        tr = highs[i] - lows[i] if i == 0 else max(
            highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)

    atr14: list[Decimal | None] = [None] * count
    for i in range(13, count):
        atr14[i] = sum(true_ranges[i-13:i+1], ZERO) / Decimal(14)

    upper: list[Decimal | None] = [None] * count
    lower: list[Decimal | None] = [None] * count
    entries = [False] * count
    exits = [False] * count
    weights: list[Decimal | None] = [None] * count
    # Same frozen ATR risk overlay as Issue #143; this test changes the entry/exit family only.
    annual_target = Decimal("0.20")
    max_weight = Decimal("0.95")
    annualization = Decimal(365).sqrt()

    for i in range(count):
        if i >= ENTRY_LOOKBACK:
            upper[i] = max(highs[i-ENTRY_LOOKBACK:i])
        if i >= EXIT_LOOKBACK:
            lower[i] = min(lows[i-EXIT_LOOKBACK:i])
        if i >= max(ENTRY_LOOKBACK, EXIT_LOOKBACK) and upper[i] is not None and lower[i] is not None:
            entries[i] = closes[i] > upper[i]
            exits[i] = closes[i] < lower[i]
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denominator = (atr14[i] / closes[i]) * annualization
            if denominator > ZERO:
                weights[i] = min(max_weight, annual_target / denominator)
    return entries, exits, weights, atr14, upper, lower

def _gates(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    lookup = {(r["window"], r["capital_initial_usdt"]): r for r in stress}
    checks = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    yearly = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *yearly)
    def add(name: str, passed: bool, details: str) -> None:
        checks.append({"gate": name, "status": "PASS" if passed else "FAIL", "details": details})
    for capital in engine.CAPITALS:
        cap = str(capital)
        for name in principal:
            row = lookup.get((name, cap))
            add(f"positive_pnl_{name}_{cap}", row is not None and Decimal(row["net_pnl_usdt"]) > ZERO,
                "missing window" if row is None else f"PnL={row['net_pnl_usdt']} USDT")
            add(f"five_roundtrips_{name}_{cap}", row is not None and int(row["completed_round_trips"]) >= 5,
                "missing window" if row is None else f"round trips={row['completed_round_trips']}")
        for name in dd_windows:
            row = lookup.get((name, cap))
            add(f"drawdown_limit_{name}_{cap}", row is not None and Decimal(row["max_drawdown_pct"]) >= Decimal("-15"),
                "missing window" if row is None else f"max drawdown={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022", cap))
        add(f"2022_nonnegative_{cap}", row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing window" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positive = sum(
            lookup.get((name, cap)) is not None and Decimal(lookup[(name, cap)]["net_pnl_usdt"]) > ZERO
            for name in yearly
        )
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive windows={positive}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing window" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and
            Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing window" if recent is None else
            f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(x["status"] != "PASS" for x in checks)
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": len(checks) - failed, "failed": failed, "checks": checks,
        "candidate_origin": "Donchian 55/20 channel hypothesis frozen in Issue #164; ATR risk overlay reuses frozen Issue #143 rule.",
        "promotion_note": "Historical archive was previously inspected; any historical pass still requires a prospective shadow holdout. NO_TRADE remains.",
    }

def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, upper, lower = donchian_signals_and_weights(items)
    indicator_rows = [
        {
            "index": i,
            "timestamp_utc": item.payload.start.isoformat(),
            "upper_55_prior_high": None if upper[i] is None else str(upper[i]),
            "lower_20_prior_low": None if lower[i] is None else str(lower[i]),
            "atr14": None if atr14[i] is None else str(atr14[i]),
            "weight": None if weights[i] is None else str(weights[i]),
            "entry_signal": entries[i],
            "exit_signal": exits[i],
        }
        for i, item in enumerate(items)
    ]
    indicator_sha = hashlib.sha256(json.dumps(indicator_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    evidence = {
        "protocol": PROTOCOL, "preregistration_issue": 164,
        "candidate": CANDIDATE, "symbol": engine.SYMBOL, "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(), "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(), "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS, "observed_bars": len(items),
        "dataset_sha256": data_sha, "indicator_series_sha256": indicator_sha,
        "entry_signal_count": sum(entries), "exit_signal_count": sum(exits),
        "unknown_market_data_completeness_count": unknown_comp, "unknown_candle_finality_count": unknown_final,
        "exchange_filter_snapshot": {
            "LOT_SIZE_step": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot_sha256": rules["sha256"],
            "scope": "current public filter only; historical filters unknown",
        },
        "frozen_rule": {
            "entry": "close[t] > max(high[t-55:t]); current candle excluded",
            "exit": "close[t] < min(low[t-20:t]); current candle excluded",
            "execution": "signal after close, execute at next daily open; terminal close liquidation",
            "ATR14": "simple 14-bar mean of true range",
            "sizing": "min(95%, 20%/((ATR14/close)*sqrt(365))); frozen at signal close",
            "max_entries_rolling_30d": engine.MAX_ENTRIES_30D,
        },
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(f), "assumed_full_spread_bps": str(sp), "assumed_slippage_bps_per_fill": str(sl)}
            for n, f, sp, sl in engine.SCENARIOS
        ],
        "retrospective_selection_warning": True,
        "data": records,
        "indicator_rows": indicator_rows,
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")

    results, all_fills, all_curves = [], [], []
    for window, start, end in engine.WINDOWS:
        for capital in engine.CAPITALS:
            for cost, fee, spread, slip in engine.SCENARIOS:
                # sma200 parameter is unused by the shared accounting engine; retain array shape for compatibility.
                result, fills, curve = engine._simulate(
                    items, rules, entries, exits, weights, atr14, [None] * len(items),
                    window, start, end, capital, cost, fee, spread, slip
                )
                # Reuse the validated engine's ledger and terminal handling, with accurate strategy-specific fill reasons.
                for fill in fills:
                    if fill.get("side") in ("BUY", "REJECTED_BUY"):
                        fill["reason"] = "Donchian close > prior 55-day high; ATR14 exposure frozen at signal close; next-open fill"
                    elif fill.get("side") == "SELL":
                        fill["reason"] = "Donchian close < prior 20-day low; next-open fill"
                bh, bh_fees = engine._buy_hold(items, rules, start, end, capital, fee, spread, slip)
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
    for filename, rows in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with (output / filename).open("w", newline="", encoding="utf-8") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            else:
                handle.write("\n")
    gates = _gates(results)
    (output / "gate_results.json").write_text(json.dumps(gates, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    report = [
        "# BTC Donchian 55/20 + ATR14 sizing — full-regime replay", "",
        "Frozen economic gate: " + gates["overall"],
        "",
        "This is a separately preregistered channel trend hypothesis. ATR sizing reuses the previously frozen Issue #143 overlay; it is not a fresh selection of the sizing rule.",
        f"Dataset: {len(items)} daily BTCUSDT candles; SHA-256 {data_sha}; indicator series SHA-256 {indicator_sha}.",
        f"Signals over archive: entries={sum(entries)}, exits={sum(exits)}.",
        f"Unknown completeness count={unknown_comp}; unknown candle finality count={unknown_final}. Historical filters/point-in-time executable quotes remain UNKNOWN.",
        "Spread/slippage inputs are modeled assumptions, not observed historical quotes.",
        "",
        "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        report.append(
            f"| {row['window']} | {row['capital_initial_usdt']} | {row['cost_scenario']} | {Decimal(row['net_pnl_usdt']):+.4f} | "
            f"{Decimal(row['return_pct']):+.2f}% | {Decimal(row['max_drawdown_pct']):.2f}% | {row['completed_round_trips']} | "
            f"{Decimal(row['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(row['total_fees_usdt']):.4f} |"
        )
    report.extend(["", f"Passed: {gates['passed']}; failed: {gates['failed']}", "", "| Gate | Status | Details |", "|---|---|---|"])
    report.extend([f"| {g['gate']} | {g['status']} | {g['details']} |" for g in gates["checks"]])
    report.extend(["", "The historical archive was inspected in related work and is not a pristine holdout. A historical gate pass would still require a prospectively registered shadow test. NO_TRADE; no live orders, leverage, private endpoints, or main changes.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({
        "status": "COMPLETED", "dataset_sha256": data_sha, "rows": len(items),
        "entries": sum(entries), "exits": sum(exits), "gate": gates["overall"],
        "passed": gates["passed"], "failed": gates["failed"], "main_changed": False
    }, sort_keys=True))
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="donchian-55-20-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__ == "__main__":
    raise SystemExit(main())
