# BTC_ALT_CROSS_ASSET_RESEARCH_V1 — UNIVERSE-RECOVERY-R2
## Ampliación de búsqueda histórica en GitHub

- **Fecha de inspección:** 2026-10-16 (UTC).
- **Repositorio:** [eltiootaku01-hue/bot-cripto](https://github.com/eltiootaku01-hue/bot-cripto).
- **Tarea:** `UNIVERSE-RECOVERY-R2`.
- **Autoridad:** propietario > CEREBRO / IA-CHAN > BOT OBRERO.
- **Modo:** documental/read-only sobre fuentes remotas y artefactos existentes. Sin cambios de código, pruebas ni backtests iniciados manualmente.
- **Main verificado al comienzo:** `b9e34f2d836b99f711d6227f88c95c7b5ba7bd85`.
- **Rama de auditoría inspeccionada al comienzo:** `docs/btc-alt-cross-asset-universe-recovery-r1`, HEAD `741e29c80edef63aa0f07bfa186f9caa83b3eaf5`.
- **PR:** [#66](https://github.com/eltiootaku01-hue/bot-cripto/pull/66), abierto y sin merge en el inicio de esta ampliación.
- **Resultado:** `PARTIAL`; `BLOCKED_UNIVERSE_DEFINITION` permanece.

## 1. Dictamen ejecutivo

La búsqueda ampliada no recuperó una fuente primaria accesible que enumere exactamente `BTCUSDT` más diecinueve altcoins y demuestre que la lista fue aprobada para `BTC_ALT_CROSS_ASSET_RESEARCH_V1`. La conclusión sigue siendo `UNKNOWN/BLOCKED`, no “la lista nunca existió”.

El antecedente más cercano continúa siendo Product V2 / R5. Product V2 construye una lista dinámica desde el universo activo de Binance Spot USDT y aplica filtros actuales y un límite configurable de candidatos; R5 documenta expresamente un panel fijo de cinco altcoins como `EXPLORATORY_ONLY`, sin reconstruir elegibilidad histórica. Ninguna de estas fuentes constituye la lista canónica solicitada.

**No se infieren ni proponen símbolos para completar las catorce altcoins faltantes.**

## 2. Alcance de la búsqueda y cobertura

### 2.1 Ramas y árboles Git

- GitHub devolvió **71 ramas actuales**, con **71 HEAD distintos** en la consulta de ramas.
- Se consultó el árbol recursivo de cada uno de los 71 HEADs. Las 71 respuestas se recibieron sin truncamiento.
- Se revisaron los nombres de archivos buscando términos relacionados con `universe`, `altcoin`, `cross-asset`, `BTC`, `research`, `Product V2`, listas de símbolos e instrumentos.
- Los árboles relevantes incluyen `main` (`b9e34f2d...`), Product V2 (`f02934d542c6b32e926bf7b10e417279ca93b3c3`), R5 (`dae415947a5d45340e4af96644328a4ba95cb9bd`), PAPER PR #64 (`33e81b57197ac4cae3af53730ac239ea9caa06fe`), ETH handoff PR #65 (`d18a90c08704bd24e0a0ba540fdcbe701545fd89`), continuidad PR #62 (`8af1f92d135ef5f135a4771383fba5f9b4cf835b`) y la rama actual de auditoría.
- No se identificó un manifiesto, CSV, JSON o documento independiente de universo canónico fijo en los nombres de archivo revisados. La única ruta con `BTC_ALT_CROSS_ASSET_RESEARCH_V1` es el informe R1 de esta auditoría, creado durante el trabajo anterior.
- En la rama R5, los archivos de investigación relevantes son `docs/PRODUCT_V2_R5_RESEARCH.md`, `bot_obrero/research_experiments.py`, `bot_obrero/research_portfolio.py` y `bot_obrero/research_strategies.py`; no se encontró un catálogo aprobado de 20 instrumentos.

**Límite de esta comprobación:** el barrido recursivo de las 71 ramas fue de nombres/rutas y metadatos de árbol; no se realizó un escaneo de texto completo de cada blob de cada rama. El contenido de las fuentes Product V2/R5 relevantes se inspeccionó por separado. Ramas eliminadas, refs inaccesibles y documentos externos no están cubiertos por esta comprobación.

### 2.2 Pull requests, issues y mensajes de commits

- Se consultaron las descripciones de los **66 PR accesibles**. No apareció una decisión anterior que fijara y aprobara el universo exacto de veinte instrumentos.
- El antecedente relacionado es [PR #63 — Product V2](https://github.com/eltiootaku01-hue/bot-cripto/pull/63), fusionado. Su descripción define un scanner para el universo activo de Spot USDT y utiliza `--universe-limit 5`; no fija diecinueve altcoins.
- Las otras referencias que mencionan `BTCUSDT` o `ETHUSDT` en PR históricos (por ejemplo [#3](https://github.com/eltiootaku01-hue/bot-cripto/pull/3), [#4](https://github.com/eltiootaku01-hue/bot-cripto/pull/4), [#16](https://github.com/eltiootaku01-hue/bot-cripto/pull/16) y [#19](https://github.com/eltiootaku01-hue/bot-cripto/pull/19)) describen pruebas técnicas de adquisición/indicadores, no una lista de investigación de 20 instrumentos.
- Las búsquedas de issues abiertos/cerrados por el identificador exacto `BTC_ALT_CROSS_ASSET_RESEARCH_V1` y por la frase `19 altcoins` no devolvieron registros.
- Las búsquedas de mensajes de commit por el identificador exacto y por términos como `universe` no devolvieron un commit que establezca la lista. Esta evidencia se limita a coincidencias en mensajes de commit; no prueba ausencia en todo el contenido histórico.
- La historia de PR #63 contiene 74 commits y se consultaron sus mensajes. Los commits pertinentes documentan un scanner BTC/altcoins y un backtest virtual; no se identificó un commit que apruebe el universo fijo de veinte instrumentos.

## 3. Fuentes de código y documentación verificadas

### 3.1 Product V2 no utiliza un panel canónico fijo de veinte instrumentos

- Código en `main`: [`bot_obrero/product_v2.py`](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/bot_obrero/product_v2.py#L112-L129) construye el universo mediante `active_usdt_universe(...)` a partir de datos de instrumentos devueltos por ExchangeInfo.
- El límite de candidatos es configurable; el valor predeterminado documentado es cinco: [`product_v2.py`](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/bot_obrero/product_v2.py#L727-L729).
- La selección recorre candidatos del snapshot que pasan filtros y toma `eligible_snapshot[:args.universe_limit]`: [`product_v2.py`](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/bot_obrero/product_v2.py#L838-L861). Es una selección dinámica basada en el snapshot/filtros actuales, no una lista histórica aprobada de diecinueve altcoins.
- La documentación de Product V2 declara que usa ExchangeInfo, ticker y bookTicker públicos y que los filtros son actuales: [`docs/HUESO_05_H1_ANALYSIS_CLI.md`](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/docs/HUESO_05_H1_ANALYSIS_CLI.md#L61-L79).

### 3.2 R5 declara explícitamente el panel como exploratorio

- La documentación R5 indica que `XRPUSDT`, `STRKUSDT`, `WUSDT`, `SUIUSDT` y `ENAUSDT` forman un panel exploratorio, que no reconstruye el universo histórico y que los filtros de instrumento son metadatos actuales: [`docs/PRODUCT_V2_R5_RESEARCH.md`](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/product-v2-r5-independent-strategies/docs/PRODUCT_V2_R5_RESEARCH.md#L24-L26).
- El código de experimentos fija `PANEL` con esos cinco símbolos, añade `BTCUSDT` como benchmark y registra `panel_classification = EXPLORATORY_ONLY`: [`research_experiments.py`](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/product-v2-r5-independent-strategies/bot_obrero/research_experiments.py#L3-L75).
- El mismo código declara que no se reconstruye la elegibilidad histórica: [`research_experiments.py`](https://github.com/eltiootaku01-hue/bot-cripto/blob/research/product-v2-r5-independent-strategies/bot_obrero/research_experiments.py#L320-L327).

Estos registros prueban que R5 evaluó un panel exploratorio de cinco altcoins y usó BTC como benchmark. No prueban que las cinco monedas pertenezcan a una lista canónica histórica ni permiten inferir las catorce restantes.

## 4. Artefactos históricos Product V2 inspeccionados

Se descargaron cuatro artefactos existentes, se inspeccionó su contenido y se calculó localmente SHA-256 sobre cada ZIP. Cada digest calculado coincidió con el digest publicado por GitHub. No se generaron artefactos nuevos ni se ejecutaron backtests.

| Artefacto / ejecución | HEAD de origen | SHA-256 verificado | Observación relevante del reporte |
|---|---|---|---|
| [Artifact 11777913249 / run 38315700839](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38315700839/artifacts/11777913249) | `f02934d542c6b32e926bf7b10e417279ca93b3c3` | `fa01e78229eaf6979612edd6868552ee0cad04716046ed88d3a5f0bbd02a1c57` | 502 pares Spot USDT activos, 502 snapshots de ticker válidos, 172 candidatos tras filtros, 5 candidatos backtesteados. El bloque de resultados presenta cinco candidatos; no es una lista histórica fija. |
| [Artifact 11762850937 / run 38281679096](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38281679096/artifacts/11762850937) | `b929b17e49c781cf5f57cb87460a47f6d8c83084` | `d3fb2653217b5ea604a78ac7a80df172f194dabcb625ebdc789c9318ec21e3ea` | 502 pares Spot USDT activos, 163 candidatos tras filtros, 5 backtesteados. |
| [Artifact 11757110462 / run 38269983472](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38269983472/artifacts/11757110462) | `60bc866981686687bb1f19e1c25d09b159a3a9a3` | `aefba64817e4536360378969aa30208b27a8a9f042e4271048282274d3c3cce9` | 502 pares Spot USDT activos, 167 candidatos tras filtros, 2 backtesteados. |
| [Artifact 11749504673 / run 38254482642](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38254482642/artifacts/11749504673) | `9e455fef0fa416db8cd1076291a0e128f14f86c3` | `c10c53db611fe6e7be81f12bffe2d42040cac066b0df78f5852c5895e99aa866` | 502 pares Spot USDT activos y 2 candidatos backtesteados. |

Los informes Product V2 contienen varias decenas de cadenas de símbolo `...USDT` porque combinan resultados, benchmark y diagnósticos de candidatos/exclusiones del snapshot actual; aparecen también stablecoins/fiat y activos tokenizados que el propio reporte clasifica como no-altcoin. Esas cadenas no deben contarse como una lista canónica de altcoins.

### Artefacto R5 previamente verificado

El informe R1 registra la descarga y verificación del [artifact 11812924702 / run 38399941811](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38399941811/artifacts/11812924702), con SHA-256 `035624ca0a86b1ab1863f85660fe37a8c103d6a39a1f180d88422bd20d9b293f`. En esta ampliación se volvió a comprobar su metadata de GitHub y se inspeccionaron el documento y el código fuente R5; no se volvió a descargar ese ZIP porque su verificación está registrada en R1. La clasificación reportada es `EXPLORATORY_ONLY`.

## 5. Hallazgos clasificados

| ID | Hallazgo | Clase | Qué permite concluir |
|---|---|---|---|
| R2-01 | Se enumeraron 71 ramas actuales y se consultaron sus 71 árboles recursivos; ninguna respuesta de árbol indicó truncamiento. | `OBSERVED` | La inspección de rutas cubre las refs de rama visibles en esa consulta, en la fecha indicada. |
| R2-02 | El inventario de rutas no reveló un manifiesto independiente con la lista canónica de 20 instrumentos. | `OBSERVED` | No se halló un archivo dedicado por nombre; no equivale a demostrar inexistencia histórica. |
| R2-03 | Los 66 cuerpos de PR se revisaron por referencias de universo/símbolos; no se encontró una aprobación anterior de la lista fija. | `OBSERVED` | La descripción de PR #63 es evidencia de universo Product V2 dinámico, no de una lista histórica aprobada. |
| R2-04 | Búsquedas de issues por el identificador exacto y `19 altcoins` no devolvieron resultados; las búsquedas de mensajes de commit indicadas tampoco hallaron una decisión. | `TESTED` | Resultado limitado de las consultas aplicadas, no prueba de inexistencia en cualquier contenido. |
| R2-05 | Cuatro artefactos Product V2 descargados tuvieron un SHA-256 local idéntico al digest publicado por GitHub. | `TESTED` | Se verificó integridad de los ZIP frente a su digest publicado; no valida elegibilidad histórica ni rentabilidad. |
| R2-06 | El scanner Product V2 usa un universo activo de Spot USDT y filtra candidatos con metadatos/snapshots actuales. | `OBSERVED` | Su lista de candidatos cambia según el snapshot y el límite configurado; no es el universo canónico requerido. |
| R2-07 | R5 define cinco altcoins como `EXPLORATORY_ONLY` y `BTCUSDT` como benchmark, e indica que no reconstruye elegibilidad histórica. | `OBSERVED` | El panel no puede elevarse a la lista aprobada de 19 altcoins. |
| R2-08 | La lista histórica exacta y evidencia de su aprobación siguen sin aparecer dentro del alcance consultado. | `UNKNOWN` | No se puede confirmar qué catorce símbolos faltan ni cuándo/quién habría aprobado la lista. |
| R2-09 | Pueden existir fuentes fuera de las refs/artifacts accesibles revisados, por ejemplo archivos eliminados, datos externos o un registro no publicado. | `UNKNOWN` | La búsqueda no permite concluir que la lista nunca existió. |
| R2-10 | No hay base suficiente para ejecutar un backtest de este proyecto con una lista histórica de 20 instrumentos. | `BLOCKED` | Mantener `BLOCKED_UNIVERSE_DEFINITION`; no inferir símbolos ni reemplazar el universo sin decisión del propietario. |

## 6. Aceptación de esta ampliación

| Criterio | Estado | Evidencia |
|---|---|---|
| R2-AC-01 — Ramas actuales inventariadas y árboles consultados | `PASS` | 71 refs de rama; 71 árboles recursivos no truncados. La comprobación de nombres/rutas no es un escaneo integral de cada blob. |
| R2-AC-02 — PR/issues/commit messages ampliados | `PASS WITH LIMITS` | 66 descripciones de PR y consultas exactas de issues/mensajes de commit; el alcance y las limitaciones están documentados. |
| R2-AC-03 — Fuentes Product V2/R5 relevantes inspeccionadas | `PASS` | Documentación/código relevante y artefactos de reportes seleccionados. |
| R2-AC-04 — Digest de cuatro artefactos Product V2 verificado | `PASS` | Los cuatro SHA-256 locales coinciden con los digests publicados por GitHub. |
| R2-AC-05 — Lista canónica recuperada | `NOT MET / UNKNOWN` | No se localizó lista exacta y aprobada de BTCUSDT + 19 altcoins. |
| R2-AC-06 — Estado operativo y continuidad registrados sin inventar símbolos | `PASS` | Este informe y el checkpoint de continuidad de la rama documental mantienen explícitamente el bloqueo. |

## 7. Restricciones y siguiente paso

- `BLOCKED_UNIVERSE_DEFINITION` permanece.
- `NO_TRADE` permanece como decisión operativa.
- No modificar código, estrategias, parámetros o workflows para resolver una incertidumbre documental.
- No iniciar pruebas ni backtests con una lista inventada.
- La vía de recuperación sigue siendo una fuente primaria accesible con los veinte símbolos exactos y su aprobación; si no aparece, el propietario debe autorizar explícitamente la creación de una lista nueva, versionada como nueva y no como reconstrucción histórica.
- Una actualización documental del PR puede disparar workflows automáticos por el evento `pull_request`. BOT OBRERO no solicita ni reintenta esos workflows; cualquier estado que se cite debe corresponder al HEAD exacto que figura en GitHub.

---

**Conclusión de BOT OBRERO:** `PARTIAL`. La búsqueda histórica se amplió a ramas, PR, issues, mensajes de commit, código Product V2/R5 y artefactos de reportes existentes. Se reforzó con evidencia positiva que Product V2 usa un universo dinámico y R5 un panel exploratorio, no una lista fija aprobada. La lista canónica de BTC + 19 altcoins continúa `UNKNOWN`; el bloqueo permanece para revisión de CEREBRO y decisión del propietario.