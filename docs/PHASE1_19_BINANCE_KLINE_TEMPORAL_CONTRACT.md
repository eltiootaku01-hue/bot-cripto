# FASE 1.19 — Contrato temporal de Klines Binance Spot

## Fuente oficial

La fuente normativa es la documentación oficial de Binance Spot. La sección de `GET /api/v3/klines` indica que las klines se identifican de forma única por su **open time**; `startTime` y `endTime` son parámetros temporales; `timeZone` es opcional y por defecto es `0 (UTC)`; y, cuando se proporciona `timeZone`, el intervalo de las klines se interpreta en ese timezone mientras que `startTime` y `endTime` siguen interpretándose en UTC. La misma documentación muestra que los timestamps de apertura y cierre forman parte del payload en milisegundos y que `limit` tiene máximo 1000.

Fuente: documentación oficial de Binance Spot, sección Kline/Candlestick data:
https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klines

La documentación general de REST también establece que los datos se devuelven cronológicamente y que, con ambos límites presentes, la consulta se comporta como `startTime` sin exceder `endTime`. Los timestamps de la API son milisegundos por defecto.

## Política temporal adoptada por BOT CRIPTO

FASE 1.19 no añade una nueva representación temporal al contrato canónico.

La política efectiva del adapter es:

```text
timeZone de Binance REST: no enviado
semántica de intervalo: UTC por defecto
startTime: timestamp absoluto en milisegundos, interpretado por Binance en UTC
endTime: timestamp absoluto en milisegundos, interpretado por Binance en UTC
open time: → candle_start → MarketData.payload.start
close time: → candle_end → MarketData.payload.end
observed_at: open time convertido a datetime UTC
received_at: reloj de recepción inyectado por el adapter
available_at: solamente desde AvailabilityEvidence explícita
```

La ausencia de `timeZone` es deliberada: Binance documenta `0 (UTC)` como valor por defecto y BOT CRIPTO mantiene una única semántica temporal estable en esta fase.

Binance soporta `timeZone`, pero BOT CRIPTO **no lo expone en FASE 1.19**. No existe parámetro público, campo canónico ni configuración de usuario para elegir timezone.

## startTime y endTime

El adapter controla actualmente exactamente estos parámetros de consulta:

- `symbol`
- `interval`
- `limit`
- `startTime`, cuando el caller lo proporciona
- `endTime`, cuando el caller lo proporciona

Los timestamps de consulta se expresan como enteros de milisegundos. El adapter rechaza valores negativos y tipos no enteros; no realiza conversión desde la timezone local del proceso.

Binance documenta los timestamps de consulta como temporales absolutos y especifica que `startTime` y `endTime` se interpretan en UTC, independientemente de `timeZone`.

Cuando se envían ambos límites, la documentación general indica que la consulta se comporta como `startTime` pero no devuelve datos que excedan `endTime`. Por ello el proyecto no inventa una segunda regla de inclusión/exclusión: la semántica de selección de Binance queda en el servidor. Las pruebas deterministas verifican que el adapter transmite exactamente los límites solicitados y preserva una kline cuyo open time coincide con cada frontera.

## Open time

Binance declara que el open time identifica de forma única la kline.

En BOT CRIPTO:

```text
Binance open time (ms)
        ↓
_ms_to_datetime(..., tz=UTC)
        ↓
candle_start
        ↓
MarketData.payload.start
        ↓
MarketData.candle_identity = instrument_id + timeframe + candle_start
```

No se redondea el timestamp, no se desplaza por timezone local y no se utiliza `received_at` para construir `candle_start`.

`observed_at` también conserva el open time. Esto mantiene la semántica establecida antes de FASE 1.19.

## Close time

El campo de cierre de la respuesta Binance se convierte directamente:

```text
Binance close time (ms)
        ↓
_ms_to_datetime(..., tz=UTC)
        ↓
candle_end
        ↓
MarketData.payload.end
```

No se calcula `candle_end` sumando una duración artificial al open time.

Esto es importante para `1M`: el intervalo mensual no se convierte a 30 días ni a otra duración fija. La semántica del intervalo de FASE 1.18 y la semántica de timestamps del payload permanecen separadas.

`close time` tampoco se utiliza para fabricar `available_at`, sustituir `received_at` ni cambiar `CandleState`, `CandleFinality` o `DataCompleteness`.

## Interval y FASE 1.18

FASE 1.18 continúa siendo la frontera provider-specific de intervalos. La semántica temporal no crea un catálogo universal ni equivalencias entre proveedores.

Se mantienen, entre otros:

- `1m`
- `1h`
- `1d`
- `1w`
- `1M`

`1M` continúa representando un intervalo mensual de calendario sin una `timedelta` fija.

## Historical pagination

FASE 1.15 permanece intacta.

El paginador continúa avanzando con:

```text
next_startTime = last_candle_close_time + 1 ms
```

El cursor se deriva del timestamp real de cierre devuelto por Binance. No utiliza:

- timezone local;
- duración calculada del intervalo;
- cursor de calendario;
- cursor artificial por proveedor.

Esto también evita depender de una duración fija para `1M`.

La prueba de FASE 1.19 verifica que, después de una primera kline con cierre `CLOSE`, la segunda consulta comienza exactamente en `CLOSE + 1 ms`.

## Availability

La semántica de availability permanece independiente de la semántica de consulta Binance.

No se deriva `available_at` desde:

- `startTime`;
- `endTime`;
- open time;
- close time;
- interval;
- timeZone.

El adapter continúa usando `AvailabilityEvidence` y el `consumer_handoff_clock` explícito cuando existe. Sin evidencia explícita, la disponibilidad permanece UNKNOWN.

## Canonical Market Data Contract

No se modifica ninguno de estos archivos:

- `bot_obrero/market_data.py`
- `bot_obrero/temporal.py`
- `bot_obrero/availability.py`

Los timestamps canónicos continúan siendo timezone-aware y representan instantes absolutos. No se añade `timeZone`, `timezone_offset` ni `candle_timezone` al contrato canónico.

## Machine timezone independence

La conversión de milisegundos Binance utiliza explícitamente:

```python
datetime.fromtimestamp(value / 1000, tz=timezone.utc)
```

Por tanto no depende de la timezone local del proceso. FASE 1.19 añade una prueba que cambia la variable `TZ` entre dos zonas y verifica el mismo instante UTC. La conversión inversa utilizada para el cursor también normaliza datetimes aware a UTC.

## Request contract y limit

El adapter no añade `timeZone`.

El request contract queda limitado a:

```text
symbol
interval
limit
startTime (opcional)
endTime (opcional)
```

`limit` conserva el contrato existente: entero entre 1 y 1000. La documentación oficial de Binance establece 500 como default y 1000 como máximo.

## Live

La ruta no cambia:

```text
metadata resolution
        ↓
validated interval
        ↓
UTC Binance REST request semantics
        ↓
BinanceSpotRestAdapter
        ↓
ProviderRecord
        ↓
MarketData
```

No se crea un segundo adapter, parser o normalizador temporal.

## Historical

La ruta no cambia:

```text
validated interval
        ↓
UTC request semantics
        ↓
fetch_historical_market_data()
        ↓
existing pagination
        ↓
MarketData[]
```

Live e historical comparten la misma frontera de consulta y la misma conversión explícita a UTC.

## Metadata-backed

Las pruebas conservan la resolución de FASE 1.17:

- `BTCUSDT` → `binance:SPOT:BTCUSDT`, `BTC/USDT`
- `ETHUSDT` → `binance:SPOT:ETHUSDT`, `ETH/USDT`

No se reintroduce mapping manual.

## Tests de FASE 1.19

Se añade `tests/test_phase1_19_binance_kline_temporal.py` para cubrir:

1. conversión de milisegundos a UTC;
2. independencia de timezone local;
3. request contract;
4. start boundary;
5. end boundary;
6. open/close preservation;
7. independencia de availability respecto de close time;
8. ausencia de `timeZone`;
9. cursor `last close + 1 ms`;
10. compatibilidad con `1m`, `1h`, `1d`, `1w`, `1M`;
11. identidad metadata-backed BTCUSDT/ETHUSDT;
12. invariancia de `available_at` frente a la ventana de consulta.

## Integración real

No se realizó una llamada real a Binance durante FASE 1.19. La validación se hizo contra la documentación oficial y con HTTP determinista simulado en tests. No se introduce polling, scheduler, worker ni proceso residente.

## Fuera de alcance

No se implementa:

- `timeZone` configurable;
- timezone de usuario;
- selección de timezone de exchange;
- UI de timezone;
- WebSocket;
- streams;
- trading;
- órdenes;
- balances;
- API keys;
- firmas;
- persistencia;
- base de datos;
- Redis;
- scheduler;
- workers;
- cambios al contrato canónico;
- equivalencias universales de timeframe;
- FASE 1.20.
