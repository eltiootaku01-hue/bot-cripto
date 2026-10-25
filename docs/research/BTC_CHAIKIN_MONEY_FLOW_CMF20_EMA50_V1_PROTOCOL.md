# BTC_CHAIKIN_MONEY_FLOW_CMF20_EMA50_V1 — frozen replay protocol

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/162
- Isolated branch: \`research/btc-chaikin-money-flow-cmf20-ema50-v1\`
- Strategy-family hypothesis: volume-weighted close-location flow changes from distribution to accumulation during a positive EMA50 price regime. It does not use abnormal-volume spikes, wick geometry, volatility squeezing, cross-asset relative value or machine learning.
- Main and \`ETH_BBMR_168_EMA720\` TOP1/PRIME remain unchanged. Research only; no private endpoints/keys, orders, margin or leverage.

## Frozen signal and execution

For each closed BTCUSDT daily candle t, \`MFM[t]=((close-low)-(high-close))/(high-low)\`; if the range is zero, MFM=0. \`MFV[t]=MFM[t]*volume[t]\`. \`CMF20[t]=sum(MFV[t-19:t+1])/sum(volume[t-19:t+1])\`; current candle is included only after completion and the 20-bar range includes the preceding 19 bars. Zero total volume means unavailable CMF and no signal.

The long-entry event is a zero-crossing \`CMF20[t-1] <= 0 and CMF20[t] > 0\` when \`close[t] > recursive EMA50[t]\`, alpha=2/(50+1), seeded on first close. Signal is decided after close t and entry occurs at the next daily open.

One Spot long position maximum, 20% of available quote cash; current public LOT_SIZE step/minQty/minNotional enforced. Close-based take-profit is +4%, stop is -3%, maximum holding 10 completed daily candles; each exit uses next daily open. Remaining position at window boundary liquidates at the last completed close. No intrabar stop assumptions.

## Dataset and cost contract

Use public Binance Spot BTCUSDT daily candles in UTC over \`[2019-01-01, 2026-10-22)\`. Require exactly all daily open timestamps, correct close times, no duplicates, coherent positive OHLC and nonnegative volumes. Any data contract failure blocks the replay; never interpolate or silently drop candles. Preserve raw-page hashes, canonical SHA-256, current exchangeInfo/filter snapshot, row counts, fills, equity curve, summary and gates. Historic point-in-time availability, past instrument filters and historic executable bid/ask/spread are UNKNOWN.

Starting balances 60 and 100 USDT, independently reset by each evaluation window. BASE fee 10 bps per fill, spread 10 bps full and slippage 5 bps/side; STRESS_1 fee 10 bps, spread 25 bps, slippage 10 bps/side; STRESS_2 fee 20 bps, spread 50 bps, slippage 20 bps/side. Spread/slippage are modeled assumptions, not observed historical quotes.

Windows: FULL \`[2020-01-01,2026-10-22)\`; EARLIER \`[2020-01-01,2023-01-01)\`; RECENT \`[2023-01-01,2026-10-22)\`; calendar years 2020–2025 and YTD \`[2026-01-01,2026-10-22)\`. Matched-cost benchmarks: cash and BTC buy-and-hold.

## Frozen promotion gates

Under STRESS_2, both capital sizes must have positive net PnL in FULL, EARLIER, RECENT and 2026YTD; at least five complete round trips in each principal window; max drawdown no worse than -15% in FULL/EARLIER/RECENT and each annual/YTD window; 2022 PnL >=0; at least four positive annual/YTD windows; and RECENT return above cash and matched-cost BTC buy-and-hold. Tests cover MFM/CMF math, flat-range behavior, EMA50, cross-zero event, data causality, next-open fills, Decimal ledger, lot rejection and terminal liquidation.

Any gate failure is \`FAIL/REJECTED/NO_TRADE\`. No post-result tuning. Workflow success says only that the code executed; historical gate pass still requires prospective shadow validation because this archived data was inspected before.
