# STRICT_POINT_IN_TIME_CAPTURE_V1 — Contrato normativo de cierre, disponibilidad, finality y revisiones

- Revisión documental: R2
- Fecha: 2026-10-16
- Autoridad: CEREBRO / IA-CHAN
- Estado: **R2_DOCUMENT_PUBLISHED — IMPLEMENTATION_NOT_AUTHORIZED**
- Decisión operativa: **NO_TRADE**
- Rama de investigación: research/strict-point-in-time-capture-v1
- Revisión previa: R1, commit 3063ff77751d5d4c5b2d5d36b982bad3b085c1f5
- Alcance autorizado: edición y publicación de este documento exclusivamente.
- Fuera de alcance: código, contratos ejecutables, tests, workflows, backtests, adquisición o captura de mercado, ejecución financiera y cambios en main.

## 1. Objetivo y regla rectora

Esta revisión R2 consolida las aclaraciones necesarias para que una implementación futura no interprete de forma incompatible la identidad de una vela, la cronología de su evidencia, las revisiones, la finality o la cobertura de captura.

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

## 2. Identidades, procedencia y canonicalización

### 2.1 Identidad lógica canónica

La identidad lógica de una vela es la tupla:

- instrument_id
- timeframe
- candle_start_utc

El timestamp de apertura se normaliza a UTC desde un timestamp timezone-aware. Los valores que representan el mismo instante deben normalizarse a la misma representación canónica antes de comparar identidad o calcular un digest. Un timestamp ingenuo, no interpretable o sin zona horaria se rechaza o queda bloqueado según el contexto; no se asume UTC de forma implícita.

Esta identidad lógica es independiente de la fuente. No identifica por sí sola un evento de recepción ni una versión de contenido. La propuesta es coherente con la intención de MarketData.candle_identity, pero no afirma que el constructor actual normalice o imponga ya esta condición.

### 2.2 Identidad de origen

Cada observación preserva, cuando exista, la procedencia nativa:

- source_id;
- provider;
- venue;
- market;
- provider_symbol;
- intervalo y timestamp nativo de apertura;
- referencia al instrumento canónico y a los metadatos usados para resolverlo;
- sesión o conexión de captura, cuando corresponda.

Los campos conservan el significado de los mapeos existentes. No se intercambian market y venue. En el adaptador WebSocket observado, provider es binance, market es SPOT, venue es BINANCE y source_id es binance-spot-websocket. REST y WebSocket pueden tener source_id distintos para una misma identidad lógica.

No se divide automáticamente el símbolo nativo para inferir activos base o cotizado. Se reutilizan los metadatos de instrumento disponibles.

### 2.3 Evento de recepción, contenido y versión

Se distinguen tres entidades:

- Evento de recepción: ocurrencia local concreta de recepción.
- Contenido canónico de vela: representación comparable de los campos económicos de una vela bajo un esquema versionado.
- Versión de contenido: contenido recuperable y su digest, vinculado a una identidad lógica y a su procedencia.

Una misma versión de contenido puede corresponder a más de un evento de recepción. Deduplicar contenido no elimina eventos de recepción ni sus timestamps.

Se distinguen dos digests:

- raw_event_digest: digest de los bytes originales solo si dichos bytes se preservaron. Si se hashea una representación transformada, se registra esa representación y su canonicalización; no se la denomina digest de los bytes originales.
- candle_content_digest: digest de los campos de vela bajo un esquema canónico versionado.

El version_id es estable y recuperable. Debe vincularse, como mínimo, a la identidad lógica, a la versión del esquema de contenido y digest, y al digest de contenido. La procedencia se registra como relación explícita. No se inventa un identificador de secuencia de proveedor que la fuente no haya entregado.

### 2.4 Canonicalización del contenido

La especificación de canonicalización del digest debe versionarse e incluir:

- lista exacta de campos incluidos y excluidos;
- orden de los campos y representación de nombres;
- representación UTC normalizada de los timestamps de vela;
- representación exacta de open, high, low, close y volume como valores decimales, sin conversión intermedia a float;
- reglas de valores ausentes, nulos y no válidos;
- codificación de texto;
- algoritmo de digest y versión del esquema.

raw_event_digest y candle_content_digest no son intercambiables. Dos mensajes con bytes o tiempos de evento distintos pueden representar el mismo contenido económico canónico. En ese caso se preservan ambos eventos y pueden apuntar al mismo contenido versionado cuando la canonicalización lo demuestre.

## 3. Cierre de fuente y correspondencia con el contrato canónico

### 3.1 Semántica de cierre por fuente

Para el WebSocket de Binance:

- k.x=false corresponde a source_closure_status=OPEN para ese evento.
- k.x=true corresponde a source_closure_status=CLOSED_CONFIRMED para ese evento concreto.
- Evidencia ausente, payload inválido o valor no interpretable corresponde a source_closure_status=UNKNOWN.

La evidencia del cierre debe referenciar el evento de origen y la versión de contenido que lo sustenta. k.x=true no demuestra inmutabilidad permanente, disponibilidad anterior a la recepción, ausencia de correcciones ni cobertura íntegra.

Para REST histórico o bajo demanda, el cierre programado pasado, el reloj local posterior al cierre, una fila OHLCV devuelta, respuestas repetidas iguales o archivos coincidentes no prueban por sí solos CLOSED_CONFIRMED. El cierre REST permanece UNKNOWN para admisión estricta hasta que una regla específica de procedencia, versionada y auditable, demuestre cómo se determina el cierre.

Esta es una regla documental propuesta. El adaptador REST existente puede tener otra conducta; R2 no modifica el código ni afirma que ya cumpla este contrato.

### 3.2 Correspondencia normativa con los campos canónicos

La evidencia lateral aporta procedencia y razones de admisión. No sustituye ni modifica por sí sola los campos canónicos de MarketData, Candle o sus enums.

| Evidencia lateral | Correspondencia normativa propuesta |
|---|---|
| source_closure_status=OPEN derivado de evento WS válido con k.x=false | La representación canónica debe reflejar CandleState.OPEN. |
| source_closure_status=CLOSED_CONFIRMED derivado de evento WS válido con k.x=true | La representación canónica puede reflejar CandleState.CLOSED para esa versión. No promueve finality. |
| source_closure_status=UNKNOWN | No se admite para una decisión que requiere cierre demostrado, aunque un campo heredado indique CLOSED. |
| Candle.completeness | Describe la completitud de la vela individual bajo el contrato aplicable. No demuestra cobertura temporal. |
| MarketData.completeness | Conserva el significado canónico existente. No se promueve a COMPLETE únicamente porque la vela esté cerrada. |
| DataQuality.VALID | Es necesaria donde el contrato la requiera, pero no prueba cierre, disponibilidad, integridad ni cobertura. |
| CandleFinality | Conserva NOT_FINAL, UNKNOWN o FINAL. No se infiere del estado de cierre. |
| MarketData.available_at | Sigue siendo la frontera temporal canónica para el consumidor. No se rellena retrospectivamente desde received_at ni desde cierre programado. |

Si la evidencia lateral contradice los campos canónicos, no se elige silenciosamente un valor ni se admite la observación. Si hay evidencia ya registrada que resuelve el conflicto, se conserva y evalúa de nuevo. Si falta evidencia, se bloquea. Si existe una infracción concluyente de identidad, estructura o validez, se rechaza.

No se crea un segundo evaluador temporal ni un segundo guard de look-ahead. El consumidor canónico continúa siendo autoridad para evaluar available_at <= decision_timestamp.

### 3.3 Regla de admisión del cierre

Para una decisión que exige una vela cerrada:

- CLOSED_CONFIRMED satisface únicamente el requisito de cierre, sujeto al resto de controles;
- OPEN no se admite para ese uso;
- UNKNOWN bloquea la admisión estricta.

Finality se evalúa de forma independiente.

## 4. Cronología, relojes, persistencia y disponibilidad

### 4.1 Timestamps distintos

La evidencia diferencia:

- source_event_time: tiempo informado por la fuente, si existe;
- received_at: instante local en que el proceso observa la recepción;
- persisted_at: instante en que la evidencia exacta queda durable y recuperable;
- available_at: primer instante demostrable en que esa versión persistida pudo ser recuperada por el consumidor relevante;
- decision_timestamp: instante de la decisión que consume la evidencia.

El tiempo de origen no sustituye el tiempo local de recepción, persistencia o disponibilidad. Ningún timestamp se copia desde otro solo porque falte el valor esperado.

### 4.2 Invariante temporal

Para cada versión admisible en modo estricto se debe demostrar la siguiente relación:

received_at <= persisted_at <= available_at <= decision_timestamp

La relación se refiere al orden real de los hechos, no solo al orden numérico de cuatro strings de timestamp.

Se permiten instantes iguales solo cuando las fronteras son realmente equivalentes o hay evidencia verificable del orden necesario. Una igualdad de timestamps no demuestra por sí sola causalidad entre recepción, persistencia, publicación y decisión.

Todos los timestamps contractuales deben ser timezone-aware y normalizarse a UTC para comparar y serializar. Si intervienen varios procesos o relojes, el mecanismo de registro debe permitir establecer el orden requerido. No se presupone sincronización perfecta, ni se interpreta un orden local de recepción como secuencia de origen.

Si la incertidumbre de reloj, el rollback o la falta de registro hace imposible establecer que un evento ocurrió antes que otro, la evidencia queda BLOCKED para la afirmación que depende de ese orden. No se corrige una cronología incoherente moviendo timestamps artificialmente.

### 4.3 Persistir antes de publicar

El orden lógico requerido para la futura captura estricta es:

1. recibir y validar el evento;
2. preservar el payload y la procedencia exactos o una representación claramente declarada;
3. persistir de forma durable el evento y la versión;
4. exponer esa versión al consumidor;
5. registrar la decisión que la consume.

La persistencia durable debe permitir recuperar la versión exacta. persisted_at no equivale automáticamente a available_at. La disponibilidad describe cuándo pudo usarla el consumidor; la persistencia describe cuándo fue durable. Las marcas pueden coincidir solo cuando la equivalencia real está demostrada.

Si persiste el evento pero no puede recuperarse la versión exacta, no se certifica para replay estricto. Si la disponibilidad se registra antes de la persistencia durable requerida, la observación no se admite como estrictamente reproducible.

La igualdad available_at == decision_timestamp es compatible con el comparador inclusivo canónico. Si el contrato además requiere probar orden causal entre eventos que comparten timestamp, la admisión queda BLOCKED cuando no hay evidencia de ese orden. No se cambia el comparador canónico para resolver ese problema.

### 4.4 Históricos descargados posteriormente

La consulta actual de una fila histórica demuestra conocimiento actual, no cuándo el sistema conocía aquella versión. Sin evidencia contemporánea, durable y recuperable de qué versión estuvo disponible, no se puede reclamar replay estrictamente point-in-time de una decisión pasada. El dataset debe etiquetarse como histórico de versión actual, no como captura contemporánea.

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

## 6. Estados WebSocket, duplicados y eventos contradictorios

### 6.1 Principio

Cada evento recibido se preserva como recepción append-only. El estado observable de una vela es una proyección derivada de eventos y relaciones demostradas; no es un único campo mutable que permita borrar el historial.

La hora local de recepción no demuestra el orden en que Binance generó los eventos. Los timestamps de origen solo sustentan afirmaciones de orden según su semántica y precisión.

### 6.2 Transiciones propuestas

| Evidencia previa | Evento nuevo | Clasificación propuesta | Tratamiento |
|---|---|---|---|
| Sin observación de la vela | Evento válido con k.x=false | OPEN | Registrar la observación abierta. No admitir como cerrada. |
| OPEN | Otro k.x=false con contenido distinto | Actualización normal de vela abierta | Registrar evento y nueva versión; no clasificar como revisión post-cierre. |
| OPEN | Evento válido con k.x=true | CLOSED_CONFIRMED | Registrar el evento de cierre y vincularlo a la versión exacta sustentada. |
| CLOSED_CONFIRMED | Mismo contenido canónico | Repetición de recepción | Preservar recepción y tiempo; no fabricar una versión económica nueva si el contenido canónico coincide. |
| CLOSED_CONFIRMED | Contenido diferente con la misma identidad lógica | REVISION_CANDIDATE | Preservar ambas versiones; bloquear nuevas decisiones afectadas hasta resolver. |
| CLOSED_CONFIRMED | Evento posterior localmente recibido con k.x=false | Evento tardío o contradictorio | No reabrir silenciosamente. Conservar y resolver como dato tardío solo si la evidencia lo demuestra; de otro modo mantener conflicto. |
| REVISION_CANDIDATE | Evidencia verificable confirma una corrección | REVISION_CONFIRMED | Añadir la relación de revisión; no reescribir decisiones anteriores. |
| Cualquier estado | Payload inválido o identidad incoherente | Evento inválido | No admitir como MarketData canónico válido; registrar el error según el contrato de adquisición aplicable. |

### 6.3 Eventos tardíos y orden del origen

Un evento recibido más tarde puede representar una observación generada antes, pero solo se clasifica como evento tardío cuando la semántica del tiempo de origen y la evidencia disponible respaldan esa conclusión. El tiempo de origen no se convierte en available_at ni fabrica un orden total no proporcionado por la fuente.

Si no se puede establecer si un evento es actualización normal, corrección o conflicto, se conserva la incertidumbre. No se selecciona automáticamente el payload con received_at más reciente. Una discrepancia material no resuelta bloquea nuevas decisiones afectadas.

Una actualización local posterior no reabre una vela cerrada por sí misma. Si una fuente contradice posteriormente su estado anterior, se registra el hecho nuevo y se resuelve de manera explícita; el estado observado anteriormente sigue disponible para reconstrucción histórica.

### 6.4 Repetición, actualización y revisión no son lo mismo

- Repetición de recepción: otro evento de recepción, incluso si su raw_event_digest coincide.
- Duplicado de contenido: una observación cuya canonicalización coincide con una versión ya registrada.
- Actualización de vela abierta: nuevo contenido mientras la evidencia de esa versión todavía es OPEN.
- Revisión candidata: contenido distinto después de una versión cerrada para la misma identidad lógica.
- Conflicto de serie histórica: contenido incompatible de la misma identidad lógica sin relación causal o de revisión demostrada.

La misma identidad lógica con el mismo contenido puede conservar varias recepciones y compartir una versión canónica. La misma identidad con contenido diferente no se descarta ni se sobrescribe silenciosamente.

Dos fuentes pueden observar el mismo contenido económico sin que coincidan los bytes o timestamps de sus mensajes. Se conservan las procedencias y solo se relacionan como el mismo contenido si la canonicalización versionada demuestra equivalencia.

## 7. Finality independiente

El enum canónico se conserva como CandleFinality = NOT_FINAL | FINAL | UNKNOWN.

- NOT_FINAL: una política explícita aplicable a la fuente y al ciclo de vida declara que la versión permanece provisional, con evidencia que sustenta esa condición.
- UNKNOWN: la evidencia no basta para determinar finality.
- FINAL: una política de finality identificada por versión, aplicable al origen y alcance declarados, dispone de evidencia auditable suficiente para demostrar que la versión satisface el criterio terminal definido.

FINAL no promete que jamás se descubrirá un error extraordinario. Declara que una condición terminal explícita se satisfizo bajo la política y alcance indicados en el instante de la afirmación. Una corrección posterior es un nuevo hecho de conocimiento; no elimina la afirmación histórica ni reescribe la decisión que la utilizó.

Para declarar FINAL se requieren:

1. política de finality versionada, aplicable a la fuente y clase de dato;
2. definición inequívoca del criterio terminal;
3. evidencia recuperable de que esta versión satisface ese criterio;
4. referencias a la política, evidencia, ámbito temporal y origen;
5. tratamiento explícito de correcciones que contradigan después la afirmación.

No basta k.x=true, superar el cierre programado, recibir respuestas idénticas ni no observar revisiones dentro de una cobertura incompleta. Para las klines Binance consideradas en el estado actual de evidencia, finality permanece UNKNOWN por defecto.

UNKNOWN no bloquea automáticamente una decisión que solo requiera cierre, disponibilidad, integridad y cobertura suficientes. Sí bloquea cuando una política explícita del experimento exige FINAL. No se promueve automáticamente FINAL por ausencia de revisiones observadas.

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

## 9. Esquema mínimo documental

Los campos de esta sección son una propuesta conceptual, no una migración SQL ni una afirmación de que ya existan en el código. Los campos obligatorios deben tener valores recuperables, no valores inventados. Un campo condicional ausente debe representarse explícitamente como desconocido o no aplicable según su semántica; no se sustituye por un valor por defecto que afirme un hecho.

### 9.1 Evento de recepción

| Campo | Obligación | Semántica |
|---|---|---|
| capture_event_id | Obligatorio | Identificador único y estable de la recepción local. |
| source_id, provider, venue, market | Según procedencia disponible | Identificadores de origen, sin intercambiar sus significados. |
| provider_symbol | Condicional | Símbolo nativo recibido. |
| instrument_id | Obligatorio para versión canónica | Instrumento resuelto mediante metadatos explícitos. |
| timeframe, candle_start_utc | Obligatorio para kline identificada | Identidad lógica canónica normalizada. |
| source_event_time | Condicional | Tiempo indicado por fuente, no disponibilidad local. |
| received_at | Obligatorio | Instante local observado de recepción. |
| raw_event_digest | Condicional | Digest del payload original preservado, con algoritmo y representación declarados. |
| raw_payload_ref | Obligatorio para evidencia reproducible | Referencia durable al payload original o representación declarada exacta. |
| transport_session_ref | Condicional | Referencia a conexión o sesión de captura. |

### 9.2 Versión de contenido

| Campo | Obligación | Semántica |
|---|---|---|
| version_id | Obligatorio | Identificador estable de la versión recuperable. |
| instrument_id, timeframe, candle_start_utc | Obligatorio | Identidad lógica canónica. |
| source/event references | Obligatorio | Procedencia y eventos que sustentan la versión. |
| content_schema_version | Obligatorio | Versión del esquema y reglas de canonicalización. |
| candle_content_digest | Obligatorio | Digest reproducible del contenido canónico. |
| digest_algorithm | Obligatorio | Algoritmo exacto. |
| candle_payload_ref | Obligatorio | Referencia al payload canónico exacto validado. |
| source_closure_status | Obligatorio | OPEN, CLOSED_CONFIRMED o UNKNOWN, respaldado por evidencia del origen. |
| finality | Obligatorio | NOT_FINAL, UNKNOWN o FINAL, sin inferir de cierre. |
| received_at | Obligatorio cuando la recepción fue observada | Instante de recepción que sustenta la versión. |
| persisted_at | Obligatorio para admisión estricta | Instante de persistencia durable demostrada. |
| available_at | Solo si se demuestra | Primer instante de disponibilidad para el consumidor; puede permanecer UNKNOWN. |
| canonical_validation_ref | Obligatorio para admisión | Evidencia de validación canónica aplicable. |

### 9.3 Evento de revisión

| Campo | Obligación | Semántica |
|---|---|---|
| revision_event_id | Obligatorio | Identificador único del evento de revisión. |
| logical_candle_identity | Obligatorio | Identidad lógica de la vela afectada. |
| previous_version_id | Condicional | Versión anterior conocida. |
| candidate_version_id | Condicional | Versión nueva identificable. |
| detected_at | Obligatorio | Momento real del descubrimiento. |
| candidate available_at | Condicional | Disponibilidad demostrada de la nueva versión; nunca retrofechada. |
| revision_event_status | Obligatorio | Uno de los estados propuestos de revisión. |
| reason_code | Obligatorio | Causa o categoría explícita de la discrepancia. |
| evidence_refs | Obligatorio | Eventos, digests y criterios utilizados. |
| resolved_at, resolution_ref | Obligatorio solo si se resuelve | Instante y evidencia de resolución. |

### 9.4 Manifiesto de decisión

| Campo | Obligación | Semántica |
|---|---|---|
| decision_id | Obligatorio | Identidad única de la decisión. |
| decision_timestamp | Obligatorio | Instante explícito y timezone-aware. |
| input_version_ids | Obligatorio | Todas las versiones exactas consumidas. |
| input_digests | Obligatorio | Integridad de los inputs recuperados. |
| input_manifest_ref | Obligatorio | Referencia durable al conjunto completo de inputs. |
| policy_version | Obligatorio | Versión de reglas temporales y de admisión. |
| strategy/configuration/model ref | Obligatorio | Configuración exacta de la decisión. |
| coverage_status_ref | Obligatorio | Evidencia de cobertura para el intervalo requerido. |
| canonical_consumption_result | Obligatorio | ACCEPTED, REJECTED o UNKNOWN del consumidor existente. |
| strict_admission_result | Obligatorio para modo estricto | ACCEPTED, BLOCKED o REJECTED con motivos explícitos. |
| revision_refs | Condicional | Revisiones o conflictos relevantes. |
| execution_ref | Condicional | Evidencia de ejecución y precio si existe un fill simulado. |

Si se necesitan muchos candles para calcular indicadores, el manifiesto debe recuperar toda la ventana de inputs necesaria; guardar solo la vela que dispara la señal no basta. Los registros de eventos, versiones, revisiones y decisiones son entidades separadas y se enlazan mediante referencias estables.

## 10. Ejecución temporal y precio de fill

La admisión de una señal no demuestra que un fill simulado sea alcanzable. El fill requiere una referencia de precio con procedencia, timestamp, disponibilidad demostrada y regla de ejecución declarada y versionada.

La apertura de la vela siguiente no es precio de ejecución válido por defecto. Si su evento ocurrió antes de la decisión o el precio no estaba disponible cuando el sistema podía actuar, ese precio no representa un fill point-in-time admisible.

Cuando decisión y precio comparten timestamp, la comparación temporal inclusiva no demuestra orden causal. Si la regla exige ese orden y no existe evidencia adicional, el fill queda BLOCKED.

Una convención simulada no demostrada por evidencia histórica debe etiquetarse como supuesto de simulación, no como ejecución observada. Si no existe referencia temporalmente válida, el fill se bloquea.

## 11. Precedencia, admisión estricta y fail-closed

### 11.1 Resultado canónico de consumo

Se preservan los estados del contrato canónico:

- ACCEPTED: la disponibilidad está demostrada y no es posterior a decision_timestamp.
- REJECTED: está demostrado que la versión no estaba disponible a tiempo.
- UNKNOWN: no hay evidencia suficiente para demostrar disponibilidad.

available_at desconocido no produce ACCEPTED. No se crea un segundo guard temporal.

### 11.2 Resultado de admisión estricta

Como capa documental separada se propone:

- ACCEPTED: todos los requisitos obligatorios para el uso concreto están demostrados.
- BLOCKED: falta evidencia obligatoria, una dimensión necesaria está UNKNOWN, la cobertura necesaria es insuficiente o existe un conflicto no resuelto.
- REJECTED: una condición concluyente de identidad o validez se incumple.

La precedencia propuesta es:

1. Una identidad imposible, payload inválido o infracción estructural concluyente produce REJECTED.
2. Si available_at > decision_timestamp, el resultado canónico es REJECTED y la admisión estricta no puede ser ACCEPTED.
3. Si available_at es desconocido, la persistencia no está demostrada, el cierre requerido es UNKNOWN o la cobertura necesaria está incompleta, la admisión es BLOCKED.
4. Si existe un conflicto de revisión material no resuelto, se bloquean nuevas decisiones afectadas; el replay histórico permanece sin cambios.
5. Solo cuando todos los requisitos obligatorios se satisfacen puede resultar ACCEPTED.

Cada resultado estricto conserva razones explícitas como AVAILABLE_AT_UNKNOWN, CLOSURE_UNPROVEN, PERSISTENCE_UNPROVEN, COVERAGE_INCOMPLETE, CLOCK_ORDER_UNPROVEN o REVISION_CONFLICT.

La capa estricta no se incorpora al enum canónico en esta revisión documental. No se deben confundir los dos resultados ni hacer que BLOCKED cambie UNKNOWN a ACCEPTED.

## 12. Matriz normativa de aceptación

| Caso | Resultado de admisión estricta |
|---|---|
| WebSocket válido con k.x=false y decisión que exige vela cerrada | BLOCKED para ese uso. |
| WebSocket válido con k.x=true, versión persistida y disponible a tiempo, integridad suficiente y sin conflicto material | Puede ser ACCEPTED; finality puede seguir UNKNOWN. |
| REST sin política verificable de cierre | BLOCKED para uso que exige cierre confirmado. |
| Sidecar y estado canónico contradictorios sin resolución demostrada | BLOCKED. |
| available_at desconocido | BLOCKED; consumo canónico UNKNOWN. |
| available_at posterior a decision_timestamp | REJECTED; consumo canónico REJECTED. |
| Payload inválido o identidad incoherente | REJECTED. |
| Falta persistencia durable o su orden no se demuestra | BLOCKED. |
| Vela completa pero ventana de inputs incompleta | BLOCKED para decisiones dependientes de esa ventana. |
| Mismo contenido recibido repetidamente | Conservar recepciones; contenido deduplicable. |
| Contenido nuevo mientras la vela sigue abierta | Actualización normal; no revisión post-cierre. |
| Contenido distinto después de cierre para la misma identidad lógica | REVISION_CANDIDATE o CONFLICT_UNRESOLVED; bloquear nuevas decisiones afectadas. |
| Revisión descubierta después de una decisión histórica | No reescribir el replay original; registrar nueva anotación de auditoría. |
| Finality UNKNOWN y la política no la exige | No bloquea por sí sola si todo lo demás se cumple. |
| Finality UNKNOWN y la política exige FINAL | BLOCKED. |
| Precio de fill anterior a la decisión o con disponibilidad no demostrada | Fill BLOCKED. |
| Igualdad de timestamps sin orden causal demostrable cuando este sea requisito | BLOCKED en la afirmación estricta afectada. |
| Captura insuficiente para certificar ausencia de revisiones | UNKNOWN; no afirmar ausencia global. |

Esta matriz es una especificación normativa, no un resultado de pruebas ni una certificación del código actual.

## 13. Compatibilidad, implementación y límites

R2 no modifica MarketData, Candle, sus enums o validaciones, EvidenceTimestamp, el evaluador canónico, adaptadores REST o WebSocket, almacenamiento, captura, workers, estrategias, backtest, tests ni workflows.

Los campos auxiliares, estados de revisión, canonicalización del digest, manifiestos y razones de bloqueo son elementos propuestos. La implementación futura debe reutilizar la política temporal canónica y su guard de look-ahead. Cualquier ampliación del esquema, enums o contratos ejecutables requiere diseño técnico adicional y autorización específica de CEREBRO.

En el código existente se observan diferencias que esta norma no resuelve automáticamente: la validación timezone-aware no implica por sí misma normalización UTC; el adaptador REST puede inferir cierre por el reloj; el adaptador WebSocket no establece por sí solo un registro durable de captura. Son brechas de implementación por evaluar en una fase expresamente autorizada, no cambios realizados por R2.

## 14. Estado de la evidencia

- Correspondencia normativa entre sidecar y contrato canónico: definida en R2; implementación no verificada.
- Normalización UTC de identidad: exigida por R2; no se afirma que el constructor actual ya la imponga.
- Orden temporal recepción-persistencia-disponibilidad-decisión: exigido para admisión estricta; no validado por pruebas en esta tarea.
- Eventos WS tardíos, duplicados y discrepantes: clasificación normativa propuesta; no implementada.
- Canonicalización y digest versionado: esquema documental definido; no implementado ni probado.
- Finality de klines Binance: UNKNOWN por defecto bajo esta evidencia; ninguna garantía de inmutabilidad afirmada.
- Cobertura completa de captura o ausencia de revisiones: no demostradas por este documento.
- Replay reproducible de decisiones: requiere manifiestos e inputs recuperables; no certificado.
- Tests, backtests, adquisición y captura: NO EJECUTADOS.
- Implementación: NO AUTORIZADA.
- Operaciones: NO_TRADE.

## 15. Referencias

1. Binance Spot WebSocket Streams — klines y campo x: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
2. Binance Public Data — README y publicación de históricos: https://github.com/binance/binance-public-data/blob/master/README.md
3. Política canónica de disponibilidad, FASE 1.12: ../PHASE1_12_AVAILABILITY_POLICY.md
4. Contrato de consumo temporal, FASE 1.23: ../PHASE1_23_MARKET_DATA_CONSUMPTION_CONTRACT.md
5. Contrato del adaptador WebSocket, FASE 1.24: ../PHASE1_24_BINANCE_SPOT_WEBSOCKET_KLINES.md
6. Auditoría de adquisición BTC/ETH: https://github.com/eltiootaku01-hue/bot-cripto/blob/research/btc-eth-follow-v1/docs/research/BTC_ETH_FOLLOW_RESEARCH_V1_DATA_ACQUISITION_AUDIT.md

La documentación de la fuente describe su contrato o los archivos distribuidos, pero no demuestra por sí sola qué versión estuvo disponible para este consumidor en cada instante histórico.

## 16. Cierre R2 y autorización

**R2 documental publicada; implementación no autorizada.**

Esta revisión responde a los seis hallazgos de la auditoría R1: (1) correspondencia entre campos canónicos y sidecar; (2) invariantes temporales y orden causal; (3) transiciones para eventos WS contradictorios o tardíos; (4) esquema mínimo para eventos, versiones, revisiones y decisiones; (5) criterios versionados y auditables para FINAL; (6) separación de duplicados, actualizaciones abiertas, revisiones y cobertura.

La aprobación documental no demuestra conformidad del sistema actual ni habilita captura, replay, backtests o promoción de históricos a point-in-time estricto. Cualquier siguiente etapa de implementación requiere autorización específica.

Regla rectora: una decisión solo puede utilizar versiones identificables, íntegras y disponibles en su instante de decisión. Una revisión posterior no reescribe silenciosamente el conocimiento histórico.

NO_TRADE permanece vigente.
