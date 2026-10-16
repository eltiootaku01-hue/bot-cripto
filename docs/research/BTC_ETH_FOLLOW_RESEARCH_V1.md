# BTC_ETH_FOLLOW_RESEARCH_V1 — Contrato de investigación

- Estado: DESIGN_ONLY
- Decisión operativa: NO_TRADE
- Universo nuevo: BTCUSDT como señal/benchmark y ETHUSDT como único activo operable virtual.
- Objetivo: al menos 365 días UTC de historia válida, con evaluación fuera de muestra.
- Seguridad: datos públicos únicamente; sin claves privadas, endpoints privados, órdenes reales, apalancamiento ni cambios al límite de ejecución financiera.
- Esta versión es nueva y versionada. No reconstruye ni reemplaza la lista histórica BTC_ALT_CROSS_ASSET_RESEARCH_V1.

## 1. Pregunta falsable

¿Una señal calculada solo con información de BTC disponible en el instante de decisión mejora la rentabilidad neta y ajustada por riesgo de una estrategia virtual sobre ETHUSDT frente a comprar y mantener ETH, una estrategia base sobre ETH y no operar, después de costes conservadores y evaluación fuera de muestra?

La correlación por sí sola no demuestra capacidad predictiva ni rentabilidad. Un resultado negativo o inconcluso mantiene NO_TRADE.

## 2. Datos y horizonte

- Mercado Binance Spot público; símbolos fijos BTCUSDT y ETHUSDT.
- Marco principal: velas horarias UTC, cerradas y completas. 4h puede ser diagnóstico complementario, no sustituto silencioso del objetivo horario.
- Se requieren al menos 365 días completos y continuos por símbolo, más calentamiento de indicadores. Si el adaptador no entrega ese horizonte, registrar BLOCKED_DATA_HORIZON; no reducir la muestra y llamarla anual.
- Validar UTC, duplicados, huecos, velas parciales, cierre/finalidad, alineación BTC/ETH y disponibilidad temporal. Rechazar huecos no reparados o datos ambiguos.
- Registrar procedencia, símbolos, intervalo, límites temporales, número de velas, adquisición, hashes de artefactos y versión de configuración.
- No usar datos futuros ni metadatos actuales como si fueran históricos.

## 3. Componentes existentes inspeccionados y brecha

En main, HEAD verificado al inicio b9e34f2d836b99f711d6227f88c95c7b5ba7bd85:

- bot_obrero/virtual_backtest.py: simulación virtual determinista, Decimal, validación de velas cerradas/completas/contiguas, señal al cierre y ejecución en apertura siguiente; límite de hasta 1.200 velas por evaluación.
- bot_obrero/product_v2.py: métricas de correlación/beta, alineación temporal, régimen BTC y supuestos explícitos de costes.
- Rama research/product-v2-r5-independent-strategies: panel R5 explícitamente exploratorio; no constituye evidencia de ventaja robusta.

Brecha técnica: 1.200 velas de 1h no cubren un año; 1.200 velas de 4h tampoco cubren 365 días. Antes del backtest anual debe inspeccionarse y, si corresponde, diseñarse paginación/chunking que reutilice el adaptador público existente, con deduplicación y validación de continuidad. No aumentar límites ni crear una nueva ruta de datos sin revisión.

Esta fase no afirma que la adquisición anual esté implementada ni que se haya ejecutado una simulación nueva.

## 4. Candidatos de estrategia

- B0 No operar: efectivo sin operaciones, referencia de coste de oportunidad.
- B1 Buy-and-hold ETH: entrada y liquidación virtual con costes incluidos.
- S1 ETH-only baseline: estrategia de tendencia existente, parámetros congelados antes del test.
- S2 BTC-gated ETH trend: señal BTC habilita o bloquea entradas de una estrategia ETH; salidas y gestión de riesgo de ETH siguen funcionando aunque BTC deje de habilitar entradas.
- S3 BTC lag test: diagnóstico de si retornos pasados de BTC predicen retornos futuros de ETH en horizontes predefinidos. Corregir por comparaciones múltiples; no elegir el mejor lag usando el test final.

Son hipótesis de investigación, no estrategias aprobadas para operar. No se ajustarán repetidamente para forzar rentabilidad.

## 5. Prevención de lookahead y sesgos

- Usar solo velas cerradas. La decisión ocurre después del cierre confirmado; el fill virtual no puede ocurrir antes de la siguiente apertura disponible.
- Cada dato debe estar disponible en el timestamp de decisión. Si available_at es desconocido, declarar la suposición conservadora y bloquear si afecta la validez.
- BTC y ETH deben compartir timestamps comparables. No rellenar huecos con datos futuros ni alinear por índice cuando los timestamps difieren.
- Separación cronológica, nunca aleatoria: desarrollo, validación walk-forward y test final bloqueado. Abrir el test final solo después de congelar estrategia, parámetros, costes y criterios.
- Indicadores y normalizaciones solo usan historia disponible hasta cada decisión.
- Universo fijo BTCUSDT/ETHUSDT en esta versión; no seleccionar activos retrospectivamente.

## 6. Costes y contabilidad

- Comisión base asumida: 0,10% por lado (0.001), no una comisión personal verificada; incluir sensibilidad a tasas superiores.
- Spread supuesto base: 10 bps de ancho completo, mitad por lado, salvo evidencia histórica fiable. No etiquetar el supuesto como spread observado.
- Slippage base: 5 bps por lado, con escenarios de estrés.
- Aplicar reglas de cantidad, step size y notional mínimo cuando sean conocidas; registrar limitaciones cuando no lo sean.
- Usar Decimal en saldos, cantidades, precios, fees y PnL; documentar redondeo y precisión.
- Cada fill debe guardar timestamp UTC, lado, precio, cantidad, fee, spread/slippage imputados, saldo USDT, posición ETH, motivo y versión de estrategia.
- Comparar todas las estrategias y benchmarks con la misma convención de entrada, costes y liquidación. Informar equity marcada a mercado y equity final liquidada neta por separado.
- Reportar retorno/PnL neto, drawdown máximo, exposición, número de operaciones, operaciones positivas, fees, costes modelados y métricas por ventana/régimen.

## 7. Evaluación fuera de muestra

- Mínimo 365 días válidos por símbolo.
- División cronológica propuesta 60% desarrollo / 20% validación / 20% test final, más walk-forward. Si el test tiene duración o trades insuficientes, declarar inconcluso.
- Congelar estrategia, parámetros, salidas, costes y criterios antes del test final.
- Reportar cada ventana por separado y agregada. Hacer sensibilidad a costes base/estrés, desplazamiento de señal, fechas de inicio/fin y exclusión de las mejores operaciones.
- Comparación primaria S2 contra B0, B1 y S1 sobre periodos idénticos. Una rentabilidad positiva aislada no basta.

## 8. Criterios de aceptación pendientes de fijar antes del test

Para siquiera considerar cambiar el dictamen, exigir: (1) datos completos y reproducibles de 365 días; (2) resultado neto positivo en el test final no usado para selección; (3) comparación favorable frente a benchmarks considerando exposición y drawdown; (4) resultados no concentrados en una operación/ventana; (5) robustez a un escenario de costes de estrés predefinido; (6) reporte reproducible de datos, configuración, fills, saldos y liquidación; (7) revisión de CEREBRO y autorización del propietario.

Los mínimos de operaciones, drawdown tolerable y mejora mínima sobre benchmarks están UNDECIDED. Deben fijarse antes de abrir el test final, nunca después de ver los resultados. Superar un backtest no autoriza órdenes reales.

## 9. Estados y límites

DESIGN_ONLY → DATA_AUDIT → BACKTEST_DEVELOPMENT → WALK_FORWARD → FINAL_OOS_REVIEW. Datos insuficientes, costes incompletos o ambigüedad implican BLOCKED/UNKNOWN. La decisión operativa permanece NO_TRADE en todos los estados. PAPER requerirá autorización separada.

## 10. Próximos pasos

1. Auditar la adquisición histórica y definir paginación que cubra al menos 365 días horarios sin gaps.
2. Definir esquema reproducible del dataset BTC/ETH y controles de integridad.
3. Fijar umbrales económicos pendientes antes del test final.
4. Tras revisión, implementar cambios mínimos y pruebas; no modificar workflows ni límites de ejecución financiera sin autorización.
5. Validar datos y desarrollo primero; congelar protocolo antes de evaluar el test final.

## 11. Evidencia de esta fase

- HEAD de main consultado al inicio: b9e34f2d836b99f711d6227f88c95c7b5ba7bd85.
- Inspeccionados: cerebro/PROJECT_STATE.md, bot_obrero/product_v2.py, bot_obrero/virtual_backtest.py, docs/PRODUCT_V2_R5_RESEARCH.md y bot_obrero/research_strategies.py en sus refs respectivas.
- No se ejecutaron pruebas ni backtests nuevos.
- Documento registrado en rama separada para revisión; main no se modifica ni se fusiona automáticamente.