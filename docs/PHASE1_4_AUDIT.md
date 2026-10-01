# BOT OBRERO — FASE 1.4 — AUDITORÍA POST-1.3.1

Fecha: 2026-10-01

## Estado auditado

- Branch: main
- HEAD auditado: 36b7f2396a318d6e06fa42b766a4a67679f93b79
- FASE 1.3.1 funcional: implementada y cubierta por CI
- Dinero real: ZERO
- Exchange real: ZERO
- Credenciales reales: ZERO

## Evidencia CI

El workflow `tests` ejecutado sobre el HEAD anterior 63b6decc... fue exitoso:
- run_id: 36802529710
- job: pytest
- instalación editable: PASS
- pytest: 66 passed in 0.18s

La verificación anterior está confirmada en los logs de GitHub Actions. El workflow usa Python 3.12 y ejecuta `python -m pip install -e ".[test] && pytest -q`.

## Auditoría del lifecycle

`bot_obrero/order_lifecycle.py` posee una máquina de estados explícita y rechaza las transiciones terminales críticas revisadas:
- FILLED -> apply_fill: bloqueado
- CANCELED -> apply_fill: bloqueado
- REJECTED -> apply_fill: bloqueado
- EXPIRED -> apply_fill: bloqueado
- FILLED/CANCELED/REJECTED/EXPIRED -> request_cancel: bloqueado
- FILLED/CANCELED/REJECTED/EXPIRED -> cancel_unknown: bloqueado

Se conserva la compatibilidad explícita NEW -> ACKNOWLEDGED para recuperación/eventos legacy.

Se añadió una defensa adicional: un fill con precio <= 0 es rechazado y no muta el estado.

### Limitación lifecycle pendiente

Todavía no existe una capa de eventos de venue con identidad de fill/trade ID persistente. Por tanto, el modelo evita sobrellenado por cantidad, pero todavía no puede demostrar por sí mismo que el mismo fill externo no haya sido aplicado dos veces con cantidades que no excedan el remaining quantity.

Estado: PARTIALLY VERIFIED.

## Hallazgo crítico de arquitectura: readiness

`ReadinessGate` y `MurphyGuard` exigen siete evidencias booleanas, pero las reciben como datos ya afirmados por el consumidor:
- reconciliation_match
- clock_valid
- stream_ready
- resources_healthy
- permissions_safe
- configuration_valid
- protection_safe

Esto evita el bypass trivial de llamar `ready()` sin argumentos, pero NO constituye una prueba de procedencia de la evidencia.

Además, `ExchangeAdapter` es todavía una abstracción y no existe un ExecutionOrchestrator integrado que fuerce arquitectónicamente la secuencia Strategy -> Risk -> Murphy -> Readiness -> Execution -> Adapter.

Estado: P1 / NOT INTEGRATED.

## Hallazgo crítico de protección

`PositionProtection.confirm()` cambia directamente a PROTECTION_CONFIRMED. La clase no recibe ni valida evidencia de una orden protectora confirmada por un exchange.

Los tests prueban correctamente que UNKNOWN no es seguro, pero todavía no prueban procedencia real de CONFIRMED.

Estado: P1 / NOT INTEGRATED.

## Hallazgo de persistencia

El `IdempotencyLedger` original vive únicamente en RAM. Un reinicio elimina su barrera contra duplicados.

Se añadió `SQLiteIdempotencyLedger` como componente durable:
- persiste client_order_id;
- persiste hash canónico de la intención;
- bloquea reutilización del mismo ID con intención distinta;
- conserva UNKNOWN después de reinicio;
- utiliza SQLite transaccional y WAL.

Estado: IMPLEMENTED + TESTED, pero NOT INTEGRATED en el flujo de submission porque todavía no existe un ExecutionOrchestrator real.

## Investigación incorporada

### Hummingbot
Su documentación de lifecycle mantiene seguimiento del order antes de enviar la petición y continúa siguiendo estados hasta completado/cancelado/expirado/fallido. Esto respalda separar tracking de submission y no tratar la respuesta inicial como el lifecycle completo.

### NautilusTrader
Su documentación actual separa Strategy, RiskEngine, ExecutionEngine, ExecutionClient y reconciliación. También documenta reconciliación de startup, persistencia de eventos, deduplicación de reportes, fills fuera de orden e incertidumbre durante cancelaciones. Esto refuerza el gap de integración del proyecto actual.

### CCXT
Su manual documenta rate limits, timeouts cuyo resultado puede ser desconocido y la necesidad de distinguir precision de límites de mercado. Esto respalda mantener retry condicionado y validaciones previas.

### AWS Builders' Library
La documentación sobre idempotent APIs señala que un timeout puede dejar desconocido si la operación ocurrió y que el retry seguro requiere un identificador de cliente estable y reconciliación. También recomienda guardar la intención asociada al identificador.

### SEC / Knight Capital
La SEC documenta el incidente de 2012 en el que un problema de software produjo numerosas órdenes erróneas y una pérdida de aproximadamente US$440 millones. Se incorpora como evidencia histórica de que fallos de software pueden producir consecuencias financieras severas incluso fuera de errores de estrategia.

## Riesgos prioritarios

| ID | Riesgo | Severidad | Estado |
|---|---|---|---|
| P1-READINESS-PROVENANCE | Booleanos de readiness pueden ser afirmados sin procedencia verificable | P1 | NOT INTEGRATED |
| P1-PROTECTION-PROVENANCE | PROTECTION_CONFIRMED no exige evidencia de venue | P1 | NOT INTEGRATED |
| P1-EXECUTION-ORCHESTRATOR | No existe todavía un flujo integrado que fuerce los guards antes del adapter | P1 | NOT IMPLEMENTED |
| P1-IDEMPOTENCY-PERSISTENCE | Ledger RAM no sobrevive crash | P1 | MITIGATED PARTIALLY |
| P1-FILL-DEDUP | No existe identidad persistente de fill/trade para deduplicación | P1 | NOT IMPLEMENTED |
| P1-RISK-LIMITS | No existe aún motor integrado de límites de exposición/PnL/drawdown | P1 | NOT IMPLEMENTED |
| P1-BACKTEST | No existe backtester completo con ejecución realista | P1 | NOT IMPLEMENTED |
| P1-EXCHANGE-INTEGRATION | No existe adapter real ni reconciliación contra venue | P1 | NOT IMPLEMENTED |

## Regla de cierre

El sistema NO está listo para trading real.

La fase actual demuestra un núcleo defensivo con tests, no una integración de trading real.

La siguiente fase debe priorizar evidencia verificable y un orquestador que haga imposible cruzar el boundary de ejecución sin pasar por los controles, antes de añadir estrategia o buscar rentabilidad.
