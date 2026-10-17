# BOT-CRIPTO — Auditoría técnica de main y casos públicos de fallos

**Fecha de revisión:** 2026-10-17  
**Repositorio:** [eltiootaku01-hue/bot-cripto](https://github.com/eltiootaku01-hue/bot-cripto)  
**Baseline inspeccionado antes de los cambios:** `main @ ff05bb6d70845ba51784da2bceee0ecf99b3e11a`  
**Método:** lectura estática de archivos y PR, inspección de estados/checks de GitHub Actions y contraste con documentación pública de mantenedores.  
**Límite:** no se ejecutó una prueba local ni un backtest manual durante esta auditoría. Los resultados de CI se citan por separado y solo corresponden al SHA que los produjo.

## Resumen ejecutivo

Se observaron defectos de continuidad documental y mantenimiento de workflows. Se prepararon dos correcciones directas en `main`: (1) eliminar el literal `[object Object]` y actualizar la continuidad del proyecto; (2) migrar las referencias first-party Actions a versiones actuales basadas en Node 24. **La validación CI del SHA creado por estas correcciones queda pendiente hasta consultar las ejecuciones que se disparen por el push.**

También quedó registrada una contradicción dentro de la PR PAPER #64: el informe de casos externos afirma que su adaptador usa journal rollback `DELETE`, pero el código consultado en el HEAD `8e6ad980481c39dd1504481724e014384ac7187f` ejecuta `PRAGMA journal_mode=WAL`. La PR permanece abierta y no se fusiona como parte de esta auditoría.

## Hallazgos, evidencia y soluciones

### A-01 — Estado de continuidad malformado y obsoleto
**Clasificación:** `OBSERVED` · **Severidad:** alta para continuidad, no evidencia de pérdida de datos.

**Evidencia:** el archivo `cerebro/PROJECT_STATE.md` en main comenzaba literalmente con `[object Object]`. Además, el texto indicaba que Product V2 estaba “en PR #63”, aunque la PR #63 ya estaba fusionada en main desde `8bbd9fc9511690db171c163b4a978a07e8aeb222`.

**Corrección aplicada en este commit:** retirar el literal inválido y reconstruir el estado de continuidad con los estados vivos conocidos de PR #64, #66, #69 y #70, preservando explícitamente `NO_TRADE`, las fronteras pendientes y la regla de no guardar un SHA propio como puntero vivo.

**Criterio de aceptación:** leer el archivo desde main; comprobar que no comienza con `[object Object]`, que no presenta PR #63 como abierta y que distingue hechos observados de bloqueos.

### A-02 — Versiones de Actions que preceden al runtime Node 24
**Clasificación:** `OBSERVED` · **Severidad:** media de mantenimiento/compatibilidad.

**Evidencia:** los seis workflows existentes en main usaban `actions/checkout@v4`, `actions/setup-python@v5` y, en el workflow de artefactos, `actions/upload-artifact@v4`. GitHub anunció el 23-09-2026 que Node 20 dejó de estar disponible en runners y que los workflows deben actualizarse a versiones de Actions compatibles con Node 24 ([anuncio oficial](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/)). El último run anterior de main, [38959108299](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38959108299), terminó en `success` el 16-10-2026; por tanto, esto se registra como alineación necesaria y riesgo de compatibilidad, **no** como afirmación de que el run observado hubiera fallado.

**Corrección aplicada en este commit:** actualizar los seis workflows a `actions/checkout@v7` y `actions/setup-python@v7`; `actions/upload-artifact@v7` donde correspondía. Estas versiones son las mismas que usa la rama PAPER auditada, cuyo run más reciente de pruebas [39063159426](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39063159426) y tres E2E asociados finalizaron en `success` para el SHA exacto `8e6ad980481c39dd1504481724e014384ac7187f`.

**Estado posterior al cambio:** `UNKNOWN/PENDING` hasta confirmar CI asociado al nuevo commit de main. No reutilizar el éxito del SHA antiguo como validación del cambio nuevo.

### A-03 — Código/documentación contradictorios sobre SQLite WAL en PR #64
**Clasificación:** `OBSERVED` para la contradicción; **`UNKNOWN`** sobre si hubo corrupción.

**Evidencia local:**
- [Código del adaptador PAPER en HEAD 8e6ad98](https://github.com/eltiootaku01-hue/bot-cripto/blob/feat/paper-operational-runtime-v1/bot_obrero/paper_adapter.py) ejecuta `PRAGMA journal_mode=WAL`.
- [Informe de casos externos en esa rama](https://github.com/eltiootaku01-hue/bot-cripto/blob/feat/paper-operational-runtime-v1/docs/research/EXTERNAL_FAILURE_CASE_STUDIES.md) afirma que el adaptador fue cambiado a journal rollback `DELETE`.

**Caso externo primario:** SQLite documentó un “WAL-reset bug” que afectaba versiones 3.7.0–3.51.2 bajo una concurrencia estrecha: al menos dos conexiones al mismo archivo en distintos hilos/procesos intentando escribir y hacer checkpoint simultáneamente. SQLite lo marca corregido en 3.51.3 y versiones posteriores; también recalca que la condición de activación es rara ([documentación oficial del bug](https://www.sqlite.org/wal.html#walresetbug), [release 3.51.3](https://www.sqlite.org/releaselog/3_51_3.html)). La API `sqlite3` de Python documenta además que las conexiones y transacciones necesitan manejarse correctamente en escenarios con hilos/escrituras concurrentes ([documentación Python](https://docs.python.org/3/library/sqlite3.html)).

**Solución requerida antes de fusionar PR #64:** elegir y documentar una política única:
1. cambiar el adaptador a journal rollback `DELETE` si WAL no es necesario, y ejecutar pruebas de concurrencia/reinicio con conexiones independientes; o
2. conservar WAL, pero verificar y registrar la versión efectiva de SQLite (mínimo 3.51.3 o una versión corregida con backport), además de probar explícitamente concurrencia, recuperación y manejo de `SQLITE_BUSY`.

No hay evidencia de que una base de BOT-CRIPTO se haya corrompido. Este informe **no** afirma que el riesgo haya ocurrido; registra la contradicción y el requisito preventivo.

### A-04 — La cotización que fija el fill no demuestra ser la que evaluó riesgo
**Clasificación:** `OBSERVED` como frontera pendiente de PR #64; no se afirma un fill financiero real.

La propia documentación PAPER indica que el adaptador obtiene el quote durante el envío, después de la autorización, y que el intent/reserva aún no contiene una identidad verificable `PaperQuoteEvidence`. Si el bid/ask cambia entre autorización y fill, no existe prueba de que riesgo y ejecución hayan usado la misma evidencia económica.

**Solución:** exigir que una identidad de cotización versionada (símbolo, bid, ask, fuente, `observed_at`, `available_at`, límite de antigüedad y huella canónica) viaje desde la decisión hasta el fill persistido. Si se necesita cambiar contratos financieros ya canonizados, detenerse y registrar `REQUIRES CEREBRO REVIEW`; no introducir un segundo motor de riesgo ni un ledger paralelo.

### A-05 — Replay económico local no equivale a reconciliación global
**Clasificación:** `OBSERVED` como límite documentado.

`paper_economic_replay.py` declara que valida la historia de fills y la proyección de balances de una cuenta PAPER; no reconcilia reservas, bindings de ejecución ni la proyección separada del ledger de idempotencia.

**Solución:** antes de autorizar una transición de reconciliación, exigir igualdad trazable entre fills persistidos, saldo inicial y final, fees, quote usado, intención/reserva, binding/estado de ejecución y ledger idempotente, con orden determinista, persistencia transaccional y comportamiento `UNKNOWN/BLOCKED` ante evidencia incompleta. La especificación B1-R sigue siendo trabajo pendiente; no se afirma que ya exista esa reconciliación.

### A-06 — El tiempo de espera de Binance no significa que la orden falló
**Clasificación:** `OBSERVED` en la documentación del proveedor; `LIVE` no habilitado.

La documentación oficial de errores de Binance define `-1006 UNEXPECTED_RESP` y `-1007 TIMEOUT` con estado de ejecución desconocido ([Binance — error codes](https://github.com/binance/binance-spot-api-docs/blob/master/errors.md)). Reintentar a ciegas puede duplicar efectos si el primer envío sí fue aceptado.

**Solución exigida para cualquier integración futura:** preservar `UNKNOWN`, congelar nuevas admisiones que puedan duplicar la intención, consultar/reconciliar usando el identificador idempotente de cliente y evidencia de fills antes de permitir un reintento. El código local de recuperación ya clasifica la evidencia ambigua como `UNKNOWN` y congela el guard; esto no demuestra que exista reconciliación con el exchange, que sigue sin implementarse.

### A-07 — El colector debe tolerar las desconexiones y probar ausencia de huecos
**Clasificación:** `OBSERVED` como requisito del proveedor y bloqueo del diseño V2; el fallo del colector local aún no está probado.

Binance documenta que las conexiones Spot WebSocket duran como máximo 24 horas y espera que el cliente se reconecte cuando recibe `serverShutdown` ([WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)). Reportes públicos de librerías también describen reconexiones duplicadas y pérdida de datos tras desconexiones: [Binance Connector Python #505](https://github.com/binance/binance-connector-python/issues/505), [CCXT #26945](https://github.com/ccxt/ccxt/issues/26945). Son ejemplos de clases de fallo en otros componentes, no pruebas de que BOT-CRIPTO haya sufrido esos fallos.

**Solución:** reconexión única e idempotente, verificación de secuencia/duplicados, recuperación por REST de todo hueco temporal, persistencia de primera recepción, rechazo de velas no cerradas y prueba de humo continua de 24 horas. No fijar T0 del periodo V2 de 365 días hasta superar estos criterios.

### A-08 — No hay evidencia de edge robusto; mantener NO_TRADE
**Clasificación:** `OBSERVED` según los informes abiertos de investigación.

PR #69 informa que la mejor regla evaluada es inestable entre escenarios de costes y folds de validación; PR #70 solo diseña un protocolo prospectivo y declara `DESIGNED / NOT RUN`. No se promueve ninguna regla como estrategia operable. Cash y buy-and-hold deben compararse con idénticos costes y una ventana verdaderamente prospectiva.

### A-09 — Validación temporal aceptaba un `tzinfo` sin offset real
**Clasificación:** `OBSERVED` en inspección estática; **corregido con prueba de regresión añadida en el seguimiento**.

**Evidencia:** `bot_obrero/temporal.py` (`EvidenceTimestamp`) y `EvidenceRecord` en `bot_obrero/execution.py` comprobaban `tzinfo is not None`, pero no `utcoffset() is not None`. La definición de Python considera consciente una fecha solo cuando `tzinfo` y su `utcoffset()` no son `None` ([documentación de datetime](https://docs.python.org/3/library/datetime.html#aware-and-naive-objects)). Un `tzinfo` personalizado que devuelva `None` para `utcoffset()` podía pasar esos chequeos y ser tratado como timestamp con zona válida, pese a representar semánticamente un datetime naïve.

**Corrección en el seguimiento de esta auditoría:** `EvidenceTimestamp`, `EvidenceRecord` y `EvidenceRecord.valid_for(now)` comprueban ambas condiciones. Se añadió `tests/test_temporal_timezone_contract.py`, que usa deliberadamente una zona con offset nulo para impedir esa regresión. El resultado de CI para el SHA que incorpora este seguimiento debe comprobarse por separado; los tests añadidos no se presentan como ejecutados localmente.

## Referencias públicas consultadas

1. [GitHub Changelog: Node 20 is no longer available in GitHub Actions (2026-09-23)](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/) — cambio de runtime y versiones de acciones.
2. [SQLite WAL — sección WAL-reset bug](https://www.sqlite.org/wal.html#walresetbug) y [SQLite 3.51.3 release](https://www.sqlite.org/releaselog/3_51_3.html) — condición de concurrencia y versión corregida.
3. [Python sqlite3](https://docs.python.org/3/library/sqlite3.html) — transacciones y comportamiento en hilos.
4. [Binance Spot API error codes](https://github.com/binance/binance-spot-api-docs/blob/master/errors.md) — timeouts y estado de ejecución desconocido.
5. [Binance Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) y [API changelog](https://github.com/binance/binance-spot-api-docs/blob/master/CHANGELOG.md) — reconexión y cambios de contrato.
6. [Binance Connector Python issue #505](https://github.com/binance/binance-connector-python/issues/505) y [CCXT issue #26945](https://github.com/ccxt/ccxt/issues/26945) — reportes públicos de reconexión/pérdida de datos en implementaciones ajenas.
7. [Freqtrade — lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/) y [QuantConnect — slippage key concepts](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts) — pruebas contra anticipación y límites de modelos de fills.

## Cambios de main incluidos con esta auditoría

- Seis workflows actualizados a referencias first-party compatibles con Node 24.
- `bot_obrero/temporal.py` y `EvidenceRecord` en `bot_obrero/execution.py` endurecidos para rechazar timestamps con `tzinfo` sin offset real.
- Añadida regresión en `tests/test_temporal_timezone_contract.py`.
- `cerebro/PROJECT_STATE.md` reconstruido sin el literal malformado y con bloqueos actuales.
- Este informe, con el hallazgo A-09, se mantiene en `docs/audits/MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md`.

No se fusionó PR #64 ni se cambió el comportamiento de ejecución financiera. No se ejecutaron órdenes, no se usaron claves privadas y no se cambió `NO_TRADE`.
