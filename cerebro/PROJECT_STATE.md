# CEREBRO / IA-CHAN — PROJECT STATE

## ÚLTIMA ACTUALIZACIÓN VERIFICADA

Fecha de referencia: 2026-10-08

Repository:

`eltiootaku01-hue/bot-cripto`

Branch principal:

`main`

DOCUMENTATION BASIS HEAD:

`052113bfe1e93d160ce1299bd9a9e1e6536869d2`

Este SHA identifica el commit de `main` contra el que se realizó esta actualización documental.

LAST FUNCTIONAL CHECKPOINT:

`72ffc2161eb7ad4dcf10d871eecb72f8e8255dfb`

Este SHA es una referencia histórica del último checkpoint funcional confirmado. No representa necesariamente el HEAD actual de `main`.

### Estado de continuidad

**DOCUMENTATION MODEL REPAIRED — LIVE HEAD MUST BE QUERIED DIRECTLY**

### Gobierno de HEAD

`PROJECT_STATE.md` NO almacena de forma permanente el SHA vivo del HEAD de `main`.

Los SHA persistidos aquí son snapshots históricos, checkpoints funcionales o bases documentales. El `main` HEAD vigente siempre debe obtenerse directamente desde GitHub.

Antes de iniciar cualquier nueva fase:

1. consultar GitHub y obtener el HEAD real de `main`;
2. leer `cerebro/PROJECT_STATE.md`;
3. comparar el HEAD real con el último checkpoint funcional, el documentation basis head y los merges posteriores conocidos;
4. distinguir cambios funcionales de cambios exclusivamente documentales;
5. detenerse solo si existe una divergencia que afecte la frontera funcional que se pretende ejecutar.

Un merge que modifica únicamente documentación puede cambiar el SHA vivo de `main` sin constituir por sí mismo un conflicto funcional.

**GitHub y el código real son SOURCE OF TRUTH.**

---

# HUESO 01 — TRADE PROPOSAL

Estado:

**MERGED / CLOSED**

PR:

`#31`

Merge commit:

`d89c94e9fba3b5de1767579eba45531e37fa1cc5`

Evidencia observada en `main`:

- `bot_obrero/trade_proposal.py`
- `tests/test_phase_hueso_01_trade_proposal.py`
- `TradeProposal`
- `build_trade_proposal`
- `max_quote_spend`

`TradeProposal` permanece separado de `Signal`, no es ejecutable y realiza validación estructural. No implementa Risk Engine ni Execution.

### HUESO 02D — TradeProposal max_quote_spend

Estado:

**MERGED / CLOSED**

PR:

`#33`

Merge commit:

`71d25fe16170e6cc28cea8514e58792fd6cb261a`

CI registrado antes del merge:

- Workflow: `tests`
- Run: `37147484265`
- Commit: `f69a0e246cf1d86f65b0ce6d94e5e4e4cb89bf2b`
- Job: `pytest`
- Resultado: `868 passed in 4.84s`

---

# HUESO 02 — SECUENCIA INTEGRADA

## 02-C — Spot economic instrument identity

Estado:

**MERGED / CLOSED**

PR:

`#32`

Merge commit:

`2d9886f121bf7c4a19e54abe9a5aac73573561ef`

La identidad económica Spot mantiene BASE/QUOTE explícitos y provider-neutral.

---

## 02-E1 — ReservationReadSet

Estado:

**MERGED / CLOSED**

El repositorio contiene la evolución persistente/read-model de Reservation asociada a esta frontera.

---

## 02-E1R — Repair del test heredado SQLite

Estado:

**MERGED / CLOSED**

PR:

`#35`

Merge commit:

`ec252bd1db3a33aa6a53b167842d3f884ca675f7`

Alcance: test-only. Se excluye `sqlite_%` de la enumeración de tablas de aplicación.

---

## 02-F1 — EffectiveCapacity

Estado:

**MERGED / CLOSED**

PR:

`#34`

Merge commit:

`27136dbad1f4b12303fb7ba9bccd49241d81689a`

El read model es provider-neutral, derivado y no persistente. Usa `Reservation.protected_capacity`, conserva UNKNOWN y separa completeness de OVERCOMMITTED.

---

## 02-G0 — Atomic admission audit/design

Estado:

**AUDITED / DESIGN COMPLETED**

No registrar este paso como Risk Engine ni como Execution.

---

## 02-G1 — Atomic Reservation admission

Estado:

**MERGED / CLOSED**

PR:

`#36`

Merge commit:

`a4986ddecd4a4e32aab036d8286a243f0462ca7c`

La frontera implementada es fail-closed y local sobre una conexión SQLite con `BEGIN IMMEDIATE`.

Se mantienen:

- comprobación atómica de capacidad;
- persistencia de Reservation + transición inicial;
- multiplicidad de Proposal no terminal;
- rollback;
- manejo fail-closed de SQLite busy/locked.

---

## 02-H0 — Proposal → Risk → Atomic Reservation audit

Estado:

**AUDITED / PARTIAL FLOW IDENTIFIED**

La auditoría estableció que la cadena contractual existe parcialmente, pero no constituye todavía un Risk Engine operativo.

---

## 02-H1 — Financial Admission Boundary design

Estado:

**DESIGNED / CLOSED**

El límite de admisión se mantiene separado de Risk Engine y Execution.

---

## 02-H2 — Financial Admission Boundary implementation

Estado:

**MERGED / CLOSED**

PR:

`#37`

HEAD previo al merge:

`2cb36a06caeda1fcde770bf56260cac2f07586aa`

Merge commit / MAIN posterior:

`01a4c1dad479fcecba1e13587c6455b1939cd436`

Evidencia observada en `main`:

- `bot_obrero/financial_admission.py`
- `tests/test_hueso_02h2_financial_admission.py`
- `FinancialAdmissionRequest`
- `FinancialAdmissionBoundary`
- `FinancialAdmissionResult`
- delegación de admisión a `SQLiteReservationStore.admit()`
- estados tipados de admisión
- identity binding entre TradeProposal y RiskDecision
- approved-only admission
- idempotency key financiera

No se introdujo Risk Engine, Strategy, Final Admission ni Execution integration.

---

## 02-H2R — Persistence idempotency across restart

Estado:

**TESTED / MERGED**

Evidencia en el test actual:

- la primera admisión persiste una Reservation;
- el store se cierra;
- se reconstruye el store desde el mismo SQLite;
- una segunda admisión con la misma request devuelve `ALREADY_ADMITTED`;
- la Reservation persistida es la misma;
- no se crea una segunda Reservation para la Proposal.

Commit de cierre en `main`:

`01a4c1dad479fcecba1e13587c6455b1939cd436`

Mensaje:

`test: verify financial admission idempotency across restart`

---

# HUESO 02-H3 — POST-MERGE VERIFICATION

Estado:

**PASS / CLOSED**

PR verificado:

`#37`

MAIN posterior:

`01a4c1dad479fcecba1e13587c6455b1939cd436`

### CI PR previo

- Run: `37282487584`
- Resultado: `1002 passed in 6.42s`

### CI post-merge

- Run: `37287615768`
- Event: `push`
- Branch: `main`
- Commit: `01a4c1dad479fcecba1e13587c6455b1939cd436`
- Job: `pytest`
- Python: `3.12.14`
- Resultado: `1002 passed in 5.98s`
- Duración observada: `16 s`

Este CI post-merge constituye evidencia real de la continuidad funcional del `main`.

---

# HUESO 03 — STRATEGY RUNTIME V1.0

Estado:

**MERGED / CLOSED**

PR:

`#41`

Título:

`HUESO 03 — Strategy Runtime v1.0`

Merge commit:

`d1287bfa03bdfa71020f41d642242da491b952da`

Archivos principales:

- `bot_obrero/strategy_runtime.py`
- `tests/test_strategy_runtime.py`

Estado arquitectónico:

**StrategyRuntime = IMPLEMENTED**

La implementación observada mantiene la frontera:

`Strategy.compute()` → `StrategicArtifact` → `Hypothesis` → `Signal`

y no introduce sizing, Risk, TradeProposal, Execution, providers, persistence, registry ni scheduler.

---

# HUESO 04 — SIZING / PORTFOLIO CONSTRUCTION V1.0

Estado:

**MERGED / CLOSED**

PR:

`#42`

Título:

`HUESO 04 — Sizing / Portfolio Construction v1.0`

Merge commit:

`60aa155162fdd52b6c75e4cd0ed3ffe90ce42b26`

Archivos:

- `bot_obrero/sizing.py`
- `tests/test_sizing.py`

Modelo implementado:

`Signal` → `FixedQuantitySizer` → `SizingResult`

Alcance real observado:

- `fixed_quantity`
- identity: `sizing.fixed_quantity`
- version: `1.0.0`
- requested quantity como `Decimal` en `effective_parameters["quantity"]`

No reinterpretar este componente como portfolio optimization, Kelly, VaR, volatility targeting u otros modelos no presentes en el repositorio.

---

# HUESO 05-A — RISK EVALUATION POLICY & RISK LIMIT APPLICABILITY

Estado:

**MERGED / CLOSED**

PR:

`#44`

Merge commit:

`80aa28771d95e9759ccb9442d30a129455d13569`

Alcance observado:

- `RiskEvaluationPolicy`
- política de freshness de valoración;
- política de evidencia mínima para aprobación;
- `RiskLimitApplicabilityResolver` determinista;
- pruebas deterministas.

Fronteras explícitamente preservadas:

- no `RiskEngine` dentro de HUESO 05-A;
- no `RiskAuthorization`;
- no `FinalAdmission`;
- no Execution bridge;
- no acceso a providers.

---

# HUESO 05-B — DETERMINISTIC RISK ENGINE V1.0

Estado:

**MERGED / CLOSED**

PR:

`#45`

Merge commit:

`72ffc2161eb7ad4dcf10d871eecb72f8e8255dfb`

Estado arquitectónico:

**RiskEngine = IMPLEMENTED**

Evidencia observada en `main`:

- `bot_obrero/risk_engine.py`
- `tests/test_hueso_05b_risk_engine.py`
- `RiskEngine`
- `RiskDecision`
- evaluación determinista y fail-closed;
- MAX_NOTIONAL sobre scope ACCOUNT;
- sin acceso a providers;
- sin reloj;
- sin mutación de inputs;
- sin Reservation;
- sin Execution.

La existencia de `RiskEngine` no implica que estén implementados los runtimes posteriores de `RiskAuthorization`, `FinalAdmission`, ejecución o reconciliación.

---

# BUG-001

Estado:

**PASS — CI POST-MERGE VERIFIED**

Root cause documentado:

**observability / connector inspection limitation**

No fue necesario modificar el workflow `tests`.

Workflow real observado:

```
name: tests
on: [push, pull_request]
```

No registrar BUG-001 como un defecto de YAML.

---

# E2E — ESTADO SEPARADO

## FASE 1.34 — INDICATORS REAL E2E

Estado:

**HISTORICALLY VERIFIED — E2E SUCCESS**

PR:

`#19`

Título:

`FASE 1.34 — Validate real Binance EMA and RSI E2E`

Merged:

**YES**

Merge commit:

`868dd1c3d34fd0ee019754f9ea391d074420f92f`

Workflow:

`phase1.34-indicators-real-e2e`

Run histórico:

`37057797709`

Evidencia observada en el job `indicators-real-e2e`:

- conclusión: `success`
- Binance Spot real
- `BTCUSDT` / `BTC/USDT`
- 14 velas reales de 1m
- EMA expected == result
- RSI expected == result
- Decimal preservado
- look-ahead EMA/RSI: PASS
- determinism EMA/RSI: PASS
- no mutation / indicator independence: PASS
- resultado: `REAL E2E PASS`

Árbol actual observado en `main`:

- `scripts/phase1_34_indicators_real_e2e.py`
- `.github/workflows/phase1_34_indicators_real_e2e.yml`

Esta es evidencia histórica de FASE 1.34. No afirmar por ella una nueva ejecución E2E sobre el `main` actual.

## SMA

Estado:

**UNKNOWN — POST-MERGE E2E NOT VERIFIED**

El workflow real de SMA no ejecuta automáticamente sobre `push` a `main`. La evidencia post-merge de tests no equivale a una nueva ejecución E2E real de Binance sobre `main`.

## Indicators

Estado:

**UNKNOWN — POST-MERGE E2E NOT VERIFIED**

El workflow real de indicadores tampoco ejecuta automáticamente sobre `push` a `main`. La existencia del workflow no constituye por sí sola evidencia de una nueva ejecución E2E post-merge sobre `main`.

Estas incertidumbres no se registran como BUG-001 y no se implementan correcciones en esta sincronización documental.

---

# CI ACTUAL

## Main

DOCUMENTATION BASIS HEAD:

`052113bfe1e93d160ce1299bd9a9e1e6536869d2`

El HEAD vivo de `main` NO se persiste como estado permanente en este documento.

Estado CI del documentation basis head `052113bfe1e93d160ce1299bd9a9e1e6536869d2`:

**UNKNOWN — no se encontró workflow run ni status asociado a este commit.**

### Última evidencia CI verificada conocida

Workflow:

`tests`

Run:

`37831334656`

Commit:

`90dce1d5ecc81b86d2085db6918bcb7ef771e050`

Job:

`pytest`

Conclusión:

**success**

Resultado exacto de tests:

**UNKNOWN**

Esta ejecución pertenece al commit histórico `90dce1d5ecc81b86d2085db6918bcb7ef771e050`. No asociarla al documentation basis head `052113bfe1e93d160ce1299bd9a9e1e6536869d2` y no usarla como evidencia de CI del HEAD actual.

---

# ARQUITECTURA FINANCIERA OBSERVADA

## Evaluación de riesgo determinista

La frontera de Risk actualmente implementada es:

```
TradeProposal
      ↓
RiskEvaluationContext
      +
RiskEvaluationPolicy
      +
RiskLimitApplicabilityResolver
      ↓
RiskEngine
      ↓
RiskDecision
```

Alcance actual de `RiskEngine`:

- evaluación determinista y fail-closed;
- MAX_NOTIONAL sobre ACCOUNT;
- sin provider access;
- sin reloj;
- sin mutación;
- sin autorización posterior;
- sin ejecución.

## Admisión financiera local

La frontera de admisión actualmente implementada es:

```
TradeProposal
      ↓
RiskDecision.APPROVED
      ↓
FinancialAdmissionBoundary
      ↓
SQLiteReservationStore.admit()
      ↓
Reservation
```

Componentes observados:

- TradeProposal
- RiskEvaluationContext
- RiskEvaluationPolicy
- RiskLimitApplicabilityResolver
- RiskEngine
- RiskDecision
- Reservation
- ReservationReadSet
- EffectiveCapacity
- atomic Reservation admission
- FinancialAdmissionRequest
- FinancialAdmissionBoundary
- FinancialAdmissionResult

### Frontera importante

`FinancialAdmissionBoundary` sigue siendo una barrera de admisión financiera local. No debe reinterpretarse como `RiskAuthorization`, `FinalAdmission` ni como Execution integration.

---

# COMPONENTES QUE SIGUEN SIN IMPLEMENTARSE

No declarar como implementados:

### RiskAuthorization

**NOT IMPLEMENTED**

### FinalAdmission

**NOT IMPLEMENTED**

### Reservation → Execution runtime

**NOT IMPLEMENTED**

### Reconciliation runtime

**NOT IMPLEMENTED**

RiskEngine ya está implementado y cerrado en HUESO 05-B.

StrategyRuntime y Sizing Engine ya están implementados y cerrados en HUESO 03 y HUESO 04, respectivamente.

No confundir contratos, primitivas o motores deterministas implementados con runtimes integrados posteriores. La ausencia de estas fronteras posteriores no se considera por sí misma un bug.

---

# GOBIERNO DE ALCANCE

No introducir en la frontera actual:

- Risk Engine final;
- Final Admission;
- OrderIntent final;
- Execution integration;
- Reconciliation runtime;
- registry;
- factory;
- plugin system;
- generic dispatcher.

StrategyRuntime y Sizing Engine son fronteras ya implementadas y no deben reinterpretarse ni generalizarse por conveniencia.

No generalizar los componentes existentes solo por conveniencia.

---

# HISTORIAL PREVIO DE CONTINUIDAD

La documentación persistente anterior registraba estados históricos que ya no corresponden al repositorio actual. Ese historial no debe prevalecer sobre la evidencia actual de GitHub.

El punto de continuidad funcional vigente para esta memoria es:

**HUESO 05-B — MERGED / CLOSED**

Último checkpoint funcional confirmado:

`72ffc2161eb7ad4dcf10d871eecb72f8e8255dfb`

La base documental de esta actualización es:

`052113bfe1e93d160ce1299bd9a9e1e6536869d2`

Este archivo no afirma que `052113bfe1e93d160ce1299bd9a9e1e6536869d2` siga siendo el HEAD vivo después de futuros merges documentales o funcionales.

---

# REGLA DE CONTINUIDAD

Este archivo es memoria documental y evidencia arquitectónica; no sustituye al repositorio.

**GitHub y el código real son SOURCE OF TRUTH.**

Los SHA almacenados en `PROJECT_STATE.md` son referencias históricas, checkpoints funcionales o bases documentales. No son un puntero vivo al HEAD actual.

Cuando exista discrepancia:

**GitHub gana al documento.**

Usar etiquetas de verdad:

- OBSERVED
- TESTED
- INFERRED
- UNKNOWN
- BLOCKED

No inventar tests, commits, CI, merges, comportamiento ni estados arquitectónicos.

### Procedimiento canónico antes de cualquier nueva frontera funcional

1. consultar GitHub directamente y obtener el HEAD real de `main`;
2. leer este archivo;
3. contrastar el HEAD real con el último checkpoint funcional, el documentation basis head y los merges posteriores conocidos;
4. distinguir si la divergencia es funcional o exclusivamente documental;
5. declarar conflicto de continuidad solo cuando la diferencia afecte la frontera funcional que se pretende ejecutar;
6. respetar stop conditions y no asumir que diseño equivale a implementación.

Un nuevo merge documental no constituye por sí mismo un conflicto funcional.

---

# ESTADO DE CONFIANZA

- Documentation basis head: **OBSERVED — `052113bfe1e93d160ce1299bd9a9e1e6536869d2`**
- Último functional checkpoint: **OBSERVED — `72ffc2161eb7ad4dcf10d871eecb72f8e8255dfb`**
- Current main HEAD: **NOT STORED — QUERY GITHUB DIRECTLY**
- CI del documentation basis head: **UNKNOWN**
- Continuidad: **REPAIRED — LIVE HEAD QUERIED DIRECTLY**
- HUESO 01 / TradeProposal: **OBSERVED — MERGED / CLOSED**
- 02-C: **OBSERVED — MERGED / CLOSED**
- 02-D: **OBSERVED — MERGED / CLOSED**
- 02-E1: **OBSERVED — MERGED / CLOSED**
- 02-E1R: **OBSERVED — MERGED / CLOSED**
- 02-F1: **OBSERVED — MERGED / CLOSED**
- 02-G0: **AUDITED / DESIGN COMPLETED**
- 02-G1: **OBSERVED — MERGED / CLOSED**
- 02-H0: **AUDITED / PARTIAL FLOW IDENTIFIED**
- 02-H1: **DESIGNED / CLOSED**
- 02-H2: **OBSERVED — MERGED / CLOSED**
- 02-H2R: **TESTED / MERGED**
- 02-H3: **PASS / CLOSED**
- BUG-001: **PASS — CI POST-MERGE VERIFIED**
- HUESO 03: **OBSERVED — MERGED / CLOSED**
- StrategyRuntime: **OBSERVED — IMPLEMENTED**
- HUESO 04: **OBSERVED — MERGED / CLOSED**
- Sizing Engine: **OBSERVED — IMPLEMENTED**
- HUESO 05-A: **OBSERVED — MERGED / CLOSED**
- HUESO 05-B: **OBSERVED — MERGED / CLOSED**
- RiskEngine: **OBSERVED — IMPLEMENTED**
- FASE 1.34: **HISTORICALLY VERIFIED — E2E SUCCESS**
- SMA post-merge E2E: **UNKNOWN**
- Indicators post-merge E2E: **HISTORICALLY VERIFIED — CURRENT-MAIN POST-MERGE E2E UNKNOWN**
- RiskAuthorization: **NOT IMPLEMENTED**
- FinalAdmission: **NOT IMPLEMENTED**
- Reservation → Execution runtime: **NOT IMPLEMENTED**
- Reconciliation runtime: **NOT IMPLEMENTED**
