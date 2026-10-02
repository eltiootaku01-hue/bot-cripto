# FASE 1.11 — Binance Spot REST Market Data Adapter

## Objetivo

FASE 1.11 integra por primera vez un proveedor real sobre la frontera provider-neutral de FASE 1.10.

Flujo implementado:

```
Binance Spot REST
      ↓
Binance kline response
      ↓
ProviderRecord
      ↓
parse_provider_payload()
      ↓
normalize_provider_record()
      ↓
InstrumentMapper
      ↓
canonical Candle / MarketData validation
      ↓
MarketData
```

El adapter no crea `MarketObservation`. La promoción posterior continúa siendo responsabilidad de `to_market_observation()`.

## Documentación oficial de Binance utilizada

Documentación oficial consultada durante la implementación:

- Spot REST Market: https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market
- General REST API Information (Spot): https://developers.binance.com/en/docs/products/spot/rest-api
- Market Data Only URLs: https://developers.binance.com/en/docs/products/spot/faqs/market_data_only
- General API Information / limits and HTTP return codes: https://developers.binance.com/en/docs/products/advanced-earn/general-info

La documentación Spot actual define `GET /api/v3/klines`, identifica las klines por open time, documenta los 12 campos de la respuesta, los parámetros `symbol`, `interval`, `startTime`, `endTime`, `timeZone` y `limit`, y fija `limit` máximo en 1000. El adapter utiliza solamente los parámetros autorizados por esta fase: `symbol`, `interval`, `limit`, `startTime`, `endTime`.

Para market data público, la documentación de Binance indica usar `https://data-api.binance.vision`, incluyendo `GET /api/v3/klines`.

## Endpoint

Configuración por defecto del adapter:

- Base URL: `https://data-api.binance.vision`
- Endpoint: `/api/v3/klines`
- Método: `GET`
- Seguridad: pública / NONE
- Autenticación: ninguna
- API key: ninguna
- Signature/HMAC/RSA/Ed25519: ninguna

La ruta no está dispersa en el código: base URL, endpoint, timeout y SourceIdentity se encapsulan en `BinanceSpotRestConfig`.

## Parámetros

El adapter soporta:

- `symbol`
- `interval`
- `limit`
- `startTime`
- `endTime`

No implementa `timeZone` porque no es necesario para la primera integración autorizada.

Una llamada usa un solo request HTTP. No hay paginación histórica.

El adapter valida localmente `1 <= limit <= 1000`. No crea un catálogo Binance de intervalos; exige un intervalo no vacío y deja que la validación específica del proveedor rechace intervalos inválidos.

## Respuesta y parsing

La respuesta de klines debe ser un array JSON no vacío.

Cada kline debe tener exactamente 12 elementos, conforme a la documentación oficial:

0. open time
1. open
2. high
3. low
4. close
5. volume
6. close time
7. quote asset volume
8. number of trades
9. taker buy base asset volume
10. taker buy quote asset volume
11. ignore

El parser valida tipos, timestamps, decimal strings, volumen de trades y estructura. Una respuesta con forma distinta se rechaza como `BinancePayloadError`.

Los datos adicionales 7, 8, 9 y 10 se conservan únicamente como metadata de provenance del proveedor; no se convierten en conceptos canónicos nuevos.

## Decimal

Los campos de precio y volumen se convierten directamente:

```
JSON string → Decimal
```

No existe paso por `float`.

La escala recibida se conserva. No se hace rounding ni normalización a una cantidad fija de decimales.

La validación estructural final de OHLCV continúa perteneciendo al `Candle` canónico de `bot_obrero.market_data`.

## Mapping

No se deriva `InstrumentIdentity` concatenando símbolos.

La resolución usa la regla explícita de FASE 1.10:

```
provider
+ provider_symbol
+ provider_market
+ provider_venue
        ↓
InstrumentIdentity
```

El primer mapping autorizado de tests/manual integration es:

```
binance + BTCUSDT + SPOT + BINANCE
        ↓
btc-usdt-spot / BTC/USDT / SPOT
```

Un símbolo desconocido produce `InstrumentMappingNotFound`.

Dos reglas exactas para la misma identidad producen `InstrumentMappingAmbiguous`.

No existe catálogo universal de Binance.

## SourceIdentity

El adapter construye explícitamente:

```
source_id = binance-spot-rest
provider  = binance
venue     = BINANCE
```

`source_id` no se deriva automáticamente desde `provider`.

No existe persistencia.

## Timestamps

Binance documenta timestamps relacionados con la API en milisegundos.

La conversión realizada es:

```
millisecond integer → timezone-aware datetime UTC
```

### observed_at

La respuesta de kline no expone una marca separada de “momento de observación” del servidor. Por ello el adapter usa el `open time` de la kline como timestamp observado de la representación de mercado y lo conserva separado de `received_at`.

Esto no significa que el sistema conociera el dato en ese momento.

### received_at

Se genera localmente inmediatamente después de recibir la respuesta HTTP.

### available_at

Permanece:

```
None
```

La respuesta pública de klines no aporta evidencia suficiente para afirmar el instante en que ese dato estuvo disponible para el sistema. No se sustituye por `observed_at` ni por `received_at`.

En consecuencia, `to_market_observation()` y `EvidenceTimestamp` siguen bloqueando el uso temporal del dato cuando `available_at` es desconocido.

## Candle state, completeness y finality

El close time de la kline se compara con el `received_at` local:

- si `received_at < close_time`: `OPEN` + `PARTIAL`;
- si `received_at >= close_time`: `CLOSED` + `COMPLETE`.

Esta clasificación usa únicamente las fronteras temporales que Binance entrega y el momento local de recepción.

`FINALITY` queda en `UNKNOWN`. La respuesta REST no aporta evidencia específica de finality y por ello no se hace la transformación prohibida `CLOSED → FINAL`.

La completitud de la colección de respuesta queda en `UNKNOWN`, porque una sola llamada sin paginación no permite demostrar que un rango solicitado está exhaustivamente cubierto.

## Quality

`HTTP 200` no se considera por sí mismo calidad canónica válida.

El adapter asigna `VALID` solamente después de:

1. parsing estricto de la respuesta;
2. validación de la estructura de la kline;
3. normalización Decimal/timestamps;
4. resolución mediante mapping explícito;
5. construcción y validación del `Candle` canónico.

Un fallo estructural o canónico produce rechazo.

## Errors

Se distinguen:

- `BinanceTransportError`: timeout, DNS/socket/transport failure.
- `BinanceHTTPError`: fallo HTTP sin semántica de API reconocible.
- `BinanceRateLimitError`: `429` o `418`, con `Retry-After` cuando Binance lo entrega.
- `BinanceAPIError`: payload de error Binance con `code` + `msg`.
- `BinancePayloadError`: JSON o estructura de kline inválida.
- `NormalizationError`, `InstrumentMappingNotFound`, `InstrumentMappingAmbiguous`, `CanonicalValidationError`: errores ya pertenecientes a la frontera de FASE 1.10.

### 429 / 418

La documentación oficial indica backoff ante `429` y que violaciones repetidas pueden provocar `418`. El adapter no realiza retries automáticos ni espera en background. Expone `retry_after_seconds` cuando existe para que la capa que llama al adapter respete el backoff.

### 403

Se trata como fallo HTTP explícito. La documentación oficial relaciona `403` con violación del WAF limit.

### 430

Se trata como fallo HTTP genérico explícito. No se atribuye una semántica de rate-limit específica no demostrada por la documentación vigente.

### 5xx

Se conserva `outcome_unknown=True`, de acuerdo con la advertencia oficial de Binance de que un `5XX` no debe tratarse como resultado operativo definitivamente fallido.

No existe retry automático.

## Range / limit / pagination

La primera integración realiza una sola llamada.

No existe paginación histórica.

No existe worker, cola ni proceso en background.

No se almacenan respuestas.

## Integración con FASE 1.10

El adapter específico de Binance no implementa nuevamente:

- `Candle`
- `MarketData`
- `InstrumentIdentity`
- `SourceIdentity`
- `EvidenceTimestamp`
- validación estructural canónica

Traduce la respuesta externa al `ProviderRecord` y reutiliza el pipeline de FASE 1.10.

La implementación no importa ni utiliza `bot_obrero.data.Candle`.

## Integración real

Se añadió:

```
scripts/phase1_11_binance_live_test.py
```

Es una prueba manual de una sola llamada contra:

```
https://data-api.binance.vision/api/v3/klines
```

con:

```
symbol=BTCUSDT
interval=1m
limit=1
```

No usa credenciales y no forma parte del CI automático, para evitar que la suite quede acoplada a Internet externo.

Comando:

```
python scripts/phase1_11_binance_live_test.py
```

## Fuera de alcance

FASE 1.11 NO implementa:

- WebSocket;
- WebSocket Streams;
- User Data Stream;
- API keys;
- secret keys;
- signatures;
- HMAC;
- RSA;
- Ed25519;
- cuentas;
- balances;
- órdenes;
- trading;
- Futures;
- Margin;
- Options;
- Loans;
- Staking;
- CCXT;
- persistence;
- database;
- Redis;
- queues;
- workers;
- background retries;
- strategy;
- indicators;
- risk;
- execution;
- nuevos cambios al contrato canónico.

## Estado del contrato

```
Canonical Market Data Contract v1.0
SIN CAMBIOS
```

FASE 1.11 solo usa las abstracciones existentes. Si un proveedor futuro requiere cambiar esas abstracciones, debe elevarse a revisión del Cerebro antes de implementarlo.
