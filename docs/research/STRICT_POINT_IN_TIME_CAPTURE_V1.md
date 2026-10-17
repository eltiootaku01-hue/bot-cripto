# STRICT_POINT_IN_TIME_CAPTURE_V1 — Contrato normativo de cierre, disponibilidad, finality y revisiones

- Fecha de revisión: 2026-10-16
- Autoridad: CEREBRO / IA-CHAN
- Estado: **R1_DOCUMENT_PUBLISHED — IMPLEMENTATION_NOT_AUTHORIZED**
- Decisión operativa: **NO_TRADE**
- Rama de investigación: `research/strict-point-in-time-capture-v1`
- Base de rama verificada: `main` @ `ff05bb6d70845ba51784da2bceee0ecf99b3e11a`
- Alcance autorizado: edición y publicación de este documento exclusivamente.
- Fuera de alcance: código, tests, workflows, backtests, adquisición o captura de mercado, cambios de contrato ejecutable y ejecución financiera.

## 1. Decisión normativa

`STRICT_POINT_IN_TIME_CAPTURE_V1` separa las siguientes propiedades:

1. **Cierre de fuente:** la fuente declara que terminó la vela.
2. **Disponibilidad temporal:** una versión concreta está disponible para el consumidor en un instante demostrable.
3. **Finality:** una política de fuente versionada permite calificar el estado de esa versión dentro de un alcance declarado.
4. **Revisión:** existe evidencia de otra versión o de una discrepancia de contenido.
5. **Completitud de vela:** el payload individual representa una vela completa según la fuente y el contrato aplicable.
6. **Integridad de observación:** identidad, contenido, procedencia y evidencia son coherentes y recuperables.
7. **Cobertura de captura:** la secuencia de evidencia cubre el intervalo requerido sin huecos o pérdidas no resueltos.

La regla central es:

**Cierre confirmado ≠ disponibilidad demostrada ≠ finality ≠ ausencia de revisiones.**

Una kline marcada como cerrada no demuestra que el dato estuviera disponible para nuestro sistema antes de recibirlo, que su contenido nunca vaya a cambiar ni que se haya capturado íntegramente el período.

Este documento fija una propuesta normativa para la captura y el replay estrictos. **No certifica que el sistema existente cumpla el contrato ni habilita por sí solo el backtest point-in-time.** La implementación requiere una autorización posterior.

## 2. Identidades y procedencia

### 2.1 Identidad canónica de la vela

La identidad lógica de la vela, independiente de quién la haya observado, es:

`(instrument_id, timeframe, candle_start_utc)`

- `instrument_id` procede de la identidad canónica del instrumento y de los metadatos explícitos.
- `timeframe` identifica el intervalo de la vela.
- `candle_start_utc` es el comienzo de la vela convertido a UTC con zona horaria explícita.

Esta identidad es coherente con `MarketData.candle_identity`. No identifica una versión de contenido ni sustituye la procedencia de origen.

### 2.2 Identidad de la serie de origen

Cada observación debe conservar por separado la procedencia y la identidad nativa de la serie. Como mínimo, la evidencia lateral de captura debe identificar:

- `source_id`, `provider` y `venue` según los contratos/mapeos existentes;
- mercado y símbolo nativo del proveedor, cuando estén disponibles;
- intervalo y timestamp nativo de apertura;
- referencia al instrumento canónico al que se mapeó el símbolo nativo.

En el adaptador WebSocket existente, los valores son `provider="binance"`, `market="SPOT"`, `venue="BINANCE"` y `source_id="binance-spot-websocket"`. Estos valores no se deben intercambiar por conveniencia documental. REST y WebSocket pueden tener distinto `source_id` aunque representen el mismo instrumento y la misma vela lógica.

No se debe inferir `base_asset` o `quote_asset` dividiendo el símbolo nativo. Se deben reutilizar los metadatos de instrumento existentes.

### 2.3 Identidad de evento y de versión

El diseño distingue tres entidades:

1. **Evento de recepción:** una ocurrencia local concreta de recepción.
2. **Contenido de vela:** los campos económicos canónicos de la vela bajo una representación versionada.
3. **Versión de contenido:** un contenido de vela asociado a la identidad lógica, su procedencia relevante y un digest reproducible.

Un mismo contenido puede recibirse varias veces. La deduplicación del contenido no puede borrar el hecho de recepciones distintas ni sus tiempos.

La evidencia distinguirá:

- `raw_event_digest`: digest de los bytes originales, solo cuando esos bytes se hayan conservado; si no, se declarará la representación exacta que se hasheó.
- `candle_content_digest`: digest de un esquema canónico y versionado de campos de vela comparables.
- `version_id`: identificador estable vinculado a la identidad lógica, versión del esquema del digest, procedencia relevante y digest de contenido.

El hash del JSON reserializado no se debe etiquetar como hash de los bytes originales. Si la representación original no se conserva, se registrarán su formato, la canonicalización aplicada y la versión del algoritmo. No se inventará un `source_sequence` que la fuente no haya proporcionado.

## 3. Cierre de fuente: regla específica por fuente

### 3.1 WebSocket de Binance

El indicador de cierre de la kline tiene esta semántica:

- `k.x = false` → `source_closure_status = OPEN`.
- `k.x = true` → `source_closure_status = CLOSED_CONFIRMED` para ese evento concreto.
- Falta de evidencia, payload inválido o estado no interpretable → `source_closure_status = UNKNOWN`.

La evidencia de cierre debe apuntar al evento de origen y a la versión de contenido que sustenta la afirmación.

`k.x=true` acredita que Binance indica que esa kline está cerrada en ese evento. No demuestra inmutabilidad permanente, disponibilidad anterior a la recepción ni ausencia de correcciones futuras.

### 3.2 REST histórico y REST bajo demanda

Una fila REST no presenta el campo WebSocket `k.x`. Por tanto, bajo este contrato estricto, ninguna de las siguientes condiciones prueba por sí sola `CLOSED_CONFIRMED`:

- el `close_time` programado quedó en el pasado;
- el reloj local es posterior al cierre programado;
- el endpoint devolvió una fila de OHLCV;
- una solicitud repetida devuelve el mismo valor;
- otra descarga o archivo contiene los mismos valores.

Para `STRICT_POINT_IN_TIME_CAPTURE_V1`, el estado de cierre de REST permanece `UNKNOWN` salvo que una regla de procedencia específica, documentada y auditable establezca cómo se prueba el cierre para esa fuente y versión. Hasta entonces, la vela REST no se admite para una decisión estricta que requiera cierre confirmado.

Esta es una regla del contrato propuesto, no una afirmación de que el adaptador REST actual ya se comporte así. No se modifica su comportamiento en esta fase.

### 3.3 Regla de admisión del cierre

Para una decisión que requiere velas cerradas:

- `CLOSED_CONFIRMED` satisface la condición de cierre, sujeta a todos los demás controles.
- `OPEN` se rechaza para ese propósito.
- `UNKNOWN` bloquea la admisión estricta.

El cierre no asigna ni determina por sí solo `finality`.

## 4. Disponibilidad temporal y persistencia

### 4.1 Semántica de `available_at`

Se conserva la regla canónica:

`available_at <= decision_timestamp`

`available_at` representa el primer instante demostrable en que la versión exacta estaba disponible para el consumidor relevante. No equivale automáticamente a:

- tiempo de apertura o cierre de la vela;
- tiempo de evento de Binance `E`;
- instante de solicitud REST;
- `received_at` del transporte;
- instante de persistencia;
- hora en que un consumidor descubre el archivo mucho después.

El evento de recepción y la frontera real del consumidor son hechos distintos. `received_at` puede coincidir con `available_at` únicamente cuando la frontera de recepción es también la frontera demostrable de disponibilidad del consumidor, sin una etapa obligatoria posterior. La igualdad nunca se presume.

### 4.2 Persistir antes de publicar evidencia estricta

Para que las decisiones puedan reproducirse a partir de evidencia durable, la futura implementación estricta deberá garantizar el orden lógico:

1. recibir y validar la observación;
2. conservar el payload y la procedencia;
3. persistir de forma durable el evento y la versión de contenido;
4. poner esa versión a disposición del consumidor;
5. registrar cualquier decisión que la consuma.

El registro debe distinguir `received_at`, `persisted_at` y `available_at`. Sus valores pueden coincidir solamente si las fronteras reales se han demostrado equivalentes; no se copian entre sí por defecto.

En el camino estricto, la disponibilidad para decisiones reproducibles no puede preceder a la persistencia durable necesaria para recuperar la versión exacta. Si el orden no puede probarse o existe un fallo de persistencia, no se certificará esa observación para replay estricto.

La marca de persistencia no sustituye la disponibilidad temporal canónica. `available_at` sigue midiendo el conocimiento del consumidor.

### 4.3 Datos históricos consultados hoy

Una consulta histórica actual establece conocimiento actual sobre un hecho pasado. No reconstruye cuándo el sistema habría conocido esa versión en una fecha anterior.

Si el sistema no conserva evidencia verificable de la versión disponible en la decisión histórica, no puede reclamar un replay estrictamente point-in-time de aquella decisión. El dataset histórico de versión actual se debe etiquetar como tal, no como captura contemporánea.

### 4.4 Orden temporal y relojes

Todos los timestamps contractuales deben ser timezone-aware y normalizarse a UTC. Los campos que representan hechos diferentes no se igualan por conveniencia.

Si el reloj de pared, la persistencia o el orden de eventos produce timestamps incoherentes —por ejemplo, una disponibilidad anterior a la recepción sin una explicación verificable—, la evidencia queda bloqueada hasta resolver la incoherencia. Si no se puede demostrar el orden requerido, el resultado debe ser fail-closed y no inferido.

## 5. Completitud, integridad y cobertura de captura

Estas dimensiones no se deben colapsar en un único campo.

### 5.1 Completitud de la vela

Describe si el payload individual contiene los campos requeridos y si la fuente permite considerarlo una vela completa. Para el WebSocket de Binance, `k.x=true` respalda que la kline se declara cerrada; los controles OHLCV y de calidad canónica siguen siendo obligatorios.

### 5.2 Integridad de la observación

Describe si el evento y la versión son recuperables, su procedencia es trazable, el digest se puede reproducir y la representación satisface las validaciones canónicas.

Un registro puede contener una kline individual completa y, aun así, no tener evidencia de persistencia íntegra, identidad de versión o disponibilidad.

### 5.3 Cobertura temporal de la captura

Describe si el sistema dispone de evidencia suficiente para todo el período requerido. La evaluación debe considerar:

- inicio y fin de la cobertura declarada;
- conexión/sesión y límites de ciclo de vida de la fuente;
- interrupciones, reinicios, pérdidas y errores de transporte;
- persistencia fallida o demorada;
- huecos, duplicados y desalineación temporal;
- disponibilidad del consumidor y versiones efectivamente recuperables;
- limitaciones de detección de pérdida cuando la fuente no proporciona una secuencia utilizable.

Una vela completa no prueba que se hayan capturado todas las velas. La ausencia de mensajes durante un tramo no se interpreta como evidencia de ausencia de eventos si la cobertura del canal no puede demostrarse.

Para una ventana de decisión, la cobertura requerida es la de todas las observaciones de entrada necesarias. Si existe un hueco o una pérdida no resuelta que afecta esa ventana, la admisión de la decisión queda bloqueada. Los tramos no afectados solo pueden evaluarse si la frontera de aislamiento está documentada y no depende del tramo comprometido.

## 6. Finality independiente del cierre

El enum canónico no se modifica:

`CandleFinality = NOT_FINAL | FINAL | UNKNOWN`

Para esta revisión:

- `NOT_FINAL`: existe evidencia específica de la fuente de que la versión todavía está en estado provisional bajo su ciclo de vida documentado.
- `UNKNOWN`: la evidencia no permite determinar el estado de finality.
- `FINAL`: una política explícita, versionada y auditable permite calificar la versión como terminal dentro de un alcance declarado.

`FINAL` no significa que sea imposible descubrir un error en el futuro ni garantiza inmutabilidad física eterna. Significa que se ha satisfecho el criterio de finality declarado para la fuente, versión y política consideradas. Cualquier corrección posterior debe registrarse como un nuevo hecho de conocimiento, sin borrar el estado observado anteriormente ni reescribir decisiones pasadas.

No constituyen prueba de `FINAL`:

- `k.x=true`;
- que el reloj haya superado el cierre programado;
- recepción satisfactoria;
- la misma respuesta recibida en consultas repetidas;
- ausencia de revisiones observadas dentro de una cobertura incompleta.

Para las klines de Binance en el estado actual de evidencia, `finality=UNKNOWN` por defecto. No se asignará `FINAL` hasta contar con una regla explícita y evidencia suficiente que satisfaga este contrato.

## 7. Revisiones append-only y relaciones entre versiones

### 7.1 Evento de revisión

Toda diferencia material observada genera un evento de revisión inmutable que vincula, según corresponda:

- identidad lógica de la vela;
- versión de contenido previamente conocida;
- nueva versión observada;
- evento o fuente donde se descubrió;
- instante real de descubrimiento;
- disponibilidad demostrable de la nueva versión;
- digests y evidencias comparativas;
- clasificación y motivo;
- decisión posterior de resolución, si existe.

Una diferencia de payload no demuestra por sí sola una corrección oficial. Puede originarse en diferencias de endpoint, esquema, agregación, caché o representación del archivo. Si la relación no puede demostrarse, permanece como candidato o conflicto sin resolver.

### 7.2 Clasificaciones del evento de revisión

Los siguientes valores son estados propuestos para los eventos de revisión; no se afirma que hoy existan como enums en código:

- `REVISION_CANDIDATE`: existe una diferencia material para la misma identidad lógica, pero su causa o relación todavía no se ha demostrado.
- `REVISION_CONFIRMED`: evidencia verificable establece que la nueva versión es una revisión de la misma vela lógica bajo la política de fuente indicada.
- `CONFLICT_UNRESOLVED`: existen representaciones discrepantes y no se puede justificar qué relación mantienen.
- `REJECTED_AS_REVISION`: la evidencia descarta que la discrepancia sea una revisión de la misma vela y documenta otra causa o una diferencia de alcance.

### 7.3 Relación entre versiones

La relación entre versiones es independiente de la clasificación del evento:

- una versión puede ser la conocida en un instante determinado;
- una versión anterior puede quedar sustituida en una vista de conocimiento posterior;
- una versión puede quedar afectada por una revisión candidata;
- dos versiones pueden permanecer en conflicto.

`SUPERSEDED` se puede usar como estado derivado de una versión en una vista temporal cuando se ha confirmado que fue sustituida. No elimina ni altera la versión original.

No se debe mantener un único campo mutable que, al cambiar, borre la historia de los eventos de revisión.

### 7.4 Afirmación de ausencia de revisiones

`NO_REVISION_OBSERVED_AS_OF_TIME` solo puede declararse para un límite temporal explícito, una fuente y un alcance de cobertura concretos, cuando la integridad de esa cobertura está documentada.

La afirmación significa únicamente que no se observó otra versión dentro del historial de evidencia declarado. No prueba que nunca existan revisiones en la fuente.

Si el historial de captura tiene interrupciones, retención incompleta u otra limitación que impide comprobar una revisión, el estado de ausencia de revisiones es `UNKNOWN`. No se debe inferir ausencia a partir de ausencia de datos.

## 8. Política de correcciones y preservación de decisiones

Se distinguen tres vistas.

### 8.1 Replay original point-in-time

Reconstruye las versiones que estaban disponibles y eran admisibles en el instante original de decisión. Una revisión descubierta más tarde no puede incluirse retroactivamente.

### 8.2 Vista corregida actual

Utiliza revisiones confirmadas conocidas y admisibles al corte temporal actual. Debe etiquetarse como una vista corregida del dataset, no como el replay original.

### 8.3 Auditoría posterior de decisiones afectadas

Puede añadir evidencia de que una decisión histórica quedó afectada por una revisión descubierta después. Esa anotación es un evento nuevo de auditoría. No modifica el payload, los timestamps, los digests ni el manifiesto original de la decisión.

Una discrepancia detectada hoy bloquea nuevas decisiones que dependan de esa evidencia mientras siga sin resolverse. No cambia por sí sola qué versión era conocida antes de su detección.

## 9. Manifiesto mínimo de decisión

El evento de recepción, la versión de contenido, el evento de revisión y la decisión son entidades distintas. El vínculo entre ellas debe ser auditable, no una reconstrucción inferida posteriormente.

El manifiesto de cada decisión debe conservar, como mínimo:

- `decision_id` y `decision_timestamp`;
- los `version_id` y digests exactos de las observaciones consumidas;
- referencias a la evidencia de cierre, disponibilidad, calidad y admisión;
- identidad/versionado de configuración, reglas y modelo que generaron la decisión;
- rango temporal de datos e inputs requeridos, con referencia durable al manifiesto de entrada completo;
- estado de cobertura y cualquier revisión o conflicto relevante para la ventana;
- referencia al evento de decisión original y, si se produce una corrección de auditoría posterior, referencia separada a esa anotación.

Si una estrategia consume indicadores derivados de muchas velas, el manifiesto debe identificar de forma recuperable todos los inputs históricos necesarios para reproducir esos indicadores, no solo la última vela usada como disparador.

La evidencia debe permitir responder: qué se conocía, cuándo se conocía, de qué versión procedía, qué inputs se consumieron y qué regla estaba activa. Si esa reconstrucción no es posible, no se declara el replay reproducible.

## 10. Regla de ejecución temporal

La admisión de una señal no demuestra por sí sola que un fill simulado sea alcanzable a un precio concreto.

Una ejecución simulada debe cumplir:

1. la decisión no ocurre antes de la disponibilidad de todos los inputs necesarios;
2. la referencia de precio de ejecución es temporalmente válida con respecto a la decisión;
3. la disponibilidad de la referencia de ejecución está demostrada;
4. la regla de ejecución y su granularidad están declaradas y versionadas;
5. el fill conserva la procedencia y el timestamp de la referencia utilizada.

La apertura de la vela siguiente no es un fill válido por defecto. Si el timestamp de esa apertura es anterior a la decisión o si su precio no estaba disponible cuando la decisión podía actuar, no debe usarse como si fuese un fill alcanzable.

La condición temporal de precio y decisión se evalúa junto con la evidencia de orden causal. La mera igualdad de timestamps no demuestra el orden de eventos; si no hay evidencia de orden para un caso de igualdad, se bloquea el fill.

Una referencia de precio posterior solo es admisible si su fuente, disponibilidad y correspondencia con la regla de ejecución están documentadas. Si no existe una referencia temporalmente válida, se bloquea el fill. Ninguna convención hipotética puede etiquetarse como ejecución point-in-time observada.

## 11. Precedencia de resultados y fail-closed

La revisión separa los estados de evidencia, el resultado canónico del consumo temporal y la decisión de admisión estricta.

### 11.1 Estado de evidencia

Ejemplos: cierre `OPEN`/`CLOSED_CONFIRMED`/`UNKNOWN`, clasificación de revisión, validez del digest y estado de cobertura.

### 11.2 Resultado de consumo canónico

Se conserva el contrato de consumo existente:

- `ACCEPTED`: hay disponibilidad demostrada y no es posterior a `decision_timestamp`.
- `REJECTED`: la evidencia temporal demuestra que el dato no estaba disponible a tiempo.
- `UNKNOWN`: falta evidencia suficiente para determinar la disponibilidad.

`available_at=None` no se transforma en aceptación. No se crea un segundo guard temporal.

### 11.3 Resultado de admisión estricta propuesto

- `ACCEPTED`: todos los requisitos obligatorios para el uso concreto están satisfechos.
- `BLOCKED`: falta evidencia obligatoria, el estado relevante es desconocido, la cobertura es insuficiente o existe un conflicto no resuelto.
- `REJECTED`: una condición de validez o identidad se incumple de forma concluyente.

El resultado debe contener uno o varios motivos explícitos. Por ejemplo, `BLOCKED / AVAILABLE_AT_UNKNOWN` no cambia el resultado del consumidor canónico de `UNKNOWN` a `ACCEPTED`.

El estado de admisión es una propuesta documental; no se afirma que el enum exista actualmente. El contrato canónico y sus consumidores no se alteran mediante esta revisión.

## 12. Matriz normativa resumida

| Caso | Estado de evidencia | Resultado de admisión estricta |
|---|---|---|
| WebSocket con `k.x=false` y decisión que exige vela cerrada | `OPEN` | `BLOCKED` |
| WebSocket con `k.x=true`, disponibilidad demostrada y observación íntegra | Cierre confirmado; finality puede seguir `UNKNOWN` | `ACCEPTED` solo si los demás requisitos se cumplen |
| REST sin regla verificable de cierre estricto | Cierre `UNKNOWN` | `BLOCKED` |
| `available_at=None` | Disponibilidad `UNKNOWN` | `BLOCKED` |
| `available_at > decision_timestamp` | Evidencia posterior a la decisión | `REJECTED` |
| Payload individual inválido o identidad incoherente | Integridad/calidad no válida | `REJECTED` |
| Vela completa, pero la ventana requerida tiene hueco sin resolver | Vela válida; cobertura insuficiente | `BLOCKED` para decisiones dependientes de esa ventana |
| Digest diferente sin relación demostrada | `REVISION_CANDIDATE` o `CONFLICT_UNRESOLVED` | `BLOCKED` para nuevas decisiones afectadas |
| Revisión confirmada disponible después de una decisión pasada | Nueva versión de conocimiento posterior | El replay original conserva la versión conocida entonces |
| Fill basado en precio anterior a la decisión o sin disponibilidad temporal demostrada | Referencia de ejecución inadmisible | Fill `BLOCKED` |
| Captura con cobertura insuficiente para certificar ausencia de revisiones | Ausencia no demostrada | `UNKNOWN`; no se afirma ausencia global de revisiones |

La matriz es normativa propuesta. No es resultado de tests ni certifica la conformidad del código actual.

## 13. Compatibilidad y límites de esta revisión

La presente revisión es documental. No modifica:

- `bot_obrero/market_data.py`;
- enums o campos del contrato canónico;
- adaptadores REST o WebSocket;
- el contrato o el evaluador canónico de consumo;
- persistencia, captura, workers o procesos residentes;
- motor de backtest, estrategias o parámetros;
- tests ni workflows.

Los nombres de campos auxiliares, eventos de revisión y manifiestos son elementos propuestos de diseño. La futura implementación deberá reutilizar la política temporal y el guard de look-ahead existentes. Toda ampliación de esquema, enum o contrato ejecutable necesita un diseño técnico aparte y autorización explícita de CEREBRO.

## 14. Estado resultante

- Separación entre cierre, disponibilidad, finality, revisiones, completitud, integridad y cobertura: **definida documentalmente en R1**.
- Identidad lógica independiente de procedencia y versión: **definida documentalmente; implementación no verificada**.
- Cierre estricto REST sin evidencia de procedencia suficiente: **bloqueado**.
- Disponibilidad histórica real de versiones obtenidas hoy: **no demostrada retroactivamente**.
- Persistencia durable, captura continua y cobertura íntegra: **no certificadas por este documento**.
- Historial de revisiones completo de Binance: **no demostrado**.
- Reproducción exacta de decisiones: **requiere manifiesto de inputs y evidencia persistida**.
- Implementación, tests, backtests, adquisición y captura: **NO EJECUTADOS / NO AUTORIZADOS**.
- Operaciones: **NO_TRADE**.

## 15. Referencias

1. [Binance Spot WebSocket Streams — klines y campo x](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).
2. [Binance Public Data — README y publicación de archivos históricos](https://github.com/binance/binance-public-data/blob/master/README.md).
3. [Política canónica de disponibilidad, FASE 1.12](../PHASE1_12_AVAILABILITY_POLICY.md).
4. [Contrato de consumo temporal, FASE 1.23](../PHASE1_23_MARKET_DATA_CONSUMPTION_CONTRACT.md).
5. [Contrato del adaptador WebSocket, FASE 1.24](../PHASE1_24_BINANCE_SPOT_WEBSOCKET_KLINES.md).
6. [Auditoría de adquisición BTC/ETH](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/btc-eth-follow-v1/docs/research/BTC_ETH_FOLLOW_RESEARCH_V1_DATA_ACQUISITION_AUDIT.md).

Las fuentes de Binance describen los contratos y archivos de origen; no demuestran por sí solas un historial completo de qué versión estuvo disponible para cada sistema consumidor en cada instante pasado.

## 16. Cierre y autorización

**R1 documental publicada; implementación no autorizada.**

Las reglas de este documento son una definición normativa propuesta para la siguiente etapa. No constituyen un dictamen de que el sistema actual las cumpla y no habilitan una captura, un replay, un backtest ni la promoción de datos históricos a estrictamente point-in-time.

La regla rectora permanece: **una decisión solo puede usar una versión cuya identidad, integridad y disponibilidad estén demostradas en su instante de decisión; ninguna corrección posterior reescribe silenciosamente el conocimiento histórico.**

`NO_TRADE` permanece vigente.