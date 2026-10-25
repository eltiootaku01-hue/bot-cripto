# COST_AWARE_XGBOOST_ETH_5D_V1 — frozen multi-regime protocol

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/161
- Isolated branch: \`research/xgboost-cost-aware-eth-five-day-v1\`
- This is a fixed nonlinear boosted-tree model, not a parameter sweep. It is distinct from the earlier cost-aware Ridge filter (Issue #148) in estimator family, daily horizon and evaluation protocol. The archived history was already inspected and is not a pristine holdout.
- Research only; no private endpoints, credentials, real orders, leverage or changes to main / ETH_BBMR_168_EMA720 TOP1/PRIME.

## Features and causal label
At completed UTC daily close t, the frozen feature set uses ETH and BTC returns over 1/3/7/14/30 days; relative returns over 3/7/14/30 days; distances from each asset's EMA20/EMA50/EMA200; each asset's realized volatility over 7/30 days; ETH/BTC ratio z-score versus preceding 30 ratios (current excluded); and ETH volume divided by median of prior 20 completed daily volumes. No future candles or current open are features.

A training row j has target \`ETHUSDT open[j+6]/open[j+1]-1\`. Only phase-aligned j where j modulo 5 = 4 are used, matching five-day cadence. At a decision index t, include the label only if \`j+6 <= t\`, so the exit open is already observable. Take latest 180 mature rows; minimum 80, otherwise stay cash. Decisions also only at indices t modulo 5 = 4, anchored to the first archived candle.

Pinned model: XGBoost 2.1.4, XGBRegressor \`objective=reg:squarederror, n_estimators=80, max_depth=2, learning_rate=0.03, min_child_weight=10, subsample=0.8, colsample_bytree=0.8, reg_lambda=10, random_state=20261025, n_jobs=1, tree_method=hist\`. No hyperparameter search. Fixed training data/labels and parameters recorded for each decision.

## Entry, exits and risk
Prediction is gross five-day ETH open-to-open return. Each scenario independently enters only if prediction is strictly greater than its exact modeled round-trip break-even plus a fixed 25 bps buffer. Signal after close; entry at next daily open; exit after five open-to-open days, at the scheduled next open. One long ETH Spot position maximum, 20% available quote balance, no short/leverage. Quantity is floor-rounded to current public ETH LOT_SIZE step; minQty and minNotional enforced. Terminal position is liquidated at last completed close for a window.

## Data, costs and windows
Public BTCUSDT and ETHUSDT Spot daily candles in UTC \`[2019-01-01, 2026-10-22)\`; validate full expected daily timestamps, close times, unique timestamps, OHLCV coherence and exact pair alignment. Missing/malformed candles block evaluation; never interpolate, forward-fill or silently drop. Persist per-symbol raw page hashes, canonical SHA-256, paired timestamps hash, current exchangeInfo/filter hashes, features/predictions/training audit, fills, equity curves, summary and gates. Historical point-in-time availability, historic lot filters and executable historical order-book quotes remain UNKNOWN; spread/slippage are modeled assumptions.

Capital restarts independently at 60 and 100 USDT. BASE: fee 10 bps/execution, full spread 10 bps and slippage 5 bps/side. STRESS_1: fee 10, spread 25, slippage 10. STRESS_2: fee 20, spread 50, slippage 20. Apply half-spread + per-side slippage on every execution.

Windows: FULL \`[2020-01-01, 2026-10-22)\`; EARLIER \`[2020-01-01, 2023-01-01)\`; RECENT \`[2023-01-01, 2026-10-22)\`; annual windows 2020–2025; 2026 YTD \`[2026-01-01, 2026-10-22)\`. Compare cash and matched-cost ETH buy-and-hold; BTC buy-and-hold is contextual.

## Frozen gates
At STRESS_2, both capitals must have positive PnL in FULL/EARLIER/RECENT/2026_YTD; >=5 closed round trips in each of those four main windows; drawdown no worse than -15% in FULL/EARLIER/RECENT and each year/YTD; 2022 PnL >=0; >=4 positive windows among 2020–2025 and 2026 YTD; and RECENT PnL exceeding cash and ETH buy-and-hold. Tests cover causal features, ratio z-score, matured-label constraint, deterministic XGBoost, thresholds, next-open entry/exit, Decimal accounting, lot rejection and terminal liquidation.

Any failed gate is FAIL/REJECTED/NO_TRADE. Do not alter the model/horizon/threshold after observing results. Workflow success confirms only technical execution; any historical pass needs a preregistered prospective shadow test.
