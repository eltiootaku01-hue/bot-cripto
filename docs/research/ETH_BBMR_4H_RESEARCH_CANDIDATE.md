# ETH Bollinger 4h + EMA180 — candidata para validación, no ganadora

**Profile:** `eth-bbmr-4h-research`  
**Estado:** `RESEARCH_ONLY / NOT PROMOTED`  
**Decisión operativa:** `NO_TRADE`  
**Uso:** replay virtual y validación de investigación solamente.

## Regla congelada

- Mercado: ETHUSDT Spot.
- Intervalo: 4 horas.
- Capital inicial en los replays: 60 USDT.
- Tamaño por entrada: 95 % del efectivo.
- Bollinger z-score: `z = (close - SMA(close, 48)) / rolling_std(close, 48, ddof=1)`.
- Entrada long si `z < -0.75` y `close > EMA180`.
- Salida si `z > +2.0` o `close <= EMA180`.
- La señal usa el cierre de una vela completada; el fill se simula en la apertura de la siguiente vela.
- Máximo cinco compras en una ventana móvil de 30 días; sin margen, apalancamiento ni cortos.

El perfil está bloqueado a `ETHUSDT/4h` y `EMA180`; los parámetros no se aceptan desde CLI. No envía órdenes ni abre conexiones privadas.

## Ejecución de replay virtual

Run base and save the hash-bound candle bundle:

```powershell
.\.venv\Scripts\bot-cripto.exe backtest --strategy eth-bbmr-4h-research --symbol ETHUSDT --interval 4h --candles 3839 --initial-quote-balance 60 --position-fraction 0.95 --min-qty 0.0001 --quantity-step 0.0001 --min-notional 5 --liquidate-at-end --dataset-output evidence/eth-bbmr-4h-research-60usdt.json
```

Run the same saved candles under STRESS_1 and STRESS_2. Do not recapture the market between scenarios:

```powershell
.\.venv\Scripts\bot-cripto.exe backtest --strategy eth-bbmr-4h-research --symbol ETHUSDT --interval 4h --candles 3839 --initial-quote-balance 60 --position-fraction 0.95 --min-qty 0.0001 --quantity-step 0.0001 --min-notional 5 --fee-rate 0.001 --spread-bps 25 --slippage-bps 10 --liquidate-at-end --dataset-input evidence/eth-bbmr-4h-research-60usdt.json
.\.venv\Scripts\bot-cripto.exe backtest --strategy eth-bbmr-4h-research --symbol ETHUSDT --interval 4h --candles 3839 --initial-quote-balance 60 --position-fraction 0.95 --min-qty 0.0001 --quantity-step 0.0001 --min-notional 5 --fee-rate 0.002 --spread-bps 50 --slippage-bps 20 --liquidate-at-end --dataset-input evidence/eth-bbmr-4h-research-60usdt.json
```

Cost assumptions:
- BASE: 0.10% commission per fill, assumed full spread 10 bps, slippage 5 bps per fill.
- STRESS_1: 0.10% commission, full spread 25 bps, slippage 10 bps.
- STRESS_2: 0.20% commission, full spread 50 bps, slippage 20 bps.

Spread/slippage are modeled assumptions, not observed historical bid/ask. The explicit `min-qty`, `quantity-step` and `min-notional` values are a fixed assumption from the archived filter snapshot; they do not prove the same exchange filters applied through the entire sample. The program warns about that boundary.

## Result that motivated the profile

An exploratory screen covered 29,179 parameter configurations across eight families on BTCUSDT and ETHUSDT. The best candidate that had positive STRESS_2 returns in all three chronological folds and at least two round-trips in each fold was this ETH 4h profile.

**Continuous replay on the inspected ~640-day archive, one 60 USDT account:**

| Scenario | Final equity | Net PnL | Cumulative return | Max drawdown | Round-trips |
|---|---:|---:|---:|---:|---:|
| BASE | 86.1879 USDT | +26.1879 | +43.65% | −13.33% | 33 |
| STRESS_1 | 79.7081 USDT | +19.7081 | +32.85% | −14.33% | 33 |
| STRESS_2 | 64.9876 USDT | +4.9876 | +8.31% | −19.11% | 33 |

The same-simulator ETH buy-and-hold baseline ended at 42.5762 USDT in STRESS_2 (−29.04%) in that period; cash remained 60 USDT. ETH fell over this sample, so beating a negative benchmark does not, by itself, establish future edge.

### Chronological folds (each fold independently restarts at 60 USDT)

| Fold | STRESS_2 final | Net PnL | Return | Max drawdown | Round-trips | Wins/losses |
|---|---:|---:|---:|---:|---:|---:|
| 2025-01-15 to 2025-10-12 | 61.9799 USDT | +1.9799 | +3.30% | −16.99% | 17 | 6/11 |
| 2025-10-12 to 2026-04-12 | 61.7099 USDT | +1.7099 | +2.85% | −3.16% | 2 | 1/1 |
| 2026-04-12 to 2026-10-17 | 61.1868 USDT | +1.1868 | +1.98% | −11.66% | 14 | 6/8 |

The combined win/loss count is 13 wins and 20 losses. The middle six-month fold contains only two round-trips, below the pre-set minimum of three round-trips per fold. Sensitivity is material: neighboring entry/exit z-score settings turn some folds negative or produce no trades.

## Search verdict

- 29,179 configs examined × three folds × three cost models = 262,611 simulated configuration/fold/cost combinations.
- 31 configs had positive STRESS_2 return in all three folds without minimum trade count.
- Five of those had at least two round-trips in each fold.
- **Zero** configs passed the strict gate of positive STRESS_2 returns in all folds, at least three round-trips in each fold, and no fold drawdown worse than −30%.

The archived 2025–2026 history was already used in prior BOT-CRIPTO research, so these folds are not a pristine untouched holdout. Multi-configuration selection inflates apparent historical results unless controlled; see the discussion of selection bias and multiple-testing in the [Deflated Sharpe Ratio paper](https://doi.org/10.3905/jpm.2014.40.5.094).

The candle archive's `available_at` is not proven point-in-time and candle finality is `UNKNOWN`; spread/slippage and lot filters are assumptions. The replay is a simulated next-open model and cannot reproduce queue priority, intrabar gaps, market impact, or actual historical fills.

## Safety / decision

This is an additional **research-only candidate**, not a winning-strategy declaration. It is not the existing 1h TOP1 and it has **not** been promoted to operational PAPER or LIVE. Project status remains `NO_TRADE`. Freeze this profile for a new evaluation; do not reoptimize it on the same test window, and do not risk real money until independent prospective data and economic reconciliation satisfy their gates.
