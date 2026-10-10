# HUESO 05-E — Reservation → Execution Admission Bridge v1.0

## Estado de la rama

Implementación en una rama de trabajo independiente y pendiente de revisión de CEREBRO. Este documento no declara la fase mergeada ni cerrada.

## Responsabilidad y límites

ReservationExecutionBridge prepara una intención únicamente desde el snapshot económico canónico y el binding de autorización persistidos durante HUESO 05-D. El caller identifica la reserva; no vuelve a suministrar TradeProposal, RiskAuthorization ni un payload económico. ExecutionOrchestrator requiere PreparedExecutionIntent y vuelve a verificar la vinculación persistida antes de llegar a ExecutionBoundary.

El bridge no evalúa riesgo, no crea Strategy, no define una caducidad arbitraria de autorización y no consulta el exchange. El estado local SUBMITTED indica que la llamada al adaptador terminó sin excepción y que el resultado local se persistió; no equivale por sí solo a una confirmación externa verificable, fill ni reconciliación.

## Snapshot canónico e identidad

SQLiteReservationStore.admit() mantiene la transacción BEGIN IMMEDIATE. Dentro de la misma transacción escribe Reservation, transición inicial, binding de autorización y reservation_trade_terms_snapshots. Si el snapshot no se puede insertar o verificar mediante lectura posterior, se revierte toda la admisión.

El snapshot contiene identidades de proposal/signal, símbolo e identidad del instrumento canónico, lado, tipo, cantidad solicitada, precio/límite económico, política de precio, identidad/versión de estrategia, decision timestamp normalizado a UTC, correlación, cuenta y estado de cuenta canónico, términos de reserva, risk_decision_id y la identidad/fingerprint/contexto/política/evidencia de autorización.

Los campos económicos decimales se guardan como cadenas normalizadas de Decimal finito; no se convierten a float ni se redondean. Los timestamps canónicos son timezone-aware y normalizados a UTC. terms_hash y intent_hash son SHA-256 deterministas para detectar inconsistencias entre los objetos persistidos y su representación. No son firmas criptográficas y no autentican quién pudo modificar directamente la base de datos.

## Vinculación y preparación

La primera preparación transaccional verifica Reservation, snapshot y authorization binding; comprueba que los términos y la identidad canónica del instrumento son coherentes; calcula client_order_id de forma estable respecto de reservation_id y fingerprint de autorización; y persiste el JSON canónico de intención más su hash. La Reservation recibe el mismo client_order_id en esa misma base de datos.

La relación durable es:

reservation_id → authorization fingerprint → client_order_id → terms_hash → intent_hash.

Una repetición compatible devuelve ALREADY_PREPARED con la misma identidad. Contradicciones producen CONFLICT, los preconditions inválidos producen REJECTED y los fallos de integridad/persistencia producen BLOCKED. UNKNOWN y cualquier SUBMISSION_STARTED que quede tras una caída no son reintentables automáticamente.

## Antes y después del efecto externo

La propia ExecutionBoundary actúa como segunda frontera obligatoria para el envío de una orden nueva. Su constructor público no acepta ni instala una autoridad de envío suministrada por el caller; crear una frontera alternativa con el bridge persistente no concede permiso. Solo la variante privada creada por ExecutionOrchestrator recibe la capacidad interna de despacho. Antes del efecto, la frontera recarga y verifica el binding autoritativo, la identidad, los hashes, los términos canónicos, la autorización y el estado persistido SUBMISSION_STARTED, y reconstruye la OrderIntent usando exclusivamente el payload persistido. El marcador SUBMISSION_STARTED por sí solo no autoriza el envío: la autorización efectiva depende además de la ruta del orquestador, que valida readiness y registra el client_order_id en el ledger idempotente antes de invocar la frontera.

La frontera consume, bajo un lock, la autorización para cada identidad de envío antes de llamar al adaptador. La misma instancia no vuelve a invocar el adaptador para esa identidad aunque el resultado quede ambiguo y el binding permanezca temporalmente en SUBMISSION_STARTED. Tras reinicio, una marca SUBMISSION_STARTED se recupera como UNKNOWN y no se reenvía automáticamente. El resultado local SUBMITTED tampoco equivale a aceptación final del exchange ni a fill o reconciliación. La semántica de cancelación permanece sin cambios.

Esta defensa cubre las rutas de API soportadas dentro del proceso Python; no constituye aislamiento frente a código arbitrario hostil que pueda inspeccionar objetos privados dentro del mismo proceso.

Antes de la llamada al adaptador, se guarda SUBMISSION_STARTED en la base autoritativa. Si falla esa escritura, no se llama al adaptador. Después de una excepción del adaptador, el bridge intenta registrar UNKNOWN y cambia la reserva ACTIVE a UNKNOWN dentro de una transacción local; el recurso protegido permanece reservado. Si el proceso cae y no se puede escribir UNKNOWN, SUBMISSION_STARTED es evidencia durable conservadora y recover_ambiguous la convierte a UNKNOWN tras reinicio.

No se reenvía automáticamente una orden cuyo resultado sea UNKNOWN. El bridge no inventa rechazo del exchange a partir de una excepción.

## Fuente canónica y ledger separado

El estado autoritativo local está en SQLiteReservationStore, incluida la tabla reservation_execution_bindings. SQLiteIdempotencyLedger permanece como otro store. No existe una transacción atómica entre ambos. synchronize_projection/recover_projection permite reconstituir idempotentemente el ledger desde la vinculación autoritativa siempre que client_order_id e intent_hash coincidan; un hash distinto sigue siendo conflicto.

La recuperación implementada es local. No obtiene ni valida order status, exchange_order_id o fills, y no es un runtime de reconciliación autónoma con el exchange.

## Readiness y autenticidad de evidencia

ExecutionOrchestrator no construye ReadinessEvidence con valores favorables por defecto. Después de evaluar EvidenceBundle, transmite los booleanos efectivamente validados a RiskGuard. Esto demuestra consistencia estructural de identidad, correlación, valor y timestamps bajo el contrato del repositorio, pero no demuestra autenticidad criptográfica del origen de EvidenceRecord.

No se añade TTL para RiskAuthorization. El decision_timestamp se conserva como provenance; la política formal de vigencia sigue pendiente de una decisión independiente de CEREBRO.

## Pruebas y evidencia observada

El alcance nuevo está en tests/test_hueso_05e_reservation_execution_bridge.py. La regresión de tests/test_phase14_execution_boundary.py demuestra que OrderIntent sin una vinculación persistida no llega al adaptador. tests/test_hueso_02e1_reservation_read_set.py fue actualizado únicamente para reconocer la tabla de snapshot obligatoria en el esquema del store. La suite cubre rollback de admisión, preparación tras reinicio, estados prohibidos, conflicto de términos, fingerprint incompatible, concurrencia, ruta genérica bloqueada, fallos pre-envío, timeout/UNKNOWN, reparación del ledger y fallo al persistir el resultado después de invocar al adaptador.

Evidencia de CI para el commit funcional probado 902060fdbed1c4131ecc1d306b78587c8a90deab:

- Instalación: python -m pip install -e ".[test]" — success.
- Workflow tests (push), run 38051391526; job pytest; conclusión success; comando pytest -q; resultado 1314 passed in 8.75s.
- Workflow tests (pull_request), run 38051394654; job pytest; conclusión success.
- Workflow phase1.31-sma-real-e2e (pull_request), run 38051394636; conclusión success.
- Workflow phase1.34-indicators-real-e2e (pull_request), run 38051394856; conclusión success.

En la iteración inicial, run 38051052520 terminó con 4 fallos y 1306 aprobados: una expectativa de tablas del test de read-set y tres resultados relacionados con la transición de envío. Se corrigieron con commits posteriores y se añadieron pruebas para fingerprint, conflicto tipado y recuperación después de un fallo al persistir el resultado post-adaptador. Los runs verdes arriba corresponden al commit funcional con esa cobertura.

La suite fue ejecutada en GitHub Actions con Python 3.12. Esta evidencia no equivale a reconciliación de exchange, autenticación criptográfica del origen de EvidenceRecord ni aprobación/merge de la PR.

## Pendientes de HUESO 05-F / CEREBRO

- Obtención y validación de evidencia externa del exchange.
- Correlación de client_order_id con exchange_order_id y fills.
- Reconciliación runtime y transición validada desde UNKNOWN.
- Política formal de vigencia de RiskAuthorization.
- Autenticidad del origen de evidencia, separada de sus hashes y binding semántico.
