# BTC_TSMOM_12M_SKIP1_EMA200_ATR14_TARGET20 — frozen replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/166
- Isolated branch: \`research/btc-12-1m-momentum-ema200-atr-v1\`
- Family: slow 12-minus-1-month time-series momentum with an EMA200 trend regime and one monthly decision. This is distinct from 90-day momentum threshold crossings, EMA-stack transitions, Donchian channels and ATR squeeze breakouts.
- ATR14 sizing reuses the frozen issue #143 formula unchanged; no parameter sweep or post-result tuning.

## Frozen signal
BTCUSDT Spot daily UTC. Decisions only at each UTC calendar month-end daily close. Momentum[t] = close[t-30]/close[t-365]-1; the latest 30 days are skipped. Regime requires close[t] > recursive EMA200[t], alpha=2/(200+1), seeded from the first close. Target long state is momentum>0 and price above EMA200. Enter on next daily open if flat and the month-end target-long condition is true; exit on next daily open if invested and the month-end condition is false. No orders between month-end decisions; at window boundaries liquidate at the last completed close.

ATR14 is the simple 14-bar arithmetic mean of true range. At entry signal close freeze weight=min(95%, 20%/((ATR14/close)*sqrt(365)); no intratrade rebalance. One long Spot position; no short/leverage; maximum five accepted entries per rolling 30 days; current LOT_SIZE and minimum-notional rules enforced.

## Data, costs and windows
Public Binance Spot BTCUSDT daily candles in UTC over \`[2018-11-27,2026-10-22)\` with 400 warm-up candles before 2020-01-01. Require all expected daily timestamps, no gaps/duplicates, valid OHLCV and correct close-times; any failure blocks evaluation without interpolation. Record canonical dataset SHA-256, indicator SHA-256, unknown completeness/finality, exchangeInfo filter snapshot/hash, fills, equity and gate outcomes. Historic point-in-time availability, historical filters and executable historical order-book quotes remain UNKNOWN.

Independent 60 and 100 USDT accounts per window. BASE fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are modeled assumptions. Compare against cash and matched-cost BTC buy-and-hold.

Windows: FULL \`[2020-01-01,2026-10-22)\`; EARLIER \`[2020-01-01,2023-01-01)\`; RECENT \`[2023-01-01,2026-10-22)\`; annual 2020–2025 and 2026 YTD \`[2026-01-01,2026-10-22)\`.

## Frozen gate
Under STRESS_2 both capital sizes must show positive PnL in FULL/EARLIER/RECENT/2026YTD, at least five round trips each principal window, max drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window, 2022 PnL>=0, at least four positive annual/YTD windows, and recent PnL above cash and BTC buy-and-hold. Tests cover 12-1 momentum/skip, calendar month-end cadence, EMA200, causality, next-open ledger, ATR sizing and terminal liquidation. Any failure = FAIL/REJECTED/NO_TRADE. Related historical trend ideas were inspected; no pristine holdout claim. Any historical pass requires prospective shadow validation. main and TOP1/PRIME remain untouched.
