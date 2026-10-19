# BTC/ETH Strategy Tournament V2 — preregistered historical screen

**Decision fixed by the protocol:** `NO_TRADE` unless the evidence supports advancement; the tournament itself cannot enable PAPER orders or LIVE.

## Purpose and limits

This job expands the earlier seven-rule historical comparison into a deterministic search over a frozen grid of 6,232 candidates from four long-only Spot families. It tests whether any candidate is stable enough to justify a separate prospective experiment. It is not allowed to convert the best-ranked historical backtest into an automatically active strategy.

The source is the previously archived Binance BTCUSDT/ETHUSDT 1-hour dataset from workflow run `39062915950`, artifact `btc-eth-follow-365d-f8b9c36768f68ee754f1cb217f920dea25ef445e`. The exact expected data SHA-256 is `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`; the configuration SHA-256 is `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`. The script recomputes the source hashes before using any price.

**Major limitation:** the archived candles do not contain trustworthy point-in-time `available_at` and finality evidence. Hash integrity proves which bytes were evaluated; it does not prove that each bar was available to a trading system when the simulated decision was made. The earlier V1 research also examined the historical validation folds and the 365-day reference segment. Consequently, neither the grid rankings nor bootstrap p-values are a new independent holdout, and no result is eligible to promote a live or PAPER strategy.

## Frozen candidate grid

The canonical parameter grid is frozen by the grid SHA-256 `76b6abf4f61bc05135ea613c813dce2f5c1a44c4869ed42ed3faa18cdf625eae`. The workflow fails closed if the generated grid SHA or expected data hashes change; changing the grid requires a new reviewed protocol version.

| Family | Main parameter dimensions |
|---|---|
| `EMA_CROSS_RSI` | Fast EMA 5–24 from a fixed list, slow EMA 24–200 from a fixed list, entry RSI minimum 30–55, exit RSI 55–80, requiring entry threshold below exit threshold |
| `TSMOM_TREND` | Momentum lookback 24–504 hours, optional ETH trend EMA, optional BTC trend gate, fixed entry momentum thresholds and exit thresholds |
| `DONCHIAN_BREAKOUT` | Prior-high breakout windows 12–96 bars, prior-low exit windows 4–36 bars where exit is shorter than entry, optional ETH trend EMA |
| `RSI_MEAN_REVERSION` | RSI period 7/10/14/21, entry threshold 15–35, exit threshold 45–65, optional ETH trend EMA |

The script does not run optimization against future data at runtime. All signals use the close of hourly bar `t`; fill attempts happen at bar `t+1` open. It resets the fictitious 31 USDT account at each validation-fold start, spends up to 95% of available quote on entry, respects the pinned dataset’s captured ETH quantity-step/minimum filters, limits new entries to five per rolling 30 days, remains long-only, and forbids leverage and shorting. The three cost scenarios, including their modeled fees, spread and slippage, are fixed in code before the GitHub Actions run.

## Run plan and gates

1. Recompute and check pinned dataset/configuration hashes.
2. Enumerate the frozen 6,232 candidates and check unique canonical parameter identities.
3. Run each candidate over three contiguous, fixed 60-day historical validation folds under BASE, STRESS_1 and STRESS_2 costs. That is 56,088 candidate/fold/cost replays.
4. Rank candidates only by the worst return across those validation folds and cost scenarios, then median BASE return. Require positive BASE and STRESS_1 returns in all three folds and nonnegative STRESS_2 returns in all three folds to enter the limited reference recheck set.
5. Run up to the top 100 validation-consistent candidates over the already-inspected 365-day historical reference slice under the same cost scenarios. These diagnostic values cannot alter the validation ranking and cannot be called an independent holdout.
6. Perturb future price suffixes for a representative from each family at three cutoffs; past signals must remain unchanged.
7. Run a family-wise max-statistic test over the full candidate grid using 2,000 fold-stratified circular moving-block bootstrap replicates with 5-day blocks against cash, BTC buy-and-hold and ETH buy-and-hold. The resulting p-values are explicitly exploratory because the source folds have already been inspected and availability/finality provenance is unknown.
8. Write JSON, CSV, Markdown and a manifest containing hashes of each output. GitHub Actions uploads these as an immutable run artifact.

**No automatic promotion rule exists in this workflow.** Any future strategy needs a new, independent, point-in-time prospective dataset, preregistered parameters/costs/benchmarks, anti-lookahead checks on that dataset, successful net-of-cost performance versus passive benchmarks, multiple-comparison correction and an explicit CEREBRO review. Current operation remains `NO_TRADE`.