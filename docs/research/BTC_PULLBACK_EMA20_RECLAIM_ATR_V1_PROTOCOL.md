# BTC_PULLBACK_EMA20_RECLAIM_ATR_V1 — frozen multi-regime replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/167
- Isolated branch: \`research/btc-pullback-reclaim-ema20-ema50-atr-v1\`
- Distinct family: controlled pullback inside a bullish long-term regime, entered only on a close reclaiming EMA20 after a prior close at/below it. It is not a wick-rejection, channel breakout, EMA-stack transition or slow monthly momentum rule.
- Reuses the exact ATR14 position-sizing overlay from Issue #143 unchanged.

## Frozen signal and sizing
BTCUSDT daily Spot, UTC. EMA20/50/200 are recursive, alpha=2/(period+1), seeded from first close. At close t, prior_high=max(high[t-30:t]) with current candle excluded; pullback_fraction=close[t]/prior_high-1. Entry if pullback_fraction is in [-12%,-4%] inclusive, close[t-1]<=EMA20[t-1], close[t]>EMA20[t], close[t]>EMA50[t], and close[t]>EMA200[t]. Entry at next daily open. Exit on next daily open after completed close if close[t]<EMA50[t] or close[t]<EMA200[t]. Terminal liquidate at window's final completed close.

ATR14 is the simple 14-bar average of true range; at signal close freeze weight=min(95%, 20%/((ATR14/close)*sqrt(365)); no intratrade rebalance. One long Spot position, no shorts/leverage; max five accepted buys in 30 rolling days; enforce current public LOT_SIZE/minQty/minNotional.

## Data and accounting
Public BTCUSDT Spot daily candles [2018-11-27, 2026-10-22) UTC, 400 warm-up bars before 2020. Strict continuity/OHLCV/close-time checks; block without interpolation or silent drops. Persist canonical data SHA-256, indicator SHA-256, metadata quality, current public filter snapshot/hash, fill ledger, equity, summaries and gates. Point-in-time availability, historic lot filters and historic executable order-book quotes remain UNKNOWN.

Starting balances 60 and 100 USDT reset by window. BASE fee 10 bps/fill, full spread assumed 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. These are assumptions. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); years 2020–2025 and 2026YTD [2026-01-01,2026-10-22).

## Frozen gates
At STRESS_2 both capital levels require positive PnL in FULL/EARLIER/RECENT/2026YTD; at least five round trips in each principal window; max DD >=-15% in FULL/EARLIER/RECENT and annual/YTD windows; 2022 PnL>=0; at least four positive annual/YTD windows; and RECENT PnL beats cash and matched-cost BTC buy-and-hold. Tests cover EMA math, previous-high exclusion, pullback bounds, EMA20 reclaim, exit conditions, causality, bounded ATR weights, next-open fills and terminal liquidation.

Any failure means FAIL/REJECTED/NO_TRADE; no post-result tuning. This archive was examined earlier, so a historic pass is not pristine holdout evidence and would still need prospective shadow validation. main and ETH_BBMR_168_EMA720 TOP1/PRIME remain untouched.
