# BTC_DMI_ADX_EMA200_ATR_V1 — frozen multi-regime replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/168
- Isolated branch: research/btc-dmi-adx-ema200-atr-v1.
- Distinct family: Wilder directional movement and ADX directional-strength crossovers with an EMA200 regime filter. This differs from Donchian breakouts, EMA-stack entry, pullback reclaim, and slow monthly momentum.
- ATR sizing reuses the frozen Issue #143 formula unchanged.

## Frozen indicators and signals
BTCUSDT daily Spot UTC:
- TR=max(high-low, abs(high-prev_close), abs(low-prev_close).
- +DM=high[t]-high[t-1] if the up move is positive and strictly exceeds the down move; else zero.
- -DM=low[t-1]-low[t] if the down move is positive and strictly exceeds the up move; else zero.
- Wilder-smoothed TR/+DM/-DM initial values are sums at indices 1..14; subsequently prior_smoothed-prior_smoothed/14+current.
- +DI/-DI=100*smoothed_DM/smoothed_TR. DX=100*abs(+DI--DI)/(+DI+-DI); denominator zero means unavailable.
- ADX14 seeds as mean of first 14 valid DX observations and then uses Wilder smoothing.
- EMA200 is recursive, alpha=2/(200+1), seeded from first close.
- Entry at completed close: +DI crosses above -DI, ADX14>=25, close>EMA200; fill at next daily open.
- Exit at completed close: -DI crosses above +DI or close<EMA200; fill next daily open.
- ATR sizing uses simple 14-bar average TR, matching Issue #143: weight=min(95%,20%/((ATR14/close)*sqrt(365))), frozen at signal close. One long Spot, max five accepted buys in rolling 30 days; no leverage/shorts. Terminal liquidation at window end.

## Data, costs and windows
Public BTCUSDT daily candles UTC [2018-11-27,2026-10-22), 400 warm-up candles. Exact timestamps, no gaps/duplicates and valid OHLCV/close times required; any failure blocks without interpolation. Persist dataset/indicator SHA-256, quality metadata, current public filter snapshot/hash, fills, equity, summary and gates. Historical point-in-time availability, historic filters and executable historic quotes remain UNKNOWN.

Capital resets independently at 60 and 100 USDT per window. BASE: fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side. STRESS_1 10/25/10. STRESS_2 20/50/20. Spread/slippage are assumptions. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); annual 2020–2025 and 2026 YTD [2026-01-01,2026-10-22).

## Frozen gates
At STRESS_2, both capitals require positive PnL in FULL/EARLIER/RECENT/2026YTD; at least five trips in every principal window; max drawdown no worse than -15% in FULL/EARLIER/RECENT and each annual/YTD window; 2022 PnL>=0; at least four positive annual/YTD windows; RECENT PnL above cash and matched-cost BTC buy-and-hold. Tests cover TR/DM/DI/DX/ADX arithmetic, Wilder seed and smoothing, EMA200, crossovers, causal inputs, next-open ledger, risk sizing and terminal liquidation.

Any failure = FAIL/REJECTED/NO_TRADE; no post-result tuning. Archive already inspected: no pristine holdout. Any historical pass still requires shadow validation. main and ETH_BBMR_168_EMA720 TOP1/PRIME remain untouched.
