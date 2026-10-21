# TP/SL loss decomposition V1 — preregistered diagnostic

**Status:** fixed before the costless counterfactual is run.  
**Branch:** `research/tp-sl-wave-simulation-2026-01-to-10`.  
**Decision:** `NO_TRADE`; diagnostic only. No trading code is enabled.

## Question
Separate the result of the already-frozen V1 prediction signals under TP +1.0% / SL -0.5% into:
1. the same signals' costless counterfactual outcome,
2. the incremental change under modeled BASE/STRESS_1/STRESS_2 friction and its effect on barrier timing,
3. observed outcome mix and turnover (TP, SL, time exits),
4. documented gap handling and same-candle ambiguity.

## Frozen inputs
- Dataset logical SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Frozen forecast ledger SHA-256: `b8f555028791d0a9d2f93dc428acf3882d5ec3488ac56221df3136e4841c3d00`.
- Original TP/SL report artifact run: `39618059542`, artifact ID `12353776753`.
- Original backtest `summary.csv` SHA-256: `0f9f76ad8f94042b304fd6a9b65d62171aa4f8311900aaafab5f2209eea0c25a`.
- Original backtest `trades.csv` SHA-256: `e54e2e0094ef898716da0e9cca10b753cf821c4f6f5ff6c2127a692e2405f550`.
- Evaluated interval: 2026-01-01 through 2026-10-17 18:59:59.999 UTC.

## Fixed counterfactual
Replay exactly the archived UP/DOWN/ABSTAIN calls, with the identical asset, strategy, horizon, entry cadence, 95% account allocation, TP +1.0% from entry fill, SL -0.5% from entry fill, OHLC chronology, next-open entry, time exits, and conservative stop-first same-candle policy. The only counterfactual change is setting fee, spread and slippage to zero. No signal or threshold is changed or refit. Compare the 114 costless portfolios with the 114 corresponding portfolios from each original cost scenario.

This costless counterfactual changes the fill price and therefore the exact trigger price levels, as the existing model defines barriers relative to the entry fill. The difference captures the **total effect of modeled transaction costs under this contract**, including changed barrier touches and trade overlap/turnover; it is not interpreted as a pure additive commission-only effect.

## Source-ledger accounting decomposition
For each trade in the original archived ledger, derive a clearly named gross-price PnL proxy as `quantity × (raw exit trigger price − raw entry open)`. Compare that proxy with recorded net PnL to calculate the execution-friction residual; report commissions separately. This is an accounting decomposition of the realized costed paths, not a substitute for the costless counterfactual because cost changes trigger levels and position sizes.

## Required grouping
Report by:
- cost scenario overall;
- frozen rule family;
- horizon (4 h, 12 h, 24 h);
- asset;
- outcome and exit reason (including `SL_GAP` and `SL_BOTH_TOUCHED_CONSERVATIVE`);
- costless vs costed portfolio matched by (asset, rule, horizon, starting capital).

Required metrics: trades, TP, SL, time exits, TP/all-trades rate, TP/(TP+SL), net profitable trade rate, total fees, gross-price PnL proxy, execution residual, costless return/PnL, costed return/PnL, difference versus costless, mean/median portfolio return, maximum drawdown, skipped signals, and positive/negative time exits.

## Guardrails
- This is post-hoc diagnosis of an already-seen historical window, not a new holdout.
- No optimisation, model changes, signal adjustments or threshold searches.
- Zero-cost is an artificial comparison, not an executable cost model or proof of edge.
- Historical point-in-time availability/finality remains UNKNOWN; hourly OHLC cannot reveal intrabar sequencing.
- `main` must remain unchanged; no PAPER/LIVE orders; `NO_TRADE` stays in force.
