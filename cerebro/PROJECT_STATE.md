# CEREBRO / IA-CHAN — PROJECT STATE

## ÚLTIMA ACTUALIZACIÓN VERIFICADA

Fecha de referencia: 2026-10-03

Repository:

`eltiootaku01-hue/bot-cripto`

Branch principal:

`main`

CURRENT MAIN HEAD:

`VERIFY DIRECTLY FROM GITHUB`

LAST VERIFIED MAIN BASE:

`036ec717364ddeff3a0bb0703acf27eea968dd5a`

LAST CONTINUITY MERGE:

PR #29

MERGE COMMIT:

`050c0d260500d2ddde3810b997b75597518a4830`

---

# CONTINUIDAD DEL HEAD

El HEAD actual de `main` NO se almacena como un valor permanente en este archivo.

Antes de iniciar cualquier nueva fase:

1. consultar GitHub;
2. verificar el HEAD real de `main`;
3. comparar contra la continuidad conocida;
4. reconstruir evidencia si existe divergencia.

GitHub gana al documento.

Este archivo mantiene únicamente referencias históricas y de continuidad; no intenta autorreferenciar el commit que contiene su propia actualización.

---

# FASE 1.45

## CONTRATOS CANÓNICOS DE ESTADO FINANCIERO + RISK DECISION

Estado:

**MERGED / CLOSED**

PR:

`#25`

Contracts introduced:

- CanonicalAccountState
- BalanceSnapshot
- CanonicalPosition
- CanonicalExposure
- RiskLimit
- RiskLimitSet
- RiskDecision
- RiskDecisionOutcome
- RiskEvidenceRef
- Completeness

### Evidencia de FASE 1.45

Branch de implementación:

`phase1.45-risk-contracts`

HEAD antes del merge:

`337bc8f0ffb0e802a88dca80b641a0d2123d7db8`

CI verificado antes del merge:

Run ID `37128838716`

Job:

`pytest`

Resultado:

`782 passed in 4.80s`

Archivos funcionales introducidos:

- `bot_obrero/risk_contracts.py`
- `tests/test_phase1_45_risk_contracts.py`

No se introdujo en esta fase:

- Risk Engine;
- position sizing;
- equity;
- agregación global de exposure;
- Signal → RiskDecision;
- RiskDecision → OrderIntent;
- persistencia;
- cambios en Execution;
- cambios en RiskGuard.

---

# DOCUMENTACIÓN DE CONTINUIDAD PERSISTENTE

Después de FASE 1.45 se integraron dos bloques documentales:

### PR #26 — continuidad operativa

Estado:

**MERGED / CLOSED**

Merge commit:

`5de3a0a1fff31c2a977a240997f9424398a35699`

Documentos:

- `cerebro/README.md`
- `cerebro/PROJECT_STATE.md`
- `obrero/README.md`

Alcance:

**Solo documentación. No se modificó código de producción, tests ni contratos.**

### PR #27 — mapa de reparación arquitectónica

Estado:

**MERGED / CLOSED**

Merge commit:

`c325056673cb005a4f931fefd0f7a55b983cdf81`

Documentos:

- `problema-de-huesos/README.md`
- `problema-de-huesos/REMEDIATION_PLAN.md`
- `problema-de-huesos/PHASE_1_46_AUDIT.md`

Alcance:

**Solo documentación arquitectónica. No se modificó código de producción, tests ni contratos existentes.**

Estos documentos son memoria y diseño arquitectónico persistente; no deben declararse como código funcional implementado.

---

# FRONTERA ARQUITECTÓNICA ACTUAL

La cadena funcional existente continúa:

**MarketData → Analysis → Strategy → Signal**

FASE 1.45 aporta contratos financieros/risk, pero no implementa el Risk Engine ni conecta todavía Signal con una evaluación financiera operativa.

La auditoría persistente de la frontera Risk identifica como orden de reparación:

**HUESO 00 — Risk Evaluation Boundary Design**

Estado:

**DESIGNED / NOT IMPLEMENTED**

Esta frontera debe cerrar primero las reglas y límites de la evaluación financiera antes de avanzar a la implementación final del Risk Engine.

La secuencia conceptual resultante es:

**Signal → TradeProposal → Financial / Operational Context → Risk Engine → RiskDecision → Typed OrderIntent → Final Admission → Execution**

El siguiente trabajo operativo, después de esta sincronización documental, es:

**HUESO 01 — TradeProposal**

Estado:

**CONTRACT GAP — BLOCKING / NOT IMPLEMENTED**

TradeProposal debe permanecer separado de Signal y no debe ser ejecutable.

---

# ESTADO ACTUAL DE COMPONENTES FUTUROS

Los siguientes componentes no deben presentarse como implementados:

### Risk Engine

**NOT IMPLEMENTED**

### TradeProposal

**NOT IMPLEMENTED**

### RiskEvaluationContext

**DESIGNED / NOT IMPLEMENTED**

### ReservationState

**DESIGNED / NOT IMPLEMENTED**

### FinancialEvidence

**DESIGNED / NOT IMPLEMENTED**

### Final Admission

**DESIGNED / NOT IMPLEMENTED**

Estos estados describen diseño o ausencia de implementación; no constituyen evidencia de código funcional existente.

---

# COMPONENTES QUE NO DEBEN REINTERPRETARSE AUTOMÁTICAMENTE

Estos componentes existentes no deben declararse canónicos solo por conveniencia:

- AccountPosition legacy;
- OwnershipBook;
- reconciliation.Snapshot;
- LifecycleOrder;
- AccountConfiguration;
- StrategyAssumptions;
- RiskGuard.

Su significado exacto debe seguir siendo el observado en el código real.

---

# GAPS BLOQUEANTES REGISTRADOS

La carpeta `problema-de-huesos/` mantiene como bloqueantes, entre otros:

- TradeProposal;
- In-flight / Reserved State;
- Strategy Read Model / sizing boundary;
- Final Admission;
- UNKNOWN / retry semantics;
- Fill Identity;
- External Activity.

Mientras existan gaps BLOQUEANTES sin resolver:

**NO avanzar a la implementación final del Risk Engine ni a ejecución financiera real.**

La lista persistente de reparación debe leerse antes de continuar una nueva frontera funcional importante.

---

# REGLA DE CONTINUIDAD

Este archivo es una **memoria de estado**, no una fuente superior al repositorio.

Si GitHub muestra un estado diferente:

**GitHub gana al documento.**

Si el contenido de este archivo queda desactualizado, debe corregirse mediante evidencia del repositorio antes de continuar.

Etiquetas de verdad utilizadas cuando corresponda:

- OBSERVED
- TESTED
- INFERRED
- UNKNOWN
- BLOCKED

---

# ESTADO DE CONFIANZA

- HEAD de main: **VERIFY DIRECTLY FROM GITHUB**
- LAST VERIFIED MAIN BASE: **OBSERVED**
- LAST CONTINUITY MERGE PR #29: **OBSERVED**
- FASE 1.45 merged/closed: **OBSERVED**
- 782 tests en FASE 1.45: **TESTED**
- PR #26 merged/closed: **OBSERVED**
- PR #27 merged/closed: **OBSERVED**
- Documentos persistentes de continuidad presentes en main: **OBSERVED**
- Risk Engine implementado: **NOT IMPLEMENTED**
- HUESO 01 / TradeProposal implementado: **NOT IMPLEMENTED**
- Próxima frontera operativa: **INFERRED / REQUIRES CEREBRO REVIEW BEFORE IMPLEMENTATION**
