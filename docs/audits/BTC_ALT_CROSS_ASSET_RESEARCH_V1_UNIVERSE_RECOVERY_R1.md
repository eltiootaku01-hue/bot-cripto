# BTC_ALT_CROSS_ASSET_RESEARCH_V1 — UNIVERSE-RECOVERY-R1

- **Inspección:** 2026-10-16 (UTC; aproximadamente 17:21Z según la hora local disponible)
- **Repositorio:** https://github.com/eltiootaku01-hue/bot-cripto
- **Tarea:** `UNIVERSE-RECOVERY-R1`
- **Autoridad:** propietario > CEREBRO / IA-CHAN > BOT OBRERO
- **Modo:** inspección documental remota e integridad de artefacto. BOT OBRERO no inició manualmente pruebas, backtests ni simulaciones; después de abrir el PR, GitHub disparó automáticamente workflows de CI. Sus estados verificados se registran en §9.
- **Resultado:** `PARTIAL`
- **Estado del universo canónico:** **no confirmado; evidencia insuficiente**
- **Dictamen:** `BLOCKED_UNIVERSE_DEFINITION` permanece vigente. Este informe no aprueba una lista ni autoriza backtesting nuevo.

## 1. Resumen ejecutivo

No se recuperó una fuente primaria accesible que declare los símbolos exactos de `BTCUSDT` más diecinueve altcoins y que demuestre aprobación del propietario. La evidencia positiva disponible identifica únicamente un panel exploratorio de cinco altcoins: `XRPUSDT`, `STRKUSDT`, `WUSDT`, `SUIUSDT` y `ENAUSDT`. `BTCUSDT` aparece como benchmark de referencia, no como prueba de que se hayan definido las otras catorce altcoins.

El artefacto R5 fue recuperado, leído y sometido a verificación documental de integridad. El SHA-256 calculado para el ZIP coincide exactamente con el digest SHA-256 publicado por GitHub. El JSON interior registra `panel_classification = EXPLORATORY_ONLY`, `overall_decision = NO_TRADE`, y declara expresamente que no se reservó un test final independiente ni se reconstruyó la elegibilidad histórica de los instrumentos.

La búsqueda no encontró una lista canónica ni un registro de aprobación en las fuentes consultadas. Esto **no demuestra que la lista nunca haya existido**. No se completan símbolos por popularidad ni por disponibilidad actual. La única conclusión defendible es que el universo histórico permanece desconocido y bloqueado hasta localizar evidencia primaria o recibir una decisión explícita del propietario.

## 2. Estado remoto verificado

### 2.1 Referencias

| Referencia | Estado observado | Evidencia |
|---|---|---|
| `main` | `b9e34f2d836b99f711d6227f88c95c7b5ba7bd85` | [commit actual observado](https://github.com/eltiootaku01-hue/bot-cripto/commit/b9e34f2d836b99f711d6227f88c95c7b5ba7bd85), fechado 2026-10-15T15:22:53Z; mensaje `docs: record verification of B1-R audit publication`. |
| `research/product-v2-r5-independent-strategies` | `dae415947a5d45340e4af96644328a4ba95cb9bd` | Comparación remota `dae415... ↔ rama`: `identical`, 0 ahead / 0 behind. [commit](https://github.com/eltiootaku01-hue/bot-cripto/commit/dae415947a5d45340e4af96644328a4ba95cb9bd). |
| PR #64 | abierta, no mergeada | [PAPER adapter](https://github.com/eltiootaku01-hue/bot-cripto/pull/64); head `33e81b57197ac4cae3af53730ac239ea9caa06fe`, base declarada `8bbd9fc9511690db171c163b4a978a07e8aeb222`. |
| PR #65 | abierta, no mergeada | [ETH strategy handoff](https://github.com/eltiootaku01-hue/bot-cripto/pull/65); head `d18a90c08704bd24e0a0ba540fdcbe701545fd89`, base `b9e34f2d836b99f711d6227f88c95c7b5ba7bd85). No es evidencia de aprobación del universo de esta tarea. |
| PR #62 | abierta, no mergeada | [chat continuity](https://github.com/eltiootaku01-hue/bot-cripto/pull/62); head `8af1f92d135ef5f135a4771383fba5f9b4cf835b`. El archivo `cerebro/CONTINUIDAD_CHAT.md` no está disponible en `main` en la ruta consultada; no se considera un registro canónico fusionado. |

El endpoint de estados combinados no devolvió estados para el SHA de `main`; esto se registra como “sin estados devueltos por esa consulta”, no como prueba de que no existan ejecuciones históricas. La consulta de ejecuciones asociadas al commit R5 tampoco devolvió runs porque el wrapper consultado filtra runs disparados por pull request y solo muestra la primera página; la relación del artefacto con el run se verificó mediante los metadatos del artefacto, no mediante esa consulta.

### 2.2 Artefacto R5

- **Artifact ID:** `11812924702`
- **Nombre:** `product-v2-r5-report-dae415947a5d45340e4af96644328a4ba95cb9bd`
- **Run ID:** `38399941811`
- **Head SHA del run:** `dae415947a5d45340e4af96644328a4ba95cb9bd`
- **Rama registrada por GitHub:** `research/product-v2-r5-independent-strategies`
- **Creación:** `2026-10-13T12:48:27Z`
- **Expiración publicada:** `2026-11-12T12:48:26Z`
- **Tamaño publicado:** 108,904 bytes
- **Digest publicado:** `sha256:035624ca0a86b1ab1863f85660fe37a8c103d6a39a1f180d88422bd20d9b293f`
- **Archivo descargado:** `product-v2-r5-report.zip`
- **SHA-256 calculado sobre el ZIP descargado:** `035624ca0a86b1ab1863f85660fe37a8c103d6a39a1f180d88422bd20d9b293f`
- **Verificación de integridad:** `TESTED — PASS`; hash local coincide con el digest publicado.
- **Contenido:** un archivo `product-v2-r5-report.json` (1,012,088 bytes según el ZIP); se pudo abrir y parsear como JSON. El hash del ZIP prueba la integridad del archivo descargado frente al digest publicado, no la verdad económica de sus resultados.

## 3. Fuentes examinadas y cobertura

1. Metadata del repositorio y SHA de commits recientes.
2. Comparación remota entre `main` y la rama R5.
3. Documento R5: [`docs/PRODUCT_V2_R5_RESEARCH.md`](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/product-v2-r5-independent-strategies/docs/PRODUCT_V2_R5_RESEARCH.md), blob SHA `73b7f63b8c9f2cfb5e15dadafa937039f350b700`.
4. Workflow R5: [`.github/workflows/product-v2-r5-research.yml`](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/product-v2-r5-independent-strategies/.github/workflows/product-v2-r5-research.yml), blob SHA `49393106de87795ad9153d0dcf7908811b8e5623`.
5. Artefacto original R5 y metadatos publicados por GitHub.
6. `cerebro/README.md`, blob SHA `392a4cf623632acd4cb01063aa40f80006f2b0a3`, que identifica `cerebro/PROJECT_STATE.md` como memoria de continuidad estable y exige que GitHub sea la fuente de verdad.
7. `cerebro/PROJECT_STATE.md`, blob SHA `59e1c6abe3097468544b91dc60d5e81decce37ef`; el contenido recuperado describe Product V2 y no declara la lista de veinte instrumentos.
8. Informe B1-R: [`docs/audits/B1_R_RECONCILIATION_ACCEPTANCE_AUDIT.md`](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/docs/audits/B1_R_RECONCILIATION_ACCEPTANCE_AUDIT.md), blob SHA `6596efc685e0ccd3442f1342084d8917686131e9`; no es una fuente de universo canónico.
9. Búsquedas de código en la rama por defecto, búsquedas de commits, búsquedas de issues y búsquedas de PR para el nombre del proyecto y términos relacionados con universo/altcoins. No devolvieron coincidencias relevantes para una lista aprobada.
10. Verificación de las rutas de continuidad y del informe propuesto. El informe de esta tarea no existía en `main` en la ruta propuesta. `cerebro/CONTINUIDAD_CHAT.md` tampoco se encontró en `main`; la PR #62 que lo propone sigue abierta y sin merge.

### Límites de cobertura

La búsqueda de código de GitHub cubre la rama por defecto y no constituye un barrido exhaustivo de todos los blobs de todas las ramas, comentarios, ejecuciones o artefactos. Las búsquedas de issues/PR/commits se limitan a resultados devueltos por las consultas realizadas. No se afirma que se inspeccionó “todo GitHub”. No se encontró una fuente primaria en el alcance consultado, pero la inexistencia histórica de esa fuente sigue siendo `UNKNOWN`.

## 4. Hallazgos clasificados

| ID | Hallazgo | Clase | Fuente y localización | Qué demuestra / qué no demuestra |
|---|---|---|---|---|
| UR-01 | `main` apunta al SHA `b9e34f2d...`. | `OBSERVED` | [commit](https://github.com/eltiootaku01-hue/bot-cripto/commit/b9e34f2d836b99f711d6227f88c95c7b5ba7bd85) | Identifica el estado remoto observado. No acredita un universo. |
| UR-02 | La rama R5 coincide con `dae415947...`. | `OBSERVED` | Comparación remota `dae415...` vs. `research/product-v2-r5-independent-strategies` | Fija el SHA R5 examinado en esta inspección. |
| UR-03 | El ZIP R5 coincide con el digest SHA-256 publicado. | `TESTED` | Artifact `11812924702`, run `38399941811` | Confirma integridad del ZIP descargado. No valida estrategia ni rentabilidad. |
| UR-04 | El panel R5 contiene `XRPUSDT`, `STRKUSDT`, `WUSDT`, `SUIUSDT`, `ENAUSDT`. | `OBSERVED` | JSON interior, `protocol.panel`; documento R5 | Confirma cinco símbolos en el panel exploratorio. No demuestra que sean la lista canónica de altcoins. |
| UR-05 | `BTCUSDT` está declarado como `benchmark_symbol`. | `OBSERVED` | JSON interior, `protocol.benchmark_symbol` | Demuestra su rol de referencia/benchmark en R5. No demuestra que exista una lista de veinte instrumentos aprobada. |
| UR-06 | Los seis símbolos tienen 1.350 barras registradas en `history_bar_counts`; `usable_symbols` contiene BTC más las cinco altcoins. | `OBSERVED` | JSON interior, `protocol.history_bar_counts`, `protocol.usable_symbols` | Evidencia que el artefacto registra esos seis símbolos en el experimento. No implica que todos tengan el mismo rol de estrategia ni elegibilidad histórica. |
| UR-07 | El panel está clasificado `EXPLORATORY_ONLY`; la decisión general del artefacto es `NO_TRADE`. | `OBSERVED` | JSON interior, `protocol.panel_classification`, `overall_decision` | El propio artefacto niega que el panel sea un universo canónico aprobado. |
| UR-08 | R5 declara que la disponibilidad de klines públicos no prueba elegibilidad de listado en cada momento y que no reconstruye filtros históricos. | `OBSERVED` | Documento R5; JSON `protocol.universe_limitations`, `limitations`, `current_instrument_filters` | Identifica limitaciones temporales/históricas; no reconstruye el universo histórico. |
| UR-09 | No se encontró una fuente primaria que enumere exactamente BTCUSDT + 19 altcoins y demuestre aprobación. | `UNKNOWN` | Búsquedas enumeradas en §3; ruta del informe no existente en main | La lista no se puede confirmar con la evidencia consultada. No demuestra que nunca haya existido. |
| UR-10 | No se encontró evidencia de aprobación del propietario para los seis símbolos como universo canónico, ni para una lista de veinte. | `UNKNOWN` | Fuentes consultadas en §3 | La autoridad de la lista sigue sin acreditarse. Ausencia de resultados no equivale a rechazo ni inexistencia. |
| UR-11 | Las búsquedas de código, commits, issues y PR no arrojaron coincidencias para el identificador exacto y consultas relacionadas. | `TESTED` | Consultas remotas realizadas el 2026-10-16 | Es un resultado limitado de búsqueda; no es prueba exhaustiva de inexistencia. |
| UR-12 | El estado de continuidad en `cerebro/PROJECT_STATE.md` es descriptivo de Product V2 y no fija el universo de veinte instrumentos. | `OBSERVED` | Blob SHA `59e1c6abe3097468544b91dc60d5e81decce37ef` | No puede usarse como aprobación del universo. |
| UR-13 | El universo histórico original y los catorce símbolos no observados siguen sin determinarse. | `BLOCKED` | Resultado combinado de UR-04 a UR-12 | No avanzar mediante inferencia de símbolos; requiere fuente primaria o decisión del propietario. |

## 5. Tabla de símbolos y roles

| Símbolo | Rol demostrado por la evidencia | ¿Contabiliza como altcoin canónica aprobada? |
|---|---|---|
| `BTCUSDT` | Benchmark de referencia declarado en R5. | No; es el instrumento de referencia solicitado, no una de las diecinueve altcoins. |
| `XRPUSDT` | Miembro del panel exploratorio; aparece en resultados de investigación de activo individual. | No confirmado. |
| `STRKUSDT` | Miembro del panel exploratorio; aparece en resultados de investigación de activo individual y en el registro de cartera compartida. | No confirmado. |
| `WUSDT` | Miembro del panel exploratorio; aparece en resultados de investigación de activo individual. | No confirmado. |
| `SUIUSDT` | Miembro del panel exploratorio; aparece en resultados de investigación de activo individual. | No confirmado. |
| `ENAUSDT` | Miembro del panel exploratorio; aparece en resultados de investigación de activo individual y en el registro de cartera compartida. | No confirmado. |
| Otras catorce altcoins | No identificadas por una fuente primaria en el alcance revisado. | `UNKNOWN`; no completar. |

**Conteo correcto:** seis símbolos distintos observados en el artefacto (uno de referencia + cinco altcoins exploratorias). No son veinte instrumentos, ni existe evidencia de que el panel de cinco sea un subconjunto aprobado de una lista fija de diecinueve.

## 6. Qué declara realmente el artefacto R5

El JSON interior registra:

- intervalo `4h`, evaluación de 180 días y 1.080 barras esperadas;
- panel fijo de cinco símbolos y `panel_classification = EXPLORATORY_ONLY`;
- benchmark `BTCUSDT`;
- seis símbolos en `usable_symbols`, cada uno con 1.350 barras en `history_bar_counts`;
- capital hipotético de 1.000 USDT procedente del ledger normalizado de Product V2, sin conversión desde ARS;
- escenarios de coste BASE, STRESS_1 y STRESS_2 como hipótesis;
- `independent_final_test = NOT_ESTABLISHED`;
- limitaciones que indican que las klines históricas no prueban elegibilidad point-in-time y que los costes de spread/slippage no son mediciones históricas;
- `overall_decision = NO_TRADE`.

Esto confirma que R5 fue un experimento con un panel exploratorio. **No convierte sus cinco altcoins en la lista canónica de diecinueve ni en una selección autorizada para una nueva fase.**

## 7. Discrepancias y riesgos

1. **Universo fijo vs. panel exploratorio:** el objetivo de esta tarea espera veinte instrumentos, pero la fuente R5 describe un panel de cinco altcoins más BTC y lo etiqueta exploratorio. Estado: no resuelto; no se debe equiparar.
2. **Universo histórico vs. disponibilidad actual:** el documento R5 advierte que klines públicas y metadatos actuales no reconstruyen la elegibilidad histórica. El artefacto no resuelve altas, bajas, cambios de símbolo o restricciones históricas.
3. **Evidencia de ejecución vs. aprobación:** la presencia de símbolos en un artefacto demuestra que el experimento los registró, no que el propietario aprobara la lista.
4. **Continuidad incompleta:** `cerebro/PROJECT_STATE.md` no identifica la lista canónica. La PR #62, que contiene un registro de continuidad separado, sigue abierta y no mergeada; no debe tratarse como estado de `main`.
5. **Búsqueda limitada:** las consultas sin resultados no son prueba de inexistencia. Para cerrar la recuperación hace falta localizar una fuente primaria adicional o recibir una decisión explícita.
6. **Riesgo de sesgo de supervivencia:** no usar la lista de activos hoy disponibles ni el panel R5 para reconstruir retroactivamente el universo histórico.
7. **Riesgo de autorización:** no ejecutar nuevos backtests, no crear un universo de conveniencia y no convertir la finalización documental en autorización científica.

## 8. Criterios de aceptación AC-01 a AC-08

| Criterio | Estado | Evidencia / motivo |
|---|---|---|
| AC-01 — Estado remoto identificado | `PASS` | SHA de main, rama R5, PR relacionadas y metadatos del artefacto registrados en §2. Las limitaciones de consultas de workflows/estados se declaran expresamente. |
| AC-02 — Fuentes trazables | `PARTIAL` | Cada afirmación material tiene una fuente o una declaración explícita de ausencia; las búsquedas no cubren exhaustivamente todos los blobs/comentarios/artefactos históricos. |
| AC-03A — Recuperación de la lista canónica | `NOT MET` / `UNKNOWN` | No hay fuente primaria accesible con veinte símbolos exactos y aprobación demostrada. La lista histórica sigue sin confirmarse. |
| AC-03B — Diagnóstico negativo correctamente delimitado | `PASS` | El informe describe las fuentes consultadas y los límites de cobertura, y no concluye que la lista nunca existió. La búsqueda negativa no se presenta como prueba exhaustiva de inexistencia. |
| AC-04 — Símbolos y roles diferenciados | `PASS` | BTC se separa como benchmark; cinco altcoins como panel exploratorio; candidatos ejecutados y universo aprobado no se confunden. |
| AC-05 — Discrepancias registradas | `PARTIAL` | Se registra la discrepancia principal entre objetivo de veinte y panel exploratorio de seis símbolos. La historia completa del universo permanece desconocida. |
| AC-06 — No se inventaron datos | `PASS` | No se añadieron símbolos ausentes ni se elevó el panel R5 a lista canónica. |
| AC-07 — Informe publicado de forma revisable | `PASS` | El informe y el checkpoint de continuidad están publicados en la rama documental del PR #66; el PR es accesible, permanece abierto y no está fusionado. Esto confirma publicación revisable, no aprobación del universo. |
| AC-08 — Estado final coherente | `PASS` | Resultado `PARTIAL`; lista no confirmada; `BLOCKED_UNIVERSE_DEFINITION` se mantiene. |

## 9. Cambios permitidos, publicación y continuidad

La auditoría no modificó código, estrategias, contratos, parámetros, definiciones de workflows ni artefactos históricos; tampoco inició manualmente pruebas, backtests ni simulaciones económicas. Después de abrir el PR #66, GitHub Actions disparó automáticamente tres workflows por el evento `pull_request`, asociados inicialmente al HEAD documental `74ef1cd293120af2bc3eac83138756cbccb17350`. En la comprobación actual, los tres aparecen como `completed` / `success`: [tests, run 38939511004](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38939511004), [phase1.31-sma-real-e2e, run 38939510983](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38939510983) y [phase1.34-indicators-real-e2e, run 38939510952](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38939510952). BOT OBRERO no los solicitó, reintentó ni canceló. Esos resultados corresponden a comprobaciones automáticas sobre ese HEAD; no validan el universo canónico, la calidad económica ni la rentabilidad. Después de las correcciones documentales, GitHub Actions ejecutó automáticamente otra tanda por el evento `pull_request` sobre el HEAD `e459525fa8e71fb8d369db366b1c6ae9b7c1e288`; en la consulta de 2026-10-16 17:37Z, también terminó `completed` / `success`: [tests, run 38941042561](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38941042561), [phase1.31-sma-real-e2e, run 38941042604](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38941042604) y [phase1.34-indicators-real-e2e, run 38941042627](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38941042627). BOT OBRERO tampoco los solicitó ni reintentó. Estos resultados son válidos para el HEAD `e459...` y no deben presentarse como checks del commit posterior que añade este registro de seguimiento; la sincronización de continuidad puede generar una nueva tanda automática. Ninguna tanda valida el universo canónico ni la rentabilidad. El artefacto R5 solo se descargó para verificar integridad y leer su contenido; no fue modificado ni regenerado.

El informe se publica en una rama documental separada y no se fusionará. Para evitar crear un segundo registro de continuidad que compita con el principal, se actualizará el registro existente `cerebro/PROJECT_STATE.md` con un checkpoint breve, conservando su contenido previo y añadiendo referencias a este informe. La documentación publicada no implica que el universo haya sido aprobado.

## 10. Decisión requerida de CEREBRO / propietario

Se requiere una de estas dos vías:

1. **Recuperación basada en evidencia:** aportar/localizar un documento, commit, decisión aprobada, manifiesto de experimento o artefacto original que enumere BTCUSDT + las diecinueve altcoins exactas y vincule la lista con su versión/fecha y aprobación.
2. **Decisión explícita del propietario:** si la lista original no puede recuperarse, decidir si se autoriza definir un universo nuevo. Esa decisión debe registrarse como una lista nueva y no como recuperación histórica; BOT OBRERO no seleccionará los símbolos por cuenta propia.

Hasta entonces:
- `BLOCKED_UNIVERSE_DEFINITION` permanece;
- la lista canónica es `UNKNOWN`;
- no se inicia backtesting ni investigación económica con una lista inventada;
- `NO_TRADE` permanece como frontera operativa vigente;
- ninguna publicación documental constituye `IMPLEMENTATION_APPROVED` ni aprobación de rentabilidad.

## 11. Registro de continuidad para el siguiente chat

- **Repositorio:** `eltiootaku01-hue/bot-cripto`
- **Main observado al inicio:** `b9e34f2d836b99f711d6227f88c95c7b5ba7bd85`
- **Rama R5 verificada:** `research/product-v2-r5-independent-strategies` = `dae415947a5d45340e4af96644328a4ba95cb9bd`
- **Artefacto examinado:** `11812924702`, run `38399941811`, digest SHA-256 verificado.
- **Informe:** `docs/audits/BTC_ALT_CROSS_ASSET_RESEARCH_V1_UNIVERSE_RECOVERY_R1.md`
- **Estado:** `PARTIAL`; universo canónico no confirmado; `BLOCKED_UNIVERSE_DEFINITION`.
- **Decisiones confirmadas:** cinco altcoins R5 son exploratorias; BTCUSDT es benchmark; no se infieren símbolos faltantes.
- **Límites vigentes:** solo documentación; sin cambios funcionales, sin pruebas/backtests iniciados manualmente por BOT OBRERO, sin operaciones financieras y sin mergear el PR. Registrar aparte cualquier workflow automático disparado por el PR.
- **Próximo paso exacto:** CEREBRO/propietario localiza evidencia primaria de la lista original o decide explícitamente si autoriza una lista nueva.
- **Condición de salida del bloqueo:** fuente trazable con los veinte símbolos y evidencia de aprobación, o decisión explícita del propietario que cree un universo nuevo sin presentarlo como histórico.

---

**Conclusión de BOT OBRERO:** `PARTIAL`. La evidencia R5 quedó recuperada y su integridad documental verificada; la lista canónica de veinte instrumentos no quedó recuperada. La auditoría termina documentalmente, pero el universo permanece bloqueado para revisión de CEREBRO y decisión del propietario.
