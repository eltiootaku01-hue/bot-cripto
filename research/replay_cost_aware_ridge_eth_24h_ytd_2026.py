"""Frozen cost-aware walk-forward ridge return filter for ETHUSDT; virtual only."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record, _validate_history

UTC = timezone.utc
INTERVAL = "4h"
BAR = timedelta(hours=4)
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
FOLDS = (
    ("FULL", START, END),
    ("FOLD_1", START, datetime(2026, 4, 1, 0, 0, tzinfo=UTC)),
    ("FOLD_2", datetime(2026, 4, 1, 0, 0, tzinfo=UTC), datetime(2026, 7, 1, 0, 0, tzinfo=UTC)),
    ("FOLD_3", datetime(2026, 7, 1, 0, 0, tzinfo=UTC), END),
)
WARMUP_BARS = 720
WARMUP_START = START - BAR * WARMUP_BARS
EVAL_BARS = int((END - START) / BAR)
EXPECTED_BARS = WARMUP_BARS + EVAL_BARS
ZERO, ONE, HUNDRED, BPS = Decimal("0"), Decimal("1"), Decimal("100"), Decimal("10000")
POSITION_FRACTION = Decimal("0.95")
SIGNAL_CADENCE = 6
HORIZON = 6
MIN_TRAINING_ROWS = 50
MAX_TRAINING_ROWS = 180
RIDGE_LAMBDA = 10.0
EDGE_BUFFER = Decimal("0.0025")  # fixed 25bp buffer above modeled round-trip break-even
MIN_QTY = Decimal("0.0001")
QTY_STEP = Decimal("0.0001")
MIN_NOTIONAL = Decimal("5")
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
ASSETS = ("ETHUSDT", "BTCUSDT")
FEATURE_NAMES = (
    "eth_ret_1", "eth_ret_6", "eth_ret_42", "eth_ret_180",
    "btc_ret_1", "btc_ret_6", "btc_ret_42", "btc_ret_180",
    "relative_ret_6", "relative_ret_42", "relative_ret_180",
    "eth_dist_ema200", "btc_dist_ema200", "eth_realized_vol_42", "btc_realized_vol_42",
)


def _fetch(symbol: str):
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo")
    )
    metadata.resolve_active(symbol)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=symbol, interval=INTERVAL,
        start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000), page_limit=1000, max_requests=5,
    )
    items = sorted((x for x in acquired if WARMUP_START <= x.payload.start < END),
                   key=lambda x: x.payload.start)
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED {symbol}: expected {EXPECTED_BARS} contiguous 4h bars, got {len(items)}; "
            f"missing={sorted(set(expected)-set(observed))[:12]}; "
            f"unexpected={sorted(set(observed)-set(expected))[:12]}"
        )
    accepted, excluded, unknown_completeness, unknown_finality = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED {symbol}: accepted={len(accepted)}, excluded={excluded}")
    if any(x.instrument.instrument_id != f"binance:SPOT:{symbol}" for x in items):
        raise RuntimeError(f"FAIL-CLOSED {symbol}: instrument identity mismatch")
    records = [_market_data_record(x) for x in items]
    blob = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return items, records, hashlib.sha256(blob).hexdigest(), unknown_completeness, unknown_finality


def _ema(values: list[Decimal], period: int):
    alpha = Decimal("2") / Decimal(period + 1)
    result: list[Decimal | None] = [None] * len(values)
    current = values[0]
    for i, value in enumerate(values):
        if i:
            current = alpha * value + (ONE - alpha) * current
        if i >= period - 1:
            result[i] = current
    return result


def _std_return(values: list[Decimal], i: int, window: int = 42) -> float | None:
    if i < window:
        return None
    returns = [float(values[j] / values[j-1] - ONE) for j in range(i-window+1, i+1)]
    return statistics.pstdev(returns)


def _ret(values: list[Decimal], i: int, lag: int) -> float | None:
    if i < lag:
        return None
    return float(values[i] / values[i-lag] - ONE)


def _make_features(closes):
    emas = {asset: _ema(closes[asset], 200) for asset in ASSETS}
    rows: list[list[float] | None] = [None] * len(closes["ETHUSDT"])
    for i in range(len(rows)):
        needed = max(200, 180)
        if i < needed or emas["ETHUSDT"][i] is None or emas["BTCUSDT"][i] is None:
            continue
        eth_returns = {lag: _ret(closes["ETHUSDT"], i, lag) for lag in (1, 6, 42, 180)}
        btc_returns = {lag: _ret(closes["BTCUSDT"], i, lag) for lag in (1, 6, 42, 180)}
        ev = _std_return(closes["ETHUSDT"], i)
        bv = _std_return(closes["BTCUSDT"], i)
        if ev is None or bv is None or any(v is None for v in eth_returns.values()) or any(v is None for v in btc_returns.values()):
            continue
        rows[i] = [
            eth_returns[1], eth_returns[6], eth_returns[42], eth_returns[180],
            btc_returns[1], btc_returns[6], btc_returns[42], btc_returns[180],
            eth_returns[6] - btc_returns[6], eth_returns[42] - btc_returns[42],
            eth_returns[180] - btc_returns[180],
            float(closes["ETHUSDT"][i] / emas["ETHUSDT"][i] - ONE),
            float(closes["BTCUSDT"][i] / emas["BTCUSDT"][i] - ONE),
            ev, bv,
        ]
    return rows


def _solve(matrix: list[list[float]], target: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting on a small regularized system."""
    n = len(target)
    a = [row[:] + [target[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ArithmeticError("ridge system is singular despite regularization")
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= scale
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if factor:
                for j in range(col, n + 1):
                    a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def _ridge_predict(training_x: list[list[float]], training_y: list[float], row: list[float]):
    n = len(training_x)
    p = len(row)
    x_mean = [sum(x[j] for x in training_x) / n for j in range(p)]
    x_scale = []
    for j in range(p):
        var = sum((x[j] - x_mean[j]) ** 2 for x in training_x) / n
        x_scale.append(math.sqrt(var) if var > 1e-18 else 1.0)
    y_mean = sum(training_y) / n
    standardized = [[(x[j] - x_mean[j]) / x_scale[j] for j in range(p)] for x in training_x]
    gram = [[0.0] * p for _ in range(p)]
    rhs = [0.0] * p
    for x, y in zip(standardized, training_y):
        centered_y = y - y_mean
        for j in range(p):
            rhs[j] += x[j] * centered_y
            for k in range(p):
                gram[j][k] += x[j] * x[k]
    for j in range(p):
        gram[j][j] += RIDGE_LAMBDA
    coefficients = _solve(gram, rhs)
    standardized_row = [(row[j] - x_mean[j]) / x_scale[j] for j in range(p)]
    prediction = y_mean + sum(standardized_row[j] * coefficients[j] for j in range(p))
    return prediction, coefficients, x_mean, x_scale, y_mean


def _break_even_gross_return(fee: Decimal, spread_bps: Decimal, slippage_bps: Decimal) -> Decimal:
    adverse = (slippage_bps + spread_bps / Decimal("2")) / BPS
    return ((ONE + adverse) * (ONE + fee) / ((ONE - adverse) * (ONE - fee))) - ONE


def _build_predictions(items, closes, opens, features, hashes):
    index_by_start = {x.payload.start: i for i, x in enumerate(items["ETHUSDT"])}
    eth_opens = opens["ETHUSDT"]
    predicted = {}
    audit = []
    feature_start = 200
    for i in range(len(items["ETHUSDT"])):
        if i % SIGNAL_CADENCE != 5 or features[i] is None:
            continue
        # A label may enter training only after its exit open (j+7) has occurred.
        candidates = [
            j for j in range(feature_start, i - 6)
            if j % SIGNAL_CADENCE == 5
            and features[j] is not None
            and j + 7 <= i
        ]
        candidates = candidates[-MAX_TRAINING_ROWS:]
        if len(candidates) < MIN_TRAINING_ROWS:
            audit.append({
                "index": i, "timestamp_utc": items["ETHUSDT"][i].payload.end.isoformat(),
                "training_rows": len(candidates), "prediction_gross_return_pct": "",
                "decision": "CASH_INSUFFICIENT_MATURE_TRAINING_ROWS",
                "latest_training_label_exit_index": max((j + 7 for j in candidates), default=None),
            })
            continue
        x = [features[j] for j in candidates]
        y = [float(eth_opens[j + 7] / eth_opens[j + 1] - ONE) for j in candidates]
        try:
            prediction, coefs, x_mean, x_scale, y_mean = _ridge_predict(x, y, features[i])
        except ArithmeticError:
            audit.append({
                "index": i, "timestamp_utc": items["ETHUSDT"][i].payload.end.isoformat(),
                "training_rows": len(candidates), "prediction_gross_return_pct": "",
                "decision": "CASH_RIDGE_SOLVER_FAILURE",
                "latest_training_label_exit_index": max(j + 7 for j in candidates),
            })
            continue
        latest_exit = max(j + 7 for j in candidates)
        if latest_exit > i:
            raise RuntimeError(f"LOOKAHEAD_GUARD: training label at index {latest_exit} exceeds decision {i}")
        predicted[i] = {"prediction": prediction, "training_rows": len(candidates)}
        audit.append({
            "index": i, "timestamp_utc": items["ETHUSDT"][i].payload.end.isoformat(),
            "training_rows": len(candidates), "prediction_gross_return_pct": prediction * 100.0,
            "training_target_mean_pct": y_mean * 100.0, "latest_training_label_exit_index": latest_exit,
            "feature_vector": json.dumps(dict(zip(FEATURE_NAMES, features[i])), sort_keys=True),
            "standardization_mean": json.dumps(x_mean), "standardization_scale": json.dumps(x_scale),
            "ridge_coefficients": json.dumps(coefs), "decision": "PREDICTION_READY",
            "asset_dataset_sha256": json.dumps(hashes, sort_keys=True),
        })
    return predicted, audit, index_by_start


def _adverse(spread: Decimal, slippage: Decimal):
    return (slippage + spread / Decimal("2")) / BPS


def _floor_step(value: Decimal, step: Decimal):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _buy_hold(items, capital, fee, spread, slippage):
    adverse = _adverse(spread, slippage)
    price = items[0].payload.open * (ONE + adverse)
    qty = _floor_step(capital * POSITION_FRACTION / (price * (ONE + fee)), QTY_STEP)
    notional = qty * price
    if qty < MIN_QTY or notional < MIN_NOTIONAL or notional * (ONE + fee) > capital:
        raise RuntimeError("FAIL-CLOSED: ETH buy-and-hold violates fixed lot assumptions")
    buy_fee = notional * fee
    cash = capital - notional - buy_fee
    exit_notional = qty * items[-1].payload.close * (ONE - adverse)
    sell_fee = exit_notional * fee
    return cash + exit_notional - sell_fee, buy_fee + sell_fee


def _simulate(all_items, capital, period_name, period_start, period_end, scenario_name, fee, spread, slippage,
              closes, opens, predictions, index_by_start, hashes):
    eval_items = [x for x in all_items["ETHUSDT"] if period_start <= x.payload.start < period_end]
    if not eval_items:
        raise RuntimeError(f"FAIL-CLOSED: period {period_name} has no evaluation bars")
    expected_start = period_start
    if eval_items[0].payload.start != expected_start:
        raise RuntimeError(f"FAIL-CLOSED: period {period_name} starts at {eval_items[0].payload.start}, expected {expected_start}")
    if period_end == END:
        expected_count = int((period_end - period_start) / BAR)
    else:
        expected_count = int((period_end - period_start) / BAR)
    if len(eval_items) != expected_count:
        raise RuntimeError(f"FAIL-CLOSED: {period_name} got {len(eval_items)} bars, expected {expected_count}")
    first_i = index_by_start[eval_items[0].payload.start]
    last_i = index_by_start[eval_items[-1].payload.start]
    quote, quantity, entry_cost = capital, ZERO, ZERO
    total_fees, exit_index = ZERO, None
    pending_buy = False
    fills, curve, realized = [], [], []
    peak = capital
    signal_count = 0

    for item in eval_items:
        i = index_by_start[item.payload.start]
        candle = item.payload
        # Exit at the scheduled next-open timestamp.
        if quantity > ZERO and exit_index == i:
            adverse = _adverse(spread, slippage)
            sell_price = opens["ETHUSDT"][i] * (ONE - adverse)
            notional = quantity * sell_price
            sell_fee = notional * fee
            proceeds = notional - sell_fee
            pnl = proceeds - entry_cost
            quote += proceeds
            total_fees += sell_fee
            realized.append(pnl)
            fills.append({
                "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
                "timestamp_utc": candle.start.isoformat(), "side": "SELL", "asset": "ETHUSDT",
                "quantity": str(quantity), "price_usdt": str(sell_price), "fee_usdt": str(sell_fee),
                "realized_pnl_usdt": str(pnl), "reason": "fixed 6-bar holding horizon",
            })
            quantity, entry_cost, exit_index = ZERO, ZERO, None

        if pending_buy:
            pending_buy = False
            if quantity == ZERO:
                adverse = _adverse(spread, slippage)
                buy_price = opens["ETHUSDT"][i] * (ONE + adverse)
                qty = _floor_step(quote * POSITION_FRACTION / (buy_price * (ONE + fee)), QTY_STEP)
                notional = qty * buy_price
                reject = None
                if qty < MIN_QTY:
                    reject = "MIN_QTY"
                elif notional < MIN_NOTIONAL:
                    reject = "MIN_NOTIONAL"
                elif notional * (ONE + fee) > quote:
                    reject = "COSTS_EXCEED_BALANCE"
                if reject:
                    fills.append({
                        "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
                        "timestamp_utc": candle.start.isoformat(), "side": "REJECTED_BUY", "asset": "ETHUSDT",
                        "quantity": str(qty), "price_usdt": str(buy_price), "fee_usdt": "0",
                        "realized_pnl_usdt": "", "reason": reject,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    quantity = qty
                    total_fees += buy_fee
                    exit_index = i + HORIZON
                    fills.append({
                        "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
                        "timestamp_utc": candle.start.isoformat(), "side": "BUY", "asset": "ETHUSDT",
                        "quantity": str(qty), "price_usdt": str(buy_price), "fee_usdt": str(buy_fee),
                        "realized_pnl_usdt": "", "reason": "cost-aware ridge forecast exceeded this scenario's net-edge threshold",
                    })

        adverse_mark = _adverse(spread, slippage)
        equity = quote if quantity == ZERO else quote + quantity * closes["ETHUSDT"][i] * (ONE - adverse_mark) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
            "timestamp_utc": candle.end.isoformat(), "equity_usdt": str(equity),
            "drawdown_pct": str((equity / peak - ONE) * HUNDRED if peak else ZERO),
            "position_eth": str(quantity),
        })

        # Decision at close i; earliest next-open entry is i+1 and fixed exit is i+7.
        if quantity != ZERO or pending_buy or i % SIGNAL_CADENCE != 5 or i + 7 > last_i:
            continue
        signal_count += 1
        record = predictions.get(i)
        if not record:
            continue
        friction = _break_even_gross_return(fee, spread, slippage) + EDGE_BUFFER
        prediction = Decimal(str(record["prediction"]))
        take_trade = prediction > friction
        fills.append({
            "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
            "timestamp_utc": candle.end.isoformat(), "side": "SIGNAL", "asset": "ETHUSDT" if take_trade else "CASH",
            "quantity": "", "price_usdt": "", "fee_usdt": "0", "realized_pnl_usdt": "",
            "reason": f"prediction_gross={prediction:.8f}; required_gross={friction:.8f}; training_rows={record['training_rows']}",
        })
        if take_trade:
            pending_buy = True

    # A trade should normally close before the period ends. If boundary timing ever leaves one open, fail closed rather than hide it.
    if quantity > ZERO:
        last = eval_items[-1].payload
        adverse = _adverse(spread, slippage)
        price = closes["ETHUSDT"][last_i] * (ONE - adverse)
        notional = quantity * price
        sell_fee = notional * fee
        proceeds = notional - sell_fee
        pnl = proceeds - entry_cost
        quote += proceeds
        total_fees += sell_fee
        realized.append(pnl)
        fills.append({
            "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
            "timestamp_utc": last.end.isoformat(), "side": "SELL", "asset": "ETHUSDT",
            "quantity": str(quantity), "price_usdt": str(price), "fee_usdt": str(sell_fee),
            "realized_pnl_usdt": str(pnl), "reason": "terminal liquidation at period end",
        })
        quantity = ZERO
        curve[-1]["equity_usdt"] = str(quote)
        curve[-1]["position_eth"] = "0"
        peak = max(peak, quote)
        curve[-1]["drawdown_pct"] = str((quote / peak - ONE) * HUNDRED if peak else ZERO)

    bh, bh_fees = _buy_hold(eval_items, capital, fee, spread, slippage)
    return {
        "period": period_name, "capital_initial_usdt": str(capital), "cost_scenario": scenario_name,
        "final_equity_usdt": str(quote), "net_pnl_usdt": str(quote - capital),
        "return_pct": str((quote / capital - ONE) * HUNDRED),
        "max_drawdown_pct": str(min((Decimal(x["drawdown_pct"]) for x in curve), default=ZERO)),
        "completed_trades": len(realized),
        "wins": sum(pnl > ZERO for pnl in realized), "losses": sum(pnl < ZERO for pnl in realized),
        "total_fees_usdt": str(total_fees), "signal_count": signal_count,
        "eth_buy_hold_final_equity_usdt": str(bh), "eth_buy_hold_net_pnl_usdt": str(bh - capital),
        "eth_buy_hold_fees_usdt": str(bh_fees), "cash_final_equity_usdt": str(capital),
        **hashes, "evaluation_bars": len(eval_items),
    }, fills, curve


def main():
    out = Path("ridge-output")
    out.mkdir(exist_ok=True)
    print("Frozen cost-aware walk-forward ridge filter; no parameter search; virtual only.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{START.isoformat()}, {END.isoformat()}); {EVAL_BARS} bars; warm-up={WARMUP_BARS} bars.")
    items, raw_records, hashes, meta = {}, {}, {}, {}
    for asset in ASSETS:
        got = _fetch(asset)
        items[asset], raw_records[asset], hashes[asset] = got[0], got[1], got[2]
        meta[asset] = {"completeness_unknown": got[3], "finality_unknown": got[4]}
        print(f"{asset} SHA-256={got[2]}; completeness UNKNOWN={got[3]}; finality UNKNOWN={got[4]}")
    timestamps = {asset: [x.payload.start for x in items[asset]] for asset in ASSETS}
    if timestamps["ETHUSDT"] != timestamps["BTCUSDT"]:
        raise RuntimeError("FAIL-CLOSED: BTC and ETH timestamps do not match")
    combined = json.dumps(raw_records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    combined_sha = hashlib.sha256(combined).hexdigest()
    manifest = {
        "evaluation_start": START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(), "warmup_bars": WARMUP_BARS,
        "interval": INTERVAL, "expected_bars_per_asset": EXPECTED_BARS, "evaluation_bars": EVAL_BARS,
        "asset_dataset_sha256": hashes, "combined_dataset_sha256": combined_sha,
        "metadata": meta, "data": raw_records,
    }
    Path(out / "dataset-evidence.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    closes = {asset: [x.payload.close for x in items[asset]] for asset in ASSETS}
    opens = {asset: [x.payload.open for x in items[asset]] for asset in ASSETS}
    features = _make_features(closes)
    canonical_hashes = {"eth_dataset_sha256": hashes["ETHUSDT"], "btc_dataset_sha256": hashes["BTCUSDT"],
                        "combined_dataset_sha256": combined_sha}
    predictions, training_audit, index_by_start = _build_predictions(items, closes, opens, features, canonical_hashes)
    with open(out / "predictions.csv", "w", newline="", encoding="utf-8") as f:
        fields = sorted({key for row in training_audit for key in row})
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(training_audit)
    summary, fills, curves = [], [], []
    for period_name, period_start, period_end in FOLDS:
        for capital in (Decimal("60"), Decimal("100")):
            for scenario_name, fee, spread, slippage in SCENARIOS:
                result, local_fills, local_curve = _simulate(
                    items, capital, period_name, period_start, period_end,
                    scenario_name, fee, spread, slippage, closes, opens, predictions, index_by_start, canonical_hashes
                )
                summary.append(result)
                fills.extend(local_fills)
                curves.extend(local_curve)
                print(
                    f"RESULT | {period_name} | capital={capital} | {scenario_name} | "
                    f"PnL={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                    f"DD={Decimal(result['max_drawdown_pct']):.3f}% | trades={result['completed_trades']} "
                    f"W/L={result['wins']}/{result['losses']} | fees={Decimal(result['total_fees_usdt']):.4f} | "
                    f"ETH B&H={Decimal(result['eth_buy_hold_net_pnl_usdt']):+.4f}"
                )
    for fname, data in (("summary.csv", summary), ("fills.csv", fills), ("equity.csv", curves)):
        with open(out / fname, "w", newline="", encoding="utf-8") as f:
            fields = sorted({key for row in data for key in row})
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(data)
    lines = [
        "# Cost-aware walk-forward ridge ETH return filter — fixed YTD 2026 replay", "",
        "**Status:** research-only; project decision remains NO_TRADE.", "",
        f"- Window: [{START.isoformat()}, {END.isoformat()}); {EVAL_BARS} evaluation bars; {WARMUP_BARS} warm-up bars.",
        f"- Dataset SHA-256: {json.dumps(canonical_hashes, sort_keys=True)}.",
        f"- Metadata: {json.dumps(meta, sort_keys=True)}.",
        f"- Daily decisions every {SIGNAL_CADENCE} completed 4h bars; fixed {HORIZON}-bar open-to-open horizon.",
        f"- Pure-Python ridge regression: {len(FEATURE_NAMES)} frozen past-only features; last {MAX_TRAINING_ROWS} mature daily samples; minimum {MIN_TRAINING_ROWS}; lambda={RIDGE_LAMBDA}.",
        "- Mature-label rule: training sample j is usable only if j+7 <= current decision index; verified with an explicit runtime guard.",
        "- Trade only when predicted gross return clears exact scenario round-trip break-even plus 25 bps; otherwise cash.",
        "- 95% sizing; assumed ETH min qty/step 0.0001; min notional 5 USDT.",
        "- Costs: BASE 10bps fee / 10bps assumed spread / 5bps slippage; STRESS_1 10/25/10; STRESS_2 20/50/20.", "",
        "| Period | Capital | Cost | Net PnL | Return | Max DD | Trades | W/L | Fees | ETH B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        lines.append(
            f"| {r['period']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | "
            f"{Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | "
            f"{Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_trades']} | "
            f"{r['wins']}/{r['losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
            f"{Decimal(r['eth_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    full = [r for r in summary if r["period"] == "FULL" and r["cost_scenario"] == "STRESS_2"]
    stress_folds = [r for r in summary if r["period"] != "FULL" and r["cost_scenario"] == "STRESS_2"]
    fold_positive_by_cap = {}
    for cap in ("60", "100"):
        rows = [r for r in stress_folds if r["capital_initial_usdt"] == cap]
        fold_positive_by_cap[cap] = sum(Decimal(r["net_pnl_usdt"]) > ZERO for r in rows)
    gate = (
        len(full) == 2
        and all(Decimal(r["net_pnl_usdt"]) > ZERO for r in full)
        and all(r["completed_trades"] >= 20 for r in full)
        and all(Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in full)
        and all(v >= 2 for v in fold_positive_by_cap.values())
    )
    lines += ["", "## Preregistered promotion gate", "",
              "STRESS_2 full-window PnL must be positive for both capitals; each capital must have >=20 completed trades; max drawdown must be no worse than -15%; at least 2 of 3 chronological folds must be positive for each capital; and the strategy must beat cash net of costs.", ""]
    lines.append(f"Positive STRESS_2 folds by capital: {json.dumps(fold_positive_by_cap, sort_keys=True)}")
    lines.append("**Historical gate: " + ("PASS" if gate else "FAIL") + "**")
    lines += ["", "The same archived dates have been used in prior project work and are not a pristine unseen holdout. Fees/spread/slippage and lot filters are assumptions, not historical executable fills. No result authorizes PAPER/LIVE. A pass requires prospective shadow validation.", ""]
    Path(out / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
