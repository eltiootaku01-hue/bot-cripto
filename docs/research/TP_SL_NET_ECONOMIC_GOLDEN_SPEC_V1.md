# TP/SL net economic contract — golden-test specification V1

**Status:** research-only specification and synthetic arithmetic acceptance vectors.  
**Branch:** `research/tp-sl-wave-simulation-2026-01-to-10`.  
**Decision gate:** `NO_TRADE`.  
**Production scope:** none. The simulator, V1 signals, historical artifacts, barrier constants and cost assumptions are not modified by this change.

## Purpose

Turn the documented gross-versus-net TP/SL semantics into repeatable numerical regression tests. Catch changes that accidentally:
- treat +1.00% TP / −0.50% SL as net account returns;
- calculate triggers from the raw candle open instead of the modeled entry fill;
- omit either entry or exit fees;
- apply modeled spread/slippage to only one side;
- confuse the legacy return field against the raw open with the gross return from entry fill;
- break the relationship between trade net PnL and final account equity.

These are **synthetic deterministic tests only**. The test does not download or consume the frozen V1 prediction ledger, historical candles, or the Jan–Oct 2026 evaluation. It does not search parameters or evaluate strategy performance.

## Frozen contract under test

Let:
- `P0` be the raw open of the entry candle;
- `e = (spread_bps / 2 + slippage_bps) / 10_000` be the modeled adverse price adjustment per side;
- `f` be the commission rate per side;
- `P_entry = P0 × (1 + e)`;
- `P_TP = P_entry × (1 + 0.01)`;
- `P_SL = P_entry × (1 − 0.005)`;
- `P_exit = exit_trigger_raw × (1 − e)`.

For these synthetic golden cases, initial capital is 1,000 USDT, entry allocation is 95% (950 USDT), the entry candle touches exactly one of the barriers, no gap or time exit occurs, and quantity remains continuous as in the historical simulator.

The current accounting equations are:

```text
quantity = 950 / (P_entry × (1 + f))
entry_fee_usdt = quantity × P_entry × f
entry_cash_outlay_usdt = quantity × P_entry + entry_fee_usdt
exit_fee_usdt = quantity × P_exit × f
total_fees_usdt = entry_fee_usdt + exit_fee_usdt
net_pnl_usdt = quantity × P_exit − exit_fee_usdt − entry_cash_outlay_usdt
net_return_on_entry_outlay_pct = 100 × net_pnl_usdt / entry_cash_outlay_usdt
```

The current legacy ledger field is:

```text
gross_price_return_pct = 100 × (exit_trigger_raw / entry_open_raw − 1)
```

It is **not** return from entry fill. For TP/SL barrier semantics, the independent assertion is:

```text
gross_return_from_entry_fill_pct = 100 × (exit_trigger_raw / entry_fill − 1)
```

That last name represents the assertion's mathematical basis in the test; this test-only change does not add or rename output columns. A future schema change must be reviewed and versioned separately.

## Cost scenarios and golden results

The production module supplies the frozen scenario configuration; the following results are literal expected values in `research/test_tp_sl_economic_contract_golden_v1.py`, not outputs recomputed by the assertions.

| Scenario | Fee per side | Execution adjustment per side | Outcome | Gross return from entry fill | Legacy `gross_price_return_pct` vs raw open | Total fees (USDT) | Net PnL (USDT) | Net return on entry outlay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BASE | 0.10% | 0.100% | TP | +1.000000% | +1.101000% | 1.9066338661 | +6.6253341658 | +0.6974035964% |
| BASE | 0.10% | 0.100% | SL | −0.500000% | −0.400500% | 1.8924123377 | −7.5819727772 | −0.7981023976% |
| STRESS_1 | 0.10% | 0.225% | TP | +1.000000% | +1.227250% | 1.9054356893 | +5.4283555195 | +0.5714058442% |
| STRESS_1 | 0.10% | 0.225% | SL | −0.500000% | −0.276125% | 1.8912319555 | −8.7611745130 | −0.9222288961% |
| STRESS_2 | 0.20% | 0.450% | TP | +1.000000% | +1.454500% | 3.8027589820 | +1.3691472056 | +0.1441207585% |
| STRESS_2 | 0.20% | 0.450% | SL | −0.500000% | −0.052250% | 3.7744438623 | −12.7600975549 | −1.3431681637% |

Golden values use tolerance `1e-8` for reported money and net-return outputs, and tighter tolerances for entry/exit trigger levels. This tolerates binary floating-point representation without permitting meaningful accounting drift. The values are tied to the current float-based research simulator; they do not establish exchange-grade decimal rounding.

## Acceptance checks

The suite executes a one-signal, one-trade synthetic replay per combination (three cost cases × TP/SL), and asserts:

1. Production constants and 95% allocation are unchanged.
2. The real entry logic uses the modeled fill; TP and SL triggers are both measured from that fill.
3. The selected outcome is the only barrier touched in that synthetic candle.
4. Entry fill, TP trigger, SL trigger, exit fill and quantity match literal golden vectors.
5. Entry fee and exit fee independently match their golden values; total fee equals their sum.
6. Gross return from entry fill is exactly the TP/SL contract (+1.00% / −0.50%) while the legacy field matches the raw-open basis and differs from the fill basis.
7. Net PnL agrees both with the golden result and with the accounting identity; net return uses the actual entry cash outlay.
8. Portfolio exit counts, fee totals, final equity and net PnL reconcile to the single trade.

## CI isolation

The dedicated workflow `.github/workflows/tp-sl-economic-golden-v1.yml` runs only this synthetic test file on the research branch when the specification, test, or test-only workflow changes. It does not download historical data, invoke the full replay, generate research performance tables, or upload backtest artifacts.

Command executed by the workflow:

```bash
python -m pytest -q research/test_tp_sl_economic_contract_golden_v1.py
```

## Explicit non-goals and promotion gates

- Do not modify the V1 forecast ledger, signals, signal generation, TP/SL constants, or cost scenarios.
- Do not rerun, recalibrate, or use the Jan–Oct 2026 window to tune a strategy.
- Do not rename or alter the existing ledger fields in this test-only task.
- Do not claim these tests prove historical profitability or real exchange execution.
- A later schema or rounding-policy change requires separate review, a versioned output contract, and tests for tick-size, lot-size and quote-currency rounding.
- Keep `main` unchanged and operational decision at **`NO_TRADE`**.
