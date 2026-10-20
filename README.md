# BOT-CRIPTO

BOT-CRIPTO is a public-market-data research and local PAPER-simulation project for Binance Spot. It includes market-data validation, SMA/EMA/RSI analysis, virtual backtests, a hash-verified BTC/ETH prospective V2 economic replay, paired Kline/bookTicker capture, and an isolated SQLite-backed PAPER execution path.

**Current economic decision: `NO_TRADE`.** No strategy has passed the required independent robustness gate. `ETH_TSMOM_168H` is a historical research candidate, not a proven winner. The prospective `BTC_ETH_STRATEGIES_V2` executor is `IMPLEMENTED / NOT RUN`; no real smoke bundle has been accepted for the frozen V2 protocol and no prospective 365-day holdout has completed. Do not infer profitability from green software tests.

## Install on Windows

Recommended automated setup (requires Git and Python 3.11 or newer):

- If you already have a clean Git clone of this repository, use the manual install commands below from the repository root.
- If you have a ZIP/source snapshot or are running from a separate tools folder, the installer creates a **separate clean clone of current `main`**. Choose a destination that does not already exist:

  ```powershell
  .\tools\windows-handoff\INSTALAR_BOTCRIPTO_PAPER.ps1 -Destination "$HOME\BOT-CRIPTO-RUN"
  ```

  The installer refuses to overwrite an existing destination, verifies that the clone's `HEAD` matches `origin/main`, creates a virtual environment, installs the package and smoke-checks the command. It does not start a market capture or execute trades.

To install manually from the root of an existing Git clone:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m bot_obrero.operator_cli status
```

## One-command operator interface

After installation, use `.venv\Scripts\bot-cripto.exe` from PowerShell, or `python -m bot_obrero.operator_cli`:

```powershell
.\.venv\Scripts\bot-cripto.exe status
.\.venv\Scripts\bot-cripto.exe analyze --symbol ETHUSDT --interval 4h --candles 100
.\.venv\Scripts\bot-cripto.exe backtest --symbol ETHUSDT --interval 1h --candles 500 --liquidate-at-end
.\.venv\Scripts\bot-cripto.exe research-btc-eth --days 365 --initial-usdt 31
.\.venv\Scripts\bot-cripto.exe prospective-v2 --help
```

- `status` reports the current project policy and the still-open strategy evidence gates.
- `analyze` is read-only and uses public Binance Spot data.
- `backtest` executes a historical **virtual** EMA/RSI simulation with modeled costs; it does not submit exchange orders.
- `research-btc-eth` is historical BTC/ETH virtual research. It is not the prospective V2 holdout and cannot activate a strategy.
- `prospective-v2` provides hash-bound filter/warm-up capture, pre-T0 registration, continuous one-year public-data capture and the final economic replay. Follow [the full V2 operator procedure](docs/research/BTC_ETH_PROSPECTIVE_V2_OPERATOR.md); the year-long collection must run on a persistent operator machine and cannot be performed by GitHub CI.

### Required real-data smoke capture

Run this only on the clean, up-to-date `main` clone created by the installer. It waits for the next UTC midnight, captures the public Kline and bookTicker streams together, and validates at least 24 hours of real overlap. Keep the PC, network, PowerShell process and repository available throughout the run:

```powershell
.\.venv\Scripts\bot-cripto.exe capture-24h --output-dir evidence/prospective-btc-eth-pair
```

This capture may take more than 24 hours because it aligns to UTC midnight and includes an overlap guard. It uses public endpoints only. A failed or interrupted capture remains blocked; do not edit manifests or hashes to force acceptance. Keep the generated evidence outside source commits and follow [the operator runbook](docs/OPERATOR_RUNBOOK.md).

## Safety boundary

- Public Spot market data only for acquisition and research.
- PAPER execution is local and uses fictitious balances.
- No credentials, private endpoints, live orders, margin, leverage or real money.
- `UNKNOWN`, missing evidence or incomplete capture means blocked, not permission to trade.
- CI passing demonstrates software behavior, not economic edge or future profit.
