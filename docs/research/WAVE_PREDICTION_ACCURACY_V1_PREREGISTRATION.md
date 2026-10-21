# Pronósticos de ondas V1 — protocolo preregistrado

**Estado:** reglas congeladas antes de calcular los aciertos de enero–octubre de 2026.  
**Rama:** investigación aislada; no habilita PAPER/LIVE.  
**Decisión por defecto:** `NO_TRADE`.  
**Tipo de modelo:** reglas deterministas, sin entrenamiento, sin estado persistente y sin memoria de resultados de pronósticos anteriores.

## Propósito

Medir si reglas transparentes pueden anticipar subidas o bajadas posteriores de BTCUSDT y ETHUSDT. Esta es una prueba de dirección/onda, no una prueba de rentabilidad de trading. Un pronóstico correcto puede seguir siendo inútil después de costes.

## Datos congelados y ventana

- Fuente: artefacto `btc-eth-follow-365d-f8b9c36768f68ee754f1cb217f920dea25ef445e`, workflow run `39062915950`, artifact ID `12111858808`.
- SHA-256 lógico del dataset: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- BTCUSDT y ETHUSDT, velas horarias alineadas. El archivo abarca 2025-01-15 19:00 UTC a 2026-10-17 18:00 UTC.
- Ventana de emisión de pronósticos: desde `2026-01-01T00:00:00Z`, inclusive, hasta el último instante que permita evaluar el horizonte más largo sin salir del archivo. Los resultados no incluyen velas posteriores al 17 de octubre.
- Se conserva el historial anterior al 1 de enero exclusivamente para calcular indicadores causales y su calentamiento; los pronósticos fuera de enero–octubre de 2026 no entran en los porcentajes.
- Integridad requerida: hashes del dataset/configuración, orden horario único, timestamps compartidos entre BTC y ETH, precios OHLC positivos y coherencia OHLC.

## Protocolo de “memoria limpia”

Cada ejecución recalcula las señales desde reglas fijas y el historial de velas disponible hasta el timestamp de la decisión. No se entrena un modelo sobre el período, no hay ajuste de parámetros según los aciertos, ni se guarda estado adaptativo entre ejecuciones. No se borra la historia de GitHub ni los informes previos: se preservan para auditoría. La información posterior a cada decisión solo se consulta para medir el resultado del pronóstico, nunca para generarlo.

## Pronósticos congelados

Las reglas se aplican por separado a BTCUSDT y ETHUSDT, excepto `BTC_LEADS_ETH_4H`, que usa exclusivamente BTC para pronosticar ETH. Los thresholds son fijos, no optimizados sobre esta ventana.

1. `ALWAYS_UP`: baseline ingenuo que pronostica subida en cada decisión.
2. `MOMENTUM_4H`: dirección del retorno de las últimas 4 velas; pronostica solo si su magnitud absoluta es al menos 0,25%.
3. `MOMENTUM_12H`: dirección del retorno de las últimas 12 velas; pronostica solo si su magnitud absoluta es al menos 0,40%.
4. `MEAN_REVERSION_4H`: dirección contraria al retorno de las últimas 4 velas, solo si la magnitud absoluta es al menos 0,75%.
5. `EMA_TREND_12_36`: subida cuando cierre > EMA12 > EMA36 y EMA12 subió frente a hace 4 velas; bajada cuando cierre < EMA12 < EMA36 y EMA12 bajó frente a hace 4 velas. En los demás casos se abstiene.
6. `RSI14_EXTREMES`: subida si RSI14 <= 30; bajada si RSI14 >= 70; en otro caso se abstiene.
7. `DONCHIAN_24H`: subida si el cierre actual supera el máximo de las 24 velas anteriores; bajada si cae por debajo del mínimo de las 24 velas anteriores. La ventana excluye expresamente la vela de decisión.
8. `SWING_STRUCTURE_12H`: subida si el máximo y el mínimo de las 12 velas recientes superan a ambos extremos de las 12 velas previas; bajada si ambos extremos son inferiores; en otro caso se abstiene.
9. `BTC_LEADS_ETH_4H`: para pronosticar ETH, retorno BTC de 4 horas >= +0,50% implica subida; <= -0,50% implica bajada; en otro caso se abstiene.

## Horizontes y definición de éxito

- Horizontes congelados: 4 h, 12 h y 24 h, calculados mediante el cierre de la vela actual y el cierre de la vela de destino.
- “Onda alcista”: retorno futuro >= +0,50%.
- “Onda bajista”: retorno futuro <= -0,50%.
- “Sin onda significativa”: el retorno futuro está entre -0,50% y +0,50%.
- `direction_accuracy_pct`: pronósticos direccionales cuya dirección coincide con el signo del retorno futuro, dividido por todos los pronósticos direccionales. No requiere movimiento de 0,50%.
- `wave_hit_pct` (indicador principal de acierto): pronósticos direccionales que alcanzan al menos +0,50% en la dirección pronosticada, dividido por todos los pronósticos direccionales. Un movimiento inferior al 0,50% cuenta como fallo para esta métrica.
- `coverage_pct`: pronósticos UP/DOWN emitidos dividido por todas las oportunidades de decisión.
- `wave_capture_pct`: ondas reales de ±0,50% correctamente anticipadas dividido por todas las ondas reales de la ventana.
- Se registran también falsos llamados (predijo dirección, pero el resultado fue “sin onda significativa”), distribución UP/DOWN, retornos futuros firmados y tablas por mes.
- Las abstenciones no se cuentan como pronósticos acertados; su efecto se muestra mediante cobertura. El baseline `ALWAYS_UP` permite comparar con una regla sin indicadores.

## Prevención de lookahead y límites

- En la decisión de índice `i`, los indicadores y reglas solo reciben velas con índice <= `i`; Donchian compara contra ventanas desplazadas para excluir la vela actual.
- Cada fila del ledger conserva timestamp de decisión, activo, regla, horizonte, predicción, variables de señal, timestamp de destino, retorno futuro y etiqueta/éxito calculados después.
- La suite perturba velas posteriores a una decisión y comprueba que su pronóstico no cambie.
- **Limitación de procedencia:** el archivo histórico marca la disponibilidad temporal/finality de las velas como UNKNOWN. Por eso la causalidad del cálculo se puede probar, pero no que cada dato estuviera disponible en vivo exactamente en la hora simulada.
- Las predicciones se solapan y no son muestras estadísticas independientes; las tasas son descriptivas y no deben tratarse como garantías ni como intervalos IID.
- La ventana ha tenido exposición en investigaciones anteriores del repositorio. Por tanto, esto es un diagnóstico histórico exploratorio, no un holdout independiente ni una validación prospectiva.
- No se modificará `main`, no se conectará ejecución real y el estado operativo seguirá siendo `NO_TRADE`, independientemente de los porcentajes observados.
