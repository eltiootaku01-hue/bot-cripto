# BTC_PARABOLIC_SAR_EMA200_ATR_V1 — frozen multi-regime replay
- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/171
- Branch: research/btc-parabolic-sar-ema200-atr-v1.
- Fixed Parabolic SAR AF step 0.02 and maximum 0.20. No parameter grid.
- ATR14 sizing reuses the frozen formula from Issue #143 without retuning.

## Frozen SAR state
Initial direction is close[1]>=close[0]. Initial long SAR=low[0], EP=max(high[0],high[1]); initial short SAR=high[0], EP=min(low[0],low[1]); AF=0.02. Long update: candidate=prior SAR+prior AF*(prior EP-prior SAR), clamp to minimum of prior two lows. If low[t]<candidate, reverse short, set SAR=prior EP, EP=low[t], AF=0.02; otherwise stay long and increase AF by 0.02 capped at 0.20 only when a new high advances EP. Short update is symmetric: clamp candidate to maximum of prior two highs; if high[t]>candidate, reverse long with SAR=prior EP, EP=high[t], AF=0.02; otherwise stay short and increase AF only when a new low advances EP. The state is computed after the completed candle; no future data or intrabar path assumption.

EMA200 recursive alpha=2/201 seeded from first close. Enter only on short-to-long state transition when close>EMA200; exit on long-to-short transition or close<EMA200. Signals after close, execution at next daily open. Terminal liquidation at each window's final completed close.

ATR14 is the simple mean of 14 true ranges. Frozen exposure weight=min(95%,20%/((ATR14/close)*sqrt(365)), fixed at entry with no intratrade rebalance. One Spot long position, no leverage/shorts, maximum five accepted buys per rolling 30 days, current public lot/minQty/minNotional checks.

## Data, costs, windows
Public Binance Spot BTCUSDT daily UTC [2018-11-27,2026-10-22), with 400 warm-up bars. Require exact daily timestamps, no gaps/duplicates, valid OHLCV and correct close times; block on any failure without interpolation/silent deletion. Preserve dataset/indicator SHA-256, metadata completeness/finality, current ExchangeInfo filters/hash, fills/equity/summaries/gates. Historical point-in-time availability, historical filters and executable historic quotes remain UNKNOWN.

Capital resets independently to 60 and 100 USDT. BASE fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); years 2020–2025 and 2026 YTD [2026-01-01,2026-10-22).

## Frozen gates
At STRESS_2, both capitals need positive PnL in FULL/EARLIER/RECENT/2026YTD; at least five round trips in each principal window; max drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL>=0; at least four positive annual/YTD windows; recent PnL above cash and matched-cost BTC buy-and-hold. Tests cover initialization, AF update/cap, clamp rules, both reversals, EMA200, causality, next-open accounting, ATR sizing and terminal liquidation. Any gate failure = FAIL/REJECTED/NO_TRADE; no post-result tuning. This is retrospective data and any pass needs prospective shadow validation. main and ETH_BBMR_168_EMA720 TOP1/PRIME remain unchanged.
