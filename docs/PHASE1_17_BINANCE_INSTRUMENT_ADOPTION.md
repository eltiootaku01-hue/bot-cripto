# FASE 1.17 — Adopción de resolución Binance Spot respaldada por ExchangeInfo

## Estado

FASE 1.17 adopta explícitamente la resolución de instrumentos creada en FASE 1.16 como frontera de entrada de la adquisición Binance Spot metadata-backed.

La implementación existente de FASE 1.16 ya encapsulaba el adapter canónico mediante `BinanceMetadataBackedAdapter`. Esta fase no crea un segundo parser, normalizador ni paginador: formaliza y prueba su uso como camino de adquisición.

## Flujo

~~~text
Binance ExchangeInfo
        ↓
BinanceInstrumentRecord
        ↓
BinanceSpotInstrumentMetadata.resolve_active()
        ↓
InstrumentIdentity
        ↓
BinanceMetadataBackedAdapter
        ↓
existing BinanceSpotRestAdapter
        ↓
ProviderRecord / canonical acquisition pipeline
        ↓
MarketData
~~~

El método público `BinanceMetadataBackedAdapter.resolve_instrument()` expone la resolución activa que alimenta la adquisición.

## Resolución de símbolos

Para cada símbolo se consumen los campos explícitos de ExchangeInfo:

- `symbol`
- `baseAsset`
- `quoteAsset`
- `status`
- `isSpotTradingAllowed`

No se analiza `BTCUSDT` para adivinar sus activos.

Ejemplos offline:

~~~text
BTCUSDT + BTC + USDT
    → binance:SPOT:BTCUSDT
    → BTC/USDT
    → SPOT

ETHUSDT + ETH + USDT
    → binance:SPOT:ETHUSDT
    → ETH/USDT
    → SPOT
~~~

La identidad sigue siendo provider-specific. No se afirma equivalencia universal con otros proveedores.

## Live

La ruta:

~~~text
metadata resolution
        ↓
InstrumentIdentity
        ↓
existing BinanceSpotRestAdapter.fetch_market_data()
        ↓
MarketData
~~~

reutiliza exactamente el parsing de klines, normalización, source identity, validación canónica y availability existentes.

No se mantiene un mapping manual `BTCUSDT → btc-usdt-spot` como dependencia operacional de esta ruta.

## Historical

La ruta:

~~~text
metadata resolution
        ↓
InstrumentIdentity
        ↓
existing BinanceSpotRestAdapter.fetch_historical_market_data()
~~~

reutiliza la paginación de FASE 1.15.

No existe una segunda paginación ni un segundo cursor.

Los tests de FASE 1.17 verifican dos páginas y que el segundo request avanza con la semántica existente de FASE 1.15.

## Availability

ExchangeInfo no participa en la evidencia temporal de disponibilidad.

La integración conserva:

- `observed_at`
- `received_at`
- `available_at`
- `consumer_handoff_clock`

Sin handoff explícito, `available_at` permanece desconocido.

Con handoff explícito, se conserva la semántica existente del adapter.

No se usa `ExchangeInfo.serverTime`, status, open time ni close time para fabricar `available_at`.

## Legacy

El camino manual continúa disponible de forma explícita:

~~~text
InstrumentMapper
+
InstrumentMappingRule
        ↓
InstrumentIdentity
~~~

No se eliminan:

- `InstrumentMapper`
- `InstrumentMappingRule`
- mappings manuales existentes
- APIs legacy del adapter

El nuevo camino metadata-backed no hace fallback silencioso al mapping manual.

## Fallos

La ruta metadata-backed conserva las defensas provider-specific de FASE 1.16:

- símbolo inexistente → `InstrumentNotFound`
- metadata inválida → error de payload/metadata
- símbolo inactivo → `InstrumentInactive`
- `isSpotTradingAllowed=False` → `InstrumentInactive`
- respuesta ambigua → `InstrumentMappingAmbiguous`
- error HTTP → `ExchangeInfoHTTPError`
- error de transporte → `ExchangeInfoTransportError`

No se convierte un fallo de resolución en una identidad inventada.

## Cache

Continúa únicamente el cache en memoria de FASE 1.16.

Se mantiene `refresh=True` como acción explícita.

No se introduce:

- persistencia;
- Redis;
- SQLite;
- scheduler;
- worker;
- background refresh;
- proceso residente.

## Contracto canónico

No se modifican:

- `bot_obrero/market_data.py`
- `bot_obrero/temporal.py`
- `bot_obrero/availability.py`

El Canonical Market Data Contract v1.0 permanece sin cambios.

## Tests

El nuevo archivo:

~~~text
tests/test_phase1_17_binance_instrument_adoption.py
~~~

demuestra:

- BTCUSDT y ETHUSDT;
- derivación explícita base/quote;
- live metadata-backed acquisition;
- historical metadata-backed acquisition;
- availability con y sin handoff;
- compatibilidad del mapper manual;
- ausencia de fallback silencioso;
- inactive / Spot disabled;
- ambiguity;
- invalid metadata;
- HTTP / transport;
- cache y refresh;
- frontera pública `resolve_instrument()`.

La suite completa de CI debe ejecutarse antes de cerrar la fase.

## Integración real

Las verificaciones online no forman parte de CI.

Si se realiza una comprobación manual, debe limitarse a una consulta puntual de ExchangeInfo para BTCUSDT y/o ETHUSDT. No hay polling, scheduler ni listener permanente.

## Fuera de alcance

FASE 1.17 no implementa:

- WebSocket;
- reconnect loops;
- streams;
- trading;
- órdenes;
- balances;
- API keys;
- firmas;
- persistencia;
- base de datos;
- Redis;
- workers;
- scheduler;
- catálogo universal de instrumentos;
- FASE 1.18.

## Contrato de adopción

La fase no elimina todavía el camino legacy. Su objetivo es demostrar que la adquisición metadata-backed puede funcionar de forma independiente del mapping manual específico de BTCUSDT, reutilizando íntegramente las abstracciones canónicas y la adquisición Binance existente.
