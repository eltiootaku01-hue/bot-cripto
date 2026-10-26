# BTC_DONCHIAN_55_20_ATR14_V1 — frozen full-regime replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/164
- Isolated branch: \`research/btc-donchian-55-20-atr-risk-v1\`
- New hypothesis: classic Donchian channel trend following with a 55-day entry breakout and a trailing 20-day low exit. Unlike issue #158, the entry does not require ATR compression; it uses a longer entry channel and an asymmetric trailing exit.
- Reuses the BTC ATR-risk branch's validated acquisition, accounting, cost, order sizing, quantity limits, window reset, and terminal liquidation helpers. The sizing rule is intentionally fixed to Issue #143's previously tested formula; it is not retuned here.

## Frozen signal

On completed BTCUSDT daily UTC candles at index t:
- Enter if \`close[t] > max(high[t-55:t])\`, with current candle excluded from the previous 55-day high.
- Exit if \`close[t] < min(low[t-20:t])\`, with current candle excluded from the previous 20-day low.
- Signal is decided at completed close; entry/exit use the next daily open. At each window end, liquidate at the last completed close.
- ATR14 = simple arithmetic average of 14 true-range bars, using prior close only.
- Exposure weight at signal close = \`min(95%, 20%/((ATR14/close)*sqrt(365)))\`; freeze that weight at entry with no intratrade rebalance.
- One Spot long position, no shorts/leverage; at most five accepted buys in any rolling 30-day period.

## Data, costs and windows
Public Binance Spot BTCUSDT daily klines in UTC on \`[2018-11-27, 2026-10-22)\`; 400 warm-up candles before 2020-01-01. Require exact continuity, valid OHLCV, no duplicates/gaps. Any contract failure blocks evaluation. Persist canonical data SHA-256, quality metadata, current exchange filter snapshot/hash, indicator hash, fills, equity, summary and gates. Current filters do not establish historical constraints; point-in-time availability and historic executable quotes remain UNKNOWN.

Capital resets independently to 60 and 100 USDT for every window. Costs: BASE fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL \`[2020-01-01,2026-10-22)\`; EARLIER \`[2020-01-01,2023-01-01)\`; RECENT \`[2023-01-01,2026-10-22)\`; 2020–2025 annual windows and 2026 YTD \`[2026-01-01,2026-10-22)\`.

## Frozen promotion gates
At STRESS_2, both capital levels must have positive net PnL in FULL/EARLIER/RECENT/2026YTD; at least five round trips in each principal window; maximum drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL >= 0; at least four positive annual/YTD windows; and RECENT net PnL higher than cash and matched-cost BTC buy-and-hold. Tests must pass for channel lookbacks excluding current candle, causality, ATR sizing, next-open fill, shared ledger, lot filters and terminal liquidation. Any failed gate = FAIL/REJECTED/NO_TRADE. No post-result tuning. Historical data inspected previously; any pass still requires a prospective shadow test. main and TOP1/PRIME remain unchanged.
