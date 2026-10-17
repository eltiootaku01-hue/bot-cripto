# STRICT_POINT_IN_TIME_CAPTURE_V1 — Contrato normativo de cierre, disponibilidad, finality y revisiones

- Revisión documental: R3
- Fecha: 2026-10-16
- Autoridad: CEREBRO / IA-CHAN
- Estado: **R3_DOCUMENT_PUBLISHED — IMPLEMENTATION_NOT_AUTHORIZED**
- Decisión operativa: **NO_TRADE**
- Rama de investigación: research/strict-point-in-time-capture-v1
- Revisión previa: R2, commit 5a3da318f69ff57d58851fd7ed43cda4a43a65fe
- Alcance autorizado: edición y publicación de este documento exclusivamente.
- Fuera de alcance: código, contratos ejecutables, tests, workflows, backtests, adquisición o captura de mercado, ejecución financiera y cambios en main.

## 1. Objetivo y regla rectora

Esta revisión R3 consolida las aclaraciones necesarias para que una implementación futura no interprete de forma incompatible la identidad de una vela, la cronología de su evidencia, las revisiones, la finality o la cobertura de captura.

Se separan siete propiedades:

1. Cierre de fuente: la fuente declara que terminó una vela en una observación concreta.
2. Disponibilidad temporal: una versión exacta pudo ser recuperada por el consumidor en un instante demostrable.
3. Finality: una política de fuente versionada permite clasificar la condición terminal de una versión dentro de un alcance declarado.
4. Revisión: hay evidencia de otra versión o de una discrepancia de contenido.
5. Completitud de vela: el payload individual satisface los requisitos aplicables a una vela.
6. Integridad de observación: identidad, contenido, procedencia y registros son coherentes y recuperables.
7. Cobertura de captura: existe evidencia suficiente del intervalo requerido y de las limitaciones de pérdida o interrupción.

**Cierre confirmado no equivale a disponibilidad demostrada, finality, ni ausencia de revisiones.** Tampoco equivale a cobertura íntegra del período.

R2 es una definición normativa documental. No certifica que el sistema actual cumpla estas reglas ni autoriza una captura, un replay point-in-time, un backtest o ejecución financiera.

## 2. Identidades, versiones y entidades de evidencia

### 2.1 Identidad lógica canónica de la vela

La identidad lógica permanece:

`(instrument_id, timeframe, candle_start_utc)`

Los tres componentes identifican el mismo fenómeno de mercado independientemente de la fuente. `candle_start_utc` se normaliza a UTC desde un timestamp timezone-aware. Un timestamp sin zona horaria no se interpreta como UTC de forma implícita.

La identidad lógica no incluye source_id, received_at, source_event_time, estado de cierre ni finality. No identifica por sí sola un evento de recepción ni una versión económica.

### 2.2 Versión de contenido económico

`CandleContentVersion` representa una versión inmutable del contenido económico canónico. Una vez creada, sus campos y digest no se sobrescriben.

Campos conceptuales mínimos:

- `content_version_id`;
- `instrument_id`, `timeframe`, `candle_start_utc`;
- `content_schema_version`;
- `candle_content_digest`;
- `digest_algorithm`;
- `candle_payload_ref` durable.

La identidad de la versión vincula la identidad lógica, la versión de canonicalización y el digest económico. La procedencia se registra mediante referencias a eventos; no se utiliza como sustituto de la identidad lógica.

La canonicalización debe especificar los campos incluidos y excluidos, orden y nombres de campos, reglas de valores ausentes, timestamps UTC, representación decimal exacta sin conversión intermedia a float, codificación y versión del algoritmo. Dos representaciones solo se declaran iguales cuando el mismo esquema versionado permite comparar sus digests.

`CandleContentVersion` no contiene como atributos mutables `received_at`, `persisted_at`, `available_at`, `source_closure_status` o `finality`. Esos datos pertenecen a eventos o bundles de evidencia.

### 2.3 Evento de recepción

`CaptureReceipt` representa cada recepción física o lógica observada. Es append-only: una recepción no se elimina porque su contenido se deduplique.

Campos conceptuales mínimos:

- `capture_event_id`;
- `source_id`, `provider`, `venue`, `market`, cuando se conozcan;
- `provider_symbol`, cuando se conozca;
- identidad lógica resuelta, si el payload puede interpretarse;
- `source_event_time`, cuando la semántica del origen esté definida;
- `observed_at`, conforme al mapeo de la fuente;
- `received_at`, la recepción local;
- `raw_event_digest` y `raw_payload_ref`, con representación y algoritmo declarados;
- `content_version_id`, si la observación puede canonicalizarse.

Cada recepción conserva su propio `received_at`. No se sustituye por la primera o última recepción de otra instancia. Si una recepción no puede producir contenido canónico válido, se puede conservar como evidencia de adquisición fallida sin crear una versión económica válida.

### 2.4 Eventos de evidencia de cierre y finality

Los estados de cierre y finality se conservan como afirmaciones/evaluaciones inmutables ligadas a la recepción que las sustenta y, cuando corresponda, a una versión económica.

Un evento conceptual de evidencia de estado debe identificar:

- `state_evidence_event_id`;
- `capture_event_id`;
- `content_version_id`, si es identificable;
- `source_closure_status`;
- referencia al campo de origen o evidencia que sustenta el estado;
- política de fuente y versión;
- tiempo de detección/registro y referencia a su evidencia temporal.

Los estados de cierre propuestos son `OPEN`, `CLOSED_CONFIRMED` y `UNKNOWN`. Una nueva afirmación de cierre no modifica la versión económica.

La finality se registra mediante una evaluación independiente, identificada por `finality_assessment_id`, con versión de contenido, evidencia de origen pertinente, resultado `NOT_FINAL` / `UNKNOWN` / `FINAL`, política versionada, criterio satisfecho, tiempo de evaluación y referencias de evidencia. Una evaluación nueva no elimina una evaluación anterior.

El enum canónico `CandleFinality` no se cambia por esta definición documental. Una evaluación posterior no demuestra que la instancia de `MarketData` usada en el pasado ya contuviera ese resultado.

### 2.5 Bundle de evidencia y disponibilidad

`EvidenceBundle` agrupa el contenido y la evidencia exacta que un consumidor puede usar. Como mínimo, identifica:

- `bundle_id` y `consumer_scope`;
- `content_version_id`;
- eventos de recepción pertinentes;
- evidencia de cierre y evaluación de finality aplicables al uso;
- validación canónica;
- estado y referencia de cobertura;
- revisiones/conflictos relevantes;
- política de admisión aplicable;
- evidencia de persistencia durable;
- `persisted_at` y `available_at`, si se pueden demostrar.

El bundle es inmutable. Si cambia la evidencia necesaria para una decisión, se construye un bundle nuevo. La disponibilidad pertenece al bundle y al ámbito de consumidor, no a la versión económica aislada. El mismo contenido puede haber estado disponible antes que la evidencia adicional de cierre, finality o cobertura.

La deduplicación del contenido no autoriza heredar un `available_at` anterior para evidencia que aún no existía. Una decisión debe identificar el bundle exacto utilizado y las afirmaciones que este contenía.

## 3. Cierre de fuente, mapeo canónico y timestamps de origen

### 3.1 Semántica de cierre por fuente

Para el WebSocket de Binance:

- `k.x=false` respalda `source_closure_status=OPEN` para esa recepción.
- `k.x=true` respalda `source_closure_status=CLOSED_CONFIRMED` para esa recepción concreta.
- Falta de evidencia, payload inválido o estado no interpretable produce `UNKNOWN`.

`k.x=true` no demuestra inmutabilidad permanente, disponibilidad previa a la recepción, ausencia de correcciones ni cobertura íntegra.

Para REST histórico o bajo demanda, el cierre programado pasado, el reloj local posterior al cierre, una fila OHLCV devuelta, respuestas repetidas iguales o archivos coincidentes no prueban por sí solos `CLOSED_CONFIRMED`. El cierre REST permanece `UNKNOWN` para admisión estricta hasta que una regla específica de procedencia, versionada y auditable, demuestre cómo se determina.

Esta es una regla documental propuesta, no una descripción de que el adaptador REST actual ya cumpla el contrato.

### 3.2 Correspondencia con el contrato canónico

La evidencia lateral añade procedencia y razones de admisión. No reemplaza ni modifica por sí sola `MarketData`, `Candle` ni sus enums.

| Evidencia lateral | Correspondencia normativa propuesta |
|---|---|
| `source_closure_status=OPEN` derivado de evento WebSocket válido con `k.x=false` | La representación canónica de esa recepción debe reflejar `CandleState.OPEN`. |
| `source_closure_status=CLOSED_CONFIRMED` derivado de evento WebSocket válido con `k.x=true` | La representación canónica puede reflejar `CandleState.CLOSED` para esa recepción. No promueve finality. |
| `source_closure_status=UNKNOWN` | No satisface una decisión que exige cierre demostrado, aunque un campo heredado indique `CLOSED`. |
| `Candle.completeness` | Describe completitud individual, no cobertura temporal. |
| `MarketData.completeness` | Conserva el significado canónico. El cierre por sí solo no promueve a `COMPLETE`. |
| `DataQuality.VALID` | Es necesaria cuando el perfil lo exige, pero no demuestra cierre, disponibilidad, integridad o cobertura. |
| `CandleFinality` | Conserva `NOT_FINAL`, `UNKNOWN` o `FINAL`; no se infiere del cierre. |
| `MarketData.available_at` | Sigue siendo la frontera canónica de disponibilidad. No se deriva retrospectivamente de `received_at` o del cierre programado. |

Si la evidencia lateral contradice el dato canónico, no se elige silenciosamente uno de los valores. Si la contradicción se puede resolver con evidencia registrada, se conserva la cadena de evidencia y se crea la evaluación/bundle correspondiente. Si no hay evidencia suficiente, se bloquea. Una infracción concluyente de identidad, estructura o validez se rechaza.

No se crea un segundo comparador de look-ahead. El consumidor canónico sigue evaluando `available_at <= decision_timestamp`.

### 3.3 Mapeo normativo de timestamps por fuente

- `source_event_time`: timestamp de origen que identifica cuándo se generó/publicó el evento solo cuando el contrato de fuente define explícitamente esa semántica.
- `observed_at`: timestamp canónico según el mapeo de fuente existente y documentado.
- `candle_start_utc`: comienzo económico de la vela, normalizado a UTC.
- `received_at`: tiempo de recepción local; nunca se retrofecha al tiempo del proveedor.
- `available_at`: disponibilidad demostrada para el consumidor; nunca se deriva solo de los campos anteriores.

Para el adaptador WebSocket actual, el evento Binance `E` se asigna a `observed_at`. R3 propone además conservarlo como `source_event_time` cuando la evidencia cruda permita verificar su origen. El mapeo documentado del adaptador debe indicar si ambos campos provienen del mismo evento; su igualdad no transforma `E` en disponibilidad local.

El campo `k.t` se usa como comienzo de vela, no como tiempo de evento del mensaje ni de disponibilidad. En una respuesta REST que no exponga un timestamp explícito de generación/publicación del evento, `source_event_time` permanece ausente/UNKNOWN. No se infiere de `k.t`, `close_time` ni el instante de solicitud. `observed_at` conserva el mapeo de REST documentado para esa fuente, incluso cuando se derive del inicio de vela; no se afirma con ello que sea hora de publicación del proveedor.

Un handoff al consumidor solo puede demostrar `available_at` cuando la frontera real del consumidor y la evidencia que la mide están definidas. Un dataset histórico descargado después no demuestra conocimiento anterior del sistema.

### 3.4 Regla de admisión del cierre

Para una decisión cuyo perfil exige vela cerrada:

- `CLOSED_CONFIRMED` satisface el requisito de cierre únicamente para el bundle que incluye la evidencia correspondiente.
- `OPEN` es un incumplimiento conocido de ese requisito y produce `REJECTED / REQUIRED_CLOSED_BUT_OPEN` para esa decisión.
- `UNKNOWN` impide demostrar el requisito y produce `BLOCKED / CLOSURE_UNPROVEN`.

Un evento de cierre recibido más tarde puede originar un bundle apto para una decisión posterior, pero no cambia el resultado de una decisión previa.

## 4. Cronología, relojes, persistencia y disponibilidad

### 4.1 Timestamps contractuales

La evidencia distingue:

- `source_event_time`: tiempo de origen cuando la semántica de la fuente está definida;
- `observed_at`: timestamp canónico según el mapeo específico de la fuente;
- `received_at`: instante local observado de recepción por cada evento de recepción;
- `persisted_at`: instante en que el bundle exacto y sus referencias necesarias quedaron durables y recuperables;
- `available_at`: primer instante demostrable en que ese bundle pudo recuperarse por el consumidor indicado;
- `decision_timestamp`: instante de la decisión que lo consume.

Ningún campo se copia desde otro solo porque falte. Todos los timestamps contractuales deben ser timezone-aware y normalizarse a UTC para serialización y comparación; esta conversión no corrige desfases entre relojes.

### 4.2 Orden de eventos, no solo comparación de timestamps

La cadena causal requerida para una decisión estricta es:

`recepción → persistencia durable del bundle → disponibilidad al consumidor → decisión`

Cuando los timestamps son comparables, la condición representativa es:

`received_at <= persisted_at <= available_at <= decision_timestamp`

Este invariante se aplica a cada ruta de evidencia que contribuye al bundle. Si hay múltiples recepciones, no se inventa un único `received_at` global ni se compara una recepción no relacionada como sustituto de la que sustenta una afirmación.

El orden real debe demostrarse con una fuente de evidencia temporal adecuada. R3 propone aceptar como evidencia de orden, cuando su semántica e integridad sean verificables:

1. un mismo dominio de reloj con identidad de proceso/sesión y contador monotónico o secuencia que demuestre el orden;
2. una relación causal explícita y auditable, como el commit durable que precede a la publicación del bundle y una traza de handoff que precede a la decisión;
3. dominios de reloj distintos con cotas de incertidumbre documentadas que demuestren, sin solapamiento, que el evento anterior ocurrió antes que el posterior.

No bastan la cercanía de timestamps, el orden de llegada observado en otro proceso o asumir sincronización perfecta de relojes.

Una referencia conceptual `TemporalOrderProof` debe identificar los eventos anterior y posterior, mecanismo de prueba, dominio de reloj o referencia causal, incertidumbre aplicable, resultado `PROVEN` / `UNPROVEN` / `CONTRADICTED` y referencias a la evidencia durable. Este nombre es propuesto, no se afirma que exista en el código.

### 4.3 Persistencia antes de publicación

El orden requerido para la futura captura estricta es:

1. recibir y validar la observación;
2. preservar payload y procedencia exactos o representación declarada;
3. persistir de forma durable el evento, versión y bundle que corresponda;
4. poner el bundle exacto a disposición del consumidor;
5. registrar la decisión que lo usa.

`persisted_at` no equivale automáticamente a `available_at`. La disponibilidad describe cuándo podía recuperarlo el consumidor; la persistencia describe cuándo quedó durable. Solo pueden coincidir si la frontera común queda demostrada.

Si no se puede recuperar el bundle exacto, no se certifica replay estricto. Si la disponibilidad se registra antes de la persistencia durable requerida, ese bundle no es admisible en modo estricto.

### 4.4 Tratamiento de relojes incoherentes

Si timestamps de pared contradicen evidencia causal, no se reescriben ni se corrigen artificialmente. Se conserva el conflicto y se evalúa si otra evidencia válida demuestra el orden real.

- Si el orden requerido queda demostrado por evidencia íntegra, se puede evaluar la admisión utilizando esa prueba; la discrepancia de timestamps permanece registrada.
- Si no queda demostrado, el resultado estricto es `BLOCKED / CLOCK_ORDER_UNPROVEN`.
- Si la evidencia temporal se contradice de forma no resoluble, el resultado es `BLOCKED / CLOCK_ORDER_CONFLICT`.

Una igualdad numérica entre timestamps no demuestra causalidad. Si la política del uso requiere un orden entre dos eventos y no existe prueba suficiente, se bloquea esa afirmación estricta aunque el comparador canónico inclusivo dé `ACCEPTED`.

El contrato canónico continúa usando `available_at <= decision_timestamp`; R3 no introduce otro comparador temporal.

### 4.5 Datos históricos consultados posteriormente

Una consulta histórica actual demuestra conocimiento actual sobre un hecho pasado, no disponibilidad para el sistema en una decisión anterior. Sin evidencia contemporánea, durable y recuperable del bundle disponible en el instante original, el resultado no se etiqueta como replay estrictamente point-in-time.

## 5. Completitud, integridad y cobertura

Las tres dimensiones se evalúan por separado.

### 5.1 Completitud de vela

Describe si el payload individual contiene los campos requeridos y satisface los requisitos de la fuente y del contrato. En WebSocket, k.x=true respalda que la fuente declara cerrada la kline de ese evento. Los controles OHLCV y de calidad canónica siguen siendo necesarios.

### 5.2 Integridad de observación

Describe si evento y versión son recuperables, los mapeos de identidad son trazables, el digest puede reproducirse y la representación supera validaciones canónicas. Una vela individual puede estar completa mientras su evidencia de persistencia, identidad de versión o disponibilidad no sea suficiente.

### 5.3 Cobertura temporal de captura

La cobertura se evalúa para el intervalo, fuente y conjunto de inputs requeridos. El manifiesto de cobertura debe especificar:

- inicio y fin de cobertura y su zona temporal;
- fuentes, sesiones y límites del ciclo de vida del canal;
- interrupciones, reinicios, pérdida o errores de transporte observados;
- fallos o demoras de persistencia;
- huecos de velas esperadas, duplicados y desalineación temporal;
- versiones recuperables y frontera de disponibilidad;
- capacidad y limitaciones para detectar pérdida cuando la fuente no proporciona una secuencia utilizable.

Una vela completa no prueba que todas las velas del período se hayan capturado. La ausencia de mensajes no demuestra ausencia de eventos si no se puede demostrar la integridad del canal en ese tramo.

La ventana requerida por una decisión incluye todas las observaciones históricas necesarias para sus indicadores y reglas. Un hueco o una pérdida sin resolver que afecte esa ventana bloquea esa decisión. Solo pueden evaluarse tramos aislados si su independencia del tramo afectado se justifica y documenta.

## 6. Estados WebSocket, recepciones, actualizaciones y revisiones

### 6.1 Principio append-only

Cada recepción se preserva. Las versiones de contenido son inmutables. Las afirmaciones de cierre y evaluaciones de finality son eventos independientes. El estado observable de una vela es una proyección reconstruida desde evidencia registrada, no un único campo mutable que sobrescribe hechos anteriores.

El tiempo local de recepción no demuestra por sí solo el orden en que Binance generó eventos. Los timestamps de origen solo prueban orden según su semántica y precisión.

### 6.2 Transiciones propuestas

| Evidencia previa | Evento nuevo | Tratamiento normativo |
|---|---|---|
| Sin observación previa | Evento válido con `k.x=false` | Registrar la recepción, crear versión económica si procede y emitir evidencia `OPEN`. |
| Vela abierta | Otro evento válido con `k.x=false`, contenido distinto | Registrar una recepción y una versión económica nueva; no clasificar automáticamente como revisión post-cierre. |
| Vela abierta | Evento válido con `k.x=true`, contenido idéntico | Registrar una recepción y nueva evidencia de cierre. Reutilizar versión económica si el digest canónico coincide. |
| Vela abierta | Evento válido con `k.x=true`, contenido distinto | Crear versión económica distinta y evidencia de cierre que la sustente. |
| Vela cerrada | Recepción posterior con contenido idéntico | Conservar la recepción; no crear una versión económica nueva solo por repetición. |
| Vela cerrada | Contenido diferente para la misma identidad lógica | Crear candidato de revisión y preservar ambas versiones; bloquear nuevas decisiones afectadas hasta resolver. |
| Evidencia previa de cierre | Evento recibido después con `k.x=false` | No reabrir silenciosamente. Registrar contradicción/evento tardío; no resolverlo sin evidencia suficiente. |
| Cualquier estado | Payload inválido o identidad incoherente | No admitir como dato canónico válido; registrar el error en la evidencia de adquisición. |

### 6.3 Clasificación de eventos

- Repetición de recepción: otro evento de recepción, incluso si su digest bruto coincide.
- Duplicado de contenido: representación que coincide bajo la misma canonicalización versionada.
- Actualización de vela abierta: nuevo contenido cuando la evidencia aplicable indica `OPEN`.
- Evidencia de cierre: evento válido con evidencia de la fuente que declara cierre.
- Revisión candidata: contenido diferente después de una versión cerrada para la misma identidad lógica.
- Conflicto de serie: representaciones incompatibles cuya relación no se puede probar.

Dos fuentes pueden registrar contenido económico igual con bytes, tiempos de mensaje o procedencias distintos. Solo se relaciona el contenido como idéntico cuando el mismo esquema canónico lo demuestra; la procedencia y las recepciones nunca se eliminan por ello.

Si no se puede determinar si un evento representa actualización normal, evento tardío, corrección o conflicto, se conserva la incertidumbre. No se elige automáticamente el payload cuyo `received_at` sea más reciente.

### 6.4 Consecuencia temporal

Una evidencia de cierre recibida después no convierte retrospectivamente un bundle abierto o desconocido en cerrado. Puede crear un bundle posterior con evidencia adicional para decisiones posteriores. El manifiesto anterior conserva el bundle y resultado originales.

Una discrepancia material no resuelta bloquea decisiones nuevas que dependan de la evidencia afectada, pero no reescribe la historia de las decisiones ya registradas.

## 7. Finality independiente y evaluaciones append-only

El enum canónico no cambia:

`CandleFinality = NOT_FINAL | FINAL | UNKNOWN`

- `NOT_FINAL`: una política explícita aplicable a la fuente declara que la versión continúa provisional, con evidencia que sustenta esa condición.
- `UNKNOWN`: no existe evidencia suficiente para determinar la finality.
- `FINAL`: una política de finality versionada, aplicable al origen y alcance declarados, dispone de evidencia auditable que demuestra que la versión satisface su criterio terminal.

`FINAL` no promete que nunca se descubrirá un error extraordinario. Declara que se ha satisfecho un criterio terminal explícito bajo una política y alcance identificados en el momento de la evaluación. Cualquier afirmación posterior genera una evaluación nueva; no borra la evaluación anterior ni reescribe decisiones anteriores.

Cada evaluación de finality debe referenciar la versión económica y las evidencias de origen relevantes, la versión de política, el criterio terminal, el resultado y el instante en que esa evaluación quedó registrada y disponible. No se modifica retrospectivamente un evento de evaluación.

No bastan `k.x=true`, superar el cierre programado, recibir respuestas repetidas iguales ni no observar revisiones dentro de cobertura incompleta. Para las klines de Binance bajo la evidencia actual, finality permanece `UNKNOWN` por defecto.

`UNKNOWN` no bloquea por sí solo un uso cuyo perfil no exige finality `FINAL`, siempre que todos los demás requisitos se satisfagan. Si el perfil exige `FINAL`, su ausencia probatoria produce `BLOCKED / FINALITY_UNPROVEN`.

Una corrección posterior no prueba automáticamente que la evaluación final anterior fuera fraudulenta o que no hubiera existido; es un nuevo evento que debe evaluarse contra la política vigente y las evidencias registradas.

## 8. Eventos de revisión, relaciones de versión y vistas temporales

### 8.1 Registro de revisión

Una revisión o discrepancia material se registra como un evento append-only, no como modificación destructiva de un único registro de vela. El evento enlaza la identidad lógica, versiones previas y candidatas cuando existan, el evento que descubre la diferencia, detected_at, disponibilidad demostrada de la nueva versión, digests comparados, motivo, evidencia de resolución y resolved_at cuando aplique.

Estados propuestos del evento:

- REVISION_CANDIDATE: hay una diferencia material, todavía sin relación demostrada.
- REVISION_CONFIRMED: la evidencia verifica que se trata de una revisión de la misma vela lógica bajo la política declarada.
- CONFLICT_UNRESOLVED: hay representaciones discrepantes y la relación no se puede resolver.
- REJECTED_AS_REVISION: hay evidencia de que la discrepancia no es una revisión de esa misma vela.

SUPERSEDED no es un estado del evento de revisión. Es una relación derivada de una versión dentro de una vista temporal posterior, una vez confirmada la sustitución.

### 8.2 Ausencia de revisiones observadas

NO_REVISION_OBSERVED_AS_OF_TIME solo puede afirmarse para una fuente, alcance, intervalo de cobertura y límite temporal explícitos, cuando la evidencia de integridad de cobertura es suficiente. Significa únicamente que no se observó otra versión dentro del historial declarado; no prueba ausencia universal de revisiones.

Si la captura tuvo interrupciones, retención incompleta o limitaciones de detección no resueltas, la ausencia de revisión es UNKNOWN.

### 8.3 Tres vistas separadas

- Replay original point-in-time: reconstruye las versiones disponibles y admisibles en el instante de decisión original. No incluye una revisión futura de forma retroactiva.
- Vista corregida actual: usa las revisiones confirmadas conocidas y admisibles al corte temporal actual. Se etiqueta como vista corregida, no como replay original.
- Auditoría posterior: añade un evento nuevo que señala que una decisión pasada puede haber quedado afectada por evidencia posterior. No modifica su payload, timestamps, digests ni manifiesto original.

Una revisión detectada después no altera lo que se conocía antes. Puede bloquear nuevas decisiones que dependan de evidencia actualmente en conflicto.

## 9. Esquema mínimo documental R3

Los nombres de esta sección son entidades conceptuales propuestas. No constituyen una migración SQL ni una afirmación de que ya existan en código. Los campos no disponibles se declaran ausentes/UNKNOWN según la semántica; no se sustituyen por valores inventados.

### 9.1 CaptureReceipt — evento de recepción

| Campo | Obligación | Semántica |
|---|---|---|
| `capture_event_id` | Obligatorio | ID único de esta recepción. |
| `source_id`, `provider`, `venue`, `market` | Según procedencia disponible | Procedencia con significados diferenciados. |
| `provider_symbol` | Condicional | Símbolo nativo. |
| Identidad lógica | Para payload identificable | Instrumento resuelto mediante metadatos explícitos, timeframe y comienzo UTC. |
| `source_event_time` | Condicional | Tiempo de origen con semántica documentada. |
| `observed_at` | Según mapeo de fuente | Timestamp canónico; no se asume universalmente igual al tiempo de origen. |
| `received_at` | Obligatorio para recepción observada | Instante local de esta recepción. |
| `raw_event_digest` | Condicional | Digest de bytes originales o representación explícita, con algoritmo identificado. |
| `raw_payload_ref` | Obligatorio para evidencia reproducible | Referencia durable al payload o representación declarada. |
| `content_version_id` | Si la observación se canonicaliza | Referencia a la versión económica. |
| sesión/transporte | Condicional | Referencia a conexión/sesión y su evidencia de ciclo de vida. |

### 9.2 CandleContentVersion — versión económica inmutable

| Campo | Obligación | Semántica |
|---|---|---|
| `content_version_id` | Obligatorio | ID estable e inmutable. |
| Identidad lógica | Obligatorio | Instrumento, timeframe, comienzo UTC. |
| `content_schema_version` | Obligatorio | Esquema y canonicalización. |
| `candle_content_digest` | Obligatorio | Digest económico reproducible. |
| `digest_algorithm` | Obligatorio | Algoritmo exacto. |
| `candle_payload_ref` | Obligatorio | Referencia al contenido canónico exacto. |
| referencias de recepción | Obligatorio | Eventos que sustentan la procedencia/contenido. |

No contiene `received_at`, `persisted_at`, `available_at`, `source_closure_status` ni `finality` como estado mutable de la versión.

### 9.3 SourceStateEvidenceEvent — evidencia de cierre

| Campo | Obligación | Semántica |
|---|---|---|
| `state_evidence_event_id` | Obligatorio | ID único del evento de evidencia. |
| `capture_event_id` | Obligatorio | Recepción que lo sustenta. |
| `content_version_id` | Si es identificable | Versión económica relacionada. |
| `source_closure_status` | Obligatorio para afirmación de cierre | OPEN, CLOSED_CONFIRMED o UNKNOWN. |
| fuente/política/evidencia | Obligatorio | Campo de origen y política aplicable que respaldan la afirmación. |
| instante de registro y prueba temporal | Obligatorio para admisión estricta | Cronología del evento de evidencia. |

### 9.4 FinalityAssessment — evaluación de finality

| Campo | Obligación | Semántica |
|---|---|---|
| `finality_assessment_id` | Obligatorio | ID único de evaluación. |
| `content_version_id` | Obligatorio | Versión evaluada. |
| `finality_result` | Obligatorio | NOT_FINAL, UNKNOWN o FINAL. |
| política/versión/criterio | Obligatorio | Criterio terminal aplicable. |
| referencias de evidencia | Obligatorio | Datos que justifican el resultado. |
| tiempos de evaluación/disponibilidad | Cuando se demuestran | No se igualan ni se retrofechan automáticamente. |

Una nueva evaluación se añade; no reemplaza la evaluación histórica.

### 9.5 EvidenceBundle — conjunto de evidencia disponible

| Campo | Obligación | Semántica |
|---|---|---|
| `bundle_id` | Obligatorio | ID estable del paquete exacto de evidencia. |
| `consumer_scope` | Obligatorio | Consumidor/frontera a la que aplica disponibilidad. |
| versión económica | Obligatorio | Contenido exacto. |
| recepciones/evidencia de cierre/finality | Según perfil | Eventos exactos que sustentan las condiciones. |
| validación canónica | Obligatorio para admisión | Referencia de validación. |
| cobertura y revisiones | Según perfil | Estado de cobertura/conflictos del intervalo requerido. |
| política de admisión | Obligatorio | Versión y requisitos del perfil. |
| `persisted_at` | Obligatorio en modo estricto | Persistencia durable del bundle exacto. |
| `available_at` | Solo si se demuestra | Primera disponibilidad recuperable de ese bundle al consumidor indicado. |
| pruebas de orden | Obligatorio para orden estricto | Referencias a evidencia causal/temporal. |

### 9.6 TemporalOrderProof — prueba de orden

| Campo | Obligación | Semántica |
|---|---|---|
| `order_proof_id` | Obligatorio cuando se necesita demostrar orden | ID de prueba. |
| evento anterior y posterior | Obligatorio | Extremos del vínculo de orden. |
| mecanismo de prueba | Obligatorio | Dominio de reloj, secuencia monotónica, relación causal o cotas de incertidumbre. |
| evidencia/referencias | Obligatorio | Registros durables que permiten reproducir la afirmación. |
| resultado | Obligatorio | PROVEN, UNPROVEN o CONTRADICTED. |
| política/versión de incertidumbre | Cuando aplique | Supuestos de reloj declarados. |

### 9.7 RevisionEvent — evento de revisión

Conserva `revision_event_id`, identidad lógica, referencias a versiones previa y candidata cuando se conozcan, `detected_at`, disponibilidad demostrada de nueva evidencia, estado, motivo, evidencia de resolución y tiempos de resolución. Los estados propuestos siguen siendo `REVISION_CANDIDATE`, `REVISION_CONFIRMED`, `CONFLICT_UNRESOLVED` y `REJECTED_AS_REVISION`. `SUPERSEDED` sigue siendo una relación derivada de versión, no un estado del evento.

### 9.8 DecisionManifest — manifiesto de decisión

| Campo | Obligación | Semántica |
|---|---|---|
| `decision_id`, `decision_timestamp` | Obligatorio | Decisión e instante timezone-aware. |
| dominios de reloj/pruebas de orden | Cuando son necesarios | Evidencia temporal exacta. |
| `bundle_ids` | Obligatorio por input | Bundles exactos consumidos. |
| `content_version_ids` y digests | Obligatorio | Versiones económicas exactas. |
| recepciones/afirmaciones utilizadas | Obligatorio | Ruta probatoria de los inputs. |
| `input_manifest_ref` | Obligatorio | Referencia durable al conjunto completo de datos, incluidas ventanas de indicadores. |
| política/perfil/configuración | Obligatorio | Reglas y estrategia exactas. |
| cobertura/revisiones | Obligatorio según perfil | Evidencia relevante de la ventana. |
| resultado canónico | Obligatorio | ACCEPTED, REJECTED o UNKNOWN del consumidor existente. |
| resultado estricto/motivos | Obligatorio en modo estricto | ACCEPTED, BLOCKED o REJECTED. |
| `execution_ref` | Condicional | Evidencia de precio/fill simulado, si existe. |

El manifiesto debe permitir reconstruir el contenido y el bundle exactos que la decisión utilizó. Conservar solo la vela disparadora de una señal no basta si sus indicadores dependieron de un historial más amplio.

## 10. Ejecución temporal y precio de fill

La admisión de una señal no demuestra que un fill simulado sea alcanzable. El fill requiere una referencia de precio con procedencia, timestamp, disponibilidad demostrada y regla de ejecución declarada y versionada.

La apertura de la vela siguiente no es precio de ejecución válido por defecto. Si su evento ocurrió antes de la decisión o el precio no estaba disponible cuando el sistema podía actuar, ese precio no representa un fill point-in-time admisible.

Cuando decisión y precio comparten timestamp, la comparación temporal inclusiva no demuestra orden causal. Si la regla exige ese orden y no existe evidencia adicional, el fill queda BLOCKED.

Una convención simulada no demostrada por evidencia histórica debe etiquetarse como supuesto de simulación, no como ejecución observada. Si no existe referencia temporalmente válida, el fill se bloquea.

## 11. Precedencia de resultados y admisión fail-closed

La admisión estricta requiere un perfil de uso explícito y versionado. El perfil declara como mínimo si exige cierre confirmado, finality FINAL, calidad válida, cobertura suficiente y una referencia de ejecución temporalmente admisible. Si falta o es ambiguo, el resultado es `BLOCKED / ADMISSION_PROFILE_UNSPECIFIED`.

Se conservan dos resultados independientes:

- Resultado canónico del consumidor: `ACCEPTED`, `REJECTED` o `UNKNOWN`, conforme al contrato existente.
- Resultado de admisión estricta: `ACCEPTED`, `BLOCKED` o `REJECTED`, de acuerdo con esta precedencia única.

### 11.1 Precedencia normativa

1. **Rechazo concluyente de integridad/validez.** Identidad inválida, payload estructuralmente inválido o una validación canónica obligatoria que falle de forma concluyente produce `REJECTED`.
2. **Rechazo temporal canónico.** Si el consumidor canónico devuelve `REJECTED` porque la disponibilidad es posterior a la decisión, la admisión estricta es `REJECTED`. Nunca se promueve a ACCEPTED por evidencia lateral.
3. **Incumplimiento conocido de requisito del perfil.** Si un perfil exige cierre y la evidencia aplicable demuestra `OPEN`, se produce `REJECTED / REQUIRED_CLOSED_BUT_OPEN`. Es un incumplimiento conocido de ese uso, no falta de evidencia. Una evidencia posterior puede habilitar un bundle para una decisión posterior, pero no cambia el resultado original.
4. **Falta de evidencia obligatoria o contradicción sin resolver.** Si no hay rechazo concluyente, pero falta una prueba necesaria, el resultado es `BLOCKED`. Esto incluye disponibilidad desconocida, orden no probado, persistencia no demostrada, cierre UNKNOWN requerido, finality UNKNOWN requerida, cobertura insuficiente, conflicto temporal o revisión material no resuelta.
5. **Aceptación.** Solo cuando no hay rechazo ni bloqueo y todos los requisitos del perfil están demostrados, la admisión es `ACCEPTED`.

Los motivos deben ser explícitos, por ejemplo: `AVAILABLE_AT_UNKNOWN`, `CLOSURE_UNPROVEN`, `REQUIRED_CLOSED_BUT_OPEN`, `FINALITY_UNPROVEN`, `PERSISTENCE_UNPROVEN`, `COVERAGE_INCOMPLETE`, `CLOCK_ORDER_UNPROVEN`, `CLOCK_ORDER_CONFLICT`, `EVIDENCE_CONTRACT_CONFLICT` o `REVISION_CONFLICT`.

Si coexisten fallos, el resultado global sigue esta precedencia; el manifiesto conserva todos los motivos aplicables, no solo el primero que se evaluó.

### 11.2 Resultado canónico preservado

El consumidor canónico mantiene el significado:

- `available_at is None` → `UNKNOWN`;
- `available_at <= decision_timestamp` → `ACCEPTED`;
- `available_at > decision_timestamp` → `REJECTED`.

No se agrega un segundo comparador temporal y no se hace que BLOCKED promueva UNKNOWN a ACCEPTED. La admisión estricta puede bloquear un dato que el comparador canónico acepte temporalmente si faltan otras pruebas obligatorias; esto no modifica el resultado canónico original.

## 12. Matriz normativa de aceptación

| Caso | Resultado canónico si es evaluable | Resultado estricto propuesto |
|---|---|---|
| Identidad/payload estructuralmente inválido | Puede no existir resultado canónico válido | REJECTED |
| `available_at > decision_timestamp` | REJECTED | REJECTED |
| `available_at=None` | UNKNOWN | BLOCKED |
| Perfil exige cierre y evidencia aplicable demuestra OPEN | Según disponibilidad | REJECTED / REQUIRED_CLOSED_BUT_OPEN |
| Perfil exige cierre y no hay evidencia suficiente | Según disponibilidad | BLOCKED / CLOSURE_UNPROVEN |
| WebSocket válido con `k.x=true`, bundle disponible y demás requisitos demostrados | ACCEPTED si la condición temporal canónica se cumple | Puede ser ACCEPTED; finality puede seguir UNKNOWN si no se exige |
| REST sin política verificable de cierre | Según disponibilidad | BLOCKED cuando el perfil exige cierre |
| Múltiples recepciones del mismo contenido | Según bundle evaluado | Preservar recepciones; reutilizar versión económica si digest coincide |
| Mismo contenido observado primero OPEN y después CLOSED | Según bundle evaluado | Reutilizar versión económica; crear evidencia de cierre y bundle posteriores |
| Evaluación de finality cambia posteriormente | Según bundle evaluado | Añadir evaluación nueva; preservar la evaluación histórica |
| Persistencia durable del bundle no demostrada | Puede estar temporalmente aceptado | BLOCKED / PERSISTENCE_UNPROVEN |
| Timestamps de pared no comparables y sin prueba causal suficiente | Puede estar temporalmente aceptado | BLOCKED / CLOCK_ORDER_UNPROVEN |
| Timestamps contradictorios y no hay resolución probatoria | Puede estar temporalmente aceptado | BLOCKED / CLOCK_ORDER_CONFLICT |
| Sidecar contradice al dato canónico sin resolución | Según evaluación temporal | BLOCKED / EVIDENCE_CONTRACT_CONFLICT |
| Ventana de inputs incompleta | Según evaluación temporal | BLOCKED / COVERAGE_INCOMPLETE |
| Revisión material no resuelta afecta los inputs requeridos | Según bundle evaluado | BLOCKED / REVISION_CONFLICT |
| Finality UNKNOWN y el perfil no la exige | Según evaluación temporal | No bloquea por sí sola |
| Finality UNKNOWN y el perfil exige FINAL | Según evaluación temporal | BLOCKED / FINALITY_UNPROVEN |
| Precio de fill anterior a la decisión o sin disponibilidad probada | Según evaluación de input | Fill BLOCKED; no se invalida automáticamente el input |
| Todos los requisitos de contenido, bundle, cronología, cobertura y perfil demostrados | ACCEPTED | ACCEPTED |

La matriz es normativa propuesta. No es evidencia de pruebas ni certificación del código existente.

## 13. Compatibilidad, implementación y límites

R3 es una revisión documental. No cambia:

- `bot_obrero/market_data.py`, sus campos o enums;
- normalización de adquisición;
- adaptadores REST o WebSocket;
- el contrato de consumo temporal o su comparador canónico;
- persistencia, almacenamiento, workers o captura;
- estrategias, backtest, tests o workflows.

Los nombres `CandleContentVersion`, `CaptureReceipt`, `SourceStateEvidenceEvent`, `FinalityAssessment`, `EvidenceBundle`, `TemporalOrderProof` y `DecisionManifest` son conceptos propuestos, no clases o tablas verificadas en el repositorio.

La futura implementación requeriría un diseño técnico aparte para almacenamiento append-only, canonicalización, referencias durables, evidencia de orden causal, perfiles de admisión y recuperación del bundle exacto. Requiere autorización explícita de CEREBRO. No se presume que la captura actual pueda reconstruir retroactivamente toda la cronología exigida.

## 14. Estado de la evidencia tras R3

- Separación de contenido económico, eventos de recepción y afirmaciones de cierre/finality: **definida normativamente en R3; implementación no verificada**.
- Disponibilidad por bundle y ámbito del consumidor: **definida normativamente; persistencia/recuperación no certificadas**.
- Orden temporal con dominios de reloj y pruebas causales: **criterio definido; no validado por pruebas en esta tarea**.
- Precedencia única entre REJECTED, BLOCKED y ACCEPTED: **definida documentalmente; no implementada ni probada**.
- Mapeo `source_event_time` / `observed_at`: **especificado por fuente; debe verificarse por adaptador en una fase autorizada**.
- Finality de klines Binance: **UNKNOWN por defecto con la evidencia actual**.
- Historial completo de revisiones, cobertura de captura y reconstrucción de decisiones: **no certificados**.
- Implementación, tests, backtests, adquisición y captura: **NO EJECUTADOS / NO AUTORIZADOS**.
- Estado documental: **R3_DOCUMENT_PUBLISHED — IMPLEMENTATION_NOT_AUTHORIZED**.
- Operaciones: **NO_TRADE**.

## 15. Referencias

1. Binance Spot WebSocket Streams — klines y campo x: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
2. Binance Public Data — README y publicación de históricos: https://github.com/binance/binance-public-data/blob/master/README.md
3. Política canónica de disponibilidad, FASE 1.12: ../PHASE1_12_AVAILABILITY_POLICY.md
4. Contrato de consumo temporal, FASE 1.23: ../PHASE1_23_MARKET_DATA_CONSUMPTION_CONTRACT.md
5. Contrato del adaptador WebSocket, FASE 1.24: ../PHASE1_24_BINANCE_SPOT_WEBSOCKET_KLINES.md
6. Auditoría de adquisición BTC/ETH: https://github.com/eltiootaku01-hue/bot-cripto/blob/research/btc-eth-follow-v1/docs/research/BTC_ETH_FOLLOW_RESEARCH_V1_DATA_ACQUISITION_AUDIT.md

La documentación de la fuente describe su contrato o los archivos distribuidos, pero no demuestra por sí sola qué versión estuvo disponible para este consumidor en cada instante histórico.

## 16. Cierre R3 y autorización

**R3 documental publicada; implementación no autorizada.**

R3 resuelve normativamente los cinco hallazgos de la auditoría R2:

1. El contenido económico queda separado de las recepciones, los estados de cierre, las evaluaciones de finality y los bundles temporales.
2. El orden causal se demuestra con evidencia explícita; los timestamps por sí solos no sustituyen esa prueba cuando los relojes no son comparables.
3. Se define una precedencia única para el resultado estricto y se preserva el resultado independiente del consumidor canónico.
4. `received_at` pertenece a cada evento de recepción, no a una versión de contenido que puede tener múltiples recepciones.
5. `source_event_time`, `observed_at`, el comienzo de vela, `received_at` y `available_at` tienen semánticas y reglas de mapeo explícitas.

La publicación documental no demuestra la conformidad del sistema actual y no autoriza captura, replay, backtests, cambios en contratos ejecutables ni ejecución financiera. Cualquier implementación requiere una nueva autorización específica de CEREBRO.

Regla rectora: el contenido económico es inmutable; las afirmaciones de cierre, finality y disponibilidad son evidencia temporal append-only. Cada decisión identifica el bundle exacto consumido y demuestra el orden causal necesario. Ninguna recepción, evaluación o revisión posterior reescribe silenciosamente el conocimiento de una decisión pasada.

`NO_TRADE` permanece vigente.
