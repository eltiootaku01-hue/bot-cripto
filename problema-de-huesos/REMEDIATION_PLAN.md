# PROBLEMA DE HUESOS — PLAN DE REPARACIÓN

## Regla

Cada reparación será una fase independiente con:

- HEAD inicial exacto;
- objetivo único;
- archivos permitidos;
- tests;
- CI;
- commit;
- PR;
- stop conditions.

No reparar cinco contratos a la vez.

---

# ORDEN PROPUESTO

## A — TradeProposal

Resolver:

`Signal → TradeProposal → Risk`

Motivo: Risk necesita una operación concreta que evaluar.

No implementar aún Risk Engine.

## B — Strategy Read Model

Definir una vista read-only de:

- balance utilizable;
- reservas/in-flight;
- position;
- restrictions.

No permitir mutación.

## C — In-Flight / Reserved State

Definir:

- Reservation;
- identity;
- correlation con OrderIntent;
- reserved capital;
- reserved exposure;
- lifecycle;
- release;
- reconciliation interaction.

Separarlo del CanonicalAccountState observado.

## D — Market Eligibility

Cerrar restricciones duras:

- status;
- precision;
- min/max quantity;
- min/max notional;
- precio válido;
- símbolo operable.

No mezclar con estrategia.

## E — Trading State / Protections

Separar:

- financial risk;
- operational state;
- protections.

Definir ACTIVE, REDUCING_ONLY, HALTED, UNKNOWN.

Luego decidir qué protecciones reales necesitamos.

## F — Risk Engine

Solo después de A-E:

`TradeProposal + AccountState + Position + Exposure + Reservations + Limits + Eligibility + Protections`

→ `RiskDecision`

Determinista y provider-neutral.

## G — UNKNOWN / Retry Semantics

Cerrar formalmente:

- UNKNOWN de la decisión;
- UNKNOWN del resultado externo;
- idempotency;
- reconciliation before retry;
- no infinite retry.

## H — Typed OrderIntent

Convertir la aprobación en una orden tipada, manteniendo Decimal.

## I — Final Admission

Cerrar:

`RiskDecision + OrderIntent → Final Admission → ExecutionBoundary`

Final Admission no repite todo Risk.

## J — Fill Identity

Definir y probar:

- client_order_id;
- exchange_order_id;
- fill_id/trade_id.

Garantizar deduplicación REST/WS.

## K — Cancel / Modify / Reduce

Diseñar flujos:

- NEW_ENTRY;
- RISK_REDUCTION;
- CANCELLATION;
- MODIFY.

## L — External Activity + Reconciliation

Cerrar:

- manual order;
- external fill;
- unexpected balance change;
- unexpected position change.

Cuando corresponda:

`UNKNOWN → FAIL CLOSED → RECONCILIATION REQUIRED`

## M — Simulation

Después del contractual live path:

- simulated exchange;
- fees;
- slippage;
- partial fills;
- latency;
- cancellation;
- rejection;
- deterministic accounting.

## N — Deterministic Replay

Usar los mismos contratos de live, sin segunda arquitectura.

## O — Causality Audit

Implementar look-ahead detector:

`decision@T`

vs datos disponibles hasta T.

---

# CRITERIO DE SALIDA

El problema de huesos se considera cerrado cuando:

- no quedan BLOQUEANTES;
- no quedan contratos ambiguos en Signal/Risk/Execution;
- no existe double-spend lógico por in-flight state;
- UNKNOWN no puede producir nueva exposición;
- fills son idempotentes;
- external activity puede llevar a fail-closed;
- Strategy puede dimensionar usando una vista segura;
- Final Admission no duplica todo Risk;
- OrderIntent financiero es inequívoco;
- Reconciliation y Canonical State permanecen separados.

Hasta entonces:

**NO CONTINUAR A LA SIGUIENTE GRAN FRONTERA FUNCIONAL.**
