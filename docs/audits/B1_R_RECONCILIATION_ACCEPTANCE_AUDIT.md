# B1-R-R4 — Auditoría integral de reconciliación PAPER y aceptación

- **Fecha:** 2026-10-15
- **Repositorio:** `eltiootaku01-hue/bot-cripto`
- **Rama de publicación:** `main`
- **HEAD de `main` antes de publicar:** `8bbd9fc9511690db171c163b4a978a07e8aeb222`
- **Rama de implementación inspeccionada:** `feat/paper-operational-runtime-v1`
- **HEAD observado de la rama inspeccionada:** `33e81b57197ac4cae3af53730ac239ea9caa06fe`
- **Relación entre referencias:** la comparación remota mostró la rama de PAPER 7 commits por delante y 0 por detrás de `main`; merge-base `8bbd9fc9511690db171c163b4a978a07e8aeb222`.
- **PR relacionada:** [#64 — feat: add isolated SQLite-backed PAPER execution adapter](https://github.com/eltiootaku01-hue/bot-cripto/pull/64). Esta auditoría no crea ni modifica la PR.
- **Modo:** inspección estática/remota; no se ejecutaron pruebas.
- **Dictamen:** `INSUFFICIENT_EVIDENCE` sobre la completitud de la reconciliación económica. La auditoría puede orientar revisión de CEREBRO; B1-R no queda implementado ni aceptado.

## 1. Alcance, continuidad y procedencia

### Evidencia observada

- `OBSERVED` — GitHub reportó `main` en `8bbd9fc9511690db171c163b4a978a07e8aeb222`, commit de merge de la PR #63.
- `OBSERVED` — `feat/paper-operational-runtime-v1` existe y GitHub la comparó como 7 commits por delante de `main`, 0 detrás, con HEAD `33e81b57197ac4cae3af53730ac239ea9caa06fe`.
- `OBSERVED` — Los archivos PAPER y sus pruebas están en esa rama de trabajo; no están en el HEAD de `main` inspeccionado. Los SHA de blob indicados abajo corresponden a los archivos descargados de esa rama, no son SHA de commit.
- `OBSERVED` — la ruta de informe `docs/audits/B1_R_RECONCILIATION_ACCEPTANCE_AUDIT.md` no existía en `main` al comprobarla antes de publicar.
- `OBSERVED` — la documentación del adaptador dice expresamente que el flujo es local/PAPER, que el resultado `SUBMITTED` no prueba aceptación de un exchange y que la recuperación local no reconcilia un exchange.
- `INFERRED` — la brecha crítica no es la ausencia de idempotencia local del adaptador: es la ausencia de un coordinador que use un fill PAPER durable como evidencia para resolver de manera verificable los estados de reserva, binding y ledger después de un resultado ambiguo.
- `UNKNOWN` — no se consultaron ni inspeccionaron archivos de bases de datos reales de usuario; el alcance se limita al código, pruebas y documentación remotos.
- `BLOCKED` — no puede certificarse la reconciliación económica integral sin una transición y un escenario de aceptación que relacionen los cuatro componentes durables.

### Limitaciones metodológicas

`TESTED: none`. No se ejecutaron pytest, linters, verificadores de tipos, benchmarks, workflows ni fallos inyectados. Los nombres y cuerpos de pruebas fueron inspeccionados estáticamente. La evidencia histórica que aparece en la descripción de la PR #64 no se considera una ejecución de esta auditoría ni demuestra la reconciliación que aquí se exige.

## 2. Inventario de rutas y procedencia

| Ruta | Referencia inspeccionada | Evidencia relevante |
|---|---|---|
| `bot_obrero/paper_adapter.py` | `feat/paper-operational-runtime-v1`; blob `a1cf3208306cf7d8b5425fb09fd7ca3df820aa8c` | Esquema SQLite PAPER, `_validated_order`, `_load_fill`, `_submit`, `snapshot`, `close`; 457 líneas. |
| `bot_obrero/execution.py` | misma rama; blob `e51167bc21d0112b4faf4714be9add6d9fb61786` | `ExecutionBoundary.submit`, `ExecutionOrchestrator.execute`, `recover_projection`; 524 líneas. |
| `bot_obrero/reservation_execution_bridge.py` | misma rama; blob `5b4270d14c683ca606649d80b9ff18f8cc034d59` | `begin_submission`, `finish_submission`, `recover_ambiguous`, validación del binding; 715 líneas. |
| `bot_obrero/persistent_ledger.py` | misma rama; blob `501cd652d60c888a58c5aca1cba51a6a92a33306` | `idempotency_ledger`, `applied_fills`, `synchronize_projection`, `register_fill`; 160 líneas. |
| `bot_obrero/reservation.py` | misma rama; blob `5cf064011392e695133e8752b14209814846db18` | Estados de reserva, snapshots canónicos, transiciones, `consume`, `mark_unknown`, transacciones locales; 1.458 líneas. |
| `tests/test_paper_adapter.py` | misma rama; blob `43974f2bde0ee84b29430d3411434c1d4fb5ed7e` | 13 pruebas declaradas para fills, persistencia, idempotencia, snapshots y composición sintética; 323 líneas. |
| `tests/test_hueso_05e_reservation_execution_bridge.py` | misma rama; blob `228de40eefb3d3f571daffe4ea6cb33d41893c83` | 28 pruebas declaradas; incluye tests 16, 17, 23 y 27 sobre recuperación/proyección ambigua con FakeAdapter; 730 líneas. |
| `docs/PAPER_ADAPTER_V1.md` | misma rama; blob `f1e6a45b4e915c1ceb8320c627189013897b9a64` | Límites del adaptador, snapshot de cuenta, universo de activos y frontera de evidencia de cotización. |
| `bot_obrero/signal_trade_proposal_bridge.py` | rama de PAPER, ruta adicional observada en la comparación de commits | Frontera Signal → TradeProposal descrita por la PR; no es el mecanismo de reconciliación del fill. |
| `cerebro/PROJECT_STATE.md` | cambiado en la rama frente a main según comparación remota | Estado del proyecto; no se usa como sustituto del código ni como evidencia de ejecución. |

Las rutas operativas del listado que no existen en `main` fueron inspeccionadas en la rama de PAPER, no atribuidas al HEAD de `main`. No se modificó ninguno de estos archivos.

## 3. Hallazgos críticos

### H01 — Fill PAPER durable y reconciliación son dos cosas distintas

`OBSERVED` — `PaperExecutionAdapter._submit` persiste en una transacción SQLite los balances afectados, una fila en `paper_fills` y el incremento de `paper_account.state_version`. La tabla usa `client_order_id` como clave primaria y guarda `intent_hash`, `intent_json`, `account_id`, `reservation_id`, `proposal_id`, instrumento, lado, cantidad, precio, comisión, timestamp y balances posteriores.

`OBSERVED` — `ExecutionOrchestrator.recover_projection` invoca `ReservationExecutionBridge.recover_ambiguous` y sincroniza el ledger a partir del estado del binding. No consulta `PaperExecutionAdapter.snapshot()` ni busca el fill persistido. Si el binding queda `UNKNOWN` o `SUBMISSION_STARTED`, mantiene resultado desconocido y congela Murphy.

`INFERRED` — puede existir un fill económico PAPER ya aplicado mientras las proyecciones de reserva, binding y ledger continúan en UNKNOWN. El camino de recuperación inspeccionado no dispone de una transición que demuestre el fill y reconcilie esa divergencia.

### H02 — Las transacciones son locales a cada almacén

`OBSERVED` — el fill y sus balances/version se confirman en la base PAPER. El binding y la transición de reserva se escriben en la base de reservas. El ledger usa otra conexión/almacén y `synchronize_projection` hace su propia transacción.

`OBSERVED` — `recover_ambiguous` cambia el binding de `SUBMISSION_STARTED` a `UNKNOWN` y, si la reserva está ACTIVE, cambia la reserva a UNKNOWN en la transacción del almacén de reservas. No consulta la base PAPER.

`INFERRED` — no existe atomicidad global entre fill PAPER, reserva, binding y ledger. La recuperación global necesita un protocolo idempotente y verificable; no puede prometerse una transacción atómica distribuida que el código no implementa.

### H03 — La prevención de reenvío es útil, pero insuficiente para la reconciliación

`OBSERVED` — la frontera de ejecución consume una clave de despacho antes de invocar el adaptador; la recuperación del bridge no envía órdenes. El adaptador PAPER también vuelve a comprobar `client_order_id` bajo `BEGIN IMMEDIATE` y devuelve `PAPER_ALREADY_FILLED` para una intención idéntica, o bloquea si el mismo ID tiene términos diferentes.

`OBSERVED` — el test 23 simula un fallo después del retorno del adaptador mediante un FakeAdapter, cierra/reabre el almacén de reservas y comprueba UNKNOWN, cero envíos del adaptador de recuperación y ledger UNKNOWN.

`INFERRED` — estos controles reducen el riesgo de duplicación de envío, pero no demuestran la recuperación del fill PAPER real: el test 23 no reconstruye ni consulta una base PAPER que contenga el fill ya aplicado.

### H04 — El snapshot comprueba coherencia estructural, no prueba completa del historial económico

`OBSERVED` — `snapshot()` lee account, universo de activos, balances y fills en una transacción de lectura; marca completeness en función del universo, conjunto de balances, valores finitos/no negativos y relación `state_version == len(fills)`.

`INFERRED` — esa condición no reproduce aritméticamente todo el historial desde balances iniciales para probar que cada balance actual es el resultado exacto de todos los fills, comisiones y activos. Un snapshot estructuralmente coherente no equivale a una reconciliación histórica independiente.

### H05 — El ledger es una proyección mutable

`OBSERVED` — `SQLiteIdempotencyLedger.synchronize_projection` es idempotente para un client ID/hash coincidente, rechaza hash discordante e inserta o actualiza el resultado. `applied_fills` registra `fill_id → client_order_id`, pero no está coordinado con `paper_fills`.

`INFERRED` — la reparación del resultado del ledger no prueba por sí sola que un efecto económico se haya aplicado una vez, ni conserva necesariamente una historia append-only de cada cambio de proyección.

## 4. Tabla de transiciones reales

| Componente | Estado/operación real observada | Efecto durable | Lo que NO demuestra |
|---|---|---|---|
| PAPER adapter | `_submit` → `PAPER_FILLED` / `PAPER_ALREADY_FILLED` | Fill + balances afectados + versión en una transacción SQLite | Reconciliación de reserva, binding o ledger. |
| PAPER adapter | mismo `client_order_id` e intención exacta | Devuelve el fill existente sin volver a aplicar balances | Recuperación coordinada entre almacenes. |
| PAPER adapter | mismo ID, intención diferente | Lanza conflicto | Resolución automática del conflicto; correctamente no lo normaliza. |
| Binding | `PREPARED → SUBMISSION_STARTED` | Marca durable previo a llamar al adaptador | Que el adaptador llegó a ejecutarse o que existe fill. |
| Binding | `SUBMISSION_STARTED → SUBMITTED/UNKNOWN/BLOCKED` mediante `finish_submission` | Proyección local de resultado | Que `SUBMITTED` significa fill verificado o reconciliado. |
| Binding | `recover_ambiguous`: `SUBMISSION_STARTED → UNKNOWN` | Estado desconocido y error de recuperación; puede marcar reserva UNKNOWN en la misma base | Consulta del fill PAPER, prueba de ausencia/presencia de fill o reconciliación económica. |
| Reserva | `ACTIVE → UNKNOWN` en recuperación ambigua | Estado de reserva y fila de transición | Consumo económico ni liberación segura. |
| Reserva | `consume(amount, evidence)` | Actualiza consumo/restante/estado y registra transición en transacción local | Que la evidencia procede de un fill PAPER válido, salvo que el consumidor lo pruebe. |
| Ledger | `register/mark_result/synchronize_projection` | Estado de resultado por client ID/hash | Atomicidad con PAPER/reserva/binding ni historia económica completa. |
| Ledger | `register_fill(fill_id, client_order_id)` | Identidad idempotente en `applied_fills` | Que coincide con la fila PAPER; no existe coordinación observada. |
| Orquestador | `recover_projection` | Recupera proyección del ledger desde binding | Reconciliación del efecto económico PAPER. |
| Resultado de ejecución | `ExecutionDecision(False, "UNKNOWN")` | Señal conservadora para el llamador | Estado económico reconciliado. |

### Estados contractuales que NO se consideran existentes

No se localizó un estado durable compartido denominado `RECONCILED`, `FILL_CONFIRMED` o equivalente. Estos nombres pueden servir como conceptos de diseño, pero no son transiciones implementadas. Cualquier nuevo estado público requiere `REQUIRES CEREBRO REVIEW`.

## 5. Matriz de trazabilidad R01–R12

Las clasificaciones evalúan la evidencia de implementación para el requisito integral de reconciliación, no la calidad aislada de cada componente.

| Req. | Componente/ruta y método | Evidencia de implementación | Pruebas existentes identificadas (inspección estática) | Cobertura | Brecha y riesgo | Prioridad | Dependencias | Criterio objetivo de cierre |
|---|---|---|---|---|---|---|---|---|
| R01 | `paper_adapter.py:_validated_order/_submit`; `reservation_execution_bridge.py:_verify_binding`; `reservation.py` trade-terms snapshot | PAPER fill stores client_order_id, intent_hash/JSON, account_id, reservation_id, proposal_id, symbol, side, quantity, price, fee; bridge binds reservation/client ID/hash and canonical terms. | Tests 10, 20, 21, 25 in `test_hueso_05e_reservation_execution_bridge.py`; PAPER tests do not mutate each persisted identity dimension across restart. | PARTIAL | Identity is strong for order intent, but there is no recovery lookup that joins a fill to reservation+binding+ledger and validates every cross-store identity before resolving UNKNOWN. | A valid fill may exist while the canonical execution projection remains UNKNOWN; mismatched account/instrument/side/quantity/restrictions are not reconciled as one unit. | HIGH | Paper adapter + reservation bridge + ledger, without changing public admission contracts. | A recovery validator returns exactly one fill and proves every identity/hash/economic field matches durable canonical terms; all mismatches block. |
| R02 | `paper_adapter.py:_load_fill`, `snapshot`; `execution.py:recover_projection`; bridge `recover_ambiguous` | Adapter can find a fill by client_order_id and snapshot all fills; execution recovery only normalizes SUBMISSION_STARTED to UNKNOWN and never queries PAPER fills. | PAPER test `test_buy_fill_uses_decimal_costs_and_persists_across_restart`; bridge tests 16, 23, 27. | PARTIAL | There is no reconciliation API classifying zero/one/multiple/discordant fill candidates, inaccessible storage, or incomplete evidence. | A durable fill is not used to resolve ambiguity; absence or store failure does not produce a richer typed recovery decision. | CRITICAL | Read-only PAPER evidence query plus explicit typed classification. | Test zero, exactly one, multiple, discordant, inaccessible, and incomplete evidence; only one exact match can proceed. |
| R03 | `paper_adapter.py:_submit/snapshot`; `paper_account`, `paper_assets`, `paper_balances`, `paper_fills` SQLite tables | Fill, two affected balances, and account state_version/update timestamp are committed together; snapshot returns assets, balances, fills and a completeness marker. | PAPER tests 1, 2, 9–13; source inspection only in this audit. | PARTIAL | Completeness checks asset/balance sets and state_version == fill count; it does not independently replay the complete fill history to prove all balances arithmetically derive from it, and not all fill fields are exposed in snapshot. | A coherent snapshot can still be semantically inconsistent with historical arithmetic or external projection records. | HIGH | Existing snapshot/fill rows and Decimal calculations. | Replay all durable fills from immutable initial balances and compare exact Decimal results, fee treatment, asset universe, version and timestamps. |
| R04 | `paper_adapter.py` separate SQLite DB; `reservation.py:_apply_transition`; bridge `finish_submission/recover_ambiguous`; `persistent_ledger.py:synchronize_projection` | Each store has local transactions. Bridge recovery updates binding and ACTIVE reservation in the reservation-store transaction; ledger projection is synchronized separately. PAPER DB is separate. | Bridge tests 2, 16, 17, 23, 27; ledger methods inspected, not executed. | MISSING | No transaction or protocol coordinates the PAPER fill/balances/version with reservation, binding, and ledger; current recovery does not read the PAPER database. | Partial commit can leave PAPER economics filled while reservation/binding/ledger remain UNKNOWN or divergent. | CRITICAL | Reuse existing DBs; define a durable, retry-safe reconciliation protocol with persisted evidence and monotonic steps. | Inject failure at each cross-store boundary in future tests; repeated recovery converges without duplicate effects and never promotes unresolved evidence. |
| R05 | `execution.py:ExecutionBoundary.submit`, `ExecutionOrchestrator.execute/recover_projection`; bridge `recover_ambiguous`; `paper_adapter.py:_submit` | Boundary consumes a single-use in-memory dispatch key before adapter call; persisted SUBMISSION_STARTED recovers to UNKNOWN; recovery projection never invokes adapter. PAPER same-client-order fill is idempotently returned if exact intent matches. | Bridge tests 15, 16, 23, 27; PAPER tests 1, 10. | PARTIAL | FakeAdapter tests prove no resend in this orchestrator path; they do not prove crash/restart recovery consults a real durable PAPER fill before making a reconciliation decision. | No resend is a strong safety property, but it currently strands known local economics in UNKNOWN rather than reconciling them. | HIGH | Existing no-resend guard and durable client_order_id uniqueness. | Future real PaperExecutionAdapter restart test: exactly one submit/fill, recovery reads the existing fill, adapter submit count remains unchanged. |
| R06 | `paper_adapter.py` primary key on `paper_fills.client_order_id`; `persistent_ledger.py:register_fill/register/synchronize_projection`; bridge recovery | PAPER transaction serializes via BEGIN IMMEDIATE and rechecks client_order_id before writing balances/fill/version; ledger has primary keys for order and applied fill IDs; projection synchronization is identity/hash guarded. | PAPER tests 1, 10, 11; bridge tests 11, 16, 17, 23, 27. | PARTIAL | PAPER fill idempotency is per client_order_id, while ledger applied_fills is a separate table and not linked to PAPER's fill record by a recovery transaction. No unified exactly-once economic application across all stores. | Repeated local adapter invocation is guarded, but multi-store recovery can still leave projections divergent; applied_fills is not the PAPER fill identity store. | CRITICAL | Existing idempotency constraints and canonical fill identity. | Run repeat/restart recovery tests proving one fill, one balance delta, one reservation consumption and stable ledger history. |
| R07 | `PaperMarketQuote`, `_fresh_quote`, `_validated_order`; bridge `_verify_binding/recover_ambiguous`; `recover_projection` | Invalid/unknown/stale/future quotes, malformed intent, identity/hash conflicts, and unknown submission outcome fail closed; ambiguous binding becomes UNKNOWN and Murphy freezes. | PAPER tests 3–5, 8, 12–13; bridge tests 7–10, 15–16, 21, 23, 25–28. | PARTIAL | Fail-closed handling exists for submission/admission, but recovery does not distinguish contradictory PAPER evidence from absent evidence because it never queries that evidence. | Unknown remains unknown (good); there is no safe transition to reconciled success. | HIGH | Reuse typed bridge errors and UNKNOWN/BLOCKED behavior. | Every contradictory, missing or inaccessible evidence scenario remains UNKNOWN/BLOCKED with no economic or dispatch mutation. |
| R08 | SQLite PAPER adapter, SQLite reservation store, SQLite idempotency ledger; `recover_projection` | The stores are persistent and adapter restart tests cover balances/fills; bridge test 23 reopens reservation store and uses a new FakeAdapter. | PAPER test 1; bridge tests 3, 16, 23. | PARTIAL | Restart recovery reconstructs reservation projection and ledger state but does not reconstruct/reconcile from PAPER adapter's persisted fill evidence. The critical restart test uses FakeAdapter, not durable PaperExecutionAdapter. | Restart preserves ambiguity even if the PAPER database has the exact fill. | CRITICAL | Open existing PAPER DB read-only for evidence; reconstruct all existing stores with canonical paths/configuration. | Close all connections after durable fill and before SUBMITTED, rebuild all components, reconcile once and repeat safely. |
| R09 | PAPER `_submit` transaction; bridge `begin_submission/finish_submission/recover_ambiguous`; reservation `_apply_transition`; ledger transactions | PAPER balances, fill row and account version share one SQLite transaction. Bridge binding+reservation transition share reservation-store transaction. Ledger mutations are separate SQLite transactions. | PAPER test 10; bridge tests 2, 13–17, 23, 27; no failure injection executed in this audit. | PARTIAL | No atomicity spans all stores. Reservation `_apply_transition` reads current reservation before opening its write context; cross-database failure windows are unresolved. No crash injection at each reconciliation write. | A durable PAPER commit followed by projection failure is a real partial-commit state; no coordinated recovery protocol exists. | CRITICAL | Use existing stores and make reconciliation steps monotonic/idempotent with durable evidence checkpoints; avoid pretending cross-file atomicity. | Fault-injection matrix at each commit boundary demonstrates deterministic resume, no duplicate debit/credit/consume, and safe UNKNOWN on uncertainty. |
| R10 | `reservation_transitions`; bridge `last_error`; `paper_fills`; ledger rows | Reservation transitions append history; bridge stores current state and last_error; PAPER fill row preserves intent JSON/hash and economics; ledger stores current result and applied fill IDs. | Bridge tests 2, 20, 22–23; PAPER test 1; static inspection. | PARTIAL | Ledger result is mutable in place; no append-only reconciliation history ties prior UNKNOWN, the discovered fill evidence, the validation decision, and final projection together. Snapshot omits intent_json from returned fill list. | Recovery cannot present a complete provenance chain across stores; current-state overwrite may obscure projection evolution. | MEDIUM | Retain existing histories and add minimal append-only reconciliation evidence only if CEREBRO approves. | Assert original fill, binding, reservation transitions and ledger state changes remain traceable with evidence references and timestamps. |
| R11 | `ReservationState`; bridge persisted binding states; `ExecutionDecision` reasons; PAPER fill `status` | Real reservation states: ACTIVE, PARTIALLY_CONSUMED, CONSUMED, RELEASED, UNKNOWN. Binding uses PREPARED, SUBMISSION_STARTED, SUBMITTED, UNKNOWN, BLOCKED; adapter returns PAPER_FILLED/PAPER_ALREADY_FILLED. ExecutionDecision is an outcome object, not a persisted state. | Bridge tests 5, 15–17, 23, 27; PAPER tests 1, 10. | PARTIAL | No persisted `RECONCILED`/`FILL_CONFIRMED` transition or common reconciliation state exists. `SUBMITTED` means local adapter call returned, not a reconciled economic fill or exchange acceptance. | Consumers could over-interpret SUBMITTED or UNKNOWN; explicit semantic separation remains essential. | HIGH | Keep current states; if a new state is needed, obtain CEREBRO review before changing contracts. | Document and test explicit meanings for submit result, durable PAPER fill, projection repaired, reconciled and blocked. |
| R12 | PAPER adapter validates terms and account universe; bridge validates binding/hash; reservation consumes/releases with transition evidence | No silent reservation consumption is performed by `recover_ambiguous`; recovery marks active reservation UNKNOWN. PAPER account initialization rejects changed universe/configuration and incomplete balances. | PAPER tests 8–13; bridge tests 7–10, 16, 23. | PARTIAL | No PAPER-fill-backed reservation consumption exists, which avoids silent consumption but also means a genuine fill can remain unmatched. Snapshot completeness is structural, not full-history proof. | Unsafe repair is not observed in the examined recovery path; complete reconciliation is missing rather than silently performed. | HIGH | Preserve no-repair default; only reconcile after strict durable identity and arithmetic validation. | Contradiction or incomplete proof causes no balance/reservation/ledger repair; a unique validated fill can drive one auditable idempotent projection. |

## 6. Matriz de casos de aceptación A01–G06

Todos los casos siguientes son **diseños futuros; no se ejecutaron**. La columna de cobertura se refiere a correspondencia con pruebas existentes inspeccionadas, no a un resultado de ejecución. `0 recovery` significa que la recuperación no debe invocar el adaptador. Si la evidencia no es completa, el número de fills puede ser desconocido; no debe interpretarse la ausencia de una fila visible como prueba de que no hubo efecto.

| ID | Precondiciones y estado persistente inicial | Acción/punto de fallo | Evidencia durable requerida | Resultado esperado | Reserva esperada | Binding esperado | Ledger esperado | Fills esperados | Envíos esperados | Invariante económico | Prueba existente relacionada | Cobertura | Brecha | Criterio futuro de aceptación |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A01 | Fill committed; reservation binding still SUBMISSION_STARTED/UNKNOWN; no SUBMITTED result | Restart after PAPER commit but before bridge finish | Query durable PAPER fills by client_order_id; no submit | Exactly one fill + matching hash and terms required | UNKNOWN until identity and economics are verified; then proposed reconciled outcome | UNKNOWN until verified; consume only by proven economic delta | SUBMISSION_STARTED/UNKNOWN until projection update | ORDER_RESULT_UNKNOWN until verified | 1 | 1 total, 0 recovery submits | One debit/credit and one account-version increment | Bridge test 23 (FakeAdapter only); PAPER test 1 (separate restart path) | PARTIAL | No join between durable PAPER fill and ambiguous binding |
| A02 | SUBMISSION_STARTED; no fill row | Recovery query returns zero candidates | No fill evidence | No fill found is not proof that dispatch never happened if store is incomplete/unavailable | UNKNOWN/BLOCKED; never infer no execution | UNKNOWN | UNKNOWN | ORDER_RESULT_UNKNOWN | 0 observed; absence alone not conclusive | 1 historical, 0 recovery | No balances changed by recovery | Bridge tests 16, 23 use FakeAdapter; no PAPER no-fill recovery test | MISSING | No typed distinction between definitive zero and incomplete query |
| A03 | One durable fill, all identity/economic fields match | Restart then inspect full evidence | No resend | One exact fill, account version/history coherent | Proposed reconcile once | Consume only verified reservation delta | Proposed projection to reconciled semantic outcome; existing state has no such state | Proposed projection synchronized idempotently | 1 | 1 historical, 0 recovery | Replayed Decimal effects equal stored balances | PAPER tests 1, 10; bridge 23 is not real fill | PARTIAL | No integrated reconciliation operation |
| A04 | One order has multiple candidate fills or duplicate/corrupt candidates | Recovery query yields >1 candidate | No resend; stop | Multiplicity itself is contradictory under one-fill-per-client-order contract | BLOCKED/UNKNOWN | No mutation | No mutation | No promotion | Ambiguous; must not assume 1 | 0 recovery | No mutation | No direct test | MISSING | No candidate classifier or multi-candidate scenario |
| A05 | PAPER DB missing, locked, corrupt or inaccessible | Recovery evidence read raises/fails | No resend | Evidence store unavailable | UNKNOWN/BLOCKED; distinguish from zero fills | No mutation | No mutation | Remain UNKNOWN/BLOCKED | Unknown | 0 recovery | No repair | No direct test | MISSING | No durable evidence-read error contract |
| A06 | Fill row lacks required intent/economic/provenance fields or snapshot incomplete | Recovery detects missing field / completeness UNKNOWN | No resend | Partial evidence only | UNKNOWN/BLOCKED | No mutation | No mutation | No promotion | Unknown | 0 recovery | No repair | PAPER test 13 checks legacy universe; no reconciliation test | PARTIAL | Snapshot structural completeness does not validate every field needed for cross-store identity |
| B01 | Persisted fill client_order_id differs from binding | Cross-record identity validation | No resend | Discordant primary identity | BLOCKED | No mutation | No mutation | No promotion | Do not match candidate | 0 recovery | No repair | Bridge tests 10, 25 cover intent/hash conflict, not PAPER fill join | PARTIAL | No cross-store client ID comparison |
| B02 | Fill reservation_id differs from canonical reservation | Cross-record validation | No resend | Reservation mismatch | BLOCKED | No mutation | No mutation | No promotion | No acceptance | 0 recovery | No repair | No direct test | MISSING | Fill stores reservation_id but recovery does not validate it against binding |
| B03 | Fill proposal_id differs from canonical trade terms | Cross-record validation | No resend | Proposal mismatch | BLOCKED | No mutation | No mutation | No promotion | No acceptance | 0 recovery | No repair | Bridge tests 8/20 cover snapshot conflicts, not fill comparison | PARTIAL | No fill-to-proposal reconciliation |
| B04 | intent_hash or canonical intent JSON differs | Recompute hash and compare canonical payload | No resend | Hash/intent mismatch | BLOCKED | No mutation | No mutation | No promotion | No acceptance | 0 recovery | No repair | PAPER test 10; bridge tests 7–10, 21, 25 | PARTIAL | Existing checks operate within each store, not across PAPER and binding |
| B05 | Account/instrument/side/quantity/price policy/max spend differs | Compare fill fields against immutable reservation terms and account | No resend | Economic term mismatch | BLOCKED | No mutation | No mutation | No promotion | No acceptance | 0 recovery | No repair | PAPER tests 5–8; bridge tests 8–9 | PARTIAL | No comprehensive cross-store economic comparison |
| B06 | Fill identity absent or duplicated/reused for another order | Inspect client_order_id uniqueness and any stable fill identity | No resend | Missing identity or duplicate association | BLOCKED/UNKNOWN | No mutation | No mutation | No promotion | Unknown | 0 recovery | No repair | Ledger tests in source; no PAPER fill identity recovery test | PARTIAL | PAPER primary key is client_order_id, not an independent fill_id; ledger applied_fills is disconnected |
| C01 | One fill and full initial history/account snapshot coherent | Replay all fills and recompute exact balances/version | No resend | Complete history with exact arithmetic | Proposed reconcile once | Consume exact authorized reservation delta only | Projection update only after validation | Projection update only after validation | 1 | 1 historical, 0 recovery | Decimal replay equals balances; version increments once per fill | PAPER tests 1, 2, 9–11 | PARTIAL | No full-history arithmetic replay in snapshot/recovery |
| C02 | Stored balances do not equal replayed fills | Recompute from initial balances | No resend | Arithmetic discrepancy | BLOCKED | No mutation | No mutation | No mutation | Existing count retained; no new fill | 0 recovery | Do not repair balances silently | No direct test | MISSING | Snapshot coherence is not a replay proof |
| C03 | Account version differs from fill count or expected version | Compare account version, update cutoff, fill sequence | No resend | Version contradiction | BLOCKED/UNKNOWN | No mutation | No mutation | No promotion | Unknown | 0 recovery | No repair | PAPER test 10 checks atomic increment, not corruption recovery | PARTIAL | No recovery contract for version mismatch |
| C04 | Legacy/incomplete fill history | Check initial asset universe and full-history provenance | No resend | History incomplete/legacy | UNKNOWN/BLOCKED | No mutation | No mutation | No promotion | Unknown | 0 recovery | No inferred history | PAPER test 13 rejects legacy unknown universe | PARTIAL | Legacy history handling not a general reconciliation classifier |
| C05 | paper_fills and ledger applied_fills disagree | Compare client order and stable fill identity across stores | No resend | Projection mismatch | UNKNOWN/BLOCKED | No mutation | No mutation | No promotion | Existing durable fill unchanged | 0 recovery | No second effect | Ledger table exists; no PAPER integration test | MISSING | No mapping from PAPER client-order fill row to ledger applied_fills identity |
| C06 | Available evidence cannot distinguish completed fill from partial/final result | Validate adapter contract and status semantics | No resend | Status/fill quantity evidence insufficient | UNKNOWN/BLOCKED | No mutation | No mutation | No promotion | Unknown | 0 recovery | No assumed completion | No direct test; PAPER adapter models immediate full fill | MISSING | PAPER schema assumes immediate fill and has no partial-fill state |
| D01 | Reservation ACTIVE; binding UNKNOWN; one durable matching fill | Read all stores and validate identity/economics | No resend | Unique matching fill | Proposed reconcile | Consume exact delta once after validation | Proposed reconciled semantic result | Synchronize projection after evidence checkpoint | 1 | 1 historical, 0 recovery | All projections converge; no extra debit | Bridge test 23 yields UNKNOWN with FakeAdapter; no real fill test | PARTIAL | This is the critical unimplemented transition |
| D02 | Reservation UNKNOWN; binding SUBMISSION_STARTED | Recovery transaction normalizes marker | No resend | No fill evidence yet | UNKNOWN | Remain UNKNOWN | UNKNOWN | ORDER_RESULT_UNKNOWN | 0 or unknown | 0 recovery | No economic mutation | Bridge tests 16, 23 | PARTIAL | Current behavior is safe but cannot reconcile known fill |
| D03 | Binding SUBMITTED; no conclusive fill evidence | Inspect binding and PAPER store | No resend | SUBMITTED alone is not a fill | UNKNOWN/BLOCKED for economic claim; retain binding history | Do not consume without evidence | Keep SUBMITTED history; no claim of reconciliation | Do not claim reconciled | Unknown | 0 recovery | No economic assumptions | Bridge test 17 is projection repair, not fill proof | MISSING | SUBMITTED is local adapter return, not durable fill verification |
| D04 | Reservation CONSUMED but no adequate fill evidence | Compare consumed delta with verified economic effect | No resend | Reservation transition lacks sufficient matching evidence | BLOCKED; do not silently reverse or consume again | Preserve existing state and flag conflict | No mutation | No promotion | Unknown | 0 recovery | No repair without authorized evidence | Reservation transition tests exist; no reconciliation test | MISSING | No cross-check against PAPER fill economics |
| D05 | Ledger intent/result conflicts with binding | Compare ledger hash/result to binding payload/state | No resend | Identity/hash or state mismatch | BLOCKED/UNKNOWN | No mutation | No overwrite that hides conflict | No overwrite that hides conflict | Existing fill unchanged | 0 recovery | No silent projection normalization | Bridge tests 17, 21, 23; ledger synchronize code inspected | PARTIAL | synchronize_projection updates result when hash matches and does not preserve append-only reconciliation history |
| D06 | Reservation, binding, ledger and one PAPER fill all match | Validate all invariants before mutation | No resend | Unique complete evidence | Proposed reconciled outcome | Consume only if not already consumed and delta matches | Projection records validated state | Idempotently synchronized | 1 | 1 historical, 0 recovery | All stores agree and repeated run is a no-op | No integrated test | MISSING | No coordinator ties all stores together |
| E01 | Failure before PAPER fill transaction commits | Injected error before commit in future test | No recovery submit until ambiguity resolved | No committed fill; store completeness required | UNKNOWN if dispatch marker exists; otherwise safe rejection according to persisted stage | No consumption | UNKNOWN/BLOCKED according to persisted stage | UNKNOWN/BLOCKED | 0 committed fills expected | 0 recovery sends | No balance/version change | PAPER transaction rollback source inspected; no failure test run | PARTIAL | No end-to-end crash test around PAPER commit |
| E02 | PAPER fill committed; bridge finish fails | Injected failure after adapter returns | Restart and read PAPER evidence; never submit | One durable fill expected | Proposed reconcile if unique/valid; otherwise UNKNOWN | Consume only after proof | Reconcile after proof | Update after proof | 1 | 1 historical, 0 recovery | Exactly one effect/version increment | Bridge test 23 (FakeAdapter); PAPER transaction separate | PARTIAL | The critical real PAPER scenario is absent |
| E03 | Failure during reservation update | Inject failure in reservation transaction | No resend | PAPER evidence remains authoritative only after validation | UNKNOWN/BLOCKED then retry idempotently | Transaction must rollback both row and transition | No mutation or resumable step | No mutation until consistent | 1 if prior commit | 0 recovery | No double consume; history consistent | Reservation `_apply_transition` transaction inspected; no injected test | PARTIAL | Cross-store checkpoint and retry semantics absent |
| E04 | Failure during binding update | Inject failure in binding write/commit | No resend | PAPER fill remains durable | UNKNOWN/BLOCKED; retry from durable evidence | No duplicate consumption | Binding transaction rollback; retain previous state | Ledger remains unknown until safe update | 1 if prior commit | 0 recovery | No duplicate effect | Bridge methods use transactions; no targeted fault test | PARTIAL | No integrated recovery checkpoint |
| E05 | Failure during ledger update | Inject ledger synchronize failure | No resend | PAPER fill and reservation/binding may already be durable | UNKNOWN/BLOCKED until projection repaired | No second consume | Preserve binding evidence | Ledger stays stale/unknown; retry idempotently | 1 | 0 recovery | No duplicate effect; projection can converge | Bridge tests 16–17 show ledger projection repair in fake flow | PARTIAL | Ledger is separate DB; no global atomicity |
| E06 | Process restarts after any partial cross-store update | Close all stores and reconstruct from durable files | Recovery reads each store; no adapter invocation | Durable evidence and each store's version/history | Resume only from proven checkpoint; otherwise UNKNOWN/BLOCKED | Never infer from memory | Use durable binding/transitions | Use durable ledger rows | No new fill | 0 recovery | No duplicate balances/consumption/history | Bridge test 23 only tests FakeAdapter restart | PARTIAL | No full PAPER DB + all projections restart scenario |
| E07 | Evidence store fails during recovery | Inject read/lock/corruption error | No resend | No trustworthy evidence | UNKNOWN/BLOCKED | No mutation | No mutation | No promotion | Unknown | 0 recovery | No repair | No direct test | MISSING | No evidence-store failure handling in recovery path |
| F01 | First recovery; one exact fill and all invariants hold | Run future reconciler once | Read-only evidence first; no submit | One unique matching fill | Proposed reconciled outcome | Apply exact delta once | Update once | Update once | 1 | 1 historical, 0 recovery | One economic effect | No integrated test | MISSING | No reconciliation method/state |
| F02 | Same unchanged evidence recovered again | Run reconciler second time | No submit | Same fill identity/hash/version | Same reconciled result/no-op | No second consume | No new contradictory transition | No duplicate projection history | 1 | 1 historical, 0 recovery | Balances and version unchanged | PAPER test 1 tests adapter duplicate; not coordinator | PARTIAL | Idempotency does not cover cross-store recovery |
| F03 | Multiple process restarts and repeated recovery | Reopen all durable DBs multiple times | No submit | Same persisted evidence on each restart | Stable reconciled or stable blocked outcome | No repeated consumption | No state regression | No result regression | 1 | 1 historical, 0 recovery | Same hashes, balances, version, histories | Bridge test 23 one restart only; FakeAdapter | PARTIAL | No multiple-restart real PAPER test |
| F04 | Ambiguous prior send and matching/unknown PAPER result | Recovery path invoked | Must not call adapter.submit | Persisted marker and PAPER evidence | Reconcile only via read path; else UNKNOWN | No mutation without proof | No new submission marker | No send status synthesized | 0 new fills | 0 recovery submits | Adapter invocation counter unchanged | Bridge tests 16, 23, 27 FakeAdapter | PARTIAL | No real PAPER adapter spy/reopen test |
| F05 | Prior fill already affected balances | Repeat recovery | No submit | Stored balances/fill/version | Stable outcome | No second delta | No duplicate transition except explicitly audited projection checkpoint | Idempotent ledger update | 1 | 0 recovery | Balances/version exactly unchanged | PAPER test 10 atomic version; no recovery test | PARTIAL | No cross-store exactly-once effect proof |
| F06 | Original fill/binding/reservation transitions and errors exist | Reconcile and repeat | No submit | Original IDs/timestamps/hash/error preserved | Append traceable reconciliation evidence | No rewrite of original consumption evidence | No destructive overwrite | No loss of prior UNKNOWN | 1 | 0 recovery | History append-only or prior states reconstructible | Bridge test 23 checks current UNKNOWN only | PARTIAL | No joined audit history across stores |
| G01 | Any contradictory identity/economics/version/history | Run validator | No submit | Contradiction found | BLOCKED | No mutation | No mutation | No promotion | Existing rows only | 0 recovery | No repair | Bridge tests 8, 21, 25 cover adjacent conflicts | PARTIAL | No contradictions across PAPER fill and other stores |
| G02 | Cannot verify store completeness or candidate uniqueness | Run validator | No submit | Incomplete/UNKNOWN evidence | UNKNOWN/BLOCKED | No mutation | No mutation | No promotion | Unknown | 0 recovery | No inferred success | PAPER test 13 adjacent; no reconciliation test | PARTIAL | No typed completeness gate for recovery |
| G03 | Balances/reservation/ledger disagree | Run recovery | No submit | Insufficient proof for compensation | BLOCKED; no silent repair | No consume/release/rollback automatically | No overwrite | No overwrite | Existing fill count unchanged | 0 recovery | No economic changes | Source shows conservative UNKNOWN; no integrated tests | PARTIAL | No cross-store reconciliation protocol |
| G04 | Recovery invoked after SUBMISSION_STARTED/UNKNOWN | Instrument adapter spy | Read-only path only | Persisted binding and fill evidence | Outcome from evidence only | As validated | As validated | As validated | No new fill | 0 recovery calls | No external effect | Bridge tests 16, 23, 27 use FakeAdapter | PARTIAL | Real Paper adapter not used in restart test |
| G05 | Normal PAPER order with valid proposal/quote and reservation | Execute ordinary path | Exactly one submit | Canonical persisted intent and fresh quote | Existing local submit result semantics retained | Existing reservation semantics unchanged unless separately reconciled | SUBMITTED means local adapter returned | Ledger records local result | 1 fill for successful immediate fill | 1 send | Decimal balances/version change once | PAPER tests 1–12; bridge test 24 synthetic flow | PARTIAL | Normal flow covered in source/test design but no tests run here; integrated reconciliation scope distinct |
| G06 | Any recovery path considers ambiguous state as permission to execute | Invoke recovery with UNKNOWN/SUBMISSION_STARTED | Must not submit or reauthorize | Ambiguous marker never becomes execution authority | UNKNOWN/BLOCKED unless exact fill proof reconciles existing effect | No mutation absent proof | No transition to SUBMITTED based solely on absence of error | No promotion | No new fill | 0 recovery sends | No new economic effect | Bridge tests 16, 23, 27 | PARTIAL | Existing guard protects dispatch; no explicit integrated PAPER recovery contract |

## 7. Escenario integral crítico — diseño futuro

**Clasificación obligatoria: `NOT_EXECUTED — DESIGN ONLY`.**

1. Construir una propuesta canónica válida y persistir la reserva, autorización, snapshot de términos y binding mediante las fronteras existentes.
2. Preparar el intent desde el snapshot persistido; verificar `client_order_id`, `reservation_id`, `proposal_id`, hash canónico, cuenta PAPER, instrumento, lado, cantidad, política de precio y presupuesto.
3. Enviar una sola vez a `PaperExecutionAdapter` con una cotización pública explícita, fresca y temporalmente válida.
4. Confirmar por lectura durable que el adaptador ha confirmado una transacción con exactamente un fill, los balances posteriores y la versión de cuenta incrementada. El punto de fallo se inyecta después del commit PAPER y antes de que `finish_submission(... outcome="SUBMITTED")` pueda confirmar el resultado.
5. Cerrar todas las conexiones de PAPER, reserva, binding y ledger. No reutilizar objetos en memoria del primer proceso.
6. Reconstruir los componentes desde las mismas rutas persistentes y configuración canónica; no enviar durante la construcción ni la recuperación.
7. Consultar el almacén PAPER de forma explícita y tratar por separado store inaccesible, snapshot incompleto, cero candidatos, un candidato y múltiples candidatos.
8. Para el caso positivo, validar una única identidad coincidente y comparar hash/JSON de intent, cuenta, reserva, propuesta, instrumento, lado, cantidad, precio/fees y evidencia durable. Reproducir los balances desde el universo e historial inicial, comprobando versión/corte temporal.
9. Solo después de la validación, ejecutar una reconciliación coordinada y reintentable que vincule evidencia de fill, reserva, binding y ledger. La operación debe distinguir entre proyección reparada y resultado económico reconciliado. La semántica exacta del nuevo resultado necesita aprobación de CEREBRO.
10. Cerrar y reconstruir todo por segunda vez. La segunda recuperación debe ser un no-op económico: no nuevo fill, no segundo consumo, no nuevo débito/crédito, no nueva llamada al adaptador y sin regresión de estado.
11. Comparar al final balances exactos `Decimal`, universo de activos, versión, historial de fills, transiciones de reserva, binding/hash, ledger/applied_fills, timestamps y errores originales.

### Capacidades reutilizables

- Fill PAPER y balances/version persisten en una transacción local.
- Intent canónico e identidad/hash de reserva son durables.
- La recuperación del binding es conservadora y no reenvía.
- La reserva registra transiciones y el ledger tiene operaciones idempotentes por identidad/hash.
- Las pruebas existentes ofrecen fixtures y escenarios de fallo con FakeAdapter.

### Capacidades no demostradas

- Recuperación que consulte el fill PAPER real después de reiniciar.
- Correlación/verificación entre fill PAPER, reserva, binding y ledger.
- Replay aritmético completo de todo el historial.
- Coordinación idempotente de los cuatro almacenes y sus fallos parciales.
- Reconciliación repetida tras varios reinicios sin ningún efecto adicional.

## 8. Propuesta de cambio mínimo (no implementada)

### Brecha raíz

No existe una ruta de recuperación que consulte evidencia PAPER durable y, tras validar su identidad/economía, la utilice para reconciliar reserva, binding y ledger. La recuperación actual solo convierte una marca ambigua en UNKNOWN y repara la proyección del ledger desde el binding.

### Orden de preferencia: REUTILIZAR > ADAPTAR > CREAR

1. **Reutilizar** `PaperExecutionAdapter.snapshot()`, `_load_fill`, los términos canónicos de reserva, `ReservationExecutionBridge`, las transacciones SQLite existentes, `ReservationTransitionEvidence` y `SQLiteIdempotencyLedger.synchronize_projection`. No exponer `_load_fill` como API de reconciliación sin revisión; preferir un método público de lectura/verificación.
2. **Adaptar** la recuperación para una fase de inspección PAPER estrictamente de solo lectura que devuelva una clasificación tipada: evidencia completa sin fill, único fill coincidente, conflicto/múltiples candidatos, evidencia incompleta o store no disponible. La ausencia de fill solo será concluyente si se demuestra la integridad y completitud del almacén consultado.
3. **Adaptar** la aplicación de proyecciones con pasos persistentes, monotónicos e idempotentes; cada paso debe poder reintentarse después de reiniciar sin invocar el adaptador ni repetir efectos económicos. No afirmar atomicidad entre archivos SQLite.
4. **Crear** únicamente un coordinador o contrato mínimo si no se puede expresar la reconciliación con los componentes existentes. Debe registrar identidad de evidencia, hash, decisión, timestamps, resultado y versión observada, preservando las transiciones originales.

### Requiere revisión de CEREBRO

- `REQUIRES CEREBRO REVIEW` — definir el significado contractual de “fill confirmado” frente a “resultado local SUBMITTED”.
- `REQUIRES CEREBRO REVIEW` — decidir si hace falta un estado durable explícito de reconciliación y cómo evitar que se confunda con autorización de una nueva ejecución.
- `REQUIRES CEREBRO REVIEW` — aprobar el mínimo registro/protocolo de checkpoints para recuperación multi-store y su política de conflictos.
- `REQUIRES CEREBRO REVIEW` — establecer la fuente autoritativa para identidad estable de fill y replay económico.
- `REQUIRES CEREBRO REVIEW` — aprobar cualquier modificación de contratos públicos, esquema compartido o semántica de estados.

### Alternativas y riesgos

- **No cambiar nada:** se mantiene el fail-closed y la prevención de reenvío; un fill PAPER real puede quedarse indefinidamente en UNKNOWN. Es más seguro que inferir éxito, pero no satisface reconciliación.
- **Marcar SUBMITTED cuando se encuentre cualquier fill:** rechazado; no valida unicidad, identidad, aritmética ni proyecciones.
- **Reutilizar solo `synchronize_projection`:** insuficiente; repara una proyección de estado, no los efectos económicos ni la reserva.
- **Crear infraestructura nueva (base de datos, cola, servicio residente):** no justificado por la evidencia disponible y fuera del cambio mínimo.

## 9. Riesgos residuales

1. Fill PAPER durable mientras reserva/binding/ledger están UNKNOWN o discordantes.
2. Incompletitud del historial económico no detectable mediante una comprobación estructural de snapshot.
3. Fallo de escritura de una proyección después de confirmar otra base de datos.
4. Conflicto entre `paper_fills` y `applied_fills` sin reconciliación común.
5. Semántica de `SUBMITTED` susceptible de confundirse con fill reconciliado.
6. Falta de prueba integrada con PaperExecutionAdapter real después de cierre y reconstrucción.
7. No existe garantía de recuperación ante store inaccesible o múltiples candidatos más allá del comportamiento conservador de los componentes individuales.

## 10. Decisión para CEREBRO

La siguiente fase no debe empezar implementando un estado nuevo de forma aislada. Primero debe aprobarse el contrato mínimo de evidencia y reconciliación: qué prueba identifica un fill único, cómo se valida la aritmética completa, qué significa reconciliado y cómo se reintentan las proyecciones sin repetir efectos. Después podrá redactarse un cambio pequeño y auditable sobre las bases existentes.

## 11. Declaración de alcance

- `CODE_MODIFIED: none`
- `TESTS_EXECUTED: none`
- `WORKFLOWS_EXECUTED: none`
- `MERGES_PERFORMED: none`
- `NON_REPORT_FILES_MODIFIED: none`
- `REPORT_PUBLICATION: VERIFIED`

Este documento es una auditoría estática y una propuesta; no declara B1-R implementado, probado, aceptado ni listo para producción.
