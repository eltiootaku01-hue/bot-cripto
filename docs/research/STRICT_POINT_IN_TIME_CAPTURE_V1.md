# STRICT_POINT_IN_TIME_CAPTURE_V1 — Contrato normativo de cierre, disponibilidad, finality y revisiones

- Fecha: 2026-10-16
- Autoridad: CEREBRO / IA-CHAN
- Estado: **APPROVED_FOR_DOCUMENTATION_ONLY — IMPLEMENTATION_NOT_AUTHORIZED**
- Decisión operativa: **NO_TRADE**
- Rama de investigación: `research/strict-point-in-time-capture-v1`
- Base remota observada al crear la rama: `main` @ `ff05bb6d70845ba51784da2bceee0ecf99b3e11a`
- Naturaleza: contrato normativo diseñado a partir del contrato canónico y la documentación existente. No constituye evidencia de captura ejecutada.
- Alcance de publicación: este documento únicamente. Sin cambios de código, tests, workflows, backtests, adquisición de datos, captura WebSocket ni ejecución financiera.

## 1. Decisión normativa

STRICT_POINT_IN_TIME_CAPTURE_V1 separa obligatoriamente tres preguntas que no son equivalentes:

1. **Cierre de la vela según la fuente:** ¿Binance indicó que la kline terminó?
2. **Disponibilidad temporal para nuestro consumidor:** ¿en qué instante demostrable pudo el sistema usar esta versión concreta?
3. **Finality y revisiones:** ¿qué evidencia demuestra que la versión no es provisional, y qué sabemos de versiones posteriores o conflictos?

Un cierre confirmado **no** demuestra disponibilidad anterior, inmutabilidad permanente ni ausencia de futuras correcciones. Por ello, un evento Binance con `k.x = true` permite afirmar que Binance marca la kline como cerrada en ese evento; por sí solo no permite asignar `CandleFinality.FINAL`.

El contrato de disponibilidad ya establecido permanece inalterado: `available_at` debe expresar el primer instante demostrable de disponibilidad para el consumidor pertinente y toda decisión cumple `available_at <= decision_timestamp`. Si ese instante no puede demostrarse, permanece `UNKNOWN/None` y no se admite la evidencia para una decisión que requiera point-in-time estricto.

La aprobación aquí es documental. No modifica el Canonical Market Data Contract ni autoriza su implementación.

## 2. Definiciones normativas

### 2.1 Identidad lógica de una vela

La identidad lógica mínima de una kline es:

`(provider, venue, symbol, interval, open_time_ms)`

Para este contrato, `provider=BINANCE` y `venue=SPOT` deben quedar identificados explícitamente. La identidad lógica **no** identifica una versión única: dos payloads con la misma identidad lógica y diferente contenido son observaciones/versiones distintas hasta resolver su relación.

La clave de versión debe incluir, como mínimo, la identidad lógica y un digest del payload de origen. No fabricar secuencias de Binance ni atribuir al origen un identificador que no haya suministrado.

### 2.2 Cierre de fuente: `source_closure_status`

Valores normativos del sidecar de captura:

- `OPEN`: la fuente indicó explícitamente que la vela sigue abierta. En el stream kline, `k.x = false` es esa señal.
- `CLOSED_CONFIRMED`: existe evidencia atribuible a Binance que confirma el cierre de esa vela. En el stream kline, `k.x = true` cumple esta función.
- `UNKNOWN`: la respuesta o artefacto disponible no demuestra suficientemente el estado de cierre.

La semántica exacta del indicador `x` está limitada a “Is this kline closed?” en el contrato oficial del stream. No debe reinterpretarse como “esta versión jamás cambiará”.

En REST histórico, un tiempo de cierre pasado, el reloj local, la hora de solicitud o la ausencia de una vela abierta no sustituyen por sí solos evidencia de cierre. La admisión REST requiere que la semántica documentada del endpoint y la validación del payload respalden el cierre; en caso contrario, el estado es `UNKNOWN`.

### 2.3 Disponibilidad del sistema: `available_at`

`available_at` es el primer instante local demostrable en que **esta versión exacta y validada** estuvo disponible para el consumidor de evidencia dentro del sistema. No equivale automáticamente a:

- `open_time`, `close_time` ni otro timestamp de mercado;
- el tiempo de evento del proveedor `E`;
- `received_at` de la trama o respuesta;
- la hora de solicitud REST;
- la hora de una captura, reejecución o consulta histórica posterior.

La captura debe registrar como mínimo el instante de recepción local y el evento observable que establece la frontera real de disponibilidad para el consumidor. Si existe validación, persistencia o handoff obligatorio posterior a la recepción, la recepción inicial no prueba por sí sola la disponibilidad en esa etapa posterior.

Un dato histórico obtenido hoy tiene disponibilidad demostrable a partir de la consulta y handoff reales actuales; la consulta no reconstruye cuándo esa misma versión pudo conocerse en el pasado. No retrofechar `available_at`.

La comparación temporal es inclusiva y obligatoria:

`available_at <= decision_timestamp`

Si `available_at` es desconocido, inválido, incoherente o posterior a la decisión, la evidencia no se admite para esa decisión.

### 2.4 Finality: `finality`

El modelo canónico actual define `CandleFinality = NOT_FINAL | FINAL | UNKNOWN`. Este contrato conserva esos valores y restringe su interpretación:

- `NOT_FINAL`: hay evidencia de que la observación todavía no ha cerrado o sigue siendo provisional según el estado documentado aplicable.
- `UNKNOWN`: no existe evidencia suficiente para decidir si la versión tiene una garantía fuerte de finality.
- `FINAL`: solo puede asignarse con evidencia independiente y explícita que respalde una garantía de finality para esa fuente, tipo de dato y versión, con una política de correcciones/versiones aplicable y referencia auditable.

**No son prueba de `FINAL`:** `k.x=true`, que el reloj haya pasado el cierre programado, la recepción exitosa, que una consulta REST devuelva un valor, que una descarga se haya repetido sin diferencias o que aún no se haya observado una corrección.

Con la evidencia disponible para este diseño, el valor conservador de una kline Binance cerrada es `finality=UNKNOWN`, a menos que una evidencia normativa posterior cumpla expresamente el criterio anterior. No se presume una garantía de inmutabilidad eterna.

### 2.5 Estado de revisiones: `revision_status`

El registro de captura debe separar las revisiones de finality. Se proponen estos valores del sidecar:

- `NO_REVISION_OBSERVED_AS_OF_TIME`: hasta el límite temporal de la evidencia registrada no se ha observado una versión diferente para la misma identidad lógica. Es una afirmación acotada al historial de captura disponible, **no** una garantía global de que la fuente jamás corrija.
- `REVISION_CANDIDATE`: se observó un payload diferente para la misma identidad lógica después de una versión cerrada, pero todavía no está probado si constituye una corrección de la misma serie/fuente o un conflicto de procedencia.
- `REVISION_CONFIRMED`: evidencia verificable establece que el proveedor publicó una versión corregida de la misma vela lógica.
- `SUPERSEDED`: una versión anterior fue desplazada por una revisión confirmada en la línea de conocimiento más reciente. La versión anterior no se borra y sigue siendo válida para replays cuyo corte temporal sea anterior a la disponibilidad de la revisión.
- `CONFLICT_UNRESOLVED`: fuentes, endpoints, archivos o versiones discrepan y no existe evidencia suficiente para decidir qué relación mantienen.

La ausencia de una revisión observada nunca permite promover automáticamente `finality` a `FINAL`.

### 2.6 Estado de admisión: `admission_status`

Valores del sidecar:

- `ACCEPTED`: admisible para decisiones/replay solo dentro del intervalo temporal demostrado y bajo los restantes requisitos de este contrato.
- `REJECTED`: el registro incumple una condición dura de validez o de identidad.
- `UNKNOWN`: faltan pruebas necesarias para decidir la admisión.
- `BLOCKED`: hay una condición conocida que impide su uso, incluida una disponibilidad desconocida o una discrepancia no resuelta.

Estos estados son una propuesta documental de captura; no se afirma que existan hoy como enum en código.

## 3. Regla de admisión point-in-time

Una vela solo puede admitirse para una decisión si se cumplen todas las condiciones siguientes:

1. La identidad de instrumento/intervalo y los límites temporales están explícitos y son válidos.
2. `source_closure_status=CLOSED_CONFIRMED` para una decisión que requiera velas cerradas.
3. La calidad canónica es `VALID`, la vela está completa, y los controles de OHLCV, unicidad, orden y continuidad no detectan una infracción.
4. La versión exacta tiene un digest verificable y su evidencia de origen está preservada de manera inmutable o referenciada de forma durable.
5. `available_at` está demostrado y `available_at <= decision_timestamp`.
6. El corte de replay incluye únicamente versiones cuya disponibilidad demostrada sea menor o igual al instante de decisión/replay.
7. No existe un conflicto sin resolver que afecte al payload consumido.
8. La decisión conserva la identidad de versión/digest realmente utilizada.

`finality=UNKNOWN` **no** impide por sí solo la admisión prospectiva de una versión cerrada que fue capturada, persistida, fechada y versionada correctamente. Sí impide afirmar que la vela es inmutable o que la fuente ofrece finality fuerte. La admisión, por tanto, es “esta versión era la conocida y admisible en este instante”, no “esta versión será siempre la última”.

`REVISION_CANDIDATE` y `CONFLICT_UNRESOLVED` bloquean la vela afectada hasta resolver la relación. Una corrección confirmada puede utilizarse solo desde su propio `available_at`; nunca se puede retrotraer a decisiones previas. Una versión `SUPERSEDED` se conserva para reproducir el conocimiento histórico anterior a la revisión, pero no sustituye a la versión corregida para decisiones posteriores a la disponibilidad de esta última.

## 4. Captura, deduplicación y control de revisiones

### 4.1 Recepción y persistencia append-only

La captura debe preservar los mensajes de origen sin reescribirlos. El contenido original, o una referencia durable e inmutable a él, se vincula a un digest criptográfico del payload recibido. El hash del JSON reserializado no se debe presentar como hash de bytes originales: si los bytes originales no se conservan, declarar qué representación se hasheó y cómo se canonicalizó.

Cada recepción es un evento temporal con su propio registro de recepción. El mismo digest puede deduplicar el contenido almacenado, pero no debe eliminar el hecho de que hubo otra recepción con otro `received_at`. El contrato no requiere inventar `source_sequence`; si Binance no entrega secuencia utilizable, se conserva como ausente.

La disponibilidad solo se asigna a partir de una frontera local realmente observable para la versión que el consumidor puede recuperar. Si no puede medirse ni reconstruirse de forma verificable, `available_at=None` y la admisión queda bloqueada.

### 4.2 Tabla de tratamiento

| Caso observado | Clasificación normativa | Tratamiento |
|---|---|---|
| `k.x=false` | `OPEN` | Registrar el evento; no usarlo como vela cerrada. |
| Primer `k.x=true` validado para esa identidad | `CLOSED_CONFIRMED` | Registrar versión cerrada; mantener `finality=UNKNOWN` por defecto. Evaluar disponibilidad y los demás requisitos por separado. |
| Misma identidad y mismo digest | Duplicado exacto de contenido | No crear una segunda versión de contenido; mantener el evento de recepción y su timestamp si la recepción fue distinta. |
| Misma identidad y digest diferente después del cierre | `REVISION_CANDIDATE` | Conservar ambas versiones; bloquear la vela afectada hasta resolver corrección frente a conflicto. |
| Se prueba que Binance corrigió la misma vela | `REVISION_CONFIRMED` para la nueva revisión; versión previa `SUPERSEDED` | Registrar enlace auditable entre versiones y el instante real en que la revisión pasó a estar disponible. |
| REST, WebSocket, archivo diario/mensual u otra representación difieren sin prueba de relación | `CONFLICT_UNRESOLVED` | No escoger silenciosamente “el último”, el más frecuente ni el valor que favorezca la estrategia. |
| Payload actual descargado difiere de una captura previa, pero no hay prueba de cuándo cambió en la fuente | Revisión candidata o conflicto según evidencia | El instante de descubrimiento local es observable; el instante histórico de publicación de la corrección es desconocido. No retrofecharlo. |
| Se detecta pérdida de mensajes, intervalo sin cobertura o falta de evidencia de persistencia | Integridad/availability bloqueada | No declarar completo ni point-in-time estricto el tramo afectado. |

Una diferencia de payload no prueba automáticamente una corrección oficial: puede provenir de una diferencia de endpoint, definición, caché, agregación o archivo. Confirmar revisión requiere evidencia que identifique la misma vela lógica y fundamente la relación de corrección. De no ser posible, mantener `CONFLICT_UNRESOLVED`.

### 4.3 Nunca sobrescribir el pasado

Cada revisión es un nuevo hecho de conocimiento. Ninguna actualización borra el payload anterior, su hash, recepción, disponibilidad ni las decisiones que lo consumieron. No modificar retrospectivamente una decisión para que parezca que usó una versión descubierta después. La vista “última versión conocida ahora” debe mantenerse conceptualmente separada del replay point-in-time.

## 5. Registro mínimo de captura

El sidecar normativo debe poder reconstruir, como mínimo, la identidad, la versión, los tiempos y la decisión de admisión. Los nombres siguientes son **propuesta de esquema**, no campos añadidos al contrato canónico ni una migración ya implementada.

| Campo | Obligatorio | Significado |
|---|---|---|
| `schema_version` | Sí | `STRICT_POINT_IN_TIME_CAPTURE_V1`. |
| `capture_event_id` | Sí | Identidad única del evento local de recepción. |
| `provider`, `venue` | Sí | Fuente y mercado explícitos; por ejemplo, Binance / Spot. |
| `symbol`, `interval`, `open_time_ms` | Sí | Identidad lógica de la vela. |
| `source_event_time_ms` | Si existe en origen | Tiempo de evento proporcionado por la fuente, preservado como provenance, nunca como `available_at` automático. |
| `received_at` | Sí para captura en vivo | Tiempo UTC local de recepción, observado. |
| `available_at` | Condicional; UNKNOWN si no se prueba | Primer instante demostrado de disponibilidad al consumidor para esta versión. |
| `payload_digest_algorithm`, `payload_digest` | Sí | Algoritmo y digest de la representación claramente identificada. |
| `raw_payload_ref` | Sí para evidencia recuperable | Referencia durable al payload original o a una representación canonicalizada declarada. |
| `version_id` | Sí | Identificador estable de la versión, vinculable a identidad lógica y digest. |
| `source_closure_status` | Sí | `OPEN`, `CLOSED_CONFIRMED` o `UNKNOWN`. |
| `finality` | Sí | `NOT_FINAL`, `UNKNOWN` o `FINAL` respaldado por evidencia suficiente. |
| `revision_status` | Sí | Estado normativo definido en §2.5, limitado al historial realmente disponible. |
| `supersedes_revision_id` | Condicional | Referencia a la versión precedente solo cuando la relación se ha establecido; null/ausente si no está demostrada. |
| `admission_status` | Sí | Resultado de admisión y, en el registro completo, motivo/evidencia de ese resultado. |
| `evidence_refs` | Sí | Referencias a mensajes, validaciones o documentos que fundamentan cierre, disponibilidad y revisiones. |

Para ser explícitos, un registro ilustrativo podría tener esta forma; los valores marcados como ejemplo **no** representan captura real:

```json
{
  "schema_version": "STRICT_POINT_IN_TIME_CAPTURE_V1",
  "capture_event_id": "EXAMPLE_ONLY_NOT_CAPTURED",
  "provider": "BINANCE",
  "venue": "SPOT",
  "symbol": "BTCUSDT",
  "interval": "1h",
  "open_time_ms": 0,
  "source_event_time_ms": null,
  "received_at": null,
  "available_at": null,
  "payload_digest_algorithm": "SHA-256",
  "payload_digest": "EXAMPLE_ONLY_NOT_A_REAL_DIGEST",
  "raw_payload_ref": null,
  "version_id": "EXAMPLE_ONLY_NOT_A_REAL_VERSION",
  "source_closure_status": "CLOSED_CONFIRMED",
  "finality": "UNKNOWN",
  "revision_status": "NO_REVISION_OBSERVED_AS_OF_TIME",
  "supersedes_revision_id": null,
  "admission_status": "BLOCKED",
  "evidence_refs": []
}
```

El ejemplo queda `BLOCKED`: le faltan tiempos observados, payload recuperable, digest real y referencias de evidencia. No es un registro listo para consumo.

## 6. Invariantes de tiempo, decisión y ejecución

- La hora de apertura/cierre de la vela describe tiempo de mercado, no conocimiento local.
- `E` de Binance, si está presente, se conserva como tiempo de evento; no sustituye al reloj local de recepción/handoff.
- La versión usada por una decisión debe poder identificarse sin ambigüedad.
- Un replay a tiempo `T` no puede leer una versión cuyo `available_at > T`, aunque esa versión sea hoy la más reciente.
- `available_at=None`, una disponibilidad inferida o timestamps incoherentes cierran la admisión.
- El tiempo de decisión se registra en la frontera real de consumo, no se iguala automáticamente a `close_time`.
- La ejecución simulada no puede ocurrir antes de la decisión ni antes de que la evidencia necesaria esté disponible. Si el siguiente open de una vela sucede antes de `available_at`, ese precio de apertura no puede presentarse como fill alcanzable por esta decisión; se requiere una referencia de precio posterior válida o la operación debe quedar bloqueada.
- Un histórico REST consultado hoy, sin un registro histórico verificable de las versiones disponibles entonces, no demuestra por sí solo una reconstrucción STRICT_POINT_IN_TIME de ese pasado.
- Pérdida de mensajes, falta de cobertura, persistencia fallida o conflicto irresuelto impiden declarar que el periodo afectado es íntegro y point-in-time.
- Un dataset de “vintage actual” y un replay de capturas con cortes de conocimiento son productos distintos; no etiquetarlos como equivalentes.

## 7. Relación con el contrato existente

**OBSERVED** en el HEAD de `main` indicado al inicio:

- `bot_obrero/market_data.py` define `CandleState`, `DataCompleteness` y `CandleFinality`, con finality `NOT_FINAL | FINAL | UNKNOWN`.
- `MarketData.available_at` es opcional. `MarketData.evidence_at()` y `to_market_observation()` bloquean la disponibilidad desconocida.
- `docs/PHASE1_12_AVAILABILITY_POLICY.md` exige disponibilidad demostrada para el consumidor, prohíbe deducirla solo de tiempos de mercado o de consulta y preserva UNKNOWN en replay.
- La adquisición REST actual no convierte por defecto una kline histórica en evidencia de la disponibilidad que habría tenido el sistema en el pasado.
- El contrato canónico no incluye actualmente el conjunto de campos de revisión/versionado aquí propuesto.

**Norma de compatibilidad para esta fase:** no se cambian `MarketData`, `Candle`, enums, adapters, consumers ni backtest. El sidecar y sus estados quedan definidos documentalmente para futura revisión de diseño. La implementación futura deberá reutilizar el contrato temporal existente y evitar un segundo guard de look-ahead; cualquier cambio contractual de campos o enums requiere revisión explícita de CEREBRO.

## 8. Criterios de cierre documental y límites que permanecen

Este documento define el contrato, pero no certifica que haya una captura continua ni que todos los consumidores actuales apliquen estas reglas. En consecuencia:

- Semántica de cierre frente a finality/revisión: **DEFINIDA DOCUMENTALMENTE**.
- Evidencia de cierre de Binance en un evento WebSocket `k.x=true`: **acredita cierre del evento, no inmutabilidad permanente**.
- `available_at` histórico real para velas obtenidas hoy: **NO DEMOSTRADO RETROACTIVAMENTE**.
- Finality fuerte y ausencia de futuras correcciones: **UNKNOWN salvo garantía independiente verificable**.
- Existencia de captura persistente continua, journal de eventos y seguimiento de revisiones: **NO DEMOSTRADO por este documento**.
- Implementación, tests, backtests y captura: **NO EJECUTADOS / NO AUTORIZADOS**.
- Estrategia y operaciones: **NO_TRADE**.

No aprobar automáticamente datos históricos de 365 días para STRICT_POINT_IN_TIME solo porque existan OHLCV actuales. Antes de cualquier estudio prospectivo u OOS con este estándar se necesita evidencia de captura/replay temporal íntegra o una decisión distinta, explícita y etiquetada, que no se denomine STRICT_POINT_IN_TIME.

## 9. Fuentes y documentos de referencia

1. Binance Spot WebSocket Streams, sección de klines, campos `E` y `k.x`: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
2. Binance Public Data — README y semántica de publicación de archivos: https://github.com/binance/binance-public-data/blob/master/README.md
3. Política canónica existente: [`docs/PHASE1_12_AVAILABILITY_POLICY.md`](../PHASE1_12_AVAILABILITY_POLICY.md).
4. Contrato temporal de consumo existente: [`docs/PHASE1_23_MARKET_DATA_CONSUMPTION_CONTRACT.md`](../PHASE1_23_MARKET_DATA_CONSUMPTION_CONTRACT.md).
5. Auditoría de adquisición BTC/ETH: [`BTC_ETH_FOLLOW_RESEARCH_V1_DATA_ACQUISITION_AUDIT.md`](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/btc-eth-follow-v1/docs/research/BTC_ETH_FOLLOW_RESEARCH_V1_DATA_ACQUISITION_AUDIT.md), publicado en `research/btc-eth-follow-v1`.

Las fuentes describen comportamientos y artefactos disponibles; no aportan por sí solas un historial completo de las versiones que Binance entregó en cada instante pasado ni una garantía de inmutabilidad perpetua.

## 10. Cierre

**Contrato documental aprobado; implementación no autorizada.**

La regla rectora es: **cierre confirmado ≠ disponibilidad demostrada ≠ finality ≠ ausencia de revisiones**. Aceptar una versión en una decisión exige conocer qué versión era, cuándo estuvo realmente disponible al consumidor y que ese tiempo no sea posterior a la decisión. Las correcciones se conservan como nuevos hechos de conocimiento, nunca se retrotraen y nunca reescriben decisiones anteriores.

No se creó ni actualizó ningún otro archivo como parte de esta publicación.
