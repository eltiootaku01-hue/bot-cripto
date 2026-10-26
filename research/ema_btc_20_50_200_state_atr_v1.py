"""Frozen BTC EMA20/EMA50/EMA200 bullish-stack state-transition replay; research only."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_EMA20_50_200_BULLISH_STACK_ATR14_TARGET20"
PROTOCOL = "BTC_EMA20_50_200_STATE_ATR_V1"
FAST_PERIOD, MEDIUM_PERIOD, SLOW_PERIOD = 20, 50, 200
ZERO, ONE = Decimal("0"), Decimal("1")

def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Recursive EMA seeded from first close; each output uses only past/current closes."""
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = ONE - alpha
    current = values[0]
    result: list[Decimal | None] = [None] * len(values)
    for i, value in enumerate(values):
        if i:
            current = alpha * value + complement * current
        if i >= period - 1:
            result[i] = current
    return result

def ema_state_signals_and_weights(items: Sequence[Any]):
    closes = [row.payload.close for row in items]
    highs = [row.payload.high for row in items]
    lows = [row.payload.low for row in items]
    count = len(items)
    ema20 = ema_series(closes, FAST_PERIOD)
    ema50 = ema_series(closes, MEDIUM_PERIOD)
    ema200 = ema_series(closes, SLOW_PERIOD)

    true_ranges: list[Decimal] = []
    for i in range(count):
        true_range = highs[i] - lows[i] if i == 0 else max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        true_ranges.append(true_range)

    atr14: list[Decimal | None] = [None] * count
    for i in range(13, count):
        atr14[i] = sum(true_ranges[i-13:i+1], ZERO) / Decimal(14)

    bullish_state = [False] * count
    entries = [False] * count
    exits = [False] * count
    weights: list[Decimal | None] = [None] * count
    annual_target = Decimal("0.20")
    max_weight = Decimal("0.95")
    annualization = Decimal(365).sqrt()

    for i in range(SLOW_PERIOD - 1, count):
        e20, e50, e200 = ema20[i], ema50[i], ema200[i]
        if e20 is None or e50 is None or e200 is None:
            continue
        bullish_state[i] = closes[i] > e200 and e20 > e50 > e200
        # At index 199 all 200 close observations are available; the prior undefined
        # regime is treated as non-bullish, so the first valid bullish stack can enter.
        prior_bullish = bullish_state[i-1] if i > SLOW_PERIOD - 1 else False
        entries[i] = bullish_state[i] and not prior_bullish
        exits[i] = e20 <= e50 or closes[i] < e200

        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denominator = (atr14[i] / closes[i]) * annualization
            if denominator > ZERO:
                weights[i] = min(max_weight, annual_target / denominator)

    return entries, exits, weights, atr14, ema20, ema50, ema200, bullish_state

def frozen_gate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    lookup = {(r["window"], r["capital_initial_usdt"]): r for r in stress}
    checks: list[dict[str, str]] = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    yearly = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *yearly)

    def add(name: str, passed: bool, details: str) -> None:
        checks.append({"gate": name, "status": "PASS" if passed else "FAIL", "details": details})

    for capital in engine.CAPITALS:
        cap = str(capital)
        for window in principal:
            row = lookup.get((window, cap))
            add(f"positive_pnl_{window}_{cap}",
                row is not None and Decimal(row["net_pnl_usdt"]) > ZERO,
                "missing result" if row is None else f"PnL={row['net_pnl_usdt']} USDT")
            add(f"five_roundtrips_{window}_{cap}",
                row is not None and int(row["completed_round_trips"]) >= 5,
                "missing result" if row is None else f"round trips={row['completed_round_trips']}")
        for window in dd_windows:
            row = lookup.get((window, cap))
            add(f"drawdown_limit_{window}_{cap}",
                row is not None and Decimal(row["max_drawdown_pct"]) >= Decimal("-15"),
                "missing result" if row is None else f"max drawdown={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022", cap))
        add(f"2022_nonnegative_{cap}",
            row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing result" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positive = sum(
            lookup.get((window, cap)) is not None
            and Decimal(lookup[(window, cap)]["net_pnl_usdt"]) > ZERO
            for window in yearly
        )
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive windows={positive}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}",
            recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}",
            recent is not None and Decimal(recent["net_pnl_usdt"]) >
            Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else
            f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")

    failed = sum(x["status"] != "PASS" for x in checks)
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": len(checks) - failed,
        "failed": failed,
        "checks": checks,
        "candidate_origin": "EMA20/50/200 state transition preregistered in Issue #165; ATR risk overlay reuses frozen Issue #143 sizing.",
        "promotion_note": "Previously inspected historical archive; any historical pass still requires prospective shadow validation. NO_TRADE remains.",
    }

def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ema20, ema50, ema200, bullish_state = ema_state_signals_and_weights(items)
    indicator_rows = []
    for i, item in enumerate(items):
        indicator_rows.append({
            "index": i,
            "timestamp_utc": item.payload.start.isoformat(),
            "close": str(item.payload.close),
            "ema20": None if ema20[i] is None else str(ema20[i]),
            "ema50": None if ema50[i] is None else str(ema50[i]),
            "ema200": None if ema200[i] is None else str(ema200[i]),
            "atr14": None if atr14[i] is None else str(atr14[i]),
            "risk_weight": None if weights[i] is None else str(weights[i]),
            "bullish_stack": bullish_state[i],
            "entry_transition": entries[i],
            "exit_condition": exits[i],
        })
    indicator_sha = hashlib.sha256(
        json.dumps(indicator_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    evidence = {
        "protocol": PROTOCOL,
        "preregistration_issue": 165,
        "candidate": CANDIDATE,
        "symbol": engine.SYMBOL,
        "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(),
        "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(),
        "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS,
        "observed_bars": len(items),
        "dataset_sha256": data_sha,
        "indicator_series_sha256": indicator_sha,
        "entry_transition_count": sum(entries),
        "exit_condition_count": sum(exits),
        "bullish_state_bars": sum(bullish_state),
        "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]),
            "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]),
            "snapshot_sha256": rules["sha256"],
            "scope": "current public filter only; historical filters not established",
        },
        "frozen_rule": {
            "ema_formula": "recursive EMA(alpha=2/(period+1)); seed is first close",
            "periods": [FAST_PERIOD, MEDIUM_PERIOD, SLOW_PERIOD],
            "bullish_stack": "close > EMA200 and EMA20 > EMA50 > EMA200",
            "entry": "bullish stack true at t and false at t-1; first valid 200-bar state may be first transition",
            "exit": "EMA20 <= EMA50 or close < EMA200",
            "execution": "signal after completed daily close; execute at next daily open; terminal close liquidation",
            "atr14": "simple 14-bar mean of true range",
            "risk_sizing": "min(95%, 20%/((ATR14/close)*sqrt(365))); frozen at signal close",
            "max_entries_rolling_30d": engine.MAX_ENTRIES_30D,
        },
        "cost_scenarios": [
            {"name": name, "fee_per_fill": str(fee), "assumed_full_spread_bps": str(spread),
             "assumed_slippage_bps_per_fill": str(slippage)}
            for name, fee, spread, slippage in engine.SCENARIOS
        ],
        "retrospective_selection_warning": True,
        "data": records,
        "indicator_rows": indicator_rows,
    }
    (output / "dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8"
    )

    results, all_fills, all_curves = [], [], []
    for window, start, end in engine.WINDOWS:
        for capital in engine.CAPITALS:
            for cost, fee, spread, slippage in engine.SCENARIOS:
                result, fills, curve = engine._simulate(
                    items, rules, entries, exits, weights, atr14, ema200,
                    window, start, end, capital, cost, fee, spread, slippage
                )
                for fill in fills:
                    if fill.get("side") in ("BUY", "REJECTED_BUY"):
                        fill["reason"] = (
                            "EMA20/EMA50/EMA200 bullish-stack false-to-true transition; "
                            "ATR14 risk weight frozen at signal close; next-open fill"
                        )
                    elif fill.get("side") == "SELL":
                        fill["reason"] = "EMA20 <= EMA50 or close < EMA200; next-open fill"
                buy_hold, buy_hold_fees = engine._buy_hold(items, rules, start, end, capital, fee, spread, slippage)
                result.update({
                    "btc_buy_hold_final_equity_usdt": str(buy_hold),
                    "btc_buy_hold_net_pnl_usdt": str(buy_hold - capital),
                    "btc_buy_hold_fees_usdt": str(buy_hold_fees),
                    "cash_final_equity_usdt": str(capital),
                    "dataset_sha256": data_sha,
                })
                results.append(result)
                all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **fill} for fill in fills)
                all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **point} for point in curve)

    for filename, rows in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with (output / filename).open("w", newline="", encoding="utf-8") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            else:
                handle.write("\n")

    gates = frozen_gate(results)
    evidence["strategy_gate"] = gates["overall"]
    evidence["output_counts"] = {
        "summary_rows": len(results),
        "fill_rows": len(all_fills),
        "equity_rows": len(all_curves),
    }
    (output / "dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8"
    )
    (output / "gate_results.json").write_text(
        json.dumps(gates, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    report = [
        "# BTC EMA20/EMA50/EMA200 bullish-stack state transition + ATR14 sizing",
        "",
        "Frozen economic gate: " + gates["overall"],
        "",
        f"Dataset: {len(items)} daily BTCUSDT candles; SHA-256 {data_sha}.",
        f"Indicator-series SHA-256: {indicator_sha}.",
        f"Entry transitions={sum(entries)}; exit-condition bars={sum(exits)}; bullish-stack bars={sum(bullish_state)}.",
        f"Unknown market-data completeness={unknown_comp}; unknown candle finality={unknown_final}.",
        "Historical filters, point-in-time data availability and historic executable quotes remain UNKNOWN.",
        "Spread/slippage are modeled assumptions, not observed historic order-book quotes.",
        "",
        "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees USDT |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        report.append(
            f"| {row['window']} | {row['capital_initial_usdt']} | {row['cost_scenario']} | "
            f"{Decimal(row['net_pnl_usdt']):+.4f} | {Decimal(row['return_pct']):+.2f}% | "
            f"{Decimal(row['max_drawdown_pct']):.2f}% | {row['completed_round_trips']} | "
            f"{Decimal(row['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(row['total_fees_usdt']):.4f} |"
        )
    report.extend([
        "",
        f"Passed: {gates['passed']}; failed: {gates['failed']}.",
        "",
        "| Gate | Status | Details |",
        "|---|---|---|",
    ])
    report.extend([f"| {item['gate']} | {item['status']} | {item['details']} |" for item in gates["checks"]])
    report.extend([
        "",
        "This historical archive was previously inspected; a historical pass is not a pristine holdout.",
        "Any eventual pass still requires prospective shadow validation. No real orders, leverage, private endpoints or main changes. NO_TRADE.",
        "",
    ])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({
        "status": "COMPLETED",
        "rows": len(items),
        "dataset_sha256": data_sha,
        "entries": sum(entries),
        "exits": sum(exits),
        "gate": gates["overall"],
        "passed": gates["passed"],
        "failed": gates["failed"],
        "main_changed": False,
    }, sort_keys=True))
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="ema-state-atr-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__ == "__main__":
    raise SystemExit(main())
