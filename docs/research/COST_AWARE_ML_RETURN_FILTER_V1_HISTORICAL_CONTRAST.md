# Historical contrast — COST_AWARE_ML_RETURN_FILTER_V1

**Verdict: `NO_ROBUST_EDGE_FOUND / NO_TRADE`**

- Historical runner: [GitHub Actions run #39850349933](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39850349933), terminal result `completed / success`.
- This means the *research procedure* completed. The strategy's own economic gate is **FAIL**.
- Candidate code tested: `2f1bc891d1f1261ff3027e850dcf98f165827982`.
- Generated UTC: `2026-10-22T20:37:52Z`.
- Starting capital: **100 USDT**, reset independently at the start of each evaluation fold.
- Product: Binance Spot `BTCUSDT`, hourly Klines, 24-hour forecast target.

## Decision in one view

| Historical window | BASE candidate net PnL | STRESS_1 candidate net PnL | STRESS_2 candidate net PnL | Best candidate final equity | Gate |
|---|---:|---:|---:|---:|---|
| 2024 | -10.18 USDT | -26.03 USDT | -15.88 USDT | 89.82 USDT | **FAIL** |
| 2025 | -11.69 USDT | -2.84 USDT | -3.18 USDT | 97.16 USDT | **FAIL** |
| 2026 YTD (Jan 1–Oct 22) | -2.68 USDT | +4.69 USDT | -7.33 USDT | 104.69 USDT | **FAIL** |

The one positive result is 2026 YTD under STRESS_1: **+4.69 USDT, not +50 USDT**. The other eight of nine fold/scenario results are losses. No fold/scenario combination meets the full gate of beating cash, beating BTC buy-and-hold, netting at least +50 USDT, and respecting the drawdown limit. The overall historical gate is **FAIL**.

## Full results

All values start with 100 USDT. Return is net of the model's assumed fees, spread, and slippage. Drawdown is the sampled hourly liquidation-equity drawdown from the replay.

| Fold | Cost scenario | Method | Final USDT | Net PnL USDT | Return % | Max DD % | Fees USDT | Trades | Win rate % | Time in market % |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024 | BASE | COST_AWARE_ML_PROXY | 89.82017314 | -10.17982686 | -10.179827 | -38.058313 | 31.28120262 | 161 | 45.962733 | 42.151394 |
| 2024 | BASE | BTC_BUY_AND_HOLD | 214.38852628 | 114.38852628 | 114.388526 | -31.356363 | 0.30448218 | 1 | 100.000000 | 100.000000 |
| 2024 | BASE | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024 | STRESS_1 | COST_AWARE_ML_PROXY | 73.97400737 | -26.02599263 | -26.025993 | -37.079569 | 19.07275297 | 116 | 44.827586 | 30.369949 |
| 2024 | STRESS_1 | BTC_BUY_AND_HOLD | 213.98227421 | 113.98227421 | 113.982274 | -31.356472 | 0.30409567 | 1 | 100.000000 | 100.000000 |
| 2024 | STRESS_1 | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024 | STRESS_2 | COST_AWARE_ML_PROXY | 84.11643765 | -15.88356235 | -15.883562 | -30.020997 | 17.50114893 | 52 | 40.384615 | 13.614115 |
| 2024 | STRESS_2 | BTC_BUY_AND_HOLD | 212.71664349 | 112.71664349 | 112.716643 | -31.348342 | 0.60583260 | 1 | 100.000000 | 100.000000 |
| 2024 | STRESS_2 | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025 | BASE | COST_AWARE_ML_PROXY | 88.31142620 | -11.68857380 | -11.688574 | -26.044288 | 19.62213684 | 100 | 39.000000 | 26.252711 |
| 2025 | BASE | BTC_BUY_AND_HOLD | 93.62799259 | -6.37200741 | -6.372007 | -33.444164 | 0.18358614 | 1 | 0.000000 | 100.000000 |
| 2025 | BASE | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025 | STRESS_1 | COST_AWARE_ML_PROXY | 97.16092490 | -2.83907510 | -2.839075 | -24.576627 | 9.41790937 | 46 | 36.956522 | 12.076247 |
| 2025 | STRESS_1 | BTC_BUY_AND_HOLD | 93.45087791 | -6.54912209 | -6.549122 | -33.441902 | 0.18341091 | 1 | 0.000000 | 100.000000 |
| 2025 | STRESS_1 | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025 | STRESS_2 | COST_AWARE_ML_PROXY | 96.81995023 | -3.18004977 | -3.180050 | -13.078213 | 4.20221990 | 11 | 54.545455 | 2.887798 |
| 2025 | STRESS_2 | BTC_BUY_AND_HOLD | 92.92169070 | -7.07830930 | -7.078309 | -33.434866 | 0.36575833 | 1 | 0.000000 | 100.000000 |
| 2025 | STRESS_2 | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026_YTD | BASE | COST_AWARE_ML_PROXY | 97.32227381 | -2.67772619 | -2.677726 | -17.753942 | 9.25524682 | 49 | 40.816327 | 15.969959 |
| 2026_YTD | BASE | BTC_BUY_AND_HOLD | 95.78595193 | -4.21404807 | -4.214048 | -38.462024 | 0.18565656 | 1 | 0.000000 | 100.000000 |
| 2026_YTD | BASE | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026_YTD | STRESS_1 | COST_AWARE_ML_PROXY | 104.68783432 | 4.68783432 | 4.687834 | -18.066656 | 4.21396382 | 23 | 56.521739 | 7.496103 |
| 2026_YTD | STRESS_1 | BTC_BUY_AND_HOLD | 95.60436541 | -4.39563459 | -4.395635 | -38.461041 | 0.18548865 | 1 | 0.000000 | 100.000000 |
| 2026_YTD | STRESS_1 | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026_YTD | STRESS_2 | COST_AWARE_ML_PROXY | 92.67447669 | -7.32552331 | -7.325523 | -19.910222 | 3.75947840 | 11 | 45.454545 | 3.585093 |
| 2026_YTD | STRESS_2 | BTC_BUY_AND_HOLD | 95.06165529 | -4.93834471 | -4.938345 | -38.457826 | 0.36996212 | 1 | 0.000000 | 100.000000 |
| 2026_YTD | STRESS_2 | CASH | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Preregistered settings used

- Fold 2024: train on 2023 history; evaluate 2024.
- Fold 2025: train on history before 2025-01-01; evaluate 2025.
- Fold 2026 YTD: train on history before 2026-01-01; evaluate 2026-01-01 through 2026-10-22 00:00 UTC.
- Fixed CPU XGBoost parameters and causal 24-hour target; no hyperparameter search or tuning on each evaluation window.
- One position maximum, long/cash, 95% capital allocation, 24-hour hold, instrument quantity/notional checks.
- BASE: 0.10% fee per side, 5 bp slippage per side, 10 bp assumed full spread; estimated round trip 40 bp, entry threshold 60 bp.
- STRESS_1: 0.10% fee per side, 10 bp slippage per side, 20 bp assumed full spread; estimated round trip 60 bp, entry threshold 90 bp.
- STRESS_2: 0.20% fee per side, 20 bp slippage per side, 40 bp assumed full spread; estimated round trip 120 bp, entry threshold 180 bp.

## Data provenance and gap handling

- Market series: Binance Spot public archive, BTCUSDT 1h.
- 66 monthly/daily ZIP archives verified against each archive's published `.CHECKSUM` file.
- 33,359 observed candles from `2023-01-01T00:00:00Z` through `2026-10-21T23:00:00Z`.
- Canonical data SHA-256: `dcff1c815c5d4eb395d8cfa042a734cabbd31cec1c9bf9b6c43711c85916522a`.
- Archive-manifest SHA-256: `3cd1595c33990387c3ce26b8943c85b7e32cf7fcda697a343531d1a46b9d1452`.
- The archives omit `2023-03-24T13:00:00Z`. The exact-time public REST Kline request returned the empty JSON array `[]` (raw response SHA-256 `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`). That missing row was **not filled or interpolated**. Rolling features and labels were calculated independently on the two continuous sections.
- The manifest and generated CSV/JSON/Markdown outputs are uploaded by the historical workflow as a 90-day GitHub Actions artifact.

## What the result means

This candidate is not the winner. In 2024 the BASE model lost 10.18 USDT while BTC buy-and-hold ended at 214.39 USDT; the model also breached its 20% BASE drawdown limit. In 2025 it lost 11.69 USDT BASE. In 2026 YTD it lost 2.68 USDT BASE and gained only 4.69 USDT under STRESS_1. No fold/scenario met all the gates.

The results are retrospective, not a guarantee of future performance. Historical Klines do not provide historic Spot best bid/ask; the backtest uses a candle-close proxy with disclosed assumed spread and slippage. This is not quote-accurate fill evidence, not the prospective 365-day holdout, and not authorization for PAPER or LIVE.

**Operational decision: `NO_TRADE`.**