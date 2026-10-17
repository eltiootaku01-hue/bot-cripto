# BOT-CRIPTO — BTC/ETH Strategy Research V1

**Fecha de registro:** 2026-10-17  
**Estado:** investigación virtual reproducible; no es PAPER operativo ni LIVE  
**Decisión:** **NO_ROBUST_EDGE_FOUND / NO_TRADE**  
**Base de la rama:** main en 8bbd9fc9511690db171c163b4a978a07e8aeb222. Esta investigación vive en una rama separada de PR #64; no cambia el código operativo ni autoriza su merge.

## 1. Propósito y criterio de decisión

Comparar un conjunto pequeño de reglas fijadas de antemano sobre el dataset BTC/ETH anual verificado, usando cash y buy-and-hold como referencias económicas, el mismo modelo de costes para todos los candidatos y cortes cronológicos explícitos. No se seleccionan parámetros para forzar rentabilidad. Una estrategia solo podrá proponer revisión posterior si muestra resultados netos positivos y coherentes en los folds de validación, el intervalo de evaluación y escenarios de costes; este estudio por sí solo no habilita una estrategia.

## 2. Procedencia e identidad de datos

- Fuente: artefacto **btc-eth-follow-365d-f8b9c36768f68ee754f1cb217f920dea25ef445e**, generado por [GitHub Actions run 39062915950](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39062915950) con el commit f8b9c36768f68ee754f1cb217f920dea25ef445e.
- Ventana disponible: 2025-01-15 19:00 UTC a 2026-10-17 18:00 UTC, 15.360 velas de una hora por activo.
- Dataset SHA-256 canónico: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Configuración de adquisición SHA-256: `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`.
- ZIP de fuente SHA-256: `9a8843e46f9907fef091a916d8ad1438705999e89e20c991936f29a7e851a3c0`.
- Activos: BTCUSDT y ETHUSDT. Las marcas horarias están alineadas; el replay utiliza los mismos bytes normalizados para todos los candidatos.

**Limitación temporal crítica:** los 15.360 registros de cada activo conservan `available_at=UNKNOWN` y la finality también es UNKNOWN para los 15.360. Los hashes acreditan identidad e integridad de los bytes, no prueban que cada vela estuviera publicada, disponible y finalizada en el instante histórico de decisión. Las reglas de lote/min-notional son el snapshot de filtros capturado en la adquisición; no existe una serie histórica de reglas del exchange por cada vela.

## 3. Separación cronológica

Todos los límites son UTC y los intervalos son semiabiertos `[inicio, fin)`. El calentamiento se conserva para indicadores y no se puntúa.

| Segmento | Velas | Inicio UTC | Fin exclusivo UTC |
|---|---:|---|---|
| Warm-up | 2.160 (90 días) | 2025-01-15 19:00 | 2025-04-15 19:00 |
| VALIDATION_1 | 1.440 (60 días) | 2025-04-15 19:00 | 2025-06-14 19:00 |
| VALIDATION_2 | 1.440 (60 días) | 2025-06-14 19:00 | 2025-08-13 19:00 |
| VALIDATION_3 | 1.440 (60 días) | 2025-08-13 19:00 | 2025-10-12 19:00 |
| EVALUATION | 8.760 (365 días) | 2025-10-12 19:00 | 2026-10-12 19:00 |
| Sin puntuar | 120 | 2026-10-12 19:00 | 2026-10-17 18:00 (última vela disponible incluida) |

Los tres folds de validación preceden y no se solapan con la evaluación de V1. **No obstante, este intervalo no es un holdout global inmaculado:** el estudio anterior ya había evaluado una ventana que se solapa parcialmente con EVALUATION para EMA/RSI y BTC-gated. Por eso este resultado es exploratorio, especialmente tras comparar siete reglas, y no debe presentarse como una confirmación prospectiva independiente. Toda optimización futura deberá dejar un periodo nuevo aún no examinado o pasar a PAPER prospectivo con reconciliación demostrada.

## 4. Contrato de simulación y costes comunes

- Capital inicial hipotético: 31 USDT; no representa ARS ni conversión a pesos.
- Spot long-only, sin apalancamiento ni posiciones cortas; hasta 95% del quote disponible por entrada; máximo de cinco entradas por ventana móvil de 30 días.
- Señal a cierre de una vela cerrada; fill simulado en la apertura de la siguiente vela. Liquidación terminal en el último cierre del periodo, aplicando el coste de salida y comisión.
- Cantidades filtradas con Decimal según los lotes y notional mínimo disponibles en el snapshot; ese snapshot no es evidencia de los filtros históricos.
- Cash mantiene 31 USDT sin operar; buy-and-hold entra al comienzo del intervalo y sale al final con los mismos supuestos de coste, y su retorno/drawdown se informa para el mismo intervalo.

| Escenario | Comisión por fill | Spread completo supuesto | Slippage por lado supuesto |
|---|---:|---:|---:|
| BASE | 0,10 % | 10 bps | 5 bps |
| STRESS_1 | 0,10 % | 25 bps | 10 bps |
| STRESS_2 | 0,20 % | 50 bps | 20 bps |

No se dispone de spread histórico ni de datos de ejecución en libro de órdenes; el spread y deslizamiento son escenarios modelados, no cotizaciones observadas.

## 5. Reglas evaluadas sin ajuste posterior

1. **EMA12_26_RSI:** ETH long-only; entrada EMA12 cruza sobre EMA26 y RSI14 ≥ 50; salida por cruce bajista o RSI14 ≥ 70.
2. **EMA24_96_CROSS:** ETH long-only por cruce de EMA24/EMA96.
3. **DONCHIAN_24_12:** compra cuando el cierre supera el máximo de las 24 velas previas; venta si queda por debajo del mínimo de las 12 velas previas. Los canales usan únicamente velas anteriores.
4. **RSI_MEANREV_EMA200:** compra en cruce descendente de RSI14 a ≤ 30 mientras cierre > EMA200; venta con RSI14 ≥ 55 o cierre < EMA200.
5. **BTC_GATED_ETH_TREND:** ETH long-only; entrada con cierre ETH > EMA50 de ETH, cierre BTC > EMA50 de BTC y momentum ETH a 72 h positivo; sale si falla cualquiera de las condiciones.
6. **ETH_TSMOM_168H:** ETH long-only; entrada con momentum de 168 h positivo y cierre > EMA100; salida si cualquiera falla.
7. **BTC_ETH_RS_ROTATION_168H:** como máximo un activo largo (BTC o ETH); selecciona el de mayor momentum positivo a 168 h, y mantiene cash si ninguno es positivo. La decisión al cierre se ejecuta en la siguiente apertura.

Cash, buy-and-hold de BTC y buy-and-hold de ETH son benchmarks, no estrategias elegibles para promoción automática.

## 6. Resultados de EVALUATION — 365 días

Retorno total modelado neto de los costes de cada escenario. Las cifras están basadas en capital inicial virtual de 31 USDT. El drawdown se calcula sobre la curva de equity simulada.

| Estrategia / referencia | BASE retorno | BASE max DD | STRESS_1 retorno | STRESS_1 max DD | STRESS_2 retorno | STRESS_2 max DD |
|---|---:|---:|---:|---:|---:|---:|
| ETH_TSMOM_168H | **+15,5138 %** | -11,7690 % | **+1,9100 %** | -18,2347 % | -26,3695 % | -35,0012 % |
| CASH | 0,0000 % | 0,0000 % | 0,0000 % | 0,0000 % | 0,0000 % | 0,0000 % |
| RSI_MEANREV_EMA200 | -5,5015 % | -8,1254 % | -7,7376 % | -9,6616 % | -13,2173 % | -14,3522 % |
| EMA12_26_RSI | -8,1642 % | -17,2901 % | -19,7500 % | -25,4714 % | -43,4641 % | -45,6546 % |
| BTC_ETH_RS_ROTATION_168H | -25,3829 % | -28,5890 % | -34,4600 % | -37,0587 % | -53,1135 % | -54,5918 % |
| BUY_HOLD_BTCUSDT | -28,1815 % | -49,7730 % | -28,3609 % | -49,7730 % | -28,8250 % | -49,7730 % |
| BUY_HOLD_ETHUSDT | -39,4498 % | -64,4777 % | -39,6010 % | -64,4777 % | -39,9923 % | -64,4777 % |
| EMA24_96_CROSS | -40,9066 % | -47,8306 % | -47,4078 % | -51,7670 % | -60,9674 % | -62,0104 % |
| BTC_GATED_ETH_TREND | -42,5025 % | -44,6673 % | -49,8513 % | -51,5124 % | -64,8043 % | -65,7650 % |
| DONCHIAN_24_12 | -51,8667 % | -53,7580 % | -57,9993 % | -59,2185 % | -70,5307 % | -70,7675 % |

El momentum ETH de 168 h con EMA100 es el candidato más fuerte en la evaluación, pero no sobrevive al escenario de estrés 2 y cae un 26,37 %. Además, su ventaja base se produjo en un periodo con selección múltiple de siete reglas y no se repitió consistentemente en validación. No se debe concluir que existe un edge robusto.

## 7. VALIDATION — retornos BASE por folds no solapados

Incluye buy-and-hold bajo la misma hipótesis de costes. Los valores son retornos netos por fold; cash es 0 % en cada fold.

| Estrategia / referencia | VALIDATION_1 | VALIDATION_2 | VALIDATION_3 |
|---|---:|---:|---:|
| CASH | 0,0000 % | 0,0000 % | 0,0000 % |
| BUY_HOLD_BTCUSDT | +24,3761 % | +15,7751 % | -7,5929 % |
| BUY_HOLD_ETHUSDT | +55,4778 % | +87,3421 % | -13,6294 % |
| EMA12_26_RSI | -1,1861 % | -9,4934 % | -1,1437 % |
| EMA24_96_CROSS | -16,6210 % | +59,7066 % | -12,3140 % |
| DONCHIAN_24_12 | -14,6647 % | -4,3318 % | -5,5995 % |
| RSI_MEANREV_EMA200 | +2,8361 % | -0,8675 % | -3,6486 % |
| BTC_GATED_ETH_TREND | -2,0794 % | +0,6530 % | +5,5483 % |
| ETH_TSMOM_168H | -10,6421 % | +4,6558 % | -14,7518 % |
| BTC_ETH_RS_ROTATION_168H | -11,1510 % | +16,9376 % | -22,8527 % |

No hay una estrategia activa con retorno neto positivo en los tres folds de validación base y, a la vez, retorno positivo bajo los tres escenarios de coste en la evaluación. Los resultados de folds no son una licencia para ajustar reglas sobre el mismo intervalo y luego volver a llamarlo holdout.

## 8. Protección contra lookahead y comprobación del motor

- La prueba de invariancia del sufijo futuro terminó en **PASS**: se perturbaron precios OHLC posteriores al timestamp de corte para ambos activos y se comprobó que las decisiones anteriores no cambiaran.
- Se comprobaron seis cortes temporales con siete estrategias (42 combinaciones de corte/estrategia revisadas).
- Donchian lee explícitamente ventanas desplazadas de velas anteriores; las decisiones tienen timestamp al cierre y los fills están desplazados a la siguiente apertura.
- Calibración contra el backtest canónico previo en la ventana 2025-04-15 19:00 UTC a 2026-04-15 19:00 UTC: EMA12/EMA26 + RSI produjo -26,2809072 % con 114 fills, frente al -26,2809072 % y 114 fills del informe de referencia. Buy-and-hold ETH reprodujo +46,1918281 %. Esa calibración comprueba paridad contable, no rentabilidad ni validez punto-en-tiempo.

Hashes del cálculo de este estudio:
- Script ejecutado SHA-256: `5a4b0b5c26d711655bb793eff827f28f548bd0050b5134ea307a6e9c3af32cd7`.
- JSON de resultados SHA-256: `db7f58f0956b794ec1a1e62008896e782efa34c47e8985a1af71cc5294949624`.
- Runtime local: Python 3.13.5, NumPy 2.3.5, pandas 2.2.3. El ejecutable de investigación está versionado en `research/btc_eth_strategies_v1.py` y sus dependencias en `research/requirements-btc-eth-strategies-v1.txt`. Se añadió `.github/workflows/btc-eth-strategies-v1-replay.yml` para descargar el artefacto fuente, reproducir el estudio y comprobar hashes, cortes, anti-lookahead y métricas de control. No se considera verificado por CI hasta revisar una ejecución exitosa de ese workflow. El script exacto ejecutado y el JSON de 120 métricas también se adjuntan a la respuesta.

## 9. Bloqueos y decisión

1. **No hay evidencia de ventaja neta robusta:** el candidato líder cae en estrés 2 y es negativo en dos folds de validación.
2. **La disponibilidad temporal sigue UNKNOWN** para todos los registros; perturbación anti-lookahead en el código no puede corregir metadatos temporales ausentes.
3. La evaluación V1 se solapa con parte del estudio previo para algunas estrategias, y se compararon siete alternativas. El resultado es exploratorio, no un test globalmente intacto.
4. Spread/slippage y filtros históricos por vela no son observados; son supuestos modelados usando el snapshot de filtros disponible.
5. No se autoriza usar ningún retorno del research como señal de operación ni como aprobación del ciclo de ejecución. La integración B1-R/PAPER y su recuperación económica siguen siendo una validación separada.
6. El simulador de esta ronda usa aritmética binaria de Python/NumPy para exploración, no Decimal de extremo a extremo como el contrato económico canónico. Aunque se calibró el resultado EMA/RSI contra el backtest de referencia, esto no lo convierte en motor financiero apto para promoción; toda futura aceptación necesitará contabilidad Decimal y reconciliación de fills/balances.

**Dictamen final: `NO_ROBUST_EDGE_FOUND`. Decisión operativa: `NO_TRADE`.** Mantener PR #64 abierta y sin merge. No habilitar LIVE, no crear órdenes reales, no prometer ganancias futuras. Una siguiente iteración requiere fuente punto-en-tiempo verificable o un holdout nuevo no consultado previamente, controles adicionales de robustez y, tras eso, PAPER prospectivo reconciliado.
