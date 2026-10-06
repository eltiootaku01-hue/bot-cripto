# BOT-CRIPTO — ARCHITECTURE TARGET DERIVADA DE LAS REFERENCIAS

Fecha: 2026-10-06

## Flujo financiero objetivo

MarketData
→ Analysis
→ Strategy Runtime
→ Signal
→ TradeProposal
→ RiskEvaluationContext
→ RiskDecision
→ RiskAuthorization
→ Financial Admission
→ Reservation
→ Execution
→ Reconciliation

## Flujo de investigación objetivo

MarketData
→ Analysis
→ Strategy Runtime
→ Replay
→ Risk/Sizing
→ Simulated Execution
→ Portfolio/Accounting
→ Metrics
→ Optimization / Validation

## Reglas de arquitectura

### R1 — Strategy no tiene autoridad financiera
Strategy produce una intención o señal estructurada.
No decide si existe capacidad financiera válida.

### R2 — Risk no cruza la frontera externa
Risk devuelve evaluación y/o autoridad cuantificada.
Execution es el dueño del transporte externo.

### R3 — Sizing es una frontera propia
Signal → cantidad/notional debe ser trazable y separado de:
- Strategy;
- Risk policy;
- exchange transport.

### R4 — Replay reutiliza contratos de live
Replay no crea un significado alternativo de:
- MarketData;
- timestamps;
- availability;
- Evidence.

### R5 — Backtest simula la economía relevante
Como mínimo:
- fills;
- fees;
- cash/balance;
- position;
- PnL;
- slippage o execution assumptions;
- timestamps.

### R6 — UNKNOWN nunca se vuelve éxito por conveniencia
Aplica a:
- transport outcomes;
- stale market data;
- missing fills;
- missing position reports;
- partial historical data;
- corrupted state.

### R7 — Reproducibilidad debe ser verificable
Cada corrida de investigación debe poder vincularse a:
- dataset identity;
- strategy parameters;
- risk parameters;
- execution assumptions;
- warmup;
- fee model;
- software/contract version.

### R8 — Recovery restaura por evidencia
Nunca inferir:
"no encontré la orden" = "la orden no existe".

### R9 — Event bus no es prerrequisito
Primero contratos.
Luego, solo si la complejidad lo exige, eventos acotados.

### R10 — Persistir lo necesario
SQLite selectiva y evidencia durable donde cambian:
- seguridad;
- autoridad;
- estado externo;
- idempotencia.

Evitar infraestructura distribuida hasta demostrar necesidad.

## Fronteras candidatas

### A — Strategy Runtime
Debe definir:
- lifecycle;
- inputs;
- warmup;
- decision timestamp;
- output Signal/TradeProposal;
- determinismo.

### B — Sizing / Portfolio Construction
Debe definir:
- capital base;
- exposición existente;
- constraints;
- target económico;
- amount/notional bounded;
- provenance.

### C — Risk Engine
Debe definir:
- RiskEvaluationContext;
- policy evaluation;
- quantitative limits;
- drawdown/exposure;
- RiskDecision;
- RiskAuthorization.

### D — Reservation → Execution
Debe definir:
- binding proposal/authorization/reservation;
- OrderIntent;
- idempotency;
- UNKNOWN external outcome;
- reconciliation trigger.

### E — Replay
Debe definir:
- deterministic clock;
- ordered observations;
- availability validation;
- replay cursor;
- same domain contracts as live.

### F — Backtest
Debe definir:
- simulated execution;
- portfolio/accounting;
- fees/slippage;
- metrics;
- result fingerprint.

### G — Optimization
Debe definir:
- explicit ParameterSpace;
- deterministic evaluation;
- train/test split;
- candidate ranking;
- anti-overfit guardrails;
- reproducibility metadata.

## Gate de cierre

Una frontera está lista para IMPLEMENT cuando CEREBRO pueda responder con evidencia:

1. ¿Cuál es el input autoritativo?
2. ¿Cuál es el output?
3. ¿Qué evidencia temporal exige?
4. ¿Qué estados pueden ser UNKNOWN?
5. ¿Quién tiene autoridad para cambiar dinero/posición?
6. ¿Cómo se garantiza idempotencia?
7. ¿Cómo se prueba?
8. ¿Cómo se reconcilia el desacuerdo con el mundo externo?

Si alguna respuesta permanece abierta, el estado correcto sigue siendo DESIGN, no IMPLEMENTED.

## Orden recomendado

1. Strategy Runtime.
2. Sizing / Portfolio Construction.
3. Risk Engine + RiskAuthorization.
4. Reservation → Execution.
5. Replay.
6. Simulated Execution / Accounting.
7. Backtest / Metrics / Fingerprint.
8. Optimization / Walk-forward.
9. Execution hardening / Recovery.
10. Orderbook/WebSocket solo si aparece una necesidad de estrategia.
