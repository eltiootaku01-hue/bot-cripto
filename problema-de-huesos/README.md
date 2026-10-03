# PROBLEMA DE HUESOS — MAPA DE REPARACIÓN ARQUITECTÓNICA

## Propósito

Esta carpeta es el registro persistente de los problemas estructurales descubiertos en el diseño del bot.

**Regla de continuidad:** no avanzar a una nueva frontera funcional importante mientras los problemas marcados como BLOQUEANTES no estén auditados, diseñados y resueltos.

GitHub y el código real siguen siendo SOURCE OF TRUTH.

---

# 1. ESTADO ORIGEN

HEAD de `main` observado al crear este registro:

`28fc3a919395883fe0afbb188ccf4d52d97ebde3`

FASE 1.45:

**MERGED / CLOSED**

PR:

`#25`

Este documento NO modifica contratos existentes y NO autoriza implementación.

---

# 2. DIAGNÓSTICO PRINCIPAL

La cadena actual:

`Signal → RiskDecision → OrderIntent → Execution`

es insuficiente para modelar de forma segura una operación completa.

El diseño objetivo deberá separar:

`Signal → TradeProposal → Risk → RiskDecision → OrderIntent → Final Admission → Execution`

y además mantener estado financiero, protección, reconciliación y accounting en paralelo.

---

# 3. HUESO 01 — TRADE PROPOSAL AUSENTE

`Signal` expresa una intención estratégica, pero no debe conocer quantity financiera, precio, notional, reglas de exchange, account state o límites de riesgo.

Risk, en cambio, necesita conocer la operación concreta que se pretende realizar.

### Decisión provisional

Introducir conceptualmente `TradeProposal` entre:

`Signal → Risk`

`TradeProposal` será NO ejecutable.

Campos conceptuales mínimos:

- signal_id;
- symbol;
- side/direction operativo;
- requested_quantity;
- requested_price o política de precio;
- order policy/type;
- strategy identity;
- decision timestamp;
- correlation_id.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 4. HUESO 02 — IN-FLIGHT / RESERVED CAPITAL

Una orden aprobada puede consumir capacidad financiera antes de que el fill llegue y actualice el estado observado del exchange.

Ejemplo:

`Proposal A → APPROVED → enviada → fill aún no recibido`

Antes de reconciliar:

`Proposal B → vuelve a observar el mismo balance`

Puede aprobar dos operaciones usando el mismo capital.

### Requisito

Definir una representación explícita de:

- capital reservado;
- órdenes in-flight;
- exposición potencial pendiente;
- reservation identity;
- relación con OrderIntent/client_order_id;
- release;
- confirmación;
- cancelación.

### Regla

No mutar silenciosamente `CanonicalAccountState` para representar reservas locales.

Separar:

**estado observado del exchange**

de:

**reservas operativas locales**.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 5. HUESO 03 — STRATEGY SIZING / READ MODEL

Si Strategy no conoce ninguna vista segura de capital o poder de compra, puede generar propuestas repetidamente imposibles.

La solución no es meter account state dentro de `Signal`.

Debe existir una vista de solo lectura para Strategy.

Debe distinguirse:

**read model**

de:

**canonical source of truth**.

Strategy no puede mutar balances, posiciones, exposure, reservas ni risk limits.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 6. HUESO 04 — FINAL ADMISSION

Una `RiskDecision` puede quedar obsoleta entre `decision_timestamp` y submission.

Pero Final Admission tampoco debe rehacer arbitrariamente todo Risk.

### Principio

Risk hace la evaluación financiera completa.

Final Admission valida únicamente invariantes de efecto inmediatamente antes del límite externo:

- trading state;
- kill switch;
- readiness crítica;
- correlation/integridad;
- vigencia de autorización;
- integridad de OrderIntent;
- protecciones duras;
- condiciones externas indispensables para cruzar ExecutionBoundary.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 7. HUESO 05 — CANCEL / MODIFY / REDUCE

El flujo de nueva entrada no cubre:

- cancelación;
- reducción;
- modificación;
- cierre de exposición.

No asumir que una cancelación debe pasar por exactamente la misma política que una nueva entrada.

Futuras clases de operación a distinguir:

- NEW_ENTRY;
- RISK_REDUCTION;
- CANCELLATION;
- MODIFY.

**Estado: ARCHITECTURAL GAP**

---

# 8. HUESO 06 — UNKNOWN Y RETRY

`RiskDecisionOutcome.UNKNOWN` debe ser fail-closed para nueva exposición.

Pero hay que distinguir:

**UNKNOWN de una decisión**

de:

**UNKNOWN del resultado de una operación externa**.

El segundo caso puede requerir idempotencia y reconciliación antes de reintentar.

Nunca permitir retry infinito ni duplicación.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 9. HUESO 07 — FILL IDENTITY

Debe distinguirse inequívocamente:

- client_order_id;
- exchange_order_id;
- fill_id / trade_id.

Un mismo fill puede llegar por WebSocket y luego por REST reconciliation.

Debe aplicarse exactamente una vez.

Modelo conceptual:

`Order → ExecutionReport → Fill → Position/Account State`

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 10. HUESO 08 — RECONCILIATION VS CANONICAL STATE

Reconciliation responde:

> ¿Las fuentes coinciden?

Canonical Account State responde:

> ¿Qué estado financiero reconoce el sistema?

Reconciliation es evidencia del estado.

No debe convertirse automáticamente en el estado canónico.

**Estado: ARCHITECTURE RULE — CERRAR**

---

# 11. HUESO 09 — EXTERNAL ACTIVITY

El exchange puede registrar actividad no generada por el bot:

- orden manual;
- fill externo;
- liquidación;
- cambio de balance;
- cambio de posición.

Debe distinguirse:

`BOT_GENERATED`

`EXTERNAL`

`UNKNOWN`

Una actividad externa relevante debe poder producir:

`UNKNOWN → FAIL CLOSED → RECONCILIATION REQUIRED`

cuando corresponda.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 12. HUESO 10 — TRADING STATE / PROTECTIONS

Separar:

**Financial Risk**

de:

**Trading Protection / Operating State**

Estados conceptuales:

- ACTIVE;
- REDUCING_ONLY;
- HALTED;
- UNKNOWN.

Protecciones potenciales:

- cooldown;
- repeated-failure lock;
- reconciliation lock;
- drawdown lock;
- symbol lock;
- global halt.

No implementar fórmulas antes de cerrar la semántica.

**Estado: ARCHITECTURAL GAP**

---

# 13. HUESO 11 — MARKET ELIGIBILITY

Separar:

**Risk APPROVED**

de:

**instrument/market executable**.

Restricciones duras potenciales:

- market active;
- trading status;
- quantity precision;
- price precision;
- min quantity;
- max quantity;
- min notional;
- max notional;
- otras reglas duras del venue.

Esto no debe mezclarse con preferencias estratégicas.

**Estado: ARCHITECTURAL GAP**

---

# 14. HUESO 12 — TYPED ORDER INTENT

Actualmente `OrderIntent` contiene `payload: Mapping[str, Any]`.

Eso es demasiado abierto frente a la disciplina Decimal del sistema.

Además, la implementación actual de execution convierte quantity a float durante validación.

Debe cerrarse un contrato tipado sin volver a introducir float.

Campos conceptuales:

- symbol;
- side;
- quantity Decimal;
- price Decimal opcional;
- order_type;
- time_in_force;
- client_order_id;
- correlation_id.

**Estado: CONTRACT GAP — IMPORTANTE**

---

# 15. HUESO 13 — SIMULATION / REPLAY

Antes de cualquier live serio debe existir una ruta:

`MarketData → Strategy → TradeProposal → Risk → OrderIntent → Simulated Exchange → Fill → Position → Account`

con:

- fees;
- slippage;
- partial fills;
- latency assumptions;
- cancellation;
- rejection;
- balance constraints;
- precision;
- resultados deterministas.

**Estado: FUTURE GATE — NO IMPLEMENTAR AÚN**

---

# 16. HUESO 14 — CAUSALITY / LOOK-AHEAD

Los contratos actuales deben permitir después:

`decision@T → hide data>T → recalculate → compare`

Objetivo:

**los datos futuros no pueden cambiar una decisión histórica.**

**Estado: FUTURE GATE**

---

# 17. HUESO 15 — STRATEGY FEEDBACK

El estado financiero debe proporcionar a Strategy una vista limitada y de solo lectura.

Conceptualmente:

`Canonical/Operational State → Strategy Read Model`

No:

`Strategy → mutación Account State`

La vista futura puede contener:

- balance utilizable según política;
- posiciones relevantes;
- capital reservado;
- restricciones activas.

Las reglas exactas de sizing se diseñarán posteriormente.

**Estado: CONTRACT GAP — BLOQUEANTE**

---

# 18. MAPA OBJETIVO

```
MarketData
   ↓
Analysis
   ↓
Strategy
   ↓
Signal
   ↓
TradeProposal
   ↓
┌─────────────────────────────────────┐
│ Account State                       │
│ Position                            │
│ Exposure                            │
│ Reserved / In-Flight State          │
│ Risk Limits                         │
│ Market Eligibility                  │
│ Trading Protections                 │
└──────────────────┬──────────────────┘
                   ↓
              Risk Engine
                   ↓
             RiskDecision
                   ↓
              OrderIntent
                   ↓
            Final Admission
                   ↓
           Execution Boundary
                   ↓
               Exchange
                   ↓
          Orders / ExecutionReports
                   ↓
                 Fills
                   ↓
             Reconciliation
                   ↓
        Canonical Account State
                   │
                   ├────────→ Strategy Read Model
                   │
                   └────────→ Risk
```

---

# 19. PRINCIPIOS DE RESOLUCIÓN

1. No meter balance dentro de Signal.
2. No meter riesgo financiero dentro de Signal.
3. No mutar CanonicalAccountState para representar reservas locales.
4. No convertir RiskGuard en RiskEngine.
5. No hacer que Final Admission repita todo Risk.
6. No tratar UNKNOWN como una orden reintentable automáticamente.
7. No tratar Reconciliation como estado canónico.
8. No duplicar fills recibidos por REST/WS.
9. No permitir que Strategy modifique estado financiero.
10. No volver a usar float para cantidades financieras.
11. No permitir nueva exposición si el estado requerido es UNKNOWN.
12. Mantener provider-neutral los contratos donde corresponda.

---

# 20. DEFINICIÓN DE "RESUELTO"

Un hueso pasa a RESUELTO solamente cuando:

- existe contrato definido;
- existe productor y consumidor definido;
- temporalidad definida;
- UNKNOWN definido;
- provenance/evidence definido;
- tests contractuales definidos;
- integración auditada;
- no existe contradicción con Execution;
- no introduce scope creep innecesario.

---

# 21. BLOQUEO

Mientras existan huesos BLOQUEANTES sin resolver:

**NO avanzar a la implementación final del Risk Engine ni a ejecución financiera real.**

Las reparaciones se harán por fases pequeñas y auditables.

---

# 22. CLASIFICACIÓN

## BLOQUEANTES

- TradeProposal
- In-flight / Reserved State
- Strategy Read Model / sizing boundary
- Final Admission
- UNKNOWN / retry semantics
- Fill Identity
- External Activity

## IMPORTANTES

- Typed OrderIntent
- Trading State / Protections
- Market Eligibility
- Cancel/Modify path

## FUTUROS

- Simulation
- Deterministic Replay
- Causality / Look-ahead audit

---

# 23. BENCHMARK EXTERNO

Estas ideas fueron contrastadas conceptualmente con:

- NautilusTrader;
- Freqtrade;
- Hummingbot;
- Jesse;
- OctoBot.

No se copia código ni se adopta complejidad solo por existir en otros proyectos.

---

# 24. REGLA FINAL

Esta carpeta es la **lista persistente de reparación estructural**.

Antes de continuar la construcción funcional importante, el CEREBRO debe volver aquí, leer el estado y cerrar los huesos uno por uno.

**Primero reparar el esqueleto. Después continuar con músculos y movimiento.**
