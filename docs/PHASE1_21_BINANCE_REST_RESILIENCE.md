# FASE 1.21 — Resiliencia y semántica de fallos del Binance Spot REST

## Alcance

Esta fase formaliza la recuperación acotada de las consultas públicas REST de Binance usadas por ExchangeInfo y klines. No modifica el contrato canónico de market data y no introduce WebSocket, trading, persistencia, workers ni scheduler.

## Clasificación

### Transporte

`BinanceTransportError` y `ExchangeInfoTransportError` representan fallos de red o timeout. El resultado final se marca como `outcome_unknown=True`: no se transforma el fallo en datos.

El error puede reintentarse de forma acotada. Si todos los intentos fallan, la excepción sigue siendo visible.

### HTTP

`BinanceHTTPError` conserva `status_code`, `retry_after_seconds`, `response_headers`, `response_body` y `outcome_unknown`.

- 4xx distintos de 418/429: no reintentables.
- 5xx: reintentables y `outcome_unknown=True`.
- Un 5xx agotado no se convierte en éxito.

### Rate limit

`BinanceRateLimitError` conserva el status y `Retry-After` cuando existe y es numéricamente válido.

- 429: reintentable de forma acotada.
- 418: solo se reintenta automáticamente cuando existe un `Retry-After` válido. Sin esa evidencia no se realiza otro intento.
- `Retry-After` tiene prioridad sobre el backoff exponencial.

Binance documenta que 429 corresponde a rate limit, 418 a bloqueo automático por violaciones repetidas y que `Retry-After` indica cuánto esperar. También documenta que los 5xx tienen estado de ejecución desconocido. La implementación conserva esas distinciones. 

## Binance API errors

Un objeto con `code` y `msg` permanece como `BinanceAPIError` y conserva `code`, `message` y `http_status`.

Un error API HTTP 4xx no se convierte en rate limit ni en HTTP genérico. Un error API transportado sobre 5xx mantiene `http_status` y puede entrar en la política de retry porque el estado de la operación sigue siendo desconocido.

## Payload inválido

HTTP 200 no implica payload válido. JSON inválido, raíz incorrecta, forma de kline incorrecta, campos faltantes, tipos inválidos, timestamps inválidos y valores decimales inválidos continúan produciendo `BinancePayloadError` o la excepción de normalización correspondiente.

Los payloads inválidos no se reintentan automáticamente: repetir una respuesta malformada no aporta evidencia de recuperación y debe permanecer visible.

## Retry policy

Se añadió `BinanceRetryPolicy`, limitada al boundary Binance REST.

Configuración por defecto:

- `max_attempts=3`.
- backoff exponencial determinista: 0.5 s, 1.0 s, 2.0 s; con máximo configurado de 5.0 s.
- sin jitter.
- `Retry-After` válido sustituye al backoff para el intento siguiente.
- el sleeper es inyectable para tests.

No existen retries infinitos ni procesos en background.

## Outcome unknown

Un timeout, error de conexión u otra falla de transporte puede ocurrir después de que el request haya salido del cliente; por eso el error final conserva `outcome_unknown=True`.

Un 5xx también conserva `outcome_unknown=True`, de acuerdo con la semántica documentada por Binance.

Un retry que finalmente obtiene una respuesta válida no fabrica éxito retroactivo: el éxito proviene de esa respuesta real y pasa nuevamente por el parser y la validación productiva.

## Histórico y max_requests

`max_requests` en `fetch_historical_market_data()` cuenta **requests HTTP físicos**, incluidos los intentos de retry.

Ejemplo:

```text
página 1 intento 1 → 500
retry página 1 → 200
página 2 → 200
```

Esto consume 3 requests físicos.

El cursor histórico no cambia durante un retry. Solo se calcula después de recibir y validar una página real:

```text
last candle close + 1 ms
```

Por tanto, un retry no avanza artificialmente el cursor, no crea una segunda página y no puede ocultar un duplicado.

Las defensas existentes de orden, identidad de candle, gaps, cursor estancado y `max_requests` permanecen activas.

## ExchangeInfo

`BinanceSpotInstrumentMetadata` utiliza la misma política acotada. ExchangeInfo conserva la separación entre:

```text
transport failure
HTTP failure
rate limit
Binance API error
payload error
instrument resolution
```

La resolución activa continúa exigiendo metadata válida y estado `TRADING` con `isSpotTradingAllowed=true`.

## Qué NO se reintenta

- errores de validación local;
- intervalos inválidos;
- rangos históricos inválidos;
- payload JSON inválido;
- forma de kline inválida;
- tipos inválidos;
- errores de normalización/canonicalización;
- 400/404 y otros HTTP 4xx ordinarios;
- errores API 4xx ordinarios;
- 418 sin `Retry-After` válido;
- duplicados, gaps u orden histórico incorrecto.

## Compatibilidad

No se modificaron:

- `MarketData`;
- `Candle`;
- `InstrumentIdentity`;
- `SourceIdentity`;
- `EvidenceTimestamp`;
- `available_at`;
- identidad de candle;
- OPEN/CLOSED/UNKNOWN;
- finality;
- completeness;
- Decimal.

`available_at` continúa dependiendo exclusivamente de evidencia explícita. La ausencia de `consumer_handoff_clock` sigue produciendo `available_at=None` en el adapter Binance.

## CI

El workflow normal `.github/workflows/tests.yml` permanece separado y offline/determinista.

La evidencia de cierre de esta fase debe registrar el run de GitHub Actions correspondiente al commit final. No se considera suficiente una ejecución local ni una afirmación textual de tests.

## Estado de cierre

Esta documentación describe únicamente capacidades implementadas en FASE 1.21. El resultado final debe actualizarse con el run real de GitHub Actions después del merge.