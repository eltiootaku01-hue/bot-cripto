# BTC/ETH 4h Relative-Strength Rotation — 15% ATR Target (Research Candidate)

**Status:** retrospective historical PASS; `SHADOW_ONLY`. Not a production approval.
**GitHub issue:** [#181](https://github.com/eltiootaku01-hue/bot-cripto/issues/181)
**Validation run:** [#40356916647](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647)
**Evidence artifact:** [rotation-15pct-atr-sensitivity](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647/artifacts/12667605121)

## Frozen rules

- Symbols: Binance Spot BTCUSDT and ETHUSDT; long or cash only, no leverage, shorts, AI, or real orders.
- Bars: closed UTC 4h candles; evaluate the signal on a completed candle and execute the virtual fill at the next 4h open.
- Every 42 completed evaluation bars (~7 days), compare trailing 180-bar returns.
- A coin is eligible only with positive 180-bar momentum and close above its own EMA200. Select the eligible asset with the greater momentum; if neither qualifies, hold cash.
- Position size at signal: `min(50%, 15% / ((ATR14 / close) * sqrt(365 * 6)))`. Freeze this requested exposure until the next target change/exit.
- Terminal-liquidate at each independent window end.

## Cost contract

| Scenario | Fee per fill | Full spread assumption | Slippage per side |
|---|---:|---:|---:|
| BASE | 0.10% | 0.10% | 0.05% |
| STRESS_1 | 0.10% | 0.25% | 0.10% |
| STRESS_2 | 0.20% | 0.50% | 0.20% |

Capital resets to 60 and 100 USDT independently for COMBINED (2025-01-01 through 2026-10-22), 2025, and 2026 YTD through 2026-10-22.

## Verified STRESS_2 metrics

| Window | Capital | Net PnL | Return | Max drawdown | Round trips |
|---|---:|---:|---:|---:|---:|
| Combined | 60 USDT | +9.9913 | +16.65% | -9.42% | 11 |
| Combined | 100 USDT | +16.7947 | +16.79% | -9.46% | 11 |
| 2025 | 60 USDT | +7.3814 | +12.30% | -6.09% | 6 |
| 2025 | 100 USDT | +12.4061 | +12.41% | -6.14% | 6 |
| 2026 YTD | 60 USDT | +3.7692 | +6.28% | -3.45% | 5 |
| 2026 YTD | 100 USDT | +6.2324 | +6.23% | -3.43% | 5 |

Dataset size: 4,254 aligned 4h records per asset including 300 warm-up bars. Metadata completeness is UNKNOWN on every candle; point-in-time availability and historical executable quotes are not established. The evaluated rules are current assumptions, and costs are modeled—not reconstructed book fills.

## Integrity hashes

- Risk-weight series SHA-256: `531c286416450f86686cb60f4a40d50c7e981ecd244d0c8e9c3211b7397e81b7`
- BTC full-record SHA-256: `b5e3f7e490af598f44885c5b24983c16d6141a3d7c3cfe6f469ff95712eceb66`
- ETH full-record SHA-256: `4fc086bbb600b4a51ee2881c5f54cc4121de583b83797e00207a2e63122dc392`
- Combined full-record SHA-256: `ab3d502ee849274926fd1b3fbb4a2e04f51c38b66249c161bb4f8584e2964a9e`
- Canonical OHLCV hashes matched the 10% artefact for all 4,254 bars per asset. Full-record hashes differ because of a volatile capture-time field.

## Promotion boundary

Historical PASS only justifies a prospectively registered shadow test. It does **not** establish a future edge and does not authorize PAPER/LIVE. Keep `NO_TRADE` outside a separately approved shadow process. Do not further tune the 15% value on this already-inspected sample. `main` and `ETH_BBMR_168_EMA720` PRIME remain untouched.
