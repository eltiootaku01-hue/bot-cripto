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

Antes de la llamada al adaptador, se guarda SUBMISSION_STARTED en la base autoritativa. Si falla esa escritura, no se llama al adaptador. Después de una excepción del adaptador, el bridge intenta registrar UNKNOWN y cambia la reserva ACTIVE a UNKNOWN dentro de una transacción local; el recurso protegido permanece reservado. Si el proceso cae y no se puede escribir UNKNOWN, SUBMISSION_STARTED es evidencia durable conservadora y recover_ambiguous la convierte a UNKNOWN tras reinicio.

No se reenvía automáticamente una orden cuyo resultado sea UNKNOWN. El bridge no inventa rechazo del exchange a partir de una excepción.

## Fuente canónica y ledger separado

El estado autoritativo local está en SQLiteReservationStore, incluida la tabla reservation_execution_bindings. SQLiteIdempotencyLedger permanece como otro store. No existe una transacción atómica entre ambos. synchronize_projection/recover_projection permite reconstituir idempotentemente el ledger desde la vinculación autoritativa siempre que client_order_id e intent_hash coincidan; un hash distinto sigue siendo conflicto.

La recuperación implementada es local. No obtiene ni valida order status, exchange_order_id o fills, y no es un runtime de reconciliación autónoma con el exchange.

## Readiness y autenticidad de evidencia

ExecutionOrchestrator no construye ReadinessEvidence con valores favorables por defecto. Después de evaluar EvidenceBundle, transmite los booleanos efectivamente validados a RiskGuard. Esto demuestra consistencia estructural de identidad, correlación, valor y timestamps bajo el contrato del repositorio, pero no demuestra autenticidad criptográfica del origen de EvidenceRecord.

No se añade TTL para RiskAuthorization. El decision_timestamp se conserva como provenance; la política formal de vigencia sigue pendiente de una decisión independiente de CEREBRO.

## Pruebas y evidencia observada

El alcance nuevo está en tests/test_hueso_05e_reservation_execution_bridge.py. La regresión de tests/test_phase14_execution_boundary.py demuestra que OrderIntent sin una vinculación persistida no llega al adaptador. tests/test_hueso_02e1_reservation_read_set.py fue actualizado únicamente para reconocer la nueva tabla de snapshot obligatoria en el esquema del store.

Evidencia de CI para el HEAD verificado 1b8688eca36097c09e8c973d993eb96c9d6f1cba:

- Workflow tests (push): run 38051249231; job pytest; conclusión success; 1313 passed in 8.47s.
- Workflow tests (pull_request): run 38051252265; job pytest; conclusión success.
- Workflow phase1.31-sma-real-e2e (pull_request): run 38051252259; conclusión success.
- Workflow phase1.34-indicators-real-e2e (pull_request): run 38051252190; conclusión success.

En una iteración anterior, el run 38051052520 terminó con 4 fallos y 1306 aprobados: una expectativa de tablas del test de read-set y la transición desde SUBMISSION_STARTED. Ambos problemas se corrigieron en commits posteriores; las cuatro ejecuciones de arriba pertenecen al HEAD final de esta iteración y terminaron satisfactoriamente.

La suite fue ejecutada en el runner GitHub Actions con Python 3.12, mediante el workflow normal tests. Esta evidencia no equivale a reconciliación de exchange ni a aprobación/merge de la PR.

## Pendientes de HUESO 05-F / CEREBRO

- Obtención y validación de evidencia externa del exchange.
- Correlación de client_order_id con exchange_order_id y fills.
- Reconciliación runtime y transición validada desde UNKNOWN.
- Política formal de vigencia de RiskAuthorization.
- Autenticidad del origen de evidencia, separada de sus hashes y binding semántico.
