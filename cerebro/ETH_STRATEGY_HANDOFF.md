# ETH Strategy — CEREBRO handoff and BOT OBRERO implementation order

**Date:** 2026-10-16  
**Repository:** `eltiootaku01-hue/bot-cripto`  
**Authority:** User > CEREBRO / IA-CHAN > BOT OBRERO  
**Operational decision until evidence says otherwise:** `NO_TRADE`  
**Mode:** research and PAPER only; no real-money execution.

## 1. Purpose

Continue the ETH strategy work without losing decisions between chats. BOT OBRERO must locate and verify the actual ETH strategy specification and implementation state in GitHub before coding. Do not invent strategy parameters or claim that any strategy guarantees profit.

## 2. Observed repository context at handoff

- `cerebro/README.md` says GitHub and actual code are the source of truth; verify live branch/HEAD, PRs, files, and CI before material decisions.
- `cerebro/PROJECT_STATE.md` describes Product V2 research with 4-hour bars, 180-day default history, maximum five BUY entries in a rolling 30-day window, explicit modeled costs (10 bps full spread, 5 bps slippage per fill, 0.1% fee per fill), and an ARS-to-USDT affordability gate that remains `CONVERSION_UNKNOWN` until verified. It explicitly says research/backtests are not proof of profitability or readiness for real money.
- The currently surfaced open PR is #64, an isolated SQLite-backed PAPER execution adapter. Its description explicitly says Product V2 currently ends in `NO_TRADE` and does not yet emit a canonical Signal consumed by a runtime. Verify the current PR HEAD and live state directly before relying on these historical details.
- A repository search performed for this handoff did not surface a verifiable ETH-specific strategy specification. This is `UNKNOWN`, not proof that no such specification exists.

## 3. BOT OBRERO — exact prompt

Act as BOT OBRERO for `eltiootaku01-hue/bot-cripto`. Your task is to finish the ETH strategy only after identifying the canonical, previously approved strategy definition in the repository/history.

### Phase A — inspect first (mandatory; no writes until inspection is reported)

1. Read `cerebro/README.md` and `cerebro/PROJECT_STATE.md`.
2. Verify current `main` HEAD, working branch, recent commits, open PRs, PR #64 current HEAD/status, and relevant CI runs. Report exact refs/SHAs and timestamps when available.
3. Search the entire accessible repository history, branches, PR descriptions/reviews/comments, docs, tests, workflow artifacts and research outputs for the ETH strategy and the last approved parameters. Look for references such as ETH, ETHUSDT, Ethereum, strategy, signal, backtest, walk-forward, fees, and risk.
4. Identify whether the ETH strategy has an explicit canonical specification. Extract, with file/line or PR/commit links, at least: timeframe; entry and exit rules; indicator definitions and warm-up; position sizing; stop/exit logic; maximum concurrent positions; trade-frequency limits; quote/data requirements; costs; benchmark; in-sample/out-of-sample periods; and acceptance thresholds.
5. Classify every claim as `OBSERVED`, `TESTED`, `INFERRED`, `UNKNOWN`, or `BLOCKED`. Do not use a prior chat summary as a substitute for repository evidence.

### Phase B — decision gate

- If an approved ETH specification is found, cite it exactly and compare it with the current implementation. Proceed to Phase C only if its rules and acceptance criteria are unambiguous and compatible with existing architecture.
- If no canonical specification is found, or if essential strategy rules/thresholds conflict or are missing, STOP. Do not fabricate indicators, parameter values, risk limits, or profitability thresholds. Publish a read-only findings report on a separate documentation branch and mark `REQUIRES CEREBRO REVIEW`, listing only the specific decisions CEREBRO/the user must make.
- Do not replace an existing approved strategy with a new strategy merely because a new one is easier to implement.

### Phase C — implement the approved ETH strategy (only after Phase B passes)

1. Inspect existing strategy interfaces and reuse them: `REUTILIZAR > ADAPTAR > CREAR`. Do not create a parallel strategy runtime, risk engine, ledger, or execution path.
2. Implement the smallest coherent change on a dedicated branch; never edit `main` directly.
3. Keep ETH strategy research independent from execution. The strategy must emit the repository's canonical signal/proposal contract; do not bypass Risk, Financial Admission, reservation, idempotency, or execution boundaries.
4. Preserve fail-closed behavior: missing/stale/future/gapped data, insufficient warm-up, unknown fees/filters, invalid Decimal values, ambiguous state, or failed risk checks must not increase exposure and may result in `NO_TRADE`.
5. Use public market data and deterministic offline fixtures for development. No private API keys, real orders, real-money trading, leverage, or margin.
6. Financial quantities and fees must use `Decimal`; timestamps must be timezone-aware and aligned to the chosen candle boundaries. Avoid look-ahead bias: a signal may use only data available at its decision timestamp, and fills must occur no earlier than the modeled executable point.
7. Include fees, spread and slippage in the economic result. Clearly distinguish measured data from modeled assumptions; do not call modeled spread historical fact.
8. Compare ETH strategy performance against at least a 0% cash benchmark and a buy-and-hold ETH benchmark over identical timestamps and cost assumptions. Use chronologically separated development and out-of-sample evaluation; report drawdown, trade count, exposure, net return, volatility or another approved risk measure, and sensitivity to costs. Do not tune on the out-of-sample segment.
9. Enforce the project's existing rolling limit of no more than five BUY entries per 30 days if it applies to this strategy; verify whether the live canonical contract scopes it globally or per instrument before implementation. Never force a trade to meet a quota.
10. Do not claim the strategy is profitable or ready for live trading from a backtest alone. A negative, fragile, insufficient or inconclusive result means `NO_TRADE`, not relaxed acceptance criteria.

### Phase D — verification and delivery

- Add deterministic unit tests for every entry/exit rule, warm-up, candle-boundary timing, missing/gapped/stale data, duplicate signals, costs, rounding, risk rejection, and no-look-ahead behavior.
- Add an end-to-end research/backtest test using deterministic fixtures. Run the focused tests and full test suite where feasible; report exact commands and actual results. Verify remote CI against the exact PR HEAD; never represent remote CI as a local test.
- Inspect the diff for scope, unintended changes, test omissions, security and architectural regressions.
- Update relevant strategy documentation and `cerebro/PROJECT_STATE.md` only on the work branch, with evidence-backed facts and clear `UNKNOWN` values.
- Open a PR; do not merge it. Do not change production workflows or execution contracts unless explicitly authorized.
- Final report must include: current HEAD/branch, discovered specification and source links, files changed, strategy rules implemented, test commands/results, CI links/status for exact SHA, backtest period and data provenance, cost assumptions, cash and ETH benchmark comparison, limitations, unresolved risks, and recommendation (`NO_TRADE` or further research unless all approved criteria pass).

## 4. Acceptance gate

Implementation is not accepted merely because tests pass. It must implement the approved strategy exactly, avoid look-ahead bias, account for costs, demonstrate reproducible out-of-sample evidence against comparable benchmarks, preserve risk and execution boundaries, and pass CI on the exact PR HEAD. Even then, this is research/PAPER evidence, not a promise of future returns or authorization for live trading.

## 5. Stop conditions

Stop and mark `REQUIRES CEREBRO REVIEW` for missing canonical ETH rules, conflicting contracts, changes to established financial/execution boundaries, unclear position-sizing or loss limits, insufficient data provenance, or a need to introduce new infrastructure. Report evidence and ask only the narrow decision needed; do not silently decide on the user's behalf.
