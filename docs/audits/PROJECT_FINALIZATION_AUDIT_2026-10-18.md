# BOT-CRIPTO — Auditoría de cierre y ruta verificable hacia >90/100

**Fecha:** 2026-10-18  
**Fuente de verdad:** estado vivo de GitHub; ramas, commits y checks del SHA exacto.  
**Ámbito:** mantenimiento, fallos upstream, preparación PAPER, investigación económica y PR abiertas.  
**Decisión de seguridad:** `NO_TRADE`; sin LIVE, claves privadas, endpoints privados ni dinero real.

## 1. Dictamen de cierre

**Preparación publicada revisada: 75/100. Objetivo >90: NO ALCANZADO.**

No se sube la puntuación por documentar una idea, añadir herramientas o lograr que compile el código. El informe de preparación vigente asigna 5/20 al área de evidencia económica. Con las otras seis áreas en su máximo, esa categoría limita el total a 85/100. Superar 90 requiere nueva evidencia económica que permita puntuar esa categoría al menos en 11/20, además de cerrar los gates técnicos pertinentes.

La evidencia disponible no demuestra un edge neto robusto y repetible: el replay V1 integrado en `main` preserva `NO_ROBUST_EDGE_FOUND / NO_TRADE`; ETH TSMOM 168h es positivo en BASE y STRESS_1, pero pierde 26,37 % en STRESS_2 y no pasa todos los folds. Los datos históricos conservan `available_at/finality` desconocidos, así que el replay no sustituye evidencia prospectiva. Por eso, **no es seguro forzar el objetivo de puntuación eliminando `NO_TRADE` ni seleccionando retrospectivamente otra estrategia**.

## 2. Cambios aplicados directamente en `main`

Los siguientes cambios se hicieron en `main`, no se dejaron como una propuesta de rama:

1. **CodeQL actualizado de forma atómica.** En `.github/workflows/codeql.yml`, `init` y `analyze` usan ahora el mismo commit inmutable `6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee`, correspondiente al tag oficial `github/codeql-action v4.39.0`. Commit del workflow: `955f0eafed21f495e11c4aaf184ec4fe01e1fef2`.
2. **Prevención de la recurrencia.** `.github/dependabot.yml` agrupa las dependencias `github/codeql-action` para que futuras actualizaciones de `init` y `analyze` se propongan juntas. Commit: `b3e8a6abdd70b3db6679454ef8b644e316909c18`.
3. **Actualizaciones de herramientas de desarrollo.** `pyproject.toml` pasa a `pytest>=9.1.1,<9.2` y `setuptools>=84.0.0`, usando las versiones propuestas por PR #88 y #89, cuyas ramas habían pasado tests, E2E, CodeQL y pip-audit antes de quedar desfasadas frente a `main`. Commit: `fdb90440a89e4284ba425689c1c2ec177902a0cf`.

### Evidencia de CI (no intercambiar SHA)

En `fdb90440a89e4284ba425689c1c2ec177902a0cf`, el workflow de tests pasó: [run 39103016285](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016285); la auditoría de dependencias pasó: [run 39103016266](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016266). El run de CodeQL [39103016289](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016289) estaba pendiente al registrar esta evidencia; no se convierte ese estado en éxito por inferencia. Confirmar el resultado final en GitHub y validar de nuevo el HEAD vivo después de los commits documentales.

El main inmediatamente anterior, `f998731c63da691d6f9f87ac52c5acacd8b9b1b3`, tenía los tres checks principales en éxito: [1610 tests](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39102250567), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39102250569) y [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39102250582). Esos resultados anteriores no sustituyen los checks de los commits posteriores.

## 3. Incidente encontrado: actualizaciones parciales de CodeQL

PR #90 actualizaba `init` y PR #91 actualizaba `analyze` por separado. Ambos checks de CodeQL fallaron con un desajuste de versiones: uno intentaba cargar la configuración `4.39.0` mientras ejecutaba `4.38.2`, y el otro informaba el caso inverso. Los logs están disponibles en [run fallido #90](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081560283) y [run fallido #91](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081616377).

**Causa:** las partes de CodeQL de un mismo workflow deben correr la misma versión. No era un fallo demostrado del análisis del código de BOT-CRIPTO, sino un fallo de configuración en el proceso de actualización.

**Corrección aplicada:** ambos pasos se actualizaron juntos al SHA oficial de v4.39.0 y Dependabot ahora agrupa el ecosistema relevante. Cerrar PR #90 y #91 solo después de confirmar que el workflow de CodeQL pasa sobre el SHA actual de `main`; de lo contrario conservar el bloqueo y diagnosticar el log nuevo.

## 4. Casos públicos contrastados con los contratos locales

Un incidente upstream demuestra que un tipo de fallo existe; no demuestra por sí solo que BOT-CRIPTO lo haya sufrido. Se distingue entre observación externa, hallazgo local y mitigación comprobada.

| Caso y fuente primaria | Fallo publicado | Aplicación a BOT-CRIPTO y estado |
|---|---|---|
| [SQLite: WAL-reset bug](https://www.sqlite.org/wal.html#walresetbug), [SQLite 3.51.3](https://www.sqlite.org/releaselog/3_51_3.html) | Bajo una carrera poco común de varias conexiones escritoras/checkpoint en WAL, determinadas versiones podían corromper la base; corregido en 3.51.3 y backports indicados por SQLite. | El adaptador y el ledger usan journal rollback `DELETE`; los tests verifican el modo. No hay evidencia de corrupción local. No afirmar atomicidad entre archivos SQLite separados. |
| [Binance Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) | Una conexión se limita a 24 horas y puede recibir `serverShutdown`; hay límites de control/ping. | El colector duradero y el validador están implementados y probados, pero todavía no existe captura real continua de 24 horas aceptada. Un test de CI no prueba captura real. |
| [Python: jerarquía de tipos](https://docs.python.org/3/reference/datamodel.html#the-standard-type-hierarchy) | `bool` es subtipo de `int`; por eso `True == 1` puede pasar validaciones numéricas ingenuas. | PR #93 exige enteros JSON de tipo exacto en versiones de esquema y secuencias. Las pruebas negativas cubren este bypass. |
| [pip-audit 2.10.1 changelog](https://github.com/pypa/pip-audit/blob/main/CHANGELOG.md) | El mantenedor corrigió un `KeyError` al procesar registros OSV cuyo campo opcional `ranges` faltaba. | El proyecto mantiene una auditoría de dependencias. Un resultado limpio solo cubre dependencias auditables y fuentes conocidas; no es prueba de ausencia total de vulnerabilidades. |
| [GitHub: retirada de Node 20](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/) | El 23-09-2026 los runners migraron a Node 24 para JavaScript Actions. | Los workflows first-party del repositorio se actualizaron/pinnearon; verificar en cada SHA posterior. |
| [Freqtrade: Lookahead analysis](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/) | Datos futuros filtrados a indicadores/señales pueden distorsionar backtests. | PR #76/#86 añaden invariancia ante cambios del sufijo futuro, incluida evaluación y ventanas de validación sobre datos sintéticos. Falta evidencia temporal prospectiva y aceptación del replay anual completo independiente. |
| [websocket-client changelog](https://github.com/websocket-client/websocket-client/blob/master/ChangeLog) y [releases](https://pypi.org/project/websocket-client/) | El mantenedor documentó una carrera de cierre entre `WebSocketApp.close()` y `run_forever()`; la corrección registrada se publicó en 1.9.2. | BOT-CRIPTO fija `websocket-client==1.9.2`, pero usa `create_connection()` síncrono, no esa ruta de `WebSocketApp`. No hay equivalencia local demostrada ni se añadió un parche innecesario. |
| [QuantConnect: stale-price loophole](https://www.quantconnect.com/announcements/20949/closing-the-stale-price-loophole-on-daily-and-hourly-data/p1) | Un modelo de fill sobre el cierre anterior en barras horarias/diarias podía utilizar precios obsoletos y exagerar backtests. | El contrato local enlaza señal/decisión a quote con identidad, disponibilidad y antigüedad; costes modelados no equivalen a libro histórico real ni a ejecución real. |

## 5. Gates para poder decir >90 de manera defendible

1. **Captura prospectiva real:** ejecutar durante al menos 86.400 segundos en máquina persistente el procedimiento de [PROSPECTIVE_BTC_ETH_CAPTURE_V1.md](../PROSPECTIVE_BTC_ETH_CAPTURE_V1.md). Aceptar solo el manifest validado de BTCUSDT y ETHUSDT, con día UTC completo de velas horarias finales, ausencia de gaps/duplicados/desorden, secuencias y hashes reproducibles, y timestamps coherentes. Conservar la evidencia inmutable y sus hashes.
2. **Holdout nuevo y preregistrado:** no fijar T0 antes de aceptar la captura y congelar estrategia, configuración, costes, dataset y métricas. El protocolo V2 pide un periodo realmente prospectivo, no un corte retrospectivo elegido después de mirar el precio.
3. **Evidencia económica suficiente:** comparar contra cash y buy-and-hold con costes idénticos, informar retorno neto, drawdown, fees y folds no solapados. Si no hay robustez neta, mantener `NO_TRADE`; no ajustar parámetros después de ver el holdout para fabricar un aprobado.
4. **Recuperación operativa observable:** probar el reinicio y las transiciones entre PAPER, reserva, execution binding y ledger en condiciones de error. Los archivos SQLite independientes no tienen una única transacción distribuida; un estado parcial debe permanecer `UNKNOWN/BLOCKED` hasta conciliarse.
5. **Revalidar HEAD exacto:** tests, E2E relevantes, CodeQL y pip-audit deben estar terminados y en éxito para el commit al que se atribuyen. No trasladar checks de un SHA padre a sus descendientes.

## 6. PR abiertas: disposición segura

- **#88 y #89 (pytest/setuptools):** sus cambios se aplicaron en `pyproject.toml` de `main`. Cerrar como supersedidas después de verificar los checks del HEAD con ambas versiones combinadas.
- **#90 y #91 (CodeQL):** el cambio combinado se aplicó en `main`; cerrar como supersedidas solo cuando CodeQL esté verde en el SHA actual.
- **#69 (estrategias BTC/ETH V1) y #70 (protocolo V2):** mantener en draft hasta que la evidencia del protocolo permita una decisión. V1 registró `NO_ROBUST_EDGE_FOUND / NO_TRADE`; V2 está `DESIGNED / NOT RUN`.
- **#66 (universo BTC/altcoins):** no fusionar sin fuente primaria que pruebe el universo canónico exacto; la recuperación histórica sigue bloqueada.
- **#67 (contrato de investigación BTC/ETH):** revisar frente a los informes V1/V2 más nuevos antes de integrar para no duplicar ni contradecir el protocolo vigente.
- **#65 (handoff ETH), #62 (nota de continuidad) y #53 (estado HUESO 05-C):** son documentos históricos de ramas anteriores. No fusionarlos encima del estado actual sin revisar diferencias; el contenido de continuidad de #53 en particular podría reemplazar un archivo de estado mucho más reciente.

## 7. Actualización de construcción — captura de quotes y estudios en main

Desde la primera versión de esta auditoría se integraron directamente en `main` las siguientes piezas adicionales:

1. **BookTicker público:** `bot_obrero/binance_book_ticker.py` valida la pareja autorizada BTCUSDT/ETHUSDT, update ID, bid/ask y cantidades en Decimal, mercado cruzado y clocks de recepción/disponibilidad; solo usa el tiempo de evento del proveedor si está presente.
2. **Captura durable de quotes:** `bot_obrero/prospective_book_ticker_capture.py` conserva JSONL por símbolo, fsync, manifiesto y hashes, reconexión y validador independiente; su suite contiene un replay sintético completo de 24 h y pruebas negativas para IDs duplicados/desordenados, gaps y manipulación del archivo. No se trata de una captura de mercado real.
3. **Pruebas del colector:** el test de rechazo de símbolos falló primero porque intentaba rechazar `ETHUSDT`, que sí está permitido. El fixture se corrigió para usar `DOGEUSDT` como símbolo no autorizado. La suite volvió a pasar **1628 pruebas** en [run 39104242145](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39104242145), CodeQL [39104242189](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39104242189) y pip-audit [39104242200](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39104242200) están en `success` para el SHA `e476ea38ca7a0655b8127f31ff2de77ca5debe56`.
4. **Investigación versionada:** el código, dependencias, reporte y workflow de BTC/ETH V1 se copiaron a rutas de investigación de `main`; el replay de siete estrategias y los controles anti-lookahead pasó [run 39103827728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103827728) y conserva `NO_ROBUST_EDGE_FOUND / NO_TRADE`. Los dos informes históricos de recuperación del universo de altcoins, el contrato de investigación V1 y su auditoría de adquisición también se preservaron en `main` con avisos de su antigüedad.
5. **Cierre documental de PR:** #65–#70 se cerraron como supersedidas/integradas con comentarios que explican qué material se preservó en `main`, y que los bloqueos científicos siguen vigentes. #46, #53, #62 y #88–#91 ya habían sido cerradas por continuidad antigua o cambios de mantenimiento integrados.
6. **Puntuación:** la matriz se actualizó de 73 a **75/100**: +1 en adquisición temporal y +1 en infraestructura de captura por el software de quotes y su validador sintético. La categoría económica sigue en 5/20; el máximo de la matriz continúa en 85/100 mientras no se demuestre evidencia económica nueva.

**Importante:** el protocolo V2 en main sigue `DESIGNED / NOT RUN`. No existe todavía una captura real Kline+bookTicker de al menos 24 h, aceptada y solapada. No se ha fijado T0 del período nuevo ni se ha demostrado un edge en un holdout futuro. El siguiente paso necesita ejecutar ambos comandos de captura desde una máquina persistente, validar los dos manifiestos y conservar hashes; tras eso habrá que fijar el T0 en un commit de preregistro y observar el año prospectivo sin modificar reglas/costes.

## 8. Criterio de cierre

Este informe, los cambios de mantenimiento descritos y el catálogo de casos externos están en GitHub. Eso **no** convierte la captura real, el holdout prospectivo ni la supervisión sostenida en hechos realizados.

No declarar 100% hasta que cada gate aplicable tenga evidencia verificable. No declarar >90 hasta que la rúbrica se pueda aumentar con nueva evidencia económica real, no con una edición de la puntuación. La conducta correcta mientras se reúnen esas pruebas sigue siendo `NO_TRADE`.
