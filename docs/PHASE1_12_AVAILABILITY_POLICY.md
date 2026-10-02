# FASE 1.12 — Política canónica de disponibilidad y evidencia temporal

## Estado

FASE 1.12 es una fase de auditoría, diseño y decisión arquitectónica.

No se modifica código productivo ni el Canonical Market Data Contract v1.0. No se implementan nuevos proveedores, WebSocket, persistencia, replay engine ni una nueva primitiva temporal.

## 1. EXISTENTE / POLÍTICA PROPUESTA / FUTURO

### EXISTENTE

El contrato actual ya contiene observed_at, received_at, available_at, EvidenceTimestamp, MarketData.evidence_at() y MarketObservation.evidence_at(). La regla temporal existente es:

available_at <= decision_timestamp

La ausencia de disponibilidad se representa con available_at = None y bloquea MarketData.evidence_at().

FASE 1.11 dejó Binance con available_at=None de forma deliberadamente conservadora.

### POLÍTICA PROPUESTA

available_at representa el primer instante demostrable en que la evidencia concreta representada por MarketData estuvo disponible para el consumidor relevante dentro del sistema.

### FUTURO / FUERA DE ALCANCE

Quedan para fases posteriores el hardening productivo de esta política, la definición exacta de la frontera de consumidor en cada adapter, WebSocket, otros proveedores, persistencia, versionado de correcciones históricas y un motor de replay.

## 2. DEFINICIONES NORMATIVAS

### observed_at

Timestamp del fenómeno o hecho de mercado según la semántica temporal de la fuente. Ejemplos: open time de una kline, event time de un mensaje live o timestamp intrínseco de una observación histórica.

No responde cuándo pudo utilizarlo nuestro sistema y por sí solo nunca autoriza una decisión.

### received_at

Timestamp en que la representación externa fue recibida por el sistema de adquisición. En REST es normalmente el momento en que la respuesta completa fue recibida; en live, el instante local de recepción del mensaje.

received_at no implica por sí solo que el consumidor de evidencia ya disponga del MarketData canónico.

### available_at

Primer instante demostrable en que la evidencia concreta representada por MarketData estuvo disponible para el consumidor relevante dentro del sistema.

No significa event time, publication time, request time ni received_at por igualdad automática.

### decision_timestamp

Instante en que la decisión pretende utilizar la evidencia.

La condición temporal sigue siendo available_at <= decision_timestamp.

## 3. REGLA DE EVIDENCIA

Un timestamp de disponibilidad debe estar respaldado por una evidencia temporal observable. Cuando la evidencia no permite localizar el primer instante de disponibilidad sin inferencia, available_at permanece UNKNOWN/None.

Evidencia suficiente: evento local o externo que identifique de forma inequívoca el momento desde el cual el consumidor podía acceder al dato.

Evidencia insuficiente: open time o close time de una vela, hora de solicitud REST, observed_at, estimaciones de latencia o un timestamp del proveedor interpretado como recepción local sin evidencia adicional.

## 4. ¿RECEIVED_AT PUEDE SER AVAILABLE_AT?

Sí, pero no por defecto.

available_at = received_at es válido cuando received_at registra un evento real, la representación completa de la evidencia ya fue recibida, esa misma frontera es la frontera desde la que el consumidor puede acceder al dato y no existe una etapa posterior obligatoria que retrase esa disponibilidad.

Ejemplo válido: respuesta completa recibida y entrega directa al consumidor de evidencia en la misma frontera temporal.

Si la respuesta llega a T1, el parseo termina a T2 y el consumidor obtiene el MarketData a T3, entonces received_at=T1 no demuestra availability a T1. La disponibilidad relevante sería T3 si esa frontera puede medirse.

Conclusión: received_at puede coincidir con available_at cuando ambas fronteras son realmente la misma; la igualdad no es una propiedad automática del contrato.

## 5. ¿OBSERVED_AT PUEDE SER AVAILABLE_AT?

No por defecto.

Solo es legítimo cuando existe evidencia independiente de que el dato estuvo disponible para el consumidor exactamente en ese mismo instante. Una kline de las 12:00 consultada a las 15:00 no fue conocimiento del sistema a las 12:00.

## 6. SOURCE AVAILABILITY VS SYSTEM AVAILABILITY

Conceptualmente existen dos fenómenos: disponibilidad/publicación de la fuente y disponibilidad dentro de nuestro sistema.

La disponibilidad canónica usada para look-ahead es la disponibilidad sistémica. Un provider publication time no sustituye automáticamente la disponibilidad local.

No se agregan provider_available_at ni system_available_at al contrato.

## 7. REST QUERY SEMANTICS

Ejemplo:

candle event time = 10:00
REST request = 15:00
response received = 15:00:01

El hecho ocurrió a las 10:00, pero el sistema aprende el dato a partir de la respuesta posterior.

El dato puede soportar decisiones desde su disponibilidad real en el sistema, no decisiones a las 10:30 como si hubiese existido conocimiento entonces.

Para REST bajo demanda, el knowledge time depende de la consulta. Un dato histórico no se convierte en conocimiento histórico del sistema.

## 8. LIVE SEMANTICS

Ejemplo:

event time = T0
local reception = T1
consumer handoff = T2

La política ideal es:

observed_at = T0
received_at = T1
available_at = T2

Si la recepción y el handoff al consumidor son la misma frontera, available_at puede ser T1.

Un publication timestamp de la fuente puede conservarse como provenance, pero no sustituye automáticamente la disponibilidad local.

## 9. HISTORICAL SEMANTICS

Para datos históricos:

event time != knowledge time

Un dato histórico consultado hoy sigue describiendo un fenómeno pasado, pero su disponibilidad para el sistema nace cuando la consulta hace que el dato esté accesible al consumidor.

## 10. REPLAY SEMANTICS

Replay no puede obtener una regla temporal más permisiva que Live.

Un replay fiel debe conservar el available_at de la evidencia original. Si la evidencia original era UNKNOWN, el replay debe mantenerla UNKNOWN.

No se permite sustituir un available_at desconocido por la hora de replay de forma silenciosa.

Una futura modalidad de replay que simule disponibilidad deberá ser una decisión explícita de esa fase y no una modificación silenciosa de MarketData.

## 11. HISTORICAL CORRECTIONS

Ejemplo:

evento T0
conocimiento original T1
corrección descubierta T2

La corrección crea una nueva realidad de conocimiento en T2. No puede utilizarse como si hubiera sido conocida en T0 o T1.

Fases posteriores deberán definir precedencia, supersession, identidad de revisión, corrección frente a reemplazo, deduplicación y replay de revisiones.

Esta fase no implementa ninguno de esos mecanismos.

## 12. LOOK-AHEAD — TABLA NORMATIVA

| observed_at | received_at | available_at | decision_timestamp | ¿usable? |
|---|---|---|---|---|
| anterior | anterior | anterior | posterior | Sí, si availability está demostrada y el dato satisface las validaciones restantes |
| anterior | posterior | posterior | anterior | NO |
| anterior | posterior | UNKNOWN | cualquier | NO |
| posterior | posterior | posterior | anterior | NO |
| anterior | anterior | anterior | posterior | Sí, bajo la misma regla de availability |
| anterior | posterior | posterior | posterior | Sí, si available_at <= decision_timestamp |
| anterior | posterior | anterior | posterior | NO por incoherencia semántica de disponibilidad sistémica |
| cualquier | cualquier | posterior | anterior | NO |

La columna decisiva para look-ahead es únicamente available_at <= decision_timestamp.

## 13. AVAILABLE_AT UNKNOWN

available_at = None significa que el sistema no puede demostrar el primer instante de disponibilidad para el consumidor de evidencia.

Permitido: conservar MarketData, inspeccionarlo, serializarlo, realizar análisis descriptivo que no lo trate como evidencia temporalmente utilizable y completar posteriormente el conocimiento si aparece evidencia temporal legítima.

Bloqueado: MarketData.evidence_at(), to_market_observation(), usarlo como evidencia para una decisión y cualquier análisis derivado que requiera evidencia temporal válida.

UNKNOWN es un estado conservador; no significa disponibilidad implícita.

## 14. MARKETDATA VS MARKETOBSERVATION

La frontera existente permanece:

MarketData -> available_at -> evidence_at(decision_timestamp) -> MarketObservation

to_market_observation() sigue exigiendo quality=VALID y available_at conocido.

No se crea un segundo mecanismo de look-ahead.

## 15. LIVE / HISTORICAL / REPLAY — UNA SOLA REGLA

Los tres modos obedecen:

available_at <= decision_timestamp

LIVE obtiene disponibilidad del evento real de recepción/handoff; HISTORICAL del momento real en que el sistema obtiene la evidencia; REPLAY conserva la disponibilidad original.

Ningún modo puede sustituir availability por observed_at.

## 16. BINANCE SPOT 1.11

El adapter actual usa conceptualmente:

observed_at = kline open time
received_at = reloj local después de completar la recepción HTTP
available_at = None

La documentación oficial de Binance define las klines por open time y expone open time, close time y OHLCV. El endpoint público GET /api/v3/klines está disponible sin autenticación mediante data-api.binance.vision, y los timestamps de respuesta son milisegundos por defecto.

La respuesta pública no proporciona un timestamp explícito cuyo significado sea “momento en que esta información estuvo disponible para nuestro sistema”. Por eso available_at=None es una decisión conservadora y correcta para FASE 1.11.

Esto no significa que received_at nunca pueda respaldar availability. Significa que el adapter actual no define explícitamente una frontera de consumidor que permita hacer esa conversión automáticamente.

NO MODIFICAR BINANCE 1.11 EN ESTA FASE.

## 17. TEST DESIGN FUTURO

Cuando se implemente o endurezca esta política deberán existir pruebas para:

- availability conocida y demostrada;
- availability UNKNOWN;
- decisión anterior, igual y posterior a availability;
- REST histórico consultado después del event time;
- REST actual bajo demanda;
- live event recibido con retraso;
- replay preservando availability original;
- replay con availability original UNKNOWN;
- observed_at == received_at sin evidencia de disponibilidad;
- received_at == available_at con evidencia de frontera de consumidor;
- available_at anterior a received_at rechazado como incoherencia semántica;
- promoción bloqueada por availability UNKNOWN;
- promoción bloqueada por quality != VALID;
- corrección histórica con knowledge/revision time posterior al event time;
- ausencia de regresión del EvidenceTimestamp existente.

## 18. DECISIÓN DE CAMBIO DE CONTRATO

CONTRACT COMPATIBLE — SIN CAMBIOS

La política puede expresarse con los campos existentes observed_at, received_at, available_at y decision_timestamp.

No se agregan provider_available_at, system_available_at, revision_id, supersedes ni version.

No se modifica:

- bot_obrero/market_data.py;
- bot_obrero/temporal.py;
- bot_obrero/analysis_contracts.py.

## 19. CONCLUSIÓN NORMATIVA

¿Qué significa available_at? El primer instante demostrable en que la evidencia representada por MarketData estaba disponible para el consumidor de evidencia dentro del sistema.

¿Cuándo puede asignarse? Cuando existe una señal temporal observable que demuestra esa disponibilidad.

¿Qué evidencia lo justifica? Una frontera real de publicación/recepción/handoff con significado inequívoco para el consumidor relevante. Para decisiones locales, la disponibilidad sistémica tiene prioridad sobre un timestamp externo de publicación.

¿Cuándo debe permanecer UNKNOWN? Cuando localizar el primer instante de disponibilidad exigiría inferencia, estimación o suposición.

¿Qué decisiones puede soportar UNKNOWN? Ninguna decisión que requiera evidencia temporal válida.

¿Cómo se evita look-ahead? Aplicando solamente available_at <= decision_timestamp y rechazando disponibilidad desconocida.

## 20. FUENTES OFICIALES UTILIZADAS

- https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market
- https://developers.binance.com/en/docs/products/spot/rest-api
- https://developers.binance.com/en/docs/products/spot/faqs/market_data_only

Se verificó: GET /api/v3/klines, identificación de kline por open time, close time, estructura de 12 elementos, timestamps en milisegundos y endpoint público data-api.binance.vision.

## 21. CIERRE

CONTRACT COMPATIBLE — SIN CAMBIOS

FASE 1.12 — COMPLETA

NO iniciar FASE 1.13.
NO agregar proveedores.
NO agregar WebSocket.
NO modificar Binance 1.11.