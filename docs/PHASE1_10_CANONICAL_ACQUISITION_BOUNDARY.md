# FASE 1.10 — Canonical Acquisition Boundary

## Alcance

FASE 1.10 adds a provider-neutral boundary for controlled provider representations.
It stops at canonical `MarketData` and does not add exchange connectors, polling,
WebSockets, storage, workers, credentials, replay, strategy, risk, or execution.

## Boundary

```
ProviderRecord
      ↓
ParseResult
      ↓
NormalizationResult
      ↓
Instrument / Source resolution
      ↓
Canonical validation
      ↓
MarketData
```

`ProviderRecord` is intentionally non-canonical. It represents the values received
from a provider before the domain contract is crossed.

`MarketData` is the first canonical object. `MarketObservation` remains a later
promotion through the existing `to_market_observation()` function.

## Evidence preservation

The implementation does not infer evidence.

- `observed_at` is taken from the provider representation.
- `received_at` is supplied explicitly by the local acquisition context.
- `available_at` is preserved exactly when supplied and remains `None` when unknown.
- `available_at` is never replaced by `observed_at` or `received_at`.
- Instrument mappings require an explicit exact rule.
- `source_id` is an explicit input and is never derived from `provider`.
- Successful parsing never upgrades quality to `VALID`.
- `PARTIAL`, `COMPLETE`, and `UNKNOWN` remain independent from quality.

## Instrument mapping

Mappings match the complete provider identity tuple:

`provider + provider_symbol + provider_market + provider_venue`.

There is no textual-equivalence fallback. An unknown mapping is rejected and more than
one exact rule is rejected as ambiguous.

## Numeric normalization

OHLCV values are converted only from exact Decimal-compatible inputs. Float and bool
inputs are rejected; decimal scale is preserved. Structural OHLCV relationships remain
the responsibility of the canonical `Candle` constructor.

## Timeframes

The boundary rejects only empty timeframe strings. It intentionally does not introduce
a new universal timeframe catalog.

## Legacy isolation

The canonical boundary imports `Candle` only from `bot_obrero.market_data`.
The legacy `bot_obrero.data.Candle` remains untouched.

## Source sequence

`source_sequence` is preserved verbatim through normalization and into `MarketData`.
The boundary does not use it to invent timestamps, reorder events, deduplicate storage,
or change candle identity.

## Canonical contract

No field or invariant in `bot_obrero/market_data.py` is changed by this phase.
Canonical validation is delegated to the existing `Candle` and `MarketData`
constructors.

## Out of scope

No real provider, exchange, API key, HTTP client, WebSocket, polling loop, storage,
queue, worker, replay engine, strategy, indicator, risk, backtesting, paper trading,
or execution integration is implemented.
