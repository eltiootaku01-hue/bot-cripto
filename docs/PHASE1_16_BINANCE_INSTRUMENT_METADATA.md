# FASE 1.16 — Binance Spot instrument metadata

## Endpoint

The provider-specific integration uses:

`GET https://data-api.binance.vision/api/v3/exchangeInfo?symbol=<SYMBOL>`

Binance's official Spot documentation lists `GET /api/v3/exchangeInfo` as the exchange-information endpoint and the market-data-only documentation explicitly permits the `data-api.binance.vision` host for this endpoint. No authentication is required for this public market-data route.

The implementation queries one symbol at a time. It does not download the complete symbol catalog.

## Provider model

`bot_obrero/binance_instruments.py` defines `BinanceInstrumentRecord` with only the provider facts required by this phase:

- `provider_symbol`
- `base_asset`
- `quote_asset`
- `market`
- `venue`
- `status`
- `is_spot_trading_allowed`

This is deliberately separate from `InstrumentIdentity`.

## Mapping

The resolution chain is:

`ExchangeInfo -> BinanceInstrumentRecord -> BinanceInstrumentResolution -> InstrumentIdentity`

The canonical symbol is formed from Binance's explicit `baseAsset` and `quoteAsset` fields as `BASE/QUOTE`. The adapter never parses `BTCUSDT` to guess where the asset boundary lies.

The canonical `instrument_id` is namespaced as:

`binance:SPOT:<provider_symbol>`

This is a provider-specific identity, not a universal equivalence claim.

For example, Binance metadata can establish:

- provider symbol: `BTCUSDT`
- base asset: `BTC`
- quote asset: `USDT`
- market: `SPOT`
- venue: `BINANCE`

It does not establish that an unrelated provider's `XBT/USD` is the same instrument.

## Resolution outcomes

The provider resolver exposes:

- `FOUND`: exactly one valid Binance symbol record exists.
- `NOT_FOUND`: Binance returned no matching symbol record.
- `AMBIGUOUS`: more than one exact provider-symbol record was returned.
- `INVALID`: required metadata is malformed or inconsistent.

Acquisition additionally rejects an inactive instrument with `InstrumentInactive`.

There is no silent fallback to the legacy manual mapper in the metadata-backed facade.

## Status

The provider status is preserved as Binance metadata rather than translated into a new universal status taxonomy.

For acquisition, the metadata-backed resolver requires:

- `status == TRADING`
- `isSpotTradingAllowed == true`

Other statuses remain identifiable metadata but are rejected by `resolve_active()` for the current market-data acquisition path.

Binance's official Spot documentation defines symbol-status values including `TRADING`, `HALT`, `BREAK`, and other lifecycle values. The implementation therefore does not reinterpret an inactive status as active.

Importantly, status is not used to fabricate `available_at`.

## Base / quote

`baseAsset` and `quoteAsset` are consumed directly from ExchangeInfo.

The implementation does not assume:

`provider_symbol == baseAsset + quoteAsset`

as a universal parsing rule.

## Venue / market / source

These remain separate concepts:

- provider = `binance`
- venue = `BINANCE`
- market = `SPOT`
- source_id = `binance-spot-rest`

The ExchangeInfo metadata does not mutate `SourceIdentity` implicitly.

The existing canonical source identity remains created by the existing acquisition layer.

## Cache

Only an in-memory lookup cache is implemented.

- no database;
- no file persistence;
- no Redis;
- no scheduler;
- no worker;
- no background refresh.

A caller may explicitly request `refresh=True`. No automatic refresh exists.

## Future refresh concern

ExchangeInfo describes current exchange/instrument metadata, so a long-lived process would eventually need an explicit freshness policy. That policy is intentionally deferred. FASE 1.16 only provides one-shot retrieval plus process-local caching.

## Market data integration

The metadata-backed facade delegates actual kline conversion to the existing `BinanceSpotRestAdapter`.

The path is therefore:

`Binance ExchangeInfo -> explicit instrument mapping -> InstrumentIdentity -> existing Binance kline adapter -> ProviderRecord -> MarketData`

Historical acquisition continues to reuse the existing `fetch_historical_market_data()` implementation rather than duplicating pagination or canonicalization logic.

## Availability and quality

ExchangeInfo is instrument metadata, not kline availability evidence.

It does not set or infer `available_at`.

It also does not upgrade `quality`, completeness, or finality. Those remain governed by the existing market-data contract.

## Legacy compatibility

The FASE 1.10 explicit `InstrumentMapper` and the FASE 1.11/1.15 Binance adapter remain available.

The metadata-backed facade is additive. Existing manual BTCUSDT mappings are not deleted in this phase, allowing compatibility evidence to be established before any future migration decision.

## Errors

Provider-specific errors include:

- `ExchangeInfoTransportError`
- `ExchangeInfoHTTPError`
- `ExchangeInfoPayloadError`
- `InstrumentNotFound`
- `InstrumentInactive`
- `InstrumentMetadataInvalid`
- `InstrumentMappingAmbiguous`

The implementation does not hide malformed metadata or mapping ambiguity.

## Tests

Offline tests are in:

`tests/test_phase1_16_binance_instruments.py`

They cover valid and malformed metadata, not-found, ambiguity, inactive status, base/quote resolution, Spot, venue, source separation, cache/refresh behavior, canonical MarketData compatibility, and legacy mapper compatibility.

The manual integration check is:

`scripts/phase1_16_binance_exchange_info.py`

It performs one public `exchangeInfo?symbol=BTCUSDT` request and is intentionally excluded from CI.

## CI verification

The functional implementation was committed to `main`. A verification PR was opened from that exact state solely to execute the repository's existing CI workflow against the complete FASE 1.16 tree.

## Compatibility

No changes were made to:

- `bot_obrero/market_data.py`
- `bot_obrero/temporal.py`
- `bot_obrero/availability.py`

Therefore the Canonical Market Data Contract v1.0 remains unchanged.
