# BTC/ETH 4h rotation — ATR target 15% (candidate v1)

**Status:** historical gates passed; prospective shadow candidate only. **Operational decision: `NO_TRADE`**.  
**Canonical research issue:** [#181](https://github.com/eltiootaku01-hue/bot-cripto/issues/181)  
**Baseline candidate:** [#173 — 10% ATR target](https://github.com/eltiootaku01-hue/bot-cripto/issues/173)  
**Replay:** [workflow #40356916647](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647)  
**Research branch:** `research/btc-eth-rotation-15pct-atr-sensitivity`  
**Tested HEAD:** `aa7874a8c4b2dd7c3bd0708fa6e2a6fe91efdc73`

## Fixed method

- Venue/universe: Binance Spot BTCUSDT and ETHUSDT. Long or cash only; no shorts, leverage, derivatives, AI/ML decisions or real orders.
- Bars: UTC 4-hour closed candles; 300 warm-up bars before the evaluation interval.
- Decision cadence: every 42 completed 4-hour evaluation bars.
- Signal: for each asset compute trailing 180-bar momentum and its EMA200. It is eligible only when momentum is positive and close is above its own EMA200. Choose the eligible asset with the stronger momentum; hold cash if neither qualifies.
- Execution: decision from completed bar; execute at the next 4-hour open. Close an existing holding before opening a replacement. Liquidate remaining position at each independent evaluation-window end.
- ATR sizing: compute ATR14 from completed bars at the signal. Set the requested quote exposure once at entry and freeze it until exit:
  `weight = min(50%, 15% / ((ATR14 / close) * sqrt(365 * 6)))`.
- Maximum exposure: 50% of available quote. The target volatility is a proxy, not realized volatility or a guarantee.
- Costs: modeled, not reconstructed historical book quotes.
  - BASE: fee 10 bps/fill; assumed full spread 10 bps; slippage 5 bps/side.
  - STRESS_1: fee 10 bps/fill; assumed full spread 25 bps; slippage 10 bps/side.
  - STRESS_2: fee 20 bps/fill; assumed full spread 50 bps; slippage 20 bps/side.
- Capital resets independently to 60 and 100 USDT for each window and scenario.
- Windows are independent: 2025-01-01 to 2026-10-22 (exclusive), calendar 2025, and 2026 YTD to 2026-10-22 (exclusive).

## STRESS_2 results

All numbers are historical virtual results net of modeled fees/spread/slippage.

| Window | Initial capital | Net PnL | Return | Max drawdown | Round trips | Fees |
|---|---:|---:|---:|---:|---:|---:|
| Combined 2025–2026 YTD | 60 USDT | +9.99129 | +16.652% | -9.416% | 11 | 0.6478 |
| Combined 2025–2026 YTD | 100 USDT | +16.79469 | +16.795% | -9.464% | 11 | 1.0877 |
| 2025 | 60 USDT | +7.38137 | +12.302% | -6.087% | 6 | 0.3515 |
| 2025 | 100 USDT | +12.40606 | +12.406% | -6.141% | 6 | 0.5919 |
| 2026 YTD | 60 USDT | +3.76917 | +6.282% | -3.447% | 5 | 0.2837 |
| 2026 YTD | 100 USDT | +6.23236 | +6.232% | -3.426% | 5 | 0.4741 |

BASE and STRESS_1 also had positive PnL in each reported window for both capital sizes.

## Comparison with the 10% ATR-target baseline

Using the STRESS_2 result previously recorded in Issue #173:

| Window | Capital | 10% target PnL | 15% target PnL | Difference |
|---|---:|---:|---:|---:|
| Combined | 60 USDT | +6.35919 | +9.99129 | +3.63210 |
| Combined | 100 USDT | +11.08489 | +16.79469 | +5.70980 |
| 2026 YTD | 60 USDT | +2.51743 | +3.76917 | +1.25174 |
| 2026 YTD | 100 USDT | +4.20539 | +6.23236 | +2.02697 |

This is a retrospective sensitivity test on the same market history, not an independent test of future performance. The 15% setting increases exposure and drawdown versus the 10% setting.

## Frozen historical gates

Historical gate result: **5/5 PASS** on the tested data and assumptions:
1. PnL positive in Combined, 2025 and 2026 YTD at both capital sizes under STRESS_2.
2. Maximum drawdown not worse than -15% in each window.
3. Beats the 10% baseline in Combined and 2026 YTD at both capital sizes.
4. At least five round trips in Combined and 2026 YTD.
5. Deterministic ATR-sizing, causality and exposure-cap tests pass.

This gate result does not authorize PAPER or LIVE. Since the target was chosen after observing the retrospective windows, use a preregistered prospective shadow period before reconsidering approval. Do not run another target-size sweep over the same dates.

## Data provenance

- Evaluated 4h bars per asset: 4,254 (BTCUSDT and ETHUSDT).
- BTC full records SHA-256: `b5e3f7e490af598f44885c5b24983c16d6141a3d7c3cfe6f469ff95712eceb66`.
- ETH full records SHA-256: `4fc086bbb600b4a51ee2881c5f54cc4121de583b83797e00207a2e63122dc392`.
- Combined full records SHA-256: `ab3d502ee849274926fd1b3fbb4a2e04f51c38b66249c161bb4f8584e2964a9e`.
- Risk-weight series SHA-256: `531c286416450f86686cb60f4a40d50c7e981ecd244d0c8e9c3211b7397e81b7`.
- Independently compared canonical OHLCV content across the 10% and 15% artifacts: all 4,254 candles per asset are identical. Canonicalization is a JSON list of `start`, `end`, `timeframe`, `open`, `high`, `low`, `close`, `volume`, serialized with sorted keys and compact separators.
  - BTC canonical OHLCV SHA-256: `31d7fd79c6b3339de58917dc502c74e2c4d289461d766e9a1a8431ac1f6bb335`.
  - ETH canonical OHLCV SHA-256: `0869fbcb3d06fb10c77bc7218efffff09da1193b0733cdefd2b3073bfac27aa1`.
  - BTC+ETH canonical OHLCV SHA-256: `be4645f9abdfc5cc984b02a232efd7d8661d6211b105c35a1a87578e6c6c5523`.
- Completeness metadata is UNKNOWN for all bars. Candle finality metadata reported UNKNOWN=0 for this run. Historical exchange-filter equivalence and executable historic quotes remain UNKNOWN.
- GitHub workflow artifact digest SHA-256: `2dc23cad1334c5eeaa97a40c8715f57487fe005cfd4510aa838190c4cd53bee0` (1,304,842 bytes): [download from workflow](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647/artifacts/12667605121).

## Decision / safeguards

- Research status: `CANDIDATE_FOR_PROSPECTIVE_SHADOW`.
- Trading status: `NO_TRADE` outside an explicitly authorized shadow process.
- Keep `main` and `ETH_BBMR_168_EMA720` PRIME untouched.
- No AI/ML model is used to make trade decisions.
