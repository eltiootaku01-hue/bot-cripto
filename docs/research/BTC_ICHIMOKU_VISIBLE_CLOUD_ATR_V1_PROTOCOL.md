# BTC_ICHIMOKU_VISIBLE_CLOUD_ATR_V1 — frozen replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/170
- Isolated branch: research/btc-ichimoku-visible-cloud-atr-v1.
- Family: canonical Ichimoku 9/26/52 cloud state with displacement handled causally. ATR14 sizing reuses Issue #143 unchanged.

## Frozen rules
BTCUSDT daily Spot UTC. Tenkan=(highest high 9 bars + lowest low 9 bars)/2; Kijun=(highest high 26 + lowest low 26)/2; raw Span A=(Tenkan+Kijun)/2; raw Span B=(highest high 52 + lowest low 52)/2. Span A/B are plotted 26 candles forward, so the cloud visible at t is raw_span[t-26], never raw_span[t].

Bullish state: close>visible cloud top; visible Span A>Span B; Tenkan>Kijun. Enter only on false-to-true transition, signal after completed daily close and execute at next open. Exit after completed close if close<Kijun or close<visible cloud bottom, execute at next open. Terminal liquidate at each window's last completed close.

ATR14 is the simple mean of 14 true ranges. Weight=min(95%,20%/((ATR14/close)*sqrt(365)), frozen at entry, no intratrade rebalance. One long Spot position, no leverage/shorts, maximum five accepted buys per rolling 30 days, enforce current public lot/minQty/minNotional.

## Data and costs
Public Binance Spot BTCUSDT daily UTC [2018-11-27,2026-10-22) with 400 warm-up bars. Require exact timestamps, no gaps/duplicates, valid OHLCV and close times. Block on any failure without interpolation. Preserve data and indicator SHA-256, quality metadata, current filter snapshot/hash, fills, equity, summary and gates. Historical availability, old filters and historic executable quotes remain UNKNOWN.

Capital resets to 60 and 100 USDT by window. BASE fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); annual 2020–2025 and 2026YTD [2026-01-01,2026-10-22).

## Frozen gates
At STRESS_2 both capitals must have positive PnL in FULL/EARLIER/RECENT/2026YTD; >=5 trips in each principal window; max DD no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL>=0; >=4 positive annual/YTD windows; recent net PnL above cash and matched-cost BTC B&H. Tests cover exact 9/26/52 arithmetic, visible-cloud displacement, causal inputs, state entry, exits, next-open fills and terminal liquidation. Any failed gate = FAIL/REJECTED/NO_TRADE; no post-result tuning. Any historical pass still requires shadow validation. main and TOP1/PRIME remain unchanged.
