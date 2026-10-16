# BTC_ETH_FOLLOW_RESEARCH_V1 — Diseño documental para simulación anual >1.200 velas

- Fecha: 2026-10-16
- Modo: diseño documental de solo lectura; publicación de este informe únicamente.
- Estado: **DESIGN_PROPOSED — IMPLEMENTATION_NOT_AUTHORIZED**
- Decisión operativa: **NO_TRADE**
- Ubicación publicada verificada: `main` (el commit de publicación terminó en la rama por defecto, no en la rama de investigación como se pretendía).
- PR de continuidad: [#67](https://github.com/eltiootaku01-hue/bot-cripto/pull/67)
- Código de referencia inspeccionado: `bot_obrero/virtual_backtest.py`, blob `4e7f73aaeae0d7e6f0fcea033d05c6caab0c405d`.
- Alcance del contenido: solo documentación. **Corrección de publicación:** la creación del archivo terminó en `main` y avanzó esa rama mediante un commit documental; esto no era el destino previsto. No se modificaron código, tests ni workflows. No se ejecutaron pruebas, backtests ni adquisiciones de mercado.

## 1. Dictamen

La solución recomendada es **separar el horizonte de evaluación del historial de calentamiento y ejecutar la ventana anual como una sola simulación lógica continua**. No ejecutar 8 o más backtests independientes y concatenar sus resultados: eso reiniciaría o alteraría el estado de indicadores, saldos, posiciones, límites de entrada y máximo de equity, y podría falsear costes y drawdown.

El cambio de diseño debe separar al menos:
1. rango total de datos necesarios (warm-up + evaluación + barras auxiliares justificadas);
2. rango exacto de evaluación de 365 días UTC;
3. política de decisión y ejecución temporal;
4. liquidación terminal y métricas netas.

La propuesta **no implementa ni autoriza** cambios. El límite actual de 1.200 velas continúa vigente hasta revisión y autorización separadas.

## 2. Hallazgos concretos en el código actual

### Límites y evaluación

- **OBSERVED:** `_MAX_CANDLES = 1200` y `BacktestConfig.validate()` rechaza `candles` fuera de 1–1.200.
- **OBSERVED:** `run_virtual_backtest(..., evaluation_start, evaluation_end)` ya contempla conceptualmente un periodo de evaluación separado del historial: los límites de evaluación excluyen warm-up de las métricas y lo conservan para indicadores.
- **OBSERVED:** incluso con esos argumentos, el número de velas evaluadas debe ser exactamente `config.candles`, y el propio `config.validate()` sigue sujeto al máximo de 1.200.
- **OBSERVED:** el CLI adquiere aproximadamente `candles + 2` velas y llama al backtest sin pasar `evaluation_start`/`evaluation_end`; por tanto, ese flujo CLI no implementa hoy el contrato anual con warm-up separado.
- **OBSERVED:** los indicadores EMA/SMA/RSI se calculan sobre prefijos históricos hasta la decisión; el límite de entradas se cuenta sobre fills previos de los últimos 30 días; equity y drawdown se actualizan durante el bucle de evaluación.
- **OBSERVED:** el resultado actual puede terminar con posición base abierta. `final_equity` es la equity marcada al último cierre; `unrealized_pnl` se informa por separado. No se observa una liquidación terminal forzada que venda la posición y reste los costes de salida.

### Consecuencia

No basta con cambiar el número 1.200. Si se amplía solo el máximo, la ruta CLI seguiría sin un contrato claro de warm-up, límites de evaluación y liquidación final. El diseño debe cerrar esos contratos juntos antes de implementar.

## 3. Contrato propuesto

### 3.1 Horizonte y datos

- Activo evaluado: `ETHUSDT`; señal/benchmark sincronizado: `BTCUSDT`.
- Intervalo primario: `1h`, límites de vela alineados a UTC.
- Ventana de evaluación: intervalo semiabierto `[evaluation_start, evaluation_end)`, con exactamente 8.760 aperturas horarias para 365 días completos. La duración real de calendario y el número esperado de velas deben verificarse explícitamente; no sustituir un año calendario por una suposición silenciosa.
- El historial adquirido debe incluir calentamiento anterior a `evaluation_start`, más cualquier barra adicional necesaria para respetar decisión al cierre y ejecución en la siguiente apertura. No contabilizar el warm-up en retornos, operaciones, exposición ni curva de equity del periodo evaluado.
- Ambos símbolos deben cubrir el mismo rango requerido y compartir las marcas temporales horarias esperadas. Rechazar cualquier gap, duplicado, vela no final, rango incompleto o desalineación; no rellenar con datos futuros ni alinear por índice.
- Persistir en el manifiesto: símbolos, intervalo, límites UTC, número de velas esperadas/recibidas, regla de warm-up, hashes del dataset normalizado, versión del esquema, fuente pública, instante de adquisición y configuración. No considerar hashes como prueba de calidad; solo fijan la identidad del artefacto.

### 3.2 API conceptual: separar historial y evaluación

Sin prescribir nombres finales de código, la configuración debería expresar por separado:

- `evaluation_start` y `evaluation_end`;
- política/periodo de calentamiento, derivada de las estrategias y congelada antes de mirar resultados;
- límite explícito de adquisición total y presupuesto de solicitudes;
- configuración de indicadores y versión de algoritmo;
- costes y reglas de fills;
- modo de liquidación terminal.

La cantidad de velas de evaluación no debe ser también el límite de todo el historial. El presupuesto máximo debe seguir siendo finito y validado; no usar un límite ilimitado ni confiar en valores por defecto silenciosos. Si se mantiene una opción de compatibilidad de 1.200 velas para los usos actuales, el camino anual debe ser explícito y versionado, sin cambiar por accidente la semántica de los backtests existentes.

### 3.3 Estrategia de ejecución recomendada

**Preferida: una única simulación lógica continua sobre el dataset completo validado.** Un año horario más warm-up es un volumen acotado y no requiere dividir el estado financiero en varios backtests. La adquisición sí puede paginarse; una vez reunidos los datos, se ordenan y validan como una sola serie antes de simular.

Si más adelante se necesita procesamiento por bloques, debe ser un detalle interno de una única corrida con estado explícito y continuo, no una sucesión de corridas independientes. Debe conservar exactamente:
- estado inicial y actual de EMA/RSI/SMA, incluidos valores previos para detectar cruces;
- timestamp e información de la señal pendiente de ejecutar en la apertura siguiente;
- saldo USDT, cantidad ETH, coste de entrada y coste base de la posición;
- fills, comisiones y PnL realizado;
- ventana móvil de entradas de 30 días;
- equity, pico histórico de equity y máximo drawdown global;
- cursor de BTC/ETH sincronizado, costes y cualquier regla de admisión;
- estado del último cierre y de la liquidación terminal.

**Condición de equivalencia:** una ejecución continua y cualquier ejecución por bloques deben producir los mismos fills ordenados, cantidades/precios/fees, saldos, PnL realizado, equity por timestamp, drawdown y liquidación terminal, con tolerancia solo donde el contrato Decimal la permita. Esta propiedad requerirá tests de equivalencia autorizados más adelante; aquí no se ejecutaron ni se añadieron tests.

## 4. Warm-up e indicadores

1. Definir el warm-up a partir del mayor periodo de indicador y de la inicialización concreta de los algoritmos, no mediante un número arbitrario elegido tras ver resultados.
2. Adquirir todas las velas de warm-up antes de `evaluation_start`; verificar que el primer punto evaluado tiene los valores de indicadores necesarios y un punto previo para detectar cruces.
3. Calcular los indicadores usando exclusivamente información disponible hasta el cierre de la vela de decisión. Una señal al cierre de la vela (t) puede ejecutarse, según el modelo declarado, en la apertura de (t+1), nunca antes.
4. No reinicializar indicadores en límites de bloques, meses, folds ni segmentos de descarga.
5. Los datos de warm-up no forman parte de métricas económicas, aunque sí determinan las primeras señales evaluables. El capital virtual debe inicializarse una sola vez en el comienzo económico definido por el contrato; no asignar capital ni PnL al periodo de warm-up.

## 5. Contabilidad, costes y drawdown

- Usar `Decimal` para saldos, cantidades, precios, fees y PnL; no convertir a `float` para cálculos financieros.
- Un único saldo inicial USDT, una posición ETH y un ledger cronológico de fills para toda la ventana de evaluación.
- Cada fill de compra resta notional y fee; cada venta suma proceeds netos de fee. Registrar por separado comisión, slippage y supuesto de spread sin descontar dos veces el mismo coste.
- Mantener sin reinicio el límite de entradas en cualquier ventana móvil de 30 días y la admisión de nuevas entradas. Las salidas de riesgo/estrategia no deben quedar bloqueadas por el filtro de entrada.
- Marcar equity al cierre de cada vela de evaluación como saldo quote más posición a precio de cierre; actualizar el máximo histórico y drawdown de forma continua desde el capital inicial de evaluación.
- La curva de equity debe tener un punto por vela de evaluación, con orden temporal estricto y sin puntos de warm-up. El máximo drawdown se calcula sobre toda esa curva, no por segmento y luego se promedian máximos.
- Mantener costes base y escenario de estrés idénticos entre estrategia y benchmarks comparables; congelarlos antes de la evaluación OOS. La tasa de comisión de 0,10%, spread de 10 bps y slippage de 5 bps por lado en el contrato son supuestos de investigación, no tarifas personales verificadas.
- No permitir que los indicadores o benchmarks consuman precios posteriores al timestamp de decisión.

## 6. Liquidación anual: definirla como evento contable terminal

El código actual informa la posición final marcada a mercado y el PnL no realizado; eso **no equivale a liquidación neta**. Para que el informe anual tenga una métrica liquidada comparable, se propone:

1. Establecer `evaluation_end` como límite exclusivo de las velas de evaluación y definir por contrato cuál es el último cierre válido usado para valorar y liquidar.
2. En ese cierre terminal, si queda ETH, generar un fill virtual SELL terminal con precio de referencia en el cierre final y aplicar la misma política explícita de slippage, spread y comisión de salida. Marcarlo como `TERMINAL_LIQUIDATION`, separado de una señal estratégica.
3. No usar la apertura de una vela posterior al final del periodo como si perteneciera al año evaluado. Si se decide que la liquidación debe ejecutarse en la siguiente apertura, esa vela adicional debe quedar fuera del periodo de señales y su inclusión temporal debe declararse explícitamente; no mezclar las dos convenciones.
4. Registrar balance antes de liquidar, cantidad liquidada, precio de referencia, precio efectivo, fee, coste supuesto, proceeds netos y balance USDT posterior.
5. Calcular retorno neto liquidado sobre el capital inicial y reportar por separado equity marcada al cierre, PnL realizado de la estrategia antes de liquidación, coste terminal y PnL neto final. Evitar sumar dos veces PnL no realizado una vez convertida la posición a efectivo.
6. El punto de equity final y la métrica de retorno neto liquidado deben ser coherentes con el saldo después del fill terminal. Definir explícitamente cómo entra el coste de liquidación en la curva de drawdown y aplicar la misma regla a todas las estrategias/benchmarks.

Si no hay posición abierta al final, registrar liquidación terminal como no necesaria, con coste cero. Si no existe una vela final válida o la integridad temporal está bloqueada, no fabricar un precio de liquidación: el resultado queda `BLOCKED`.

## 7. Invariantes de aceptación propuestos (todavía no probados)

1. Número exacto de velas evaluadas y rango UTC semiabierto idéntico al manifiesto; warm-up excluido de métricas.
2. No se reinician indicadores, saldo, posición, fees, límite móvil de entradas, equity peak ni drawdown entre bloques.
3. Cada fill estratégico se produce como máximo en la apertura posterior a su señal; no hay fills antes de la disponibilidad modelada.
4. Todo fill tiene cantidades/precios/fees finitos, no negativos donde corresponda, y saldo no negativo; la contabilidad de cada fill reconcilia con el balance.
5. Fees totales iguales a la suma de fees del ledger; PnL realizado reconciliado con las operaciones cerradas; equity marcada reconciliada con saldo más posición a mercado.
6. El fill terminal, si aplica, liquida exactamente la posición residual y deja posición base cero; el balance final coincide con proceeds netos y costes de salida.
7. Drawdown global se calcula a partir del máximo histórico de equity de la corrida completa; no se reinicia en bloques.
8. Reejecutar el mismo dataset/configuración produce un ledger y métricas deterministas.
9. La corrida rechaza huecos, duplicados, timestamps fuera de UTC, datos incompletos y desalineación BTC/ETH.
10. Comparar estrategia y benchmarks con el mismo rango de evaluación, capital inicial, reglas de valoración/liquidación y costes comparables.

Estos son criterios de diseño propuestos, no resultados de tests. Requieren revisión de CEREBRO y autorización separada antes de implementarse.

## 8. Riesgos que permanecen abiertos

- **Disponibilidad histórica:** `available_at=None` y `finality=UNKNOWN` no prueban disponibilidad punto-en-tiempo. El contrato de la investigación debe etiquetar la hipótesis de decisión al cierre o bloquear la evaluación si se requiere evidencia de disponibilidad real.
- **BTC como filtro de ETH:** además del backtest de ETH, el evaluador debe recibir una serie BTC validada y sincronizada. No se debe introducir un nuevo motor de indicadores si pueden reutilizarse los existentes.
- **Complejidad de indicadores:** el código actual recalcula indicadores sobre prefijos históricos. Una ventana anual puede aumentar el coste de cálculo; medirlo solo en una etapa de implementación autorizada. Si se optimiza a cálculo incremental, exigir equivalencia numérica/temporal con el cálculo existente.
- **OOS y selección:** mantener separación cronológica y no cambiar umbrales, costes, estrategia o regla terminal después de observar el periodo final reservado.
- **Datos reproducibles:** no adquirir ni persistir datos en esta tarea. La futura etapa debe registrar identidad/versiones del dataset y su validación.

## 9. Decisión y siguiente paso permitido

- Límite de 1.200 velas: **OBSERVED — bloqueo vigente**.
- Diseño de evaluación anual continua: **PROPOSED — pendiente de aprobación**.
- Warm-up y contabilidad global: **definidos a nivel de contrato propuesto; no implementados**.
- Liquidación terminal neta: **brecha de comportamiento identificada; contrato propuesto; no implementado**.
- Pruebas/backtests/datos de mercado: **NO EJECUTADOS**.
- Cambios de código/tests/workflows: **NINGUNO**.
- Operaciones: **NO_TRADE**.

Siguiente paso recomendado tras revisar este informe: decidir si se aprueba este contrato para una fase posterior de diseño técnico detallado. La implementación, ampliación de límites y ejecución de pruebas requieren autorización separada. La fusión del PR no se propone ni se realiza en este informe.
