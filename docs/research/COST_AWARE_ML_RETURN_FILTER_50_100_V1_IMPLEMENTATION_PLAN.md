# Plan mínimo de implementación aislada — COST_AWARE_ML_RETURN_FILTER_50_100_V1

**Fecha:** 2026-10-21 UTC  
**Estado del plan:** `REGISTERED / IMPLEMENTATION NOT STARTED`  
**Candidato:** [dictamen COST_AWARE_ML_RETURN_FILTER_50_100_V1_DECISION.md](COST_AWARE_ML_RETURN_FILTER_50_100_V1_DECISION.md)  
**Rama exclusiva:** `research/cost-aware-ml-return-filter-50-100-v1`  
**HEAD de rama al registrar este plan:** `25f29ffca3315d7b07f872c910293c26455425c6`  
**HEAD de `main` observado:** `c523c47364b6fb4febd9c48385bc63bcde87ea61`  
**Decisión operativa:** `NO_TRADE`

> Esta publicación agrega solo este documento. No se han modificado módulos Python, tests, dependencias ni workflows; tampoco se ejecutaron pruebas, entrenamientos, backtests, capturas o workflows. El plan queda preregistrado en GitHub antes de cualquier ejecución económica.

## 1. Resultado de la inspección

### Estado observado del repositorio

- `pyproject.toml` declara `requires-python >=3.11`, dependencia de runtime `websocket-client==1.9.2`, extra de tests con pytest y excluye `research*` del descubrimiento de paquetes distribuibles. Por tanto, el candidato ML debe permanecer en `research/` y no sumarse a dependencias del ejecutor operativo.
- `research/requirements-btc-eth-strategies-v1.txt` ya aísla `numpy==2.3.5` y `pandas==2.2.3` para investigación. No existe actualmente una dependencia XGBoost en el manifiesto del proyecto que se inspeccionó.
- `research/strategy_tournament_v2.py` y `research/test_strategy_tournament_v2.py` aportan patrones reutilizables: hashes canónicos, datos sintéticos, parámetros deterministas, costes fijos, benchmark pasivo, bootstrap reproducible y una prueba de perturbación de sufijo futuro. Ese torneo tiene 6.232 candidatas y no encontró un ganador elegible; no se volverá a ejecutar ni a reajustar como parte de este plan.
- `bot_obrero/prospective_btc_eth_v2.py` y `tests/test_prospective_btc_eth_v2.py` ya contienen patrones de timestamps UTC, velas cerradas, `Decimal`, quotes recibidas después de la decisión, edad máxima del quote, hashes, validación de huecos, reproducción determinista y pruebas de invariancia frente a cambios del futuro. Se reutilizarán los contratos o validadores adecuados, no se copiará un segundo colector ni se modificará el runtime.
- El histórico canónico documentado contiene BTCUSDT/ETHUSDT de 1 h, desde 2025-01-15 19:00 UTC hasta 2026-10-17 18:00 UTC, con hash lógico `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`. En los registros previos, `available_at` y finality históricos son `UNKNOWN`; el hash prueba identidad, no disponibilidad punto-en-tiempo.
- La [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) permanece abierta y bloqueada a la aceptación de una captura real emparejada Kline + bookTicker de al menos 24 h. No hay T0 prospectivo aceptado según esa evidencia. Esta condición no se debe simular, completar con backfill ni saltar.

**Clasificación:** arquitectura observada; pruebas nombradas observadas en el árbol; compatibilidad exacta de las ruedas XGBoost/NumPy/Pandas aún pendiente de comprobación; edge económico y resultado de entrenamiento desconocidos.

## 2. Límites de alcance

Este plan autoriza primero diseño e implementación en la rama ya creada. No autoriza merge, cambio de `main`, ejecución PAPER/LIVE, acceso privado de exchange, backtest con el histórico usado para seleccionar, búsqueda de hiperparámetros, ni inicio del año prospectivo. La implementación y los tests sintéticos deberán revisarse antes de proponer cualquier evaluación prospectiva.

No se incorporará este candidato como una octava regla dentro de `BTC_ETH_STRATEGIES_V2` ni se editará su protocolo, métricas, resultados, calendario ni gate. Este es un estudio separado, con su propio modelo, manifiesto, T0 y decisión.

## 3. Archivos mínimos previstos en la siguiente fase

1. `research/cost_aware_ml_return_filter_v1.py` — módulo autocontenido para schema/features causales, creación de labels maduros, entrenamiento único, carga/verificación del modelo, filtro de coste, simulador reproducible y salida de métricas/manifest. No importará ni invocará el adaptador de órdenes.
2. `research/test_cost_aware_ml_return_filter_v1.py` — tests sintéticos y deterministas, sin red ni lectura del archivo histórico real.
3. `research/requirements-cost-aware-ml-v1.txt` — dependencias estrictamente de investigación fijadas por versión. No modificar `pyproject.toml` ni los requisitos del runtime.
4. Este plan y el dictamen existente quedan como especificación. La futura implementación debe modificar solo los ficheros enumerados de investigación y sus tests, además del manifiesto de dependencias; los cambios de contrato requieren revisión de CEREBRO.

No se crea ahora ninguno de los ficheros de implementación ni el requirements nuevo.

## 4. Dependencias y reproducibilidad

La versión oficial estable actual de XGBoost documenta distribución de paquetes binarios y el uso de CPU; el formato nativo JSON/UBJSON es la vía recomendada para conservar el modelo, mientras que pickle/joblib no es estable entre versiones y no debe usarse para artefactos no confiables:
- [XGBoost Installation Guide](https://xgboost.readthedocs.io/en/stable/install.html)
- [XGBoost Model IO](https://xgboost.readthedocs.io/en/stable/tutorials/saving_model.html)
- [XGBoost Python API](https://xgboost.readthedocs.io/en/stable/python/python_api.html)

Regla mínima:
- Fijar una versión exacta de Python y todas las dependencias de investigación en el archivo aislado; el candidato de entorno inicial es Python 3.13.16.
- La propuesta de versión del dictamen es XGBoost `3.4.1`, NumPy `2.3.5`, pandas `2.2.3`; aún se debe validar disponibilidad de wheels y compatibilidad real en el entorno elegido antes de ejecutar tests o entrenar. No instalar una versión flotante ni cambiar de versión después de empezar a puntuar sin incrementar la versión del protocolo.
- Registrar el lock/resolución exacta de dependencias, sistema operativo y versión Python. Si la combinación no instala limpiamente, detenerse y actualizar el plan con evidencia antes de ejecutar la suite; no hacer cambios silenciosos para “hacerlo pasar”.
- Fijar `random_state=20261021`, `n_jobs=1`, `tree_method=hist` y los hiperparámetros indicados en el dictamen. No habilitar GPU ni entrenamiento distribuido en V1.
- Guardar el modelo con `save_model(... .json)` o `save_model(... .ubj)`. El manifest debe contener SHA-256 del modelo y del dataset de entrenamiento, versión de modelo, versión de librería, lista y orden de las features, hiperparámetros completos, semilla, código/commit SHA, timestamps del conjunto y hash de configuración. Incluir también los parámetros de entrenamiento que el archivo del modelo no conserva por sí solo.
- Las pruebas de determinismo se ejecutarán en un entorno fijado; exigir idénticas predicciones dentro de tolerancia cero o la tolerancia numérica que quede registrada antes de probar, y paridad de predicción al guardar/cargar. No exigir igualdad binaria entre sistemas/arquitecturas distintos sin evidencias de que sea una garantía soportada.

La dependencia XGBoost nunca pasa a `[project].dependencies`, CLI general ni código de ejecución. El research extraset no debe elevar el coste ni el riesgo de despliegue del bot.

## 5. Contrato de entrada, labels y entrenamiento

### Datos permitidos

- Señal sobre BTCUSDT Spot, timeframe 1 h, con velas cerradas y recibidas; decisión solo después de que la última vela esté marcada FINAL/COMPLETE.
- Features según el dictamen: retornos rezagados 1/3/6/12/24/72/168 h; volatilidad en 24/72/168 h; rango y cuerpo de la última vela; estadísticos de volumen basados solo en ventanas completas previas.
- El entrenamiento inicial puede usar el histórico fijado exclusivamente para ajustar el modelo antes de T0. Su rendimiento retrospectivo no puede usarse como evidencia de rentabilidad, escoger hiperparámetros ni estimar el resultado esperado.
- Hash del dataset de entrenamiento esperado: el hash lógico archivado arriba, o una nueva fuente de entrenamiento aceptada con su propio SHA/manifest. Cualquier diferencia de bytes/configuración requiere revisión previa y queda versionada; no sustituir fuentes sin documentarlo.

### Etiqueta de entrenamiento

Para una fila de decisión en la hora `t`, `y_t = close[t+24h] / close[t] - 1`, retornos brutos. No se usarán columnas de resultados futuros como features.

- La fila solo es entrenable si su cierre objetivo y toda su ventana están completos antes de T0.
- Purga mínima de 24 h entre el último timestamp de label del entrenamiento y T0; no admitir labels que atraviesen el inicio prospectivo.
- Orden cronológico explícito; prohibido barajar para validación temporal o usar folds aleatorios.
- No debe ejecutarse un backtest histórico para seleccionar hiperparámetros, arquitectura o umbral. El modelo congelado se evalúa únicamente en el segmento prospectivo nuevo.
- El diseño etiqueta retorno bruto OHLC como objetivo del regresor; la señal solo se acepta contra una estimación del coste de ida y vuelta, y toda decisión económica final se mide desde fills quote-aware. La diferencia entre la etiqueta aproximada y los fills reales/simulados debe describirse como limitación, nunca esconderse como “net return prediction”.

## 6. Contrato de señal y coste

Se congelan los parámetros indicados en el dictamen:
- Un pronóstico bruto a 24 h por cada vela cerrada.
- Entrada únicamente cuando `predicted_return > 1.5 × estimated_round_trip_cost`; el factor 1.5 no se ajusta mirando resultados.
- Una posición BTC como máximo, largo-only, sin órdenes privadas; 95 % del saldo de quote como límite de asignación, con filtros Spot efectivos y redondeo hacia abajo.
- Entrada al ask y salida al bid del primer quote elegible posterior a la decisión/salida, recibido con edad ≤2 segundos; falta de quote, desalineación del reloj o agujero de datos bloquean la observación. No usar la apertura/cierre OHLC anterior como precio alternativo.
- Salida después de 24 h y en el primer quote válido que cumpla el límite temporal de salida. Sin stop-loss, take-profit o salida discrecional en V1.
- Comisión por cada fill, calculada sobre el notional ejecutado conforme al contrato; slippage adverso añadido al fill. El bid/ask ya incorpora el spread: **no sumar una segunda penalización de spread** en contabilidad.
- Contrastar BASE: fee 0,10 % y slippage 5 bps por lado; STRESS_1: fee 0,10 % y slippage 10 bps; STRESS_2: fee 0,20 % y slippage 20 bps. Spread obtenido de los quotes aceptados. Registrar cada componente y conciliar el PnL a equity inicial/final.
- Cálculo de cantidad, fees, balances y PnL con `Decimal`; nunca redondear al alza para satisfacer un mínimo de orden. Si el tamaño de capital no permite cumplir los filtros Spot, rechazar la entrada y registrarlo.

Benchmarks: cash estable en 100 USDT, buy-and-hold BTC y buy-and-hold ETH con inicio/final en los mismos límites temporales y costes equivalentes; los fills de benchmark también requieren quotes válidos.

## 7. Suite mínima de pruebas sintéticas antes de cualquier evaluación

Se pueden reutilizar las convenciones y fixtures de `research/test_strategy_tournament_v2.py` y `tests/test_prospective_btc_eth_v2.py`, pero añadir pruebas específicas del modelo:

1. **Causalidad de features:** calcular prefijo y secuencia completa; las features hasta cada cutoff deben coincidir. Perturbar solo el sufijo futuro en al menos 24 cutoffs deterministas; ningún valor previo podrá cambiar.
2. **Alineación de labels:** probar manualmente ejemplos con `t+24h`; rechazar objetivo ausente, duplicado o no cronológico. Confirmar que la purga evita que cualquier label de entrenamiento toque o atraviese T0.
3. **Aislamiento del periodo puntuado:** alterar completamente el conjunto prospectivo y comprobar que el fit/bytes del modelo congelado no cambian; la rutina de entrenamiento no debe aceptar el conjunto de evaluación como argumento.
4. **Orden e identidad de features:** cambiar orden, nombre o una unidad de feature debe fallar cerrado contra el schema/hash, no producir una predicción válida silenciosamente.
5. **Reproducibilidad:** dos ajustes sobre el mismo fixture sintético, seed/configuración/versiones fijas y un solo hilo dan predicciones idénticas según la regla numérica preregistrada; modelo guardado y recargado devuelve las mismas predicciones.
6. **Costes:** con precios constantes y predicción sin superar el umbral, cero entradas; con señal, las comisiones y slippage reducen equity; el spread se contabiliza solo vía ask/bid. Probar fee de entrada y salida, gaps temporales, balance insuficiente, cantidad redondeada, notional mínimo y reconciliación de equity.
7. **Causalidad temporal de fills:** un quote anterior o igual a la decisión no se puede utilizar como fill; solo un quote posterior, positivo, bid ≤ ask y fresco puede ser elegible. Ausencia/staleness debe bloquear, no recurrir a OHLC.
8. **Fallos cerrados:** hashes no coincidentes, velas abiertas, huecos/duplicados, datos nulos/no finitos, timestamps UTC ausentes, quote inválido, artefacto de modelo/configuración cambiados y versión no soportada devuelven error explícito/INCOMPLETE.
9. **Benchmarks y reporte:** fixture sintético que cuadre capital inicial + PnL neto = equity final; cash permanece exactamente sin operar; las comparaciones usan la misma ventana/costes; el informe conserva también resultados negativos.

Estas pruebas sintéticas validan implementación/contratos; no constituyen una evidencia de rentabilidad ni sustituyen la captura prospectiva.

## 8. Secuencia por gates

| Gate | Acción | Salida requerida | Regla de bloqueo |
|---|---|---|---|
| G0 — plan | Este documento versionado antes de ejecutar | SHA de plan y rama verificados | No iniciar tests, training ni workflows si no se logra verificar el plan |
| G1 — dependencia | Crear requirements aislado y comprobar compatibilidad del entorno fijado | Resolución exacta, versiones y hash de manifest | Si la instalación/wheels falla, actualizar plan antes de continuar |
| G2 — código | Implementar módulo y tests sintéticos en `research/` | Diff acotado; nada en runtime, `main` o workflows existentes | Cualquier cambio de contrato exige revisión CEREBRO |
| G3 — pruebas sintéticas | Ejecutar solo el archivo de tests de investigación, inicialmente de forma local/aislada | Test log completo y SHA probado | No ejecutar datos históricos ni iniciar CI hasta autorización separada |
| G4 — inspección | Revisar diff, tests, costes, hashes y guardrails | Dictamen CEREBRO que acepte/rechace el ejecutor | No iniciar captura puntuable/T0 antes de pasar G4 |
| G5 — evidencia de captura | Completar #98 y validar el paquete Kline + bookTicker | Status VALID, solapamiento exigido, hashes y manifiestos | Si falta captura real aceptada, T0 queda sin fijar |
| G6 — freeze | Entrenar una sola vez con datos previos a T0, exportar modelo/manifest, registrar dependencias y reglas en commit de preregistro | SHA del código/modelo/config/dataset + T0 fijado antes de la primera hora de scoring | Un cambio posterior invalida el preregistro y requiere protocolo nuevo |
| G7 — evaluación | Solo tras aprobación expresa, ejecutar evaluación prospectiva completa de 365 días | Artefactos, fills, métricas y manifiesto íntegros | Hueco/quote inválido/falta de provenance => INCOMPLETE / NO_TRADE |
| G8 — dictamen | Aplicar todas las puertas del dictamen original | CANDIDATE_FOR_SECOND_INDEPENDENT_REVIEW o NO_ROBUST_EDGE_FOUND | No promover automáticamente a PAPER ni LIVE |

**Este trabajo solo completa G0.** No se ha ejecutado ningún elemento de G1–G8. El hecho de que G0 esté registrado no autoriza por sí solo a iniciar el siguiente gate.

## 9. Gates económicos que no deben alterarse

Se heredan del dictamen original, sin ajuste posterior:
- Capital inicial hipotético: 100 USDT y objetivo neto mínimo +50 USDT; +100 USDT se informa como objetivo superior, no como garantía.
- Neto BASE ≥+50 USDT; STRESS_1 positivo; STRESS_2 no negativo.
- Drawdown máximo ≤20 % BASE y ≤25 % en ambos escenarios de estrés.
- Superar cash, buy-and-hold BTC y buy-and-hold ETH en BASE, además del exceso neto positivo ante cash en los tres escenarios.
- Al menos 60 round-trips en el año y 10 por trimestre, o `COVERAGE_INSUFFICIENT`.
- Intervalos bootstrap móvil en bloques de 7 días, 10.000 réplicas, seed registrada, corrección Holm al 5 % para las comparaciones predefinidas; el límite inferior del exceso BASE debe ser positivo frente a cada benchmark.
- Integridad completa de 8.760 horas y quotes de los fills/marcas requeridos; pruebas anti-lookahead y hashes verificados.

Si la integridad falla: `INCOMPLETE / NO_TRADE`. Con datos íntegros pero un gate económico/estadístico fallido: `NO_ROBUST_EDGE_FOUND / NO_TRADE`. No habrá un segundo intento de optimización reportado como un holdout independiente.

## 10. Controles finales de alcance

- `main` no se modifica ni recibe commits de este trabajo.
- No se alteran señal V1, modelos V2, tests generales, workflows generales, configuración de ejecución, adaptadores, barreras ni ledger de PAPER.
- No se ejecutan workflows por este plan, ni se crea uno ahora.
- No se ejecutan backtests históricos, fits del modelo, benchmarks ni órdenes.
- No se declaran XGBoost, la predicción o el umbral como ganador.
- Antes de cada fase posterior se verifica rama/HEAD y diff; si el HEAD cambió inesperadamente, detenerse y reconciliar.
- Estado operativo continuo: **`NO_TRADE`**.

## 11. Fuentes de diseño

- [Dictamen del candidato cost-aware ML V1](COST_AWARE_ML_RETURN_FILTER_50_100_V1_DECISION.md)
- [Protocolo prospectivo BTC/ETH V2](BTC_ETH_STRATEGIES_V2_PROTOCOL.md), usado como referencia de integridad, custodia y evaluación causal; no se modifica con este candidato.
- [Torneo histórico V2 y límites de su evidencia](BTC_ETH_STRATEGY_TOURNAMENT_V2_RESULTS_2026-10-19.md)
- [Issue #98 — captura real pendiente](https://github.com/eltiootaku01-hue/bot-cripto/issues/98)
- [XGBoost installation](https://xgboost.readthedocs.io/en/stable/install.html); [XGBoost model IO](https://xgboost.readthedocs.io/en/stable/tutorials/saving_model.html)


## Nota de corrección de compatibilidad (2026-10-22)

La primera ejecución del gate de resolución se detuvo antes de instalar dependencias: GitHub Windows no tiene disponible CPython 3.12.15 en su manifiesto de intérpretes. La publicación oficial de Python 3.12.15 es solo de código fuente y Python 3.12.10 fue la última 3.12 con instaladores Windows. Para evitar fijar un runtime de Windows sin instaladores actuales, el entorno aislado de esta implementación se ha cambiado a **CPython 3.13.16 x86-64** antes de T0. pandas 2.2.3 documenta compatibilidad con Python 3.13; la resolución binaria exacta y el test sintético siguen siendo gates, no se dan por aprobados hasta el resultado del workflow.

Este cambio afecta únicamente al entorno de investigación y queda versionado aquí; no modifica features, modelo, horizonte, señal, costes ni criterios económicos. Fuentes: [Python 3.12.15](https://www.python.org/downloads/release/python-31215/), [Python 3.12.11 / último instalador de la serie 3.12](https://www.python.org/downloads/release/python-31211/), [pandas 2.2.3 y Python 3.13](https://pandas.pydata.org/pandas-docs/version/2.2.3/whatsnew/v2.2.3.html).
