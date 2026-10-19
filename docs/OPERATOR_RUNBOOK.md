# BOT-CRIPTO operator runbook

## Purpose

Use the installed `bot-cripto` command as the single entry point for the existing analysis, virtual backtest, research and prospective-capture modules. This is an operator facade over the versioned package, not a new financial execution engine.

## First run

```powershell
.\.venv\Scripts\bot-cripto.exe status
.\.venv\Scripts\bot-cripto.exe analyze --help
.\.venv\Scripts\bot-cripto.exe backtest --help
.\.venv\Scripts\bot-cripto.exe research-btc-eth --help
.\.venv\Scripts\bot-cripto.exe capture-24h --help
```

The status report intentionally keeps `NO_TRADE`: the historical leader `ETH_TSMOM_168H` failed the registered robustness criteria, and V2 has not started. No code path in this command changes that decision.

## Historical virtual simulation

Example single-symbol simulation:

```powershell
.\.venv\Scripts\bot-cripto.exe backtest --symbol ETHUSDT --interval 1h --candles 500 --liquidate-at-end --dataset-output evidence/ethusdt-virtual.json
```

This uses the existing deterministic EMA/RSI virtual backtest; the fees, spread and slippage are assumptions configured for the simulation. Inspect the emitted fills, final equity, drawdown, and evidence hashes. This is not the seven-candidate prospective V2 study.

Paired BTC/ETH historical research:

```powershell
.\.venv\Scripts\bot-cripto.exe research-btc-eth --days 365 --initial-usdt 31 --dataset-output evidence/btc-eth-historical.json
```

This is retrospective research. It must not be relabeled as a future holdout, and a result does not clear the operational strategy gate.

## Real public-data smoke capture (P0)

Prerequisites: Git and Python 3.11+, a persistent Windows machine, an up-to-date clean clone of `main`, stable internet, and enough disk space. Start before the next UTC midnight to avoid waiting until the following day.

```powershell
git fetch origin main
git status --short
git switch main
git pull --ff-only origin main
.\.venv\Scripts\bot-cripto.exe capture-24h --output-dir evidence/prospective-btc-eth-pair
```

The paired collector itself enforces the source-provenance gate: repository root, branch `main`, clean relevant source paths, and exact equality among `HEAD`, `origin/main`, and live remote `main`. Do not edit source while the capture is waiting or running. The collector runs the independent Kline and bookTicker validators and the joint overlap validator. It must finish with `status: VALID`; otherwise keep the artifacts and logs for diagnosis.

A valid 24-hour smoke capture establishes acquisition/temporal integrity only. It does not demonstrate profitability. The 365-day prospective holdout must be preregistered and separately completed before changing the decision from `NO_TRADE`.

## Failure and safety policy

- Keep the original manifest, logs and datasets after a failure. Never patch a failed manifest, timestamp, count or hash to make it valid.
- Do not commit large JSONL capture files to `main`; preserve immutable copies and record their hashes.
- Do not use keys, private API endpoints, live orders, margin, leverage or real funds.
- On `UNKNOWN`, invalid evidence, stale quote, data gap or failed reconciliation: stop and diagnose; do not increase exposure.
