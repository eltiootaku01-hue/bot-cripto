# BTC→ETH independent-episode test V1 — preregistration

**Status:** protocol frozen before running the V1 episode-result comparison.  
**Decision boundary:** exploratory historical study only; this protocol cannot unlock PAPER automation or LIVE.  
**Decision default:** `NO_TRADE`.

## Provenance and known exposure

- Archived dataset logical SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Configuration SHA-256: `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`.
- 15,360 aligned hourly candles each for BTCUSDT and ETHUSDT, 2025-01-15 19:00 UTC through 2026-10-17 18:00 UTC.
- The archive has unknown historical `available_at` and candle finality for all 30,720 rows. These prices permit retrospective exploration but are not verified as information available to a historical live decision.
- **Important exposure disclosure:** the broad V1 diagnostic was already inspected before this hypothesis was formalized. Its earlier BTC-leading-ETH signal had only 10 accepted non-overlapping episodes and a negative BASE result. This V1 episode analysis is therefore a fixed follow-up/diagnostic, **not** a new untouched holdout. No p-value here can promote a strategy as a winner.

## One fixed hypothesis; no parameter search

**Hypothesis:** after a strong four-hour BTC impulse, if ETH has not already made a large move and remains above its own 100-hour EMA, ETH's following 24-hour long-only return may be positive and may exceed the return from passive benchmarks after modeled transaction costs.

At the close of hourly bar (t), a signal fires only if all are true:
1. BTCUSDT close-to-close four-hour return (geq +1.50%).
2. ETHUSDT close-to-close four-hour return is between (-0.50%) and (+0.80%), inclusive.
3. ETHUSDT close at (t) is strictly greater than its 100-hour EMA calculated with bars through (t) only.

Execution/replay:
- Decision is after close (t); entry price proxy is ETH open of bar (t+1).
- Exit price proxy is ETH open of bar (t+25), a fixed 24-hour holding interval after entry.
- Only one position is allowed at a time. Once a signal is accepted, any later signal whose decision timestamp would overlap its holding interval is skipped. The next eligible episode can begin when the previous trade exits, without overlapping exposure.
- Returns compound sequential accepted episodes within each calendar month; months with no episodes return 0% for the strategy.
- No leverage, no short positions, no parameter retuning, no ranking of multiple candidate rules.

## Cost scenarios, frozen

Each modeled round trip applies entry and exit costs symmetrically:
- `BASE`: fee 0.10% each side, assumed full spread 10 bps, slippage 5 bps each side.
- `STRESS_1`: fee 0.10% each side, assumed full spread 25 bps, slippage 10 bps each side.
- `STRESS_2`: fee 0.20% each side, assumed full spread 50 bps, slippage 20 bps each side.

These are assumptions, not measured historical fill-level spreads/slippage.

## Monthly comparators

Create a row for every calendar month touched by the archive, including months without a signal, for each of the three cost scenarios:
- Strategy: compound net episode returns executed with that scenario's costs; 0% in no-trade months.
- Cash: 0% return.
- BTC monthly isolated buy-and-hold reference: buy at first hourly open at/after the UTC month boundary, sell at first hourly open at/after the next UTC month boundary, apply that scenario's round-trip costs.
- ETH monthly isolated buy-and-hold reference: same monthly boundary convention, applied to ETH.

The per-month isolated buy-and-hold is a monthly reference, not a continuously held account. Also report continuous BTC and ETH buy-and-hold over the full archive with only one modeled round trip at the overall endpoints, under the same three cost scenarios, to avoid conflating the two interpretations. Report win months, months with trades, worst month, max drawdown on the sequential episode-return series, trade count, gross/net averages, medians, and win rate. Missing coverage, schema mismatch, altered pinned hashes or non-finite values must fail closed.

## Bootstrap and inference (frozen)

- Primary comparison: monthly strategy BASE **net log return minus the maximum of cash, isolated monthly BTC buy-and-hold BASE and isolated monthly ETH buy-and-hold BASE log return**. This conservative monthly excess must beat all three monthly alternatives.
- Secondary paired comparisons: strategy minus each of cash/BTC/ETH monthly references for each of BASE/STRESS_1/STRESS_2. Mark secondary results exploratory; do not promote a winner from the most favorable cell.
- Use a paired **circular moving-block bootstrap over the ordered monthly differences**, block length 3 consecutive months, 10,000 replicates, fixed seed `20261019`. Generate a percentile 95% interval from resampled observed differences and a one-sided null-centered bootstrap p-value for mean difference (>0), with +1 finite-sample correction.
- Also report a paired 3-month block interval for mean daily event-return excess as a diagnostic, but do not substitute the hourly event count for independent sample size.
- There are only about 22 calendar-month units. Report this low effective sample size and bootstrap uncertainty plainly.

## Eligibility rule and interpretation

This retrospective screen is **not eligible to promote** any strategy, regardless of the numeric output. Report whether the predefined hypothesis is supported or not supported on this historical screen, but keep the operational decision `NO_TRADE`. A stronger future claim requires a separately preregistered future period with trustworthy point-in-time availability/finality, untouched until all rules and costs are frozen. No PAPER/LIVE or real orders can be authorized by this study.

## Required outputs and integrity

Store the exact signal definition, this protocol hash, source logical/raw hashes, configuration hash, Python/dependency versions, run SHA, monthly result CSVs, episode trade ledger, bootstrap comparisons, full-period benchmark references, and an output manifest with SHA-256 for each artifact. Retain all months and negative outcomes. Workflow must recheck pinned hashes, counts, file sizes and output hashes before uploading evidence.
