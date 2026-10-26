"""Frozen BTC Ichimoku visible-cloud trend-state replay; research only."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_ICHIMOKU_9_26_52_VISIBLE_KUMO_ATR14"
PROTOCOL = "BTC_ICHIMOKU_VISIBLE_CLOUD_ATR_V1"
TENKAN_PERIOD, KIJUN_PERIOD, SENKOU_B_PERIOD, DISPLACEMENT = 9, 26, 52, 26
ZERO, ONE = Decimal("0"), Decimal("1")


def midpoint(highs: Sequence[Decimal], lows: Sequence[Decimal], i: int, period: int) -> Decimal | None:
    if period <= 0 or i < period - 1 or i >= len(highs) or i >= len(lows):
        return None
    top = max(highs[i - period + 1:i + 1])
    bottom = min(lows[i - period + 1:i + 1])
    return (top + bottom) / Decimal(2)


def displaced_visible_series(raw: Sequence[Decimal | None], displacement: int = DISPLACEMENT) -> list[Decimal | None]:
    """At t, display only raw span from t-displacement, the cloud visible on that candle."""
    if displacement < 0:
        raise ValueError("DISPLACEMENT_MUST_BE_NONNEGATIVE")
    out: list[Decimal | None] = [None] * len(raw)
    for i in range(displacement, len(raw)):
        out[i] = raw[i - displacement]
    return out


def ichimoku_series(items: Sequence[Any]) -> dict[str, list[Decimal | None]]:
    highs = [item.payload.high for item in items]
    lows = [item.payload.low for item in items]
    closes = [item.payload.close for item in items]
    n = len(items)
    tenkan = [midpoint(highs, lows, i, TENKAN_PERIOD) for i in range(n)]
    kijun = [midpoint(highs, lows, i, KIJUN_PERIOD) for i in range(n)]
    span_b_raw = [midpoint(highs, lows, i, SENKOU_B_PERIOD) for i in range(n)]
    span_a_raw: list[Decimal | None] = [
        None if tenkan[i] is None or kijun[i] is None else (tenkan[i] + kijun[i]) / Decimal(2)
        for i in range(n)
    ]
    span_a_visible = displaced_visible_series(span_a_raw, DISPLACEMENT)
    span_b_visible = displaced_visible_series(span_b_raw, DISPLACEMENT)
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a_raw": span_a_raw,
        "span_b_raw": span_b_raw,
        "span_a_visible": span_a_visible,
        "span_b_visible": span_b_visible,
    }


def bullish_state_at(closes, tenkan, kijun, span_a_visible, span_b_visible, i: int) -> bool:
    arrays = (tenkan, kijun, span_a_visible, span_b_visible)
    if i < 0 or i >= len(closes) or any(i >= len(x) for x in arrays):
        return False
    ten, kij, a, b = (x[i] for x in arrays)
    if any(x is None for x in (ten, kij, a, b)):
        return False
    cloud_top = max(a, b)
    return closes[i] > cloud_top and a > b and ten > kij


def exit_condition_at(closes, kijun, span_a_visible, span_b_visible, i: int) -> bool:
    arrays = (kijun, span_a_visible, span_b_visible)
    if i < 0 or i >= len(closes) or any(i >= len(x) for x in arrays):
        return False
    kij, a, b = (x[i] for x in arrays)
    if any(x is None for x in (kij, a, b)):
        return False
    return closes[i] < kij or closes[i] < min(a, b)


def ichimoku_signals_and_weights(items: Sequence[Any]):
    closes = [item.payload.close for item in items]
    highs = [item.payload.high for item in items]
    lows = [item.payload.low for item in items]
    n = len(items)
    ichi = ichimoku_series(items)
    state = [False] * n
    entries = [False] * n
    exits = [False] * n
    for i in range(n):
        state[i] = bullish_state_at(closes, ichi["tenkan"], ichi["kijun"],
                                    ichi["span_a_visible"], ichi["span_b_visible"], i)
        entries[i] = state[i] and not (state[i - 1] if i > 0 else False)
        exits[i] = exit_condition_at(closes, ichi["kijun"], ichi["span_a_visible"], ichi["span_b_visible"], i)

    true_ranges: list[Decimal] = []
    for i in range(n):
        if i == 0:
            true_ranges.append(highs[i] - lows[i])
        else:
            true_ranges.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr14: list[Decimal | None] = [None] * n
    for i in range(13, n):
        atr14[i] = sum(true_ranges[i - 13:i + 1], ZERO) / Decimal(14)

    weights: list[Decimal | None] = [None] * n
    annual_target, max_weight, annualization = Decimal("0.20"), Decimal("0.95"), Decimal(365).sqrt()
    for i in range(n):
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denominator = (atr14[i] / closes[i]) * annualization
            if denominator > ZERO:
                weights[i] = min(max_weight, annual_target / denominator)
    return entries, exits, weights, atr14, ichi, state


def frozen_gate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stress = [x for x in results if x["cost_scenario"] == "STRESS_2"]
    lookup = {(x["window"], x["capital_initial_usdt"]): x for x in stress}
    checks = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *annual)
    def add(name, passed, detail):
        checks.append({"gate": name, "status": "PASS" if passed else "FAIL", "details": detail})
    for capital in engine.CAPITALS:
        cap = str(capital)
        for window in principal:
            row = lookup.get((window, cap))
            add(f"positive_pnl_{window}_{cap}", row is not None and Decimal(row["net_pnl_usdt"]) > ZERO,
                "missing result" if row is None else f"PnL={row['net_pnl_usdt']} USDT")
            add(f"five_roundtrips_{window}_{cap}", row is not None and int(row["completed_round_trips"]) >= 5,
                "missing result" if row is None else f"round trips={row['completed_round_trips']}")
        for window in dd_windows:
            row = lookup.get((window, cap))
            add(f"drawdown_limit_{window}_{cap}", row is not None and Decimal(row["max_drawdown_pct"]) >= Decimal("-15"),
                "missing result" if row is None else f"max DD={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022", cap))
        add(f"2022_nonnegative_{cap}", row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing result" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positives = sum(lookup.get((window, cap)) is not None and Decimal(lookup[(window, cap)]["net_pnl_usdt"]) > ZERO for window in annual)
        add(f"four_positive_annual_windows_{cap}", positives >= 4, f"positive windows={positives}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(x["status"] != "PASS" for x in checks)
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": len(checks)-failed, "failed": failed, "checks": checks,
        "candidate_origin": "Ichimoku 9/26/52 visible-cloud state preregistered in Issue #170; ATR sizing reused from Issue #143.",
        "promotion_note": "Historical archive previously inspected; any historic pass needs prospective shadow validation. NO_TRADE.",
    }


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ichi, state = ichimoku_signals_and_weights(items)
    indicator_rows = []
    for i, item in enumerate(items):
        indicator_rows.append({
            "index": i, "timestamp_utc": item.payload.start.isoformat(), "close": str(item.payload.close),
            **{key: None if series[i] is None else str(series[i]) for key, series in ichi.items()},
            "bullish_kumo_state": state[i], "entry_transition": entries[i], "exit_condition": exits[i],
            "atr14_simple_mean": None if atr14[i] is None else str(atr14[i]),
            "risk_weight": None if weights[i] is None else str(weights[i]),
        })
    indicator_sha = hashlib.sha256(json.dumps(indicator_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evidence = {
        "protocol": PROTOCOL, "preregistration_issue": 170, "candidate": CANDIDATE,
        "symbol": engine.SYMBOL, "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(), "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(), "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS, "observed_bars": len(items),
        "dataset_sha256": data_sha, "indicator_series_sha256": indicator_sha,
        "entry_signal_count": sum(entries), "exit_condition_count": sum(exits), "bullish_state_bars": sum(state),
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot_sha256": rules["sha256"],
            "scope": "current public filter only; historical filters not established",
        },
        "frozen_rule": {
            "periods": {"tenkan": TENKAN_PERIOD, "kijun": KIJUN_PERIOD, "senkou_b": SENKOU_B_PERIOD},
            "displacement": DISPLACEMENT,
            "visible_cloud": "visible span at t equals raw span computed at t-26, never current forward projection",
            "entry": "close>visible cloud top; visible span A>span B; Tenkan>Kijun; false-to-true state transition",
            "exit": "close<Kijun or close<visible cloud bottom",
            "execution": "completed daily close signal; next daily open; terminal close liquidation",
            "atr14_sizing": "simple 14-bar mean TR; min(95%,20%/((ATR14/close)*sqrt(365))) frozen at entry",
            "max_entries_rolling_30d": engine.MAX_ENTRIES_30D,
        },
        "cost_scenarios": [
            {"name": name, "fee_per_fill": str(fee), "assumed_full_spread_bps": str(spread),
             "assumed_slippage_bps_per_fill": str(slippage)}
            for name, fee, spread, slippage in engine.SCENARIOS
        ],
        "retrospective_selection_warning": True, "data": records, "indicator_rows": indicator_rows,
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for window, start, end in engine.WINDOWS:
        for capital in engine.CAPITALS:
            for cost, fee, spread, slippage in engine.SCENARIOS:
                result, fills, curve = engine._simulate(items, rules, entries, exits, weights, atr14, ichi["kijun"],
                                                        window, start, end, capital, cost, fee, spread, slippage)
                for fill in fills:
                    if fill.get("side") in ("BUY", "REJECTED_BUY"):
                        fill["reason"] = "Bullish Ichimoku state: close above visible Kumo, Span A>Span B, Tenkan>Kijun; ATR weight frozen at signal close"
                    elif fill.get("side") == "SELL":
                        fill["reason"] = "Close below Kijun or visible Kumo bottom; next-open or terminal close"
                buy_hold, buy_hold_fees = engine._buy_hold(items, rules, start, end, capital, fee, spread, slippage)
                result.update({
                    "btc_buy_hold_final_equity_usdt": str(buy_hold), "btc_buy_hold_net_pnl_usdt": str(buy_hold-capital),
                    "btc_buy_hold_fees_usdt": str(buy_hold_fees), "cash_final_equity_usdt": str(capital),
                    "dataset_sha256": data_sha,
                })
                results.append(result)
                all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **fill} for fill in fills)
                all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **point} for point in curve)
    for filename, rows in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with (output / filename).open("w", newline="", encoding="utf-8") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            else:
                handle.write("\n")
    gates = frozen_gate(results)
    evidence["strategy_gate"] = gates["overall"]
    evidence["output_counts"] = {"summary_rows": len(results), "fill_rows": len(all_fills), "equity_rows": len(all_curves)}
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    (output / "gate_results.json").write_text(json.dumps(gates, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report = [
        "# BTC Ichimoku visible-cloud trend-state + ATR14 sizing", "",
        "Frozen economic gate: " + gates["overall"], "",
        f"Daily candles={len(items)}; dataset SHA-256={data_sha}; indicator SHA-256={indicator_sha}.",
        f"Entry signals={sum(entries)}; exit-condition bars={sum(exits)}; bullish-state bars={sum(state)}.",
        f"Market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}.",
        "Historical filters, point-in-time availability and executable historic quotes remain UNKNOWN.",
        "Spread/slippage are assumptions, not observed historic order-book quotes.", "",
        "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees USDT |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        report.append(f"| {row['window']} | {row['capital_initial_usdt']} | {row['cost_scenario']} | {Decimal(row['net_pnl_usdt']):+.4f} | {Decimal(row['return_pct']):+.2f}% | {Decimal(row['max_drawdown_pct']):.2f}% | {row['completed_round_trips']} | {Decimal(row['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(row['total_fees_usdt']):.4f} |")
    report.extend(["", f"Passed: {gates['passed']}; failed: {gates['failed']}.", "",
                   "| Gate | Status | Details |", "|---|---|---|"])
    report.extend([f"| {x['gate']} | {x['status']} | {x['details']} |" for x in gates["checks"]])
    report.extend(["", "The historical archive was inspected previously; no pristine holdout claim. Any historic pass still needs prospective shadow validation.",
                   "No live orders, leverage, private endpoints or main changes. NO_TRADE.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "rows": len(items), "dataset_sha256": data_sha,
                      "entries": sum(entries), "exits": sum(exits), "state_bars": sum(state),
                      "gate": gates["overall"], "passed": gates["passed"], "failed": gates["failed"],
                      "main_changed": False}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="ichimoku-visible-cloud-atr-output")
    return run(Path(parser.parse_args(argv).output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
