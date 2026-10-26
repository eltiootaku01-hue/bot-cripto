# BTC_KAMA_EFFICIENCY_STATE_EMA200_ATR_V1 — frozen replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/169
- Branch: research/btc-kama-efficiency-state-ema200-atr-v1.
- New family: Kaufman adaptive moving average using Efficiency Ratio, with a bullish state transition and EMA200 regime. This differs from fixed EMA crossover, Wilder DMI/ADX, Donchian, pullback reclaim and slow monthly momentum.
- ATR14 sizing reuses Issue #143 unchanged.

## Frozen rule
BTCUSDT Spot daily UTC. ER10=abs(close[t]-close[t-10])/sum(abs(close[k]-close[k-1]), k=t-9..t), unavailable when denominator is zero. KAMA seed first close, FastSC=2/3, SlowSC=2/31, SC=(ER*(FastSC-SlowSC)+SlowSC)^2, KAMA[t]=KAMA[t-1]+SC*(close[t]-KAMA[t-1]) once ER exists. EMA200 is recursive with alpha=2/201. No signals before EMA200 and five-bar KAMA slope are available.

Bullish state requires ER>=0.35, close>KAMA, KAMA[t]>KAMA[t-5], close>EMA200. Enter only on false-to-true state transition, with signal at completed close and fill at next daily open. Exit at next open after completed close if KAMA[t]<=KAMA[t-5] or close<EMA200. Terminal liquidation at the last completed close per evaluation window.

ATR14 is a simple mean of 14 true ranges. Frozen exposure weight=min(95%,20%/((ATR14/close)*sqrt(365)), fixed at entry and no intratrade rebalance. One Spot long position, no short/leverage, max five accepted buys per rolling 30 days, current minQty/step/minNotional enforced.

## Data, costs, windows
Public Binance Spot BTCUSDT daily candles UTC [2018-11-27,2026-10-22), including 400 warm-up bars. Require all expected daily timestamps, correct close times, no gaps/duplicates and valid OHLCV; block on any failure without interpolation. Record dataset and indicator SHA-256, data quality metadata, current public filter snapshot/hash, fills, equity, summaries and gates. Historical point-in-time availability, historical filters and executable historic quote costs remain UNKNOWN.

Starting cash resets independently at 60 and 100 USDT. BASE fee 10 bps/fill, full spread assumed 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are modeled assumptions, not observed historical quotes. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); annual windows 2020–2025 and 2026YTD [2026-01-01,2026-10-22).

## Frozen gates
At STRESS_2, both capitals require positive PnL in FULL/EARLIER/RECENT/2026YTD; at least five complete round trips in every principal window; max DD no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL>=0; at least four positive annual/YTD windows; and RECENT PnL above cash and matched-cost BTC buy-and-hold. Tests cover ER, KAMA smoothing, EMA200 warm-up, state transition, slope exit, causal data, next-open fills, ATR sizing and terminal liquidation.

Any failed gate=FAIL/REJECTED/NO_TRADE; no post-result tuning. The archive was previously inspected; any historical pass remains retrospective and needs prospective shadow validation. main and ETH_BBMR_168_EMA720 TOP1/PRIME remain unchanged.
