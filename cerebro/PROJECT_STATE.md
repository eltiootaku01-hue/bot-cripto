# CEREBRO / IA-CHAN — PROJECT STATE

## ÚLTIMA ACTUALIZACIÓN VERIFICADA

Fecha de referencia: 2026-10-10

Repository:

`eltiootaku01-hue/bot-cripto`

Branch principal:

`main`

DOCUMENTATION BASIS HEAD:

`b9481f5690c6db6f831660da56399163218576ea`

Este SHA identifica el commit de `main` usado como base para esta actualización documental. Es una referencia histórica, no un puntero vivo al HEAD.

LAST FUNCTIONAL CHECKPOINT:

`b9481f5690c6db6f831660da56399163218576ea`

Este SHA es el último checkpoint funcional confirmado para esta actualización, después del merge de HUESO 05-E y su CI post-merge exitoso. Es una referencia histórica y no representa necesariamente el HEAD actual de `main`.

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

En el checkpoint histórico de HUESO 05-B, `RiskAuthorization` todavía no estaba implementado. Esa frontera se incorporó después en HUESO 05-C. La existencia de `RiskEngine` o `RiskAuthorization` no implica `FinalAdmission`, ejecución ni reconciliación integradas.

---

# HUESO 05-C — RISK AUTHORIZATION V1.0 + DECISION PROVENANCE BINDING

Estado:

**MERGED / CLOSED**

PR:

`#52`

URL:

[HUESO 05-C en GitHub](https://github.com/eltiootaku01-hue/bot-cripto/pull/52)

HEAD de la PR:

`1ec386ebafdc0426ce45fd13ab45520c345189fd`

Merge commit / MAIN posterior:

`f9b6785ab322d99f503b438ee5d504a9b0eab2c8`

Evidencia observada en `main`:

- `bot_obrero/risk_authorization.py`
- `RiskAuthorization`
- `RiskAuthorizationResult`
- `RiskAuthorizationStatus`
- `authorize_risk_decision`
- Binding de provenance en `RiskDecision`: `evaluation_context_id`, `policy_id` y `policy_version`.
- `RiskDecision.from_risk_evaluation()` deriva ese binding desde `RiskEvaluationContext` y `RiskEvaluationPolicy`.
- Validación de relaciones entre propuesta, decisión, contexto, política, timestamp y evidencia.
- Resultado fail-closed: `AUTHORIZED`, `REJECTED` o `UNKNOWN`.
- El artefacto `RiskAuthorization` solo puede representar `AUTHORIZED`.
- Los resultados `REJECTED` o `UNKNOWN` no exponen un artefacto de autorización.
- La evidencia de riesgo se comprueba por igualdad estructural, no por identidad de objetos Python.
- Contratos inmutables y cobertura específica de tests.

La frontera añadida es:

`RiskDecision → RiskAuthorization`

Esta implementación no equivale a `FinalAdmission`, no integra `Reservation → Execution`, no implementa reconciliation runtime y no demuestra un runtime autónomo de producción.

### CI post-merge

- Workflow: `tests`
- Run: `37881330778`
- Evento: `push`
- Branch: `main`
- Commit: `f9b6785ab322d99f503b438ee5d504a9b0eab2c8`
- Job: `pytest`
- Resultado: `success`
- Tests: `1242 passed in 7.78s`

[CI post-merge HUESO 05-C](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/37881330778)

---

# HUESO 05-D — AUTHORIZATION-ENFORCED FINANCIAL ADMISSION V1.0

Estado:

**MERGED / CLOSED — POST-MERGE VERIFIED**

PR:

`#55`

URL:

[HUESO 05-D en GitHub](https://github.com/eltiootaku01-hue/bot-cripto/pull/55)

Merge commit / último HEAD funcional confirmado por esta fase:

`e21a129f146f2af8f889c09a094fba5cd78ba41c`

### Implementación observada en `main`

Archivos y contratos inspeccionados:

- `bot_obrero/financial_admission.py`
- `bot_obrero/reservation.py`
- `bot_obrero/risk_authorization.py`
- `tests/test_hueso_05d_final_admission.py`
- `FinancialAdmissionRequest`
- `FinancialAdmissionBoundary`
- `RiskAuthorization` con vínculo semántico validado contra la decisión, propuesta, contexto, política, timestamp y evidencia de riesgo.

Propiedades verificadas en el código y los tests:

- La solicitud de admisión es inmutable y exige `RiskDecision.APPROVED`, autorización `AUTHORIZED`, contexto/política consistentes, cuenta canónica completa y evidencia explícita coincidente.
- El fingerprint semántico SHA-256 de la autorización se incorpora a la clave de idempotencia financiera v2 junto con la identidad y los términos económicos de la reserva.
- Los términos canónicos de reserva se derivan a partir de la propuesta y el instrumento canónico usando `Decimal`; la validación rechaza una cantidad, activo o tipo de recurso que no coincida con los términos económicos admitidos.
- En el punto de escritura, `SQLiteReservationStore.admit()` abre la transacción atómica `BEGIN IMMEDIATE` y ejecuta la validación canónica de la clase `FinancialAdmissionRequest`, no una validación sustituida por una instancia.
- La reserva, su transición inicial y el vínculo persistido en `reservation_authorization_bindings` se escriben en la misma operación atómica; los tests verifican rollback ante fallo de escritura del vínculo.
- Se rechazan subclases de `FinancialAdmissionRequest` con validación sobrescrita; las pruebas también cubren la sustitución de `validate` por instancia y muestran que la escritura vuelve a aplicar la validación canónica.
- Una reserva histórica sin vínculo de autorización válido no se considera autorizada: la admisión falla de forma cerrada con estado `REJECTED` y motivo `EXISTING_RESERVATION_AUTHORIZATION_UNKNOWN`.
- La suite incluye cobertura de idempotencia tras reiniciar el store, conflicto de contexto de idempotencia, atomicidad/rollback y llamadas concurrentes equivalentes que convergen en una sola reserva.

### CI post-merge

- Workflow: `tests`
- Run ID: `38034655787`
- Evento: `push`
- Rama: `main`
- Commit SHA: `e21a129f146f2af8f889c09a094fba5cd78ba41c`
- Job: `pytest`
- Python: `3.12.15`
- Resultado: `1286 passed in 8.13s`
- Conclusión: `success`

[CI post-merge HUESO 05-D](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38034655787)

### Limitaciones explícitas

- No estima ni reserva comisiones implícitas.
- No conecta la reserva con Execution.
- No demuestra un runtime autónomo integrado de producción.
- El fingerprint SHA-256 es un vínculo semántico determinista, no una firma criptográfica.

El título histórico “Final Admission” no convierte a `FinancialAdmissionBoundary` en un runtime autónomo de admisión final. Esta fase registra únicamente la frontera financiera de admisión actualmente implementada.

---


# HUESO 05-E — RESERVATION → EXECUTION ADMISSION BRIDGE V1.0

Estado:

**IMPLEMENTED / MERGED / CLOSED — POST-MERGE VERIFIED**

PR:

`#57`

URL:

[HUESO 05-E en GitHub](https://github.com/eltiootaku01-hue/bot-cripto/pull/57)

HEAD aprobado e integrado:

`b4f7fe66541118c130d17a153e193c98ef4bf458`

`main` anterior al merge:

`539f0fb1cb9980509f96de3d0b9d3a36247bdd26`

Merge commit / HEAD de `main` observado después del merge:

`b9481f5690c6db6f831660da56399163218576ea`

Método: merge normal de GitHub, sin squash ni rebase.

Fecha de merge: `2026-10-10 14:39:48 UTC`.

### Archivos principales observados

- `bot_obrero/reservation_execution_bridge.py`
- `bot_obrero/execution.py`
- `bot_obrero/persistent_ledger.py`
- `bot_obrero/reservation.py`
- `tests/test_hueso_05e_reservation_execution_bridge.py`
- `tests/test_phase14_execution_boundary.py`
- `docs/HUESO_05_E_RESERVATION_EXECUTION_BRIDGE.md`

### Puente y propiedades verificadas

HUESO 05-E implementa un puente persistente local entre Reservation y la ruta autorizada de Execution. No equivale a un runtime autónomo de producción ni a reconciliación con un exchange.

- `ReservationExecutionBridge` prepara `PreparedExecutionIntent` desde el snapshot económico canónico y el binding de autorización persistidos; el caller identifica la reserva y no vuelve a suministrar los términos económicos para crear autoridad alternativa.
- El binding persistido vincula la reserva, el fingerprint de autorización, `client_order_id`, `terms_hash` e `intent_hash`. Los hashes son SHA-256 deterministas sobre representaciones canónicas; no son firmas criptográficas ni autentican quién pudo modificar directamente la base de datos.
- El marcador durable `SUBMISSION_STARTED` se registra antes de invocar el adaptador. Si su escritura falla, no se realiza la llamada al adaptador.
- `ExecutionOrchestrator` valida readiness y evidencia, verifica la vinculación persistida y registra el `client_order_id` en el ledger idempotente antes del efecto externo. `ExecutionBoundary` vuelve a cargar y verificar el binding y reconstruye la intención de orden usando exclusivamente el payload persistido.
- La cobertura R3 en `tests/test_phase14_execution_boundary.py` verifica que una ruta ordinaria con `OrderIntent` genérico y sin vinculación persistida no llega al adaptador. Esto no convierte el proceso Python en una sandbox contra código hostil que emplee reflexión dentro del mismo proceso.
- `UNKNOWN` y un `SUBMISSION_STARTED` que quede tras una caída no habilitan reenvío automático. Una excepción no se interpreta como prueba de rechazo del exchange.
- La recuperación y la reparación de la proyección del ledger son locales. `synchronize_projection` / `recover_projection` pueden reconstruir idempotentemente la proyección desde la vinculación autoritativa si coinciden `client_order_id` e `intent_hash`; no existe una transacción atómica entre ambos stores.

### CI post-merge — evidencia funcional

- Workflow: `tests`
- Run ID: `38060502389`
- Evento: `push`
- Branch: `main`
- Commit SHA probado: `b9481f5690c6db6f831660da56399163218576ea`
- Job: `pytest`
- Job ID: `114237628651`
- Conclusion: `success`
- Tests: `1320 passed in 7.23s`

[CI post-merge HUESO 05-E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38060502389)

El log confirma checkout del SHA exacto del merge commit y finalización correcta de `pytest -q`. Este CI es evidencia post-merge de tests, no evidencia de E2E nuevo sobre este `main`.

### E2E pre-merge — conservar su alcance

Las siguientes ejecuciones corresponden al HEAD directo R3 o a la integración de PR; no deben presentarse como E2E post-merge en el nuevo `main`.

- Push tests R3: run `38057021135`, `1320 passed in 7.02s` sobre el HEAD directo de R3.
- PR tests R3: run `38057023134`, `1320 passed in 8.72s` sobre la integración sintética.
- SMA E2E: run `38057023184`, `REAL E2E PASS` sobre la integración de PR.
- EMA/RSI E2E: run `38057023166`, `REAL E2E PASS` sobre la integración de PR.

### Límites operativos explícitos

- `SUBMITTED` significa que la llamada al adaptador terminó sin excepción y que el resultado local se registró; no demuestra aceptación final del exchange ni fill.
- `UNKNOWN` nunca permite reenvío automático.
- La recuperación actual opera sobre persistencia local; no demuestra reconciliación con un exchange.
- Los hashes SHA-256 son vínculos deterministas de integridad/consistencia, no firmas criptográficas.
- La protección de autoridad verificada por R3 cubre rutas ordinarias probadas; no constituye una sandbox contra reflexión hostil en el mismo proceso.
- El runtime autónomo integrado de producción sigue **NOT DEMONSTRATED**.
- SMA post-merge E2E: **UNKNOWN**.
- EMA/RSI post-merge E2E: **UNKNOWN**; los E2E registrados arriba siguen siendo pre-merge.

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

`b9481f5690c6db6f831660da56399163218576ea`

Este es el commit de `main` usado como base para esta actualización documental. El HEAD vivo no se persiste como un campo permanente.

### Evidencia CI post-merge HUESO 05-E — checkpoint funcional actual

- Workflow: `tests`
- Run ID: `38060502389`
- Evento: `push`
- Branch: `main`
- Commit SHA: `b9481f5690c6db6f831660da56399163218576ea`
- Job: `pytest`
- Job ID: `114237628651`
- Conclusion: `success`
- Tests: `1320 passed in 7.23s`

[CI post-merge HUESO 05-E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38060502389)

Esta ejecución corresponde al checkpoint funcional post-merge de HUESO 05-E. No es evidencia de CI del commit documental que se cree posteriormente y no demuestra por sí sola una nueva ejecución E2E real de Binance.

### Evidencia CI posterior al merge de HUESO 05-D — histórica

- Workflow: `tests`
- Run ID: `38034655787`
- Evento: `push`
- Branch: `main`
- Commit SHA: `e21a129f146f2af8f889c09a094fba5cd78ba41c`
- Job: `pytest`
- Python: `3.12.15`
- Conclusion: `success`
- Tests: `1286 passed in 8.13s`

[Run de CI post-merge HUESO 05-D](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38034655787)

Esta ejecución verifica el checkpoint funcional indicado. No es evidencia de CI del commit documental que se cree posteriormente y no demuestra por sí sola una nueva ejecución E2E real de Binance.

### Evidencia CI posterior al merge de HUESO 05-C — histórica

- Workflow: `tests`
- Run ID: `37881330778`
- Evento: `push`
- Branch: `main`
- Commit SHA: `f9b6785ab322d99f503b438ee5d504a9b0eab2c8`
- Job: `pytest`
- Conclusion: `success`
- Tests: `1242 passed in 7.78s`

[Run histórico de CI post-merge HUESO 05-C](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/37881330778)

Esta ejecución pertenece al checkpoint funcional de HUESO 05-C y permanece como evidencia histórica. No atribuirla a HUESO 05-D ni a un commit documental posterior.

### Evidencia CI anterior conocida — histórica

- Workflow: `tests`
- Run ID: `37831334656`
- Evento: `push`
- Branch: `main`
- Commit SHA: `90dce1d5ecc81b86d2085db6918bcb7ef771e050`
- Conclusion: `success`
- Conteo exacto de tests: `UNKNOWN`

[Run histórico de CI](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/37831334656)

Esta ejecución corresponde a un commit anterior y no debe asociarse al documentation basis head ni reemplazar la evidencia post-merge de HUESO 05-D.

### Estado E2E actual

- SMA sobre el `main` actual: **UNKNOWN — nueva ejecución E2E real post-merge no verificada**.
- EMA/RSI sobre el `main` actual: **UNKNOWN — nueva ejecución E2E real post-merge no verificada**.

Los E2E históricos registrados en la sección E2E conservan su alcance histórico. El workflow `tests` no sustituye a una ejecución específica E2E de Binance.

---

# ARQUITECTURA FINANCIERA OBSERVADA

## Evaluación y autorización de riesgo

La evaluación determinista y su frontera posterior de autorización están implementadas como componentes separados:

```
TradeProposal + RiskEvaluationContext + RiskEvaluationPolicy
      ↓
RiskEngine (con RiskLimitApplicabilityResolver)
      ↓
RiskDecision
  [evaluation_context_id, policy_id, policy_version]
      ↓
authorize_risk_decision(...)
      ↓
RiskAuthorization (solo cuando el resultado es AUTHORIZED)
```

Alcance observado de `RiskEngine`:

- evaluación determinista y fail-closed;
- MAX_NOTIONAL sobre scope ACCOUNT;
- sin provider access;
- sin reloj;
- sin mutación de inputs;
- no construye por sí mismo `RiskAuthorization`; esa validación corresponde a `authorize_risk_decision`;
- no ejecuta órdenes.

Alcance observado de `RiskAuthorization`:

- verifica el binding de proposal, decisión, contexto, política, timestamps y evidencia;
- deriva un artefacto inmutable únicamente para estado `AUTHORIZED`;
- `REJECTED` y `UNKNOWN` no exponen un artefacto `RiskAuthorization`;
- permanece separado de `FinalAdmission`, Reservation y Execution.

## Admisión financiera local

La frontera de admisión financiera observada después de HUESO 05-D es:

```
TradeProposal + RiskDecision.APPROVED + RiskAuthorization.AUTHORIZED
      ↓ (vínculos de decisión, propuesta, contexto, política y evidencia)
FinancialAdmissionRequest
      ↓
FinancialAdmissionBoundary
      ↓
SQLiteReservationStore.admit()
      ↓ (validación canónica + transacción BEGIN IMMEDIATE)
Reservation + transición inicial + vínculo persistido de autorización
```

Componentes observados:

- `FinancialAdmissionRequest` inmutable, con vinculación semántica explícita de `RiskAuthorization`.
- `FinancialAdmissionBoundary`, como frontera local después de una autorización explícita.
- `SQLiteReservationStore.admit()`, que vuelve a validar la solicitud en el límite de escritura.
- Derivación de términos de reserva a partir de la propuesta y el instrumento canónico usando `Decimal`.
- Clave de idempotencia v2 enlazada con los términos económicos y el fingerprint semántico de autorización.
- Persistencia atómica de Reservation, transición inicial y vínculo de autorización.
- Tratamiento fail-closed de reservas históricas que carecen de vínculo de autorización válido.

### Frontera importante

En el alcance propio de HUESO 05-D se incorporó admisión financiera con autorización vinculada; aquella fase no creó un `FinalAdmission` autónomo distinto de `FinancialAdmissionBoundary`, no implementó reconciliation runtime y no demostró un runtime autónomo integrado de producción. La integración local persistente Reservation → Execution se añadió posteriormente en HUESO 05-E, documentada a continuación. HUESO 05-D no estima ni reserva comisiones implícitas. El fingerprint SHA-256 constituye un vínculo semántico, no una firma criptográfica.

---

# COMPONENTES QUE SIGUEN SIN IMPLEMENTARSE

### HUESO 05-E — Reservation → Execution Admission Bridge

**IMPLEMENTED / MERGED / POST-MERGE VERIFIED — puente persistente local; no es un runtime autónomo de producción ni reconciliación externa.**

### FinalAdmission autónomo distinto de `FinancialAdmissionBoundary`

**NOT IMPLEMENTED — no existe evidencia de una implementación separada de `FinancialAdmissionBoundary`.**

### Autonomous integrated production runtime

**NOT DEMONSTRATED — la integración local del puente no demuestra operación autónoma integrada de producción.**

### Reconciliation runtime con un exchange

**NOT IMPLEMENTED — la recuperación y reparación implementadas son locales; no prueban reconciliación externa.**

RiskEngine está implementado en HUESO 05-B y `RiskAuthorization` en HUESO 05-C.

StrategyRuntime y Sizing Engine ya están implementados y cerrados en HUESO 03 y HUESO 04, respectivamente.

No confundir contratos, primitivas o motores deterministas implementados con runtimes integrados posteriores. HUESO 05-E sí implementa el puente persistente local Reservation → Execution en los límites documentados; siguen sin demostrarse un `FinalAdmission` autónomo separado, un runtime autónomo integrado de producción o un runtime de reconciliación con el exchange.

---

# GOBIERNO DE ALCANCE

No introducir en la frontera actual:

- modificaciones o generalizaciones no autorizadas del `RiskEngine` implementado en HUESO 05-B;
- `FinalAdmission`;
- OrderIntent final;
- cambios o generalizaciones adicionales del puente persistente Reservation → Execution implementado en HUESO 05-E, salvo autorización específica;
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

**HUESO 05-E — MERGED / CLOSED — POST-MERGE VERIFIED**

Último checkpoint funcional confirmado por esta actualización:

`b9481f5690c6db6f831660da56399163218576ea`

La base documental de esta actualización es:

`b9481f5690c6db6f831660da56399163218576ea`

Ambos SHA son referencias históricas para esta actualización; el primero identifica el último checkpoint funcional confirmado y el segundo el `main` exacto usado como base documental. Ninguno representa un puntero vivo permanente de `main`.

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

- Documentation basis head: **OBSERVED — `b9481f5690c6db6f831660da56399163218576ea` (base documental histórica de esta sincronización)**
- Último functional checkpoint: **OBSERVED — `b9481f5690c6db6f831660da56399163218576ea` (HUESO 05-E; CI post-merge success)**
- Current main HEAD: **NOT STORED — QUERY GITHUB DIRECTLY**
- CI del documentation basis head / HUESO 05-E: **OBSERVED — `tests`, run `38060502389`, success, `1320 passed in 7.23s`**
- CI post-merge HUESO 05-D: **HISTORICAL — `tests`, run `38034655787`, success, `1286 passed in 8.13s`**
- CI post-merge HUESO 05-C: **HISTORICAL — `tests`, run `37881330778`, success, `1242 passed in 7.78s`**
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
- HUESO 05-C: **OBSERVED — MERGED / CLOSED**
- RiskAuthorization: **OBSERVED — IMPLEMENTED — HUESO 05-C**
- HUESO 05-D / PR #55: **OBSERVED — MERGED / CLOSED — POST-MERGE VERIFIED**
- FinancialAdmissionRequest: **OBSERVED — IMPLEMENTED**
- FinancialAdmissionBoundary: **OBSERVED — IMPLEMENTED — local authorization-enforced admission**
- Authorization fingerprint / idempotency v2: **OBSERVED — semantic SHA-256 binding; not a cryptographic signature**
- HUESO 05-D CI post-merge: **HISTORICAL — success, 1286 passed in 8.13s**
- HUESO 05-E / PR #57: **OBSERVED — MERGED / CLOSED — POST-MERGE VERIFIED**
- ReservationExecutionBridge / PreparedExecutionIntent: **OBSERVED — IMPLEMENTED — persistent local bridge**
- `SUBMISSION_STARTED`: **OBSERVED — durable local marker before adapter invocation**
- `SUBMITTED`: **OBSERVED — local adapter call returned without exception and local result was recorded; exchange acceptance/fill UNKNOWN**
- `UNKNOWN`: **OBSERVED — conservative handling; no automatic resend**
- `terms_hash` / `intent_hash`: **OBSERVED — deterministic SHA-256 consistency links, not signatures**
- Recovery / ledger projection repair: **OBSERVED — local only; external exchange reconciliation UNKNOWN**
- Autonomous integrated production runtime: **NOT DEMONSTRATED**
- FinalAdmission autónomo distinto de `FinancialAdmissionBoundary`: **NOT IMPLEMENTED**
- Reconciliation runtime con exchange: **NOT IMPLEMENTED**
- HUESO 05-E CI post-merge: **TESTED — `tests`, run `38060502389`, success, 1320 passed in 7.23s**
- FASE 1.34: **HISTORICALLY VERIFIED — E2E SUCCESS**
- SMA post-merge E2E: **UNKNOWN — no nueva ejecución real post-merge verificada**
- Indicators post-merge E2E: **UNKNOWN — los E2E disponibles son históricos/pre-merge**
