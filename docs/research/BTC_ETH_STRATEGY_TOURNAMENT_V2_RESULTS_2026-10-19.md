# BTC/ETH Strategy Tournament V2 — resultado verificado

**Fecha de ejecución:** 2026-10-19 UTC  
**Estado CI del torneo:** `success`  
**Commit exacto analizado:** `ded04085f58be4bd8a99c09046930b9c496b7341`  
**Run del torneo:** [GitHub Actions #39314143874](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39314143874)  
**PR de implementación:** [#119](https://github.com/eltiootaku01-hue/bot-cripto/pull/119)  
**Artefacto inmutable del run:** `btc-eth-strategy-tournament-v2-ded04085f58be4bd8a99c09046930b9c496b7341` (JSON completo, CSV de métricas, resumen Markdown y manifiesto; SHA-256 del ZIP: `491c686f89e7a9a1d363f830b4700c1131f8e781125d6e295f2368814cfb7266`).

## Dictamen

**No se encontró una estrategia ganadora robusta. Decisión operativa: `NO_TRADE`.**

Este resultado procede de una búsqueda histórica de muchas variantes, no de un holdout prospectivo. No promover parámetros ni habilitar PAPER automático, LIVE, claves privadas, margen, apalancamiento u órdenes reales a partir de este resultado.

## Volumen y controles completados

- **6.232** configuraciones únicas, congeladas antes del replay y de hash `76b6abf4f61bc05135ea613c813dce2f5c1a44c4869ed42ed3faa18cdf625eae`.
- Cuatro familias: EMA/cruce RSI, momentum con filtros de tendencia, ruptura Donchian y reversión a la media.
- **56.166** replay-escenarios candidato × ventana × costes, de los que 56.088 corresponden a validación y 78 a un diagnóstico histórico separado de los 26 finalistas. Son escenarios de simulación, no 56.166 pruebas unitarias.
- **56.202** filas de métricas incluyendo benchmarks.
- **12/12** comprobaciones representativas de invariancia frente a sufijos futuros pasaron.
- Bootstrap moving-block estratificado por fold, con bloques de 5 días, estadístico máximo sobre toda la familia buscada y **2.000 réplicas por benchmark**, semilla fija `20261019`.
- La workflow volvió a verificar los hashes de entrada, el tamaño y SHA-256 de cada salida y los conteos exactos. La workflow terminó `success` en el SHA de `main` indicado arriba.

## Resultados que impiden la promoción

| Filtro / medida | Resultado |
|---|---:|
| Candidatos positivos en BASE y STRESS_1 en los tres folds y no negativos en STRESS_2 | 26 |
| Candidatos positivos en BASE en los tres folds | 255 |
| Candidatos no negativos en todas las ventanas y escenarios de costes | 183 |
| Candidatos que vencen cash, buy-and-hold BTC y buy-and-hold ETH en BASE en cada fold | 0 |
| Finalistas que también superan todos los controles del diagnóstico histórico largo | 0 |

El primer puesto descriptivo de validación fue `C05793`, `DONCHIAN_BREAKOUT`, con ventana de entrada 72 h y salida 36 h. Sus retornos netos simulados en los tres folds de 60 días fueron:

| Escenario | Fold 1 | Fold 2 | Fold 3 |
|---|---:|---:|---:|
| BASE | +18,86 % | +42,82 % | +8,83 % |
| STRESS_1 | +16,43 % | +40,23 % | +7,98 % |
| STRESS_2 | +10,26 % | +34,60 % | +6,05 % |

Esos números positivos **no bastan para llamarla ganadora**: los folds ya se habían examinado en investigación anterior. En la comprobación descriptiva del período histórico de 365 días ya revisado —que no se usó para ordenar los candidatos— el mismo candidato perdió **25,42 %** en BASE, **31,78 %** en STRESS_1 y **45,90 %** en STRESS_2, con drawdown máximo reportado de **−48,67 %**. Además, no venció a los benchmarks pasivos en todos los folds.

## Corrección por búsqueda múltiple

| Benchmark | Réplicas bootstrap | p-valor familiar exploratorio |
|---|---:|---:|
| Cash | 2.000 | 0,06497 |
| Buy-and-hold BTC | 2.000 | 0,23488 |
| Buy-and-hold ETH | 2.000 | 0,95952 |

Ninguno de estos resultados aporta evidencia suficiente para declarar una ventaja neta robusta. Los p-valores son exploratorios y no convierten datos históricos en evidencia de ejecución futura.

## Identidad y limitación de los datos

- Dataset SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Configuración SHA-256: `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`.
- En ambos activos, **15.360/15.360 velas** tienen disponibilidad histórica (`available_at`) desconocida y finality desconocida. El hash verifica identidad de bytes, no que los datos estuvieran disponibles en cada momento de decisión.
- Los costes son tres escenarios modelados: BASE (fee 0,10 %, spread completo supuesto 10 bps, slippage 5 bps por lado), STRESS_1 (0,10 %, 25 bps, 10 bps) y STRESS_2 (0,20 %, 50 bps, 20 bps). No son tarifas/spreads históricos medidos por fill.
- El capital inicial de los replays es **31 USDT ficticios**, Spot long-only, sin apalancamiento y con máximo de cinco entradas por ventana móvil de 30 días.

## Qué puede cambiar el dictamen

Hace falta una captura real aceptada de Binance Spot Kline + bookTicker con al menos 24 horas de solapamiento (seguimiento [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98)), un preregistro con código y reglas congelados, y después un período futuro independiente con datos punto-en-tiempo válidos. Cualquier hueco o disponibilidad desconocida debe bloquear la evaluación, no repararse retroactivamente para fingir que una cotización estaba disponible.

**Resultado persistente:** el torneo encontró candidatos para estudiar, pero **cero estrategias elegibles para promoción**. Mantener `NO_TRADE` hasta que la evidencia prospectiva independiente satisfaga el gate preregistrado.
