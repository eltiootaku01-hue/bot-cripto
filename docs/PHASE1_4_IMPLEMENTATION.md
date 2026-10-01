# BOT OBRERO — FASE 1.4 — IMPLEMENTACIÓN

## Alcance

FASE 1.4 cierra el límite entre intención interna y efecto externo sin conectar ningún exchange real. `ExchangeAdapter` continúa siendo una abstracción simulable.

## Execution Boundary

`ExecutionOrchestrator` ejecuta, en orden:

1. validación estructural del `OrderIntent`;
2. validación de Evidence Provenance y frescura;
3. `RiskGuard` → `ReadinessGate` → `MurphyGuard`;
4. registro persistente en `SQLiteIdempotencyLedger`;
5. transición lifecycle a `PENDING_SUBMIT`;
6. `ExecutionBoundary`;
7. `ExchangeAdapter.submit()`.

El adapter expone `submit()` y `cancel()` solamente con un permiso interno emitido por el boundary. Los métodos públicos no aceptan una llamada sin ese permiso y las subclases no pueden sobreescribir esos entry points. El trabajo concreto del adapter se implementa en `_submit()` / `_cancel()`.

## Evidence Provenance

`EvidenceRecord` registra tipo, valor, fuente, timestamps, `intent_id`, `correlation_id`, secuencia opcional y expiración opcional. `EvidenceBundle` exige una evidencia única y coherente para cada requisito de readiness.

Una evidencia es válida solamente si:

- pertenece al mismo `intent_id`;
- pertenece al mismo `correlation_id` del bundle;
- tiene valor booleano `True` para la autorización;
- fue observada antes de la decisión;
- la decisión ya ocurrió cuando se evalúa;
- no expiró.

La validación reutiliza `EvidenceTimestamp` para conservar la defensa temporal existente.

## Persistent Idempotency

`SQLiteIdempotencyLedger` persiste el hash canónico de la intención antes del cruce del boundary. Un mismo `client_order_id` con la misma intención devuelve `DUPLICATE`; el mismo ID con una intención distinta produce `IdempotencyConflict`.

El registro inicial queda como `PENDING_SUBMIT`. Un timeout o pérdida de respuesta se registra como `ORDER_RESULT_UNKNOWN` y congela Murphy. No existe retry automático desde `UNKNOWN`.

## Protection Provenance

`PositionProtection.confirm()` exige una `EvidenceRecord` de tipo `protection_safe`, vinculada al intento, con procedencia y frescura válidas. Una confirmación ausente, incorrecta o expirada produce `PROTECTION_UNKNOWN` y no `PROTECTION_CONFIRMED`.

## Fill Identity

La identidad externa del fill se representa mediante `fill_id` y se persiste en la tabla `applied_fills` del mismo SQLite ledger. `ExecutionOrchestrator.apply_external_fill()` registra la identidad antes de mutar el lifecycle y revierte el registro si la aplicación del fill falla. Un `fill_id` ya aplicado no puede volver a mutar el estado, incluso después de restart.

Esto resuelve la deduplicación local/persistente sin introducir un nuevo sistema de eventos o infraestructura. Sigue fuera de alcance la definición de cómo un exchange real proporcionaría/garantizaría ese `fill_id`.

## UNKNOWN / Recovery

Timeout, pérdida de respuesta y errores de submission no se convierten en éxito ni en fallo definitivo: quedan `ORDER_RESULT_UNKNOWN`, congelan Murphy y requieren reconciliación antes de una decisión de retry.

Al reiniciar, el ledger conserva `PENDING_SUBMIT`, `SUBMITTED` o `ORDER_RESULT_UNKNOWN`. El sistema no reenvía automáticamente una intención ya registrada.

## Reconciliación

La reconciliación existente continúa siendo la fuente para distinguir `MATCH`, `MISMATCH`, `INCOMPLETE` y `UNKNOWN`. FASE 1.4 no implementa un adapter de venue ni un proceso real de reconciliación externa.

## Invariantes cubiertas por tests

- no submit sin readiness válida;
- no submit sin evidence válida;
- no nueva exposición ante UNKNOWN;
- no segundo efecto para intención duplicada;
- no aplicación duplicada de un fill persistido;
- no regresión de estados terminales del lifecycle;
- no `PROTECTION_CONFIRMED` sin evidencia válida;
- no READY con evidencia ausente, contradictoria o expirada;
- no bypass del entry point público del execution boundary.

## Deliberadamente fuera de alcance

- exchange real, API keys y dinero real;
- testnet;
- estrategia de trading;
- optimización de rentabilidad;
- Risk Engine completo de fases posteriores;
- backtesting/walk-forward/paper trading;
- ML o LLM en el execution path;
- Redis, Kafka, microservicios o infraestructura distribuida.
