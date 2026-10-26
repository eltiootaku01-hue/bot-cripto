"""Frozen BTC KAMA efficiency-state transition + EMA200 and ATR14 sizing replay."""
from __future__ import annotations

import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_KAMA_ER10_BULL_STATE_EMA200_ATR14"
PROTOCOL = "BTC_KAMA_EFFICIENCY_STATE_EMA200_ATR_V1"
ER_PERIOD = 10
FAST_PERIOD = 2
SLOW_PERIOD = 30
SLOPE_LOOKBACK = 5
ER_MIN = Decimal("0.35")
EMA_PERIOD = 200
ZERO, ONE = Decimal("0"), Decimal("1")


def ema_series(values: Sequence[Decimal], period: int = EMA_PERIOD) -> list[Decimal | None]:
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


def efficiency_ratio(closes: Sequence[Decimal], i: int, period: int = ER_PERIOD) -> Decimal | None:
    """ER = absolute net movement / sum of absolute movements over past period bars."""
    if period <= 0 or i < period or i >= len(closes):
        return None
    net = abs(closes[i] - closes[i - period])
    path = sum((abs(closes[j] - closes[j - 1]) for j in range(i - period + 1, i + 1)), ZERO)
    if path <= ZERO:
        return None
    return net / path


def kama_series(closes: Sequence[Decimal], period: int = ER_PERIOD,
                fast_period: int = FAST_PERIOD, slow_period: int = SLOW_PERIOD) -> tuple[list[Decimal | None], list[Decimal | None]]:
    """KAMA seeded from the first close; adaptive smoothing only once ER is available."""
    if not closes:
        return [], []
    if period <= 0 or fast_period <= 0 or slow_period <= 0:
        raise ValueError("KAMA_PERIODS_MUST_BE_POSITIVE")
    fast_sc = Decimal(2) / Decimal(fast_period + 1)
    slow_sc = Decimal(2) / Decimal(slow_period + 1)
    er = [efficiency_ratio(closes, i, period) for i in range(len(closes))]
    kama: list[Decimal | None] = [None] * len(closes)
    kama[0] = closes[0]
    for i in range(1, len(closes)):
        prior = kama[i - 1]
        assert prior is not None
        if er[i] is None:
            kama[i] = prior
            continue
        smoothing = (er[i] * (fast_sc - slow_sc) + slow_sc) ** 2
        kama[i] = prior + smoothing * (closes[i] - prior)
    return er, kama


def bullish_state_at(closes: Sequence[Decimal], er: Sequence[Decimal | None],
                     kama: Sequence[Decimal | None], ema200: Sequence[Decimal | None],
                     i: int) -> bool:
    if i < max(EMA_PERIOD - 1, ER_PERIOD, SLOPE_LOOKBACK) or i >= len(closes):
        return False
    if any(i >= len(values) for values in (er, kama, ema200)):
        return False
    if er[i] is None or kama[i] is None or kama[i - SLOPE_LOOKBACK] is None or ema200[i] is None:
        return False
    return (
        er[i] >= ER_MIN
        and closes[i] > kama[i]
        and kama[i] > kama[i - SLOPE_LOOKBACK]
        and closes[i] > ema200[i]
    )


def exit_condition_at(closes: Sequence[Decimal], kama: Sequence[Decimal | None],
                      ema200: Sequence[Decimal | None], i: int) -> bool:
    if i < SLOPE_LOOKBACK or i >= len(closes) or i >= len(kama) or i >= len(ema200):
        return False
    if kama[i] is None or kama[i - SLOPE_LOOKBACK] is None or ema200[i] is None:
        return False
    return closes[i] < ema200[i] or kama[i] <= kama[i - SLOPE_LOOKBACK]


def kama_signals_and_weights(items: Sequence[Any]):
    closes = [row.payload.close for row in items]
    highs = [row.payload.high for row in items]
    lows = [row.payload.low for row in items]
    n = len(items)
    ema200 = ema_series(closes, EMA_PERIOD)
    er, kama = kama_series(closes, ER_PERIOD, FAST_PERIOD, SLOW_PERIOD)

    true_ranges: list[Decimal] = []
    for i in range(n):
        if i == 0:
            true_ranges.append(highs[i] - lows[i])
        else:
            true_ranges.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            ))
    atr14: list[Decimal | None] = [None] * n
    for i in range(13, n):
        atr14[i] = sum(true_ranges[i - 13:i + 1], ZERO) / Decimal(14)

    state = [False] * n
    entries = [False] * n
    exits = [False] * n
    weights: list[Decimal | None] = [None] * n
    annual_target, max_weight, annualization = Decimal("0.20"), Decimal("0.95"), Decimal(365).sqrt()

    for i in range(n):
        state[i] = bullish_state_at(closes, er, kama, ema200, i)
        prev_state = state[i - 1] if i > 0 else False
        entries[i] = state[i] and not prev_state
        exits[i] = exit_condition_at(closes, kama, ema200, i)
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denominator = (atr14[i] / closes[i]) * annualization
            if denominator > ZERO:
                weights[i] = min(max_weight, annual_target / denominator)
    return entries, exits, weights, atr14, ema200, er, kama, state


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
        positive = sum(lookup.get((window, cap)) is not None and Decimal(lookup[(window, cap)]["net_pnl_usdt"]) > ZERO for window in annual)
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive windows={positive}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(x["status"] != "PASS" for x in checks)
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": len(checks) - failed, "failed": failed, "checks": checks,
        "candidate_origin": "KAMA efficiency-state preregistered in Issue #169; ATR sizing reused unchanged from Issue #143.",
        "promotion_note": "Historical archive and related trend ideas were inspected before; any historical pass requires prospective shadow validation. NO_TRADE remains.",
    }


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ema200, er, kama, state = kama_signals_and_weights(items)
    indicators = []
    for i, item in enumerate(items):
        indicators.append({
            "index": i, "timestamp_utc": item.payload.start.isoformat(), "close": str(item.payload.close),
            "er10": None if er[i] is None else str(er[i]),
            "kama10_fast2_slow30": None if kama[i] is None else str(kama[i]),
            "kama_slope5": None if i < SLOPE_LOOKBACK or kama[i] is None or kama[i-SLOPE_LOOKBACK] is None else str(kama[i]-kama[i-SLOPE_LOOKBACK]),
            "ema200": None if ema200[i] is None else str(ema200[i]),
            "atr14_simple_mean": None if atr14[i] is None else str(atr14[i]),
            "risk_weight": None if weights[i] is None else str(weights[i]),
            "bullish_state": state[i], "entry_transition": entries[i], "exit_condition": exits[i],
        })
    indicator_sha = hashlib.sha256(json.dumps(indicators, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    evidence = {
        "protocol": PROTOCOL, "preregistration_issue": 169, "candidate": CANDIDATE,
        "symbol": engine.SYMBOL, "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(), "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(), "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS, "observed_bars": len(items),
        "dataset_sha256": data_sha, "indicator_series_sha256": indicator_sha,
        "entry_signal_count": sum(entries), "exit_condition_count": sum(exits), "bullish_state_bars": sum(state),
        "er_available_count": sum(x is not None for x in er), "kama_available_count": sum(x is not None for x in kama),
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot_sha256": rules["sha256"],
            "scope": "current public filter only; historical filters not established",
        },
        "frozen_rule": {
            "er_formula": "abs(close[t]-close[t-10])/sum(abs(close[k]-close[k-1]), k=t-9..t)",
            "kama": "seed first close; SC=(ER*(2/3-2/31)+2/31)^2; KAMA=prior+SC*(close-prior)",
            "bullish_state": "ER>=0.35; close>KAMA; KAMA[t]>KAMA[t-5]; close>EMA200",
            "entry": "false-to-true bullish state transition, next daily open",
            "exit": "KAMA[t]<=KAMA[t-5] or close<EMA200, next daily open",
            "atr14_sizing": "simple 14-bar mean TR; min(95%,20%/((ATR14/close)*sqrt(365))) frozen at entry",
            "max_entries_rolling_30d": engine.MAX_ENTRIES_30D,
        },
        "cost_scenarios": [
            {"name": name, "fee_per_fill": str(fee), "assumed_full_spread_bps": str(spread), "assumed_slippage_bps_per_fill": str(slippage)}
            for name, fee, spread, slippage in engine.SCENARIOS
        ],
        "retrospective_selection_warning": True, "data": records, "indicator_rows": indicators,
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for window, start, end in engine.WINDOWS:
        for capital in engine.CAPITALS:
            for cost, fee, spread, slippage in engine.SCENARIOS:
                result, fills, curve = engine._simulate(items, rules, entries, exits, weights, atr14, ema200,
                                                        window, start, end, capital, cost, fee, spread, slippage)
                for fill in fills:
                    if fill.get("side") in ("BUY", "REJECTED_BUY"):
                        fill["reason"] = "ER10>=0.35, close>KAMA, rising KAMA5 slope and close>EMA200; ATR sizing frozen at signal close"
                    elif fill.get("side") == "SELL":
                        fill["reason"] = "KAMA slope failed or close<EMA200; next-open or terminal close"
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
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            else:
                handle.write("\n")
    gates = frozen_gate(results)
    evidence["strategy_gate"] = gates["overall"]
    evidence["output_counts"] = {"summary_rows": len(results), "fill_rows": len(all_fills), "equity_rows": len(all_curves)}
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    (output / "gate_results.json").write_text(json.dumps(gates, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    report = [
        "# BTC KAMA efficiency-state transition + EMA200 + ATR14 sizing", "",
        "Frozen economic gate: " + gates["overall"], "",
        f"Daily candles={len(items)}; dataset SHA-256={data_sha}; indicator SHA-256={indicator_sha}.",
        f"Entry signals={sum(entries)}; exit-condition bars={sum(exits)}; bullish-state bars={sum(state)}.",
        f"ER available={evidence['er_available_count']}; KAMA available={evidence['kama_available_count']}.",
        f"Market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}.",
        "Historical filters, point-in-time availability and historic executable quotes remain UNKNOWN.",
        "Spread/slippage are modeled assumptions, not observed historical order-book quotes.", "",
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
    report.extend(["", f"Passed: {gates['passed']}; failed: {gates['failed']}.", "",
                   "| Gate | Status | Details |", "|---|---|---|"])
    report.extend([f"| {x['gate']} | {x['status']} | {x['details']} |" for x in gates["checks"]])
    report.extend(["", "The historical archive and related trend families were previously inspected; this is not a pristine holdout.",
                   "Any historical pass still needs prospective shadow validation. No live orders, leverage, private endpoints or main changes. NO_TRADE.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "rows": len(items), "dataset_sha256": data_sha,
                      "entries": sum(entries), "exits": sum(exits), "state_bars": sum(state),
                      "gate": gates["overall"], "passed": gates["passed"], "failed": gates["failed"],
                      "main_changed": False}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="kama-efficiency-atr-output")
    return run(Path(parser.parse_args(argv).output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
