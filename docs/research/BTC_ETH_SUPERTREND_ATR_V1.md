# BTC/ETH 4h Supertrend ATR(10,3) — no-AI research candidate

**Preregistered issue:** [#185](https://github.com/eltiootaku01-hue/bot-cripto/issues/185)
**Status:** Research only. `NO_TRADE` until historical gates pass and a separate prospective shadow holdout is approved.

## Frozen strategy rules
- Instruments: BTCUSDT and ETHUSDT on Binance Spot; long or cash only, no shorts, leverage or AI/ML.
- Timeframe: closed UTC 4h candles. Compute indicators at the completed candle close and execute any target change at the next 4h open.
- Trend filter: Supertrend with simple rolling True Range ATR(10) and multiplier 3.0, with standard final upper/lower band recursion. The current candle is allowed only after it closes; all past values must be invariant to appended future candles.
- Eligibility: Supertrend bullish, close above its own EMA200, and trailing 42-candle return positive.
- Decision cadence: one decision after every six fully completed 4h candles (24 hours). If both coins qualify, select the one with the stronger 42-candle return; if neither qualifies, target cash.
- Sizing: the same risk overlay as #181, `min(50%, 15% / ((ATR14 / close) × sqrt(365 × 6)))`, computed at signal close and frozen until the next target change.
- No intrabar stop assumptions; sell before buying on a switch; terminal liquidate at each independent evaluation window.

## Cost and evaluation protocol
Use independent initial capital of 60 and 100 USDT across COMBINED 2025-01-01 to 2026-10-22, 2025, and 2026 YTD to 2026-10-22. Compare BASE (10 bps fee/fill, 10 bps full spread, 5 bps slippage/side), STRESS_1 (10/25/10 bps), and STRESS_2 (20/50/20 bps). Matched benchmarks are cash, BTC buy-and-hold, ETH buy-and-hold, and the #181 15% ATR candidate.

## Frozen promotion gates (STRESS_2)
1. Positive net PnL in every window at both capital levels.
2. Max drawdown no worse than -15% in every window/capital.
3. At least five complete round trips in COMBINED and 2026 YTD at both capital levels.
4. Beat the #181 15% ATR candidate in COMBINED and 2026 YTD at both capital levels.
5. Beat cash, BTC buy-and-hold and ETH buy-and-hold in 2026 YTD.
6. Deterministic Supertrend warmup, causal-prefix, sizing and exposure-cap tests pass, and generated artifacts are hash-manifested.

Failure of any gate means `REJECTED / NO_TRADE`; no parameter rescue on these same dates. An historical PASS only permits consideration for prospective shadow testing. It does not prove future returns or authorize PAPER/LIVE.

## Data limitations
Market-data completeness metadata and historical point-in-time availability may remain UNKNOWN; current Binance filters are not proof of historical filter equivalence; fee/spread/slippage are modeled assumptions rather than historic executable quotes. Gaps, unaligned assets, or invalid exchange metadata must fail closed without interpolation.

`main` and ETH_BBMR_168_EMA720 PRIME must remain unchanged.