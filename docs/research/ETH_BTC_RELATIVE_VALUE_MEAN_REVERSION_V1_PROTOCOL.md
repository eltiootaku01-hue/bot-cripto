# ETH_BTC_RELATIVE_VALUE_MEAN_REVERSION_V1 — frozen protocol

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/160
- Isolated branch: \`research/eth-btc-relative-value-zscore-v1\`
- Parent: main as observed before the experiment. This candidate never modifies main or \`ETH_BBMR_168_EMA720\` TOP1/PRIME.
- Reuses the existing daily public-data, Decimal accounting, fill, cost, filter and artifact core from isolated research code. No order placement or private endpoints.

## Frozen hypothesis

At close of completed UTC day t, define the relative ratio \`r[t] = ETHUSDT close[t] / BTCUSDT close[t]\`. Compute mean and population standard deviation from the prior 60 completed daily ratios \`r[t-60:t]\`, excluding t. Let \`z[t] = (r[t] - prior_mean) / prior_std\`. A zero or nonfinite standard deviation produces no signal.

The BTC regime filter must also pass: BTC close[t] > recursive EMA100[t], alpha=2/(100+1), seeded by the first close, using only closes available through t. Require both at least 100 bars of EMA history and the full prior 60-day ratio history. Enter long ETH only when \`z[t] <= -1.50\` and the BTC filter passes. Signal decision is at close; entry occurs at the following daily ETH open.

## Position and exits

- One ETH Spot long position maximum; no shorting/leverage. Allocate 20% of available quote cash, floor-round to the current public ETHUSDT LOT_SIZE step and enforce minQty/minNotional.
- Exit when a completed ETH close is at least entry fill x 1.04, no more than entry fill x 0.97, or after 10 completed holding daily candles; execute at next daily open.
- Terminal liquidation at the last completed close of each window with modeled close costs. Reuse Decimal accounting, commissions, spread/slippage, rejected-order and fill/equity artifact infrastructure.

## Data and cost contract

- Public Binance Spot BTCUSDT and ETHUSDT daily klines in UTC, \`[2019-01-01, 2026-10-22)\`. Independently validate exact daily starts, close times, duplicates and OHLCV, then require the timestamp arrays to be byte-for-byte identical. Any gap or mismatch blocks the entire replay. Never interpolate, forward-fill or silently remove a candle.
- Record each symbol's raw-page hashes, canonical dataset hash, counts, first/last timestamps and current exchangeInfo/filter hash; record the paired timestamp hash.
- Point-in-time data availability, historic order-book quotes and historic exchange-filter equivalence are UNKNOWN. Spread/slippage are modeled assumptions.
- Capital resets independently to 60 and 100 USDT. Costs:
  - BASE: fee 10 bps per fill, full spread 10 bps, slippage 5 bps/side.
  - STRESS_1: fee 10 bps, spread 25 bps, slippage 10 bps/side.
  - STRESS_2: fee 20 bps, spread 50 bps, slippage 20 bps/side.
- Apply half-spread plus side-specific slippage on every fill. Compare cash, ETH buy-and-hold (primary same-asset benchmark), and BTC buy-and-hold (secondary context), using matched-cost benchmark execution and 95% initial capital allocation.

## Fixed windows and promotion gates

Windows: FULL_2020_2026 \`[2020-01-01, 2026-10-22)\`; EARLIER_2020_2022 \`[2020-01-01, 2023-01-01)\`; RECENT_2023_2026 \`[2023-01-01, 2026-10-22)\`; calendar years 2020–2025; 2026_YTD \`[2026-01-01, 2026-10-22)\`.

Under STRESS_2, for both capitals: net PnL >0 in FULL/EARLIER/RECENT/2026_YTD; at least five completed round trips in each principal window; max drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL >=0; at least four positive annual/YTD windows; and RECENT PnL greater than cash and matched-cost ETH buy-and-hold. Tests must pass for paired timestamps, causal z-score, BTC EMA regime, next-open fills, Decimal ledger, quantity rounding/filter rejection and terminal liquidation.

Any gate failure means FAIL/REJECTED/NO_TRADE. No post-result parameter modifications. A workflow success only certifies technical completion. Historical archive was examined previously and is not a pristine holdout; any historical pass requires independent prospective shadow validation.
