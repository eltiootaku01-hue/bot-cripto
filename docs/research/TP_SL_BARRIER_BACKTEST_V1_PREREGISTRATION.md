# TP/SL Barrier Backtest V1 — preregistered protocol

**Status:** fixed before the first TP/SL result run.  
**Branch:** `research/tp-sl-wave-simulation-2026-01-to-10`.  
**Decision gate:** `NO_TRADE`; research-only, no PAPER/LIVE order submission.  
**No parameter tuning:** the V1 signal ledger, time horizons, barrier values, capital levels, costs, and execution conventions below are frozen for this replay.

## Immutable inputs

- Dataset artifact run: `39062915950`, artifact ID `12111858808`.
- Dataset logical SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Frozen V1 forecast artifact run: `39611639871`, artifact ID `12350826389`.
- V1 forecast ledger SHA-256 (file `predictions.csv`): `b8f555028791d0a9d2f93dc428acf3882d5ec3488ac56221df3136e4841c3d00`.
- Dataset: aligned BTCUSDT and ETHUSDT hourly OHLCV history, with forecasts issued from 2026-01-01 00:00 UTC; archived candles end at 2026-10-17 18:59:59.999 UTC. Remaining October data are unavailable in this pinned source.
- The input loader validates the dataset logical hash and the prediction-file hash recorded in the archived V1 manifest. Simulator reads only forecast decision timestamp, asset, strategy, horizon, and signal class. It does not load the V1 future-return or outcome columns.

## Frozen signals

Use the original V1 predictions exactly as stored in the immutable ledger. Strategy definitions are not recalculated or retuned in this backtest. The three V1 horizons (4 h, 12 h, and 24 h) define the maximum holding interval. The 57 existing asset × strategy × horizon groups are each evaluated at 60 USDT and 100 USDT under all three cost scenarios, for 342 result rows.

Only an `UP` prediction may open a long Spot position. `DOWN` and `ABSTAIN` do not open short positions and do not force an early exit. Each (asset, strategy, horizon) combination is a separate virtual portfolio with no overlapping position; an `UP` call while a position is open is skipped.

## Entry, barriers, and exit chronology

- Decision is made at the close of the signal candle.
- Enter at the next hourly candle open, with modeled adverse execution cost.
- Allocate 95% of available quote balance per entry, inclusive of the entry fee.
- TP trigger is 1.00% above the modeled entry fill; SL trigger is 0.50% below the modeled entry fill.
- Check the entry candle's high/low for intrabar barrier touches after the entry at open.
- On bars after entry, first check opening gaps: an open at/below SL exits at that open (gap risk, possibly beyond -0.50%); an open at/above TP exits at the TP level without crediting any better opening price.
- Otherwise evaluate OHLC barrier touches. If low <= SL and high >= TP in the same candle, classify SL first because OHLC cannot reveal the intrabar path. An isolated SL executes at its trigger; an isolated TP executes at its trigger.
- If neither barrier hits during the horizon, close at the close of the horizon candle and label `TIME_EXIT`. Time exits are not counted as TP wins, even if net PnL is positive.
- Exit fill then receives the modeled half-spread plus slippage penalty and exit fee. Both entry and exit fees are applied to executed notional.
- One TP/SL position at a time per group. After an exit, a new UP signal at that candle close may schedule entry on the next candle.

## Frozen costs

| Scenario | Fee per side | Assumed full spread | Slippage per side | Approximate modeled round trip |
|---|---:|---:|---:|---:|
| BASE | 0.10% | 10 bps | 5 bps | 0.40% |
| STRESS_1 | 0.10% | 25 bps | 10 bps | 0.65% |
| STRESS_2 | 0.20% | 50 bps | 20 bps | 1.30% |

Spreads and slippage are assumptions, not measured historical order-book values. Exchange quantity/lot filters are not simulated; fills use continuous quantities.

## Required results and definitions

For every asset, frozen strategy, horizon, initial capital, and cost scenario, report:
- counts of UP/DOWN/ABSTAIN signals, skipped UP calls while a position is open, number of executed trades;
- TP exits, SL exits, and time exits;
- `tp_before_sl_rate_all_trades_pct = TP / all trades`;
- `barrier_resolved_win_rate_pct = TP / (TP + SL)`, excluding time exits from that denominator;
- net profitable-trade rate, average/median net trade return, total fees, final equity, net PnL, return, and hourly-close mark-to-liquidation maximum drawdown;
- cash (0%) and matching-asset buy-and-hold with the same starting capital and modeled cost scenario;
- trade-level ledger, monthly realized PnL summary, full summary table, benchmark table, run logs, and SHA-256 manifest.

Drawdown uses hourly-close equity marked net of modeled liquidation costs. It is not a tick-level drawdown measure.

## Limits and promotion

- Same-candle ordering is unknown; stop-first tie policy is conservative but does not fix unobserved quote availability.
- A stop-loss order can lose more than 0.50% if price gaps through the trigger.
- The archived market data has historical point-in-time `available_at` / finality marked UNKNOWN.
- The window was exposed to prior V1 research and is not an untouched holdout. This replay answers whether the fixed TP/SL proposal worked historically on this archive, not whether it will work in the future.
- Selecting the best among 342 result rows is multiple testing; any top table is descriptive only.
- No thresholds may be changed after seeing this evaluation and re-reported as an untouched test.
- Regardless of numerical results, the operational decision remains `NO_TRADE` until separate, robust, net-of-cost validation.
