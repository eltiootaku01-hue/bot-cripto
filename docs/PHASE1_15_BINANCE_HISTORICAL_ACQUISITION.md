# FASE 1.15 — Adquisición histórica acotada de klines Binance Spot

## Estado

FASE 1.15 añade adquisición histórica síncrona y acotada mediante múltiples llamadas al mismo endpoint público de Binance Spot:

~~~text
GET /api/v3/klines
~~~

La salida sigue siendo:

~~~text
list[MarketData]
~~~

No se crea un modelo canónico histórico paralelo.

Fuentes oficiales de Binance:
- https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klines
- https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md

La documentación oficial vigente indica que las klines están identificadas por open time, las respuestas se entregan cronológicamente, startTime selecciona desde ese instante, y limit tiene máximo 1000 para este endpoint. Binance documenta además data-api.binance.vision para market data público.

## 1. API

Se añade:

~~~text
BinanceSpotRestAdapter.fetch_historical_market_data(
    symbol,
    interval,
    start_time,
    end_time,
    page_limit=1000,
    max_requests=1000,
)
~~~

Devuelve list[MarketData].

start_time y end_time son milisegundos UNIX y son obligatorios.

## 2. Paginación

La progresión es temporal y determinista:

~~~text
start_time
  ↓
request N
  ↓
última kline
  ↓
last close time + 1 ms
  ↓
startTime de request N+1
~~~

No se inventa un cursor numérico de página ni se utilizan clocks locales.

El siguiente cursor se obtiene del Candle ya canónico:

~~~text
next_cursor = last_candle.end + 1 ms
~~~

Esto evita repetir la kline anterior en una consulta cuyo startTime es inclusivo.

## 3. Terminación

La adquisición termina cuando:

- la respuesta es vacía;
- el siguiente cursor supera end_time;
- la página contiene menos elementos que page_limit;
- se alcanza max_requests y ya sería necesario pedir otra página.

No existe loop infinito.

## 4. Page limit

Se respeta el máximo oficial vigente:

~~~text
1 <= page_limit <= 1000
~~~

No se adopta ningún límite de rango adicional que Binance no documente para Spot klines.

## 5. Duplicados

La identidad lógica utilizada es la existente en MarketData:

~~~text
instrument_id + timeframe + start
~~~

Si una kline aparece nuevamente, se lanza:

~~~text
DuplicateKline
~~~

No se duplica silenciosamente ni se altera source_id para ocultar el caso.

## 6. Orden

La adquisición valida el orden cronológico y no reordena por received_at.

Para klines consecutivas:

~~~text
start_1 < start_2 < start_3 ...
~~~

Un desorden dentro de una página o una superposición temporal se informa con:

~~~text
UnexpectedPageOrder
~~~

## 7. Cursor estancado

Una página siguiente que retrocede respecto del cursor actual se informa con:

~~~text
PaginationStalled
~~~

No se fabrica un timestamp alternativo para forzar progreso.

## 8. Gaps

La continuidad se verifica con los propios timestamps de la kline:

~~~text
expected_next_start = previous_candle.end + 1 ms
~~~

- siguiente start igual al esperado: continuidad;
- siguiente start mayor: HistoricalGapDetected;
- siguiente start menor: UnexpectedPageOrder;
- misma identidad lógica ya vista: DuplicateKline.

No se crean synthetic candles, volumen cero ni copias de la vela anterior.

La detección se aplica a discontinuidades entre klines efectivamente devueltas. Una ausencia de datos antes de la primera kline o después de la última no se inventa como gap: un rango válido sin datos devuelve [].

## 9. Range semantics

start_time > end_time se rechaza con:

~~~text
InvalidHistoricalRange
~~~

start_time == end_time es válido. Puede devolver una kline si existe en ese instante o [] si no existe.

Una ventana válida sin datos devuelve [] y no se transforma automáticamente en error de proveedor.

## 10. Range coverage vs MarketData completeness

MarketData.completeness conserva exactamente su semántica existente.

Una página válida no convierte automáticamente completeness en COMPLETE.

La cobertura del rango pertenece a la adquisición histórica y se considera satisfactoria cuando la ejecución termina normalmente sin duplicados, desorden, gaps internos, cursor estancado ni agotamiento prematuro de max_requests.

No se agrega un campo nuevo a MarketData para representar esta cobertura.

## 11. received_at

Cada request reutiliza fetch_market_data(), por lo que cada respuesta obtiene su propio received_at.

No se reemplazan los timestamps de todas las klines por la hora del primer o del último request.

Ejemplo:

~~~text
page 1 → received_at T1
page 2 → received_at T2
page 3 → received_at T3
~~~

La provenance temporal existente queda preservada por página.

## 12. Availability

La política de FASE 1.14 no cambia.

Por defecto:

~~~text
consumer_handoff_clock = None
available_at = None
~~~

La adquisición histórica no usa open time, close time, cursor, número de página, received_at ni request time para inferir availability.

Si el adapter ya fue configurado explícitamente con consumer_handoff_clock, cada llamada subyacente mantiene la semántica de FASE 1.14.

## 13. Look-ahead

La consulta histórica no convierte el event/open time en knowledge time.

Por defecto:

~~~text
observed_at = open time de Binance
received_at = recepción de su página
available_at = UNKNOWN
~~~

El único guard temporal sigue siendo EvidenceTimestamp:

~~~text
available_at <= decision_timestamp
~~~

No se crea otro mecanismo de look-ahead.

## 14. Candle state / finality

Se reutiliza íntegramente la lógica de FASE 1.11.

No cambia:

~~~text
OPEN / CLOSED / UNKNOWN
FINAL / NOT_FINAL / UNKNOWN
~~~

Varias páginas no convierten una kline en FINAL.

## 15. Decimal

Cada página pasa por el parser Binance existente.

OHLCV continúa usando Decimal y se conserva la escala. No se introduce float.

## 16. Reutilización

No se duplica:

- HTTP;
- errores HTTP;
- parsing;
- mapping;
- normalization;
- canonicalization;
- availability.

Cada página entra por fetch_market_data().

## 17. Errores de FASE 1.15

~~~text
InvalidHistoricalRange
PaginationStalled
UnexpectedPageOrder
DuplicateKline
HistoricalGapDetected
MaximumRequestsExceeded
~~~

Los errores no se convierten silenciosamente en UNKNOWN y no generan datos artificiales.

## 18. Tests

Se añade:

~~~text
tests/test_phase1_15_binance_historical.py
~~~

Cobertura:

- una página;
- dos páginas;
- múltiples páginas;
- cursor close + 1 ms;
- received_at por página;
- duplicados;
- orden;
- gap;
- cursor estancado;
- max_requests;
- rango inválido;
- start == end;
- rango válido sin datos;
- availability UNKNOWN;
- Decimal;
- rechazo de float;
- independencia del orden respecto de received_at;
- Candle canónico;
- límite page_limit=1000.

Los tests usan HTTP simulado y no dependen de Internet.

## 19. Integración real

No se convirtió CI en una descarga online.

No se ejecutó una adquisición histórica real durante esta fase; la verificación de proveedor se limita a la documentación oficial y a la simulación determinista de HTTP en tests.

## 20. Contrato canónico

No se modifican:

- bot_obrero/market_data.py;
- bot_obrero/temporal.py;
- bot_obrero/availability.py.

No se agregan HistoricalCandle, HistoricalMarketData ni ReplayCandle.

Canonical Market Data Contract v1.0 — SIN CAMBIOS.

## 21. Fuera de alcance

No se implementa:

- WebSocket;
- User Data Stream;
- otro exchange;
- CCXT;
- trading;
- balances;
- órdenes;
- API keys;
- firmas;
- persistencia;
- base de datos;
- Redis;
- queues;
- workers;
- background ingestion;
- scheduler;
- replay;
- estrategia;
- indicadores;
- riesgo;
- ejecución;
- FASE 1.16.
