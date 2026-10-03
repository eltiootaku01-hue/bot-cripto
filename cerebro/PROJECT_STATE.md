# CEREBRO / IA-CHAN — PROJECT STATE

## ÚLTIMA ACTUALIZACIÓN VERIFICADA

Fecha de referencia: 2026-10-03

Repository:

`eltiootaku01-hue/bot-cripto`

Branch principal:

`main`

HEAD verificado:

`28fc3a919395883fe0afbb188ccf4d52d97ebde3`

Commit:

`Merge pull request #25 from eltiootaku01-hue/phase1.45-risk-contracts`

---

# FASE ACTUAL

## FASE 1.45 — CONTRATOS CANÓNICOS DE ESTADO FINANCIERO + RISK DECISION

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

---

# EVIDENCIA DE FASE 1.45

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

# FRONTERA ARQUITECTÓNICA ACTUAL

La cadena anterior quedó:

**AnalysisSnapshot → Strategy → StrategicArtifact → Hypothesis → Signal**

FASE 1.45 agrega contratos financieros/risk, pero NO conecta todavía el Signal con una evaluación de riesgo operativa.

La frontera siguiente debe auditar primero:

**Signal → Risk evaluation → RiskDecision → OrderIntent**

Antes de implementar esa conexión, el Cerebro debe verificar nuevamente:

- fuente canónica de account state;
- posiciones;
- exposure;
- límites;
- temporalidad/frescura;
- semantics de UNKNOWN;
- correlation_id;
- relación entre RiskDecision y futura autorización de OrderIntent.

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

Su significado exacto debe seguir siendo el observado en el código.

---

# REGLA DE CONTINUIDAD

Este archivo es una **memoria de estado**, no una fuente superior al repositorio.

Si GitHub muestra un estado diferente:

**GitHub gana.**

Marcar el archivo como desactualizado y reconstruir evidencia antes de continuar.

---

# ESTADO DE CONFIANZA

- HEAD de main: **OBSERVED**
- FASE 1.45 merged: **OBSERVED**
- 782 tests: **TESTED**
- Risk Engine aún no implementado al cierre de FASE 1.45: **OBSERVED**
- Próxima frontera exacta: **INFERRED / REQUIRES AUDIT BEFORE IMPLEMENTATION**
