# FASE 1.46 — AUDITORÍA DEL RISK FRONTIER

## Estado observado

Repository:
`eltiootaku01-hue/bot-cripto`

Main:
`28fc3a919395883fe0afbb188ccf4d52d97ebde3`

FASE 1.45:
MERGED / CLOSED

---

# CONCLUSIÓN

La frontera:

`Signal → RiskDecision`

NO está cerrada para una evaluación financiera real.

FASE 1.45 proporcionó los contratos base correctos, pero dejó gaps estructurales que deben resolverse antes del Risk Engine.

---

# GAPS BLOQUEANTES

## 1. TradeProposal

`Signal` no debe absorber quantity, price, notional, account state ni risk limits.

`OrderIntent` aparece después de Risk.

Por tanto falta un contrato intermedio:

`Signal → TradeProposal → Risk`

TradeProposal debe ser no-ejecutable y transportar la operación económica que Risk debe evaluar.

---

## 2. Correlation Context

Actualmente:

- Signal: sin correlation_id
- RiskDecision: correlation_id obligatorio
- OrderIntent: correlation_id obligatorio
- EvidenceRecord: correlation_id obligatorio
- EvidenceBundle: correlation_id obligatorio

Falta definir el originador contractual y la propagación única del correlation context desde Strategy hasta Execution.

---

## 3. In-flight / Reserved State

Account State observado no refleja necesariamente las reservas locales de órdenes todavía no llenadas.

Sin un estado de reservas puede existir double-spend lógico.

Debe existir una representación separada de:

- reserved capital;
- reserved exposure;
- in-flight order;
- lifecycle;
- release;
- reconciliation interaction.

No mutar silenciosamente CanonicalAccountState para representar reservas locales.

---

## 4. Strategy Read Model

Strategy necesita una vista de solo lectura para producir propuestas razonables.

La vista puede incluir:

- balance utilizable;
- posiciones relevantes;
- capital reservado;
- restricciones activas.

No debe permitir mutación financiera.

Debe mantenerse separado de Canonical Source of Truth.

---

## 5. Temporal Availability of Financial Evidence

Los contratos financieros actuales tienen `as_of`, pero no una disponibilidad temporal equivalente a `available_at`.

Por tanto:

`as_of <= decision_timestamp`

no demuestra por sí solo que la evidencia ya estaba disponible para Risk.

Debe diseñarse una solución provider-neutral para demostrar disponibilidad de evidencia financiera.

---

## 6. Risk Approval Evidence

`RiskDecision` actualmente permite estructuralmente:

`APPROVED + evidence=()`

El contrato no obliga evidencia financiera mínima.

Debe decidirse dónde vive esa política:

- en el contrato;
- en el Risk Engine;
- o mediante un contrato de evaluación más rico.

No modificar automáticamente RiskDecision solo por este hallazgo.

---

## 7. RiskLimit Applicability

`RiskLimit` y `RiskLimitSet` existen, pero falta:

- selección;
- scope applicability;
- metric taxonomy;
- conflict resolution;
- temporal applicability;
- semantics of empty/no-applicable limits.

---

## 8. Completeness / UNKNOWN Semantics

Debe cerrarse cómo se interpreta:

- COMPLETE;
- PARTIAL;
- UNKNOWN.

Especialmente para:

- AccountState;
- Position;
- Exposure;
- RiskLimitSet;
- reconciliation.

Regla base:

`UNKNOWN != APPROVED`

y la incertidumbre que impida demostrar seguridad financiera no puede producir nueva exposición.

---

## 9. Exposure / Valuation Freshness

`CanonicalExposure.notional` no se calcula automáticamente, lo cual se conserva.

Pero falta definir:

- qué ocurre con notional=None;
- freshness máxima de valuation;
- relación entre valuation_as_of y decision_timestamp.

---

## 10. Final Admission

RiskDecision puede quedar obsoleta después de ser emitida.

Pero Final Admission no debe repetir todo Risk.

Debe ser una barrera de efecto que compruebe únicamente:

- autorización vigente;
- trading state;
- kill switch;
- integridad de OrderIntent;
- correlation;
- protecciones duras;
- invariantes inmediatamente previas a ExecutionBoundary.

---

## 11. Fill Identity

Falta cerrar identidad canónica para:

- client_order_id;
- exchange_order_id;
- fill_id/trade_id.

Debe garantizar deduplicación entre REST y WebSocket.

---

## 12. External Activity

Debe distinguirse entre:

- BOT_GENERATED;
- EXTERNAL;
- UNKNOWN.

Una actividad externa relevante para exposición debe poder llevar a:

`UNKNOWN → FAIL CLOSED → RECONCILIATION REQUIRED`

cuando corresponda.

---

# GAPS IMPORTANTES

- Typed OrderIntent sin `Mapping[str, Any]` como contrato financiero genérico.
- Trading State / Protections.
- Market Eligibility.
- Cancel / Modify / Reduce paths.

---

# GAPS FUTUROS

- Simulated Exchange.
- Deterministic Replay.
- Causality / Look-ahead audit.
- Shadow / Dry Run.

---

# NOTAS SOBRE HALLAZGOS QUE NO DEBEN FORZARSE TODAVÍA

Los siguientes hallazgos existen pero deben resolverse junto con el contrato/productor correspondiente, no mediante parches arbitrarios:

### Balance total / available / locked

No asumir automáticamente que `available + locked == total` hasta cerrar la semántica de Spot V1 y distinguir exchange-locked de local-reserved.

### Position quantity sign

No imponer signo hasta cerrar formalmente el scope Spot V1.

### account_id dentro de RiskDecision

No es necesariamente necesario si la decisión queda completamente vinculada a evidencia financiera con account scope inequívoco.

### Evidence kind taxonomy

Debe diseñarse junto con el modelo de evidencia final, no como una lista improvisada de strings.

---

# FRONTERA REVISADA

La arquitectura futura debe tender a:

```
Signal
  ↓
TradeProposal
  ↓
Financial / Operational Context
  ├─ Canonical Account State
  ├─ Position
  ├─ Exposure
  ├─ In-flight / Reserved State
  ├─ Risk Limits
  ├─ Market Eligibility
  └─ Trading Protections
          ↓
      Risk Engine
          ↓
      RiskDecision
          ↓
      Typed OrderIntent
          ↓
      Final Admission
          ↓
      Execution
          ↓
      Orders / ExecutionReports
          ↓
      Fills
          ↓
      Reconciliation
          ↓
      Canonical Account State
```

Strategy debe recibir un:

`Strategy Read Model`

de solo lectura.

---

# ORDEN DE REPARACIÓN

1. Risk Evaluation Boundary design.
2. TradeProposal contract.
3. Correlation context.
4. Strategy Read Model.
5. In-flight / Reserved State.
6. Market Eligibility.
7. Trading State / Protections.
8. Risk Engine.
9. UNKNOWN / Retry semantics.
10. Typed OrderIntent.
11. Final Admission.
12. Fill Identity.
13. Cancel / Modify / Reduce.
14. External Activity + Reconciliation.
15. Simulation.
16. Deterministic Replay.
17. Causality Audit.
18. Shadow / Dry Run.

---

# REGLA

No avanzar al Risk Engine final mientras los gaps BLOQUEANTES no estén cerrados.

No avanzar a ejecución financiera real.

GitHub y el código real siguen siendo SOURCE OF TRUTH.
