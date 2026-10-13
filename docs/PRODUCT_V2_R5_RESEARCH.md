# Product V2-R5 — investigación de estrategias independientes

## Alcance y seguridad

Esta rama añade una interfaz de señales local a la investigación histórica y un libro contable de cartera compartida. Ninguno importa ni invoca el runtime PAPER o financiero. Las señales solo reciben observaciones hasta el cierre actual; los fills individuales siguen ocurriendo en la apertura de la siguiente vela evaluada.

## Estrategias congeladas

- **EMA/RSI:** adaptador de compatibilidad para la regla histórica de EMA rápida/lenta y filtro RSI. El motor de un solo activo conserva su ruta anterior por defecto.
- **A — Donchian 20/10:** compra si el cierre supera los máximos de las 20 velas anteriores; vende si cae bajo los mínimos de las 10 velas anteriores. La vela actual queda excluida del rango.
- **B — reversión a la media:** media y desviación estándar poblacional Decimal de los 20 cierres anteriores; EMA 200 calculada hasta la vela actual; compra en close <= mean - 2σ si close > EMA200; vende al volver a la media o al caer a mean - 3σ. Decimal.sqrt() se usa con el contexto Decimal activo y su precisión predeterminada; no se redondean precios antes de comparar.
- **C — fortaleza relativa:** cartera USDT hipotética compartida, máximo una posición long, selección diaria UTC con momentum de 42 velas 4h estrictamente positivo y cierre sobre EMA 50. Cambios de activo venden primero y compran después con el efectivo neto.

## Capital, costes y límites

El saldo base de investigación procede del ledger normalizado existente de Product V2: **1.000 USDT hipotéticos**, no una conversión del presupuesto ARS. Escenarios congelados:

| Escenario | Fee por fill | Spread completo supuesto | Slippage por fill |
|---|---:|---:|---:|
| BASE | 0,1 % | 10 pb | 5 pb |
| STRESS 1 | 0,1 % | 25 pb | 10 pb |
| STRESS 2 | 0,2 % | 50 pb | 20 pb |

El spread es una hipótesis, no una medición histórica. No se atribuye precisión de lote histórica a los activos; la cartera usa cantidades fraccionarias ideales acotadas por el efectivo disponible, porque los filtros actuales no reconstruyen filtros históricos. El panel XRPUSDT, STRKUSDT, WUSDT, SUIUSDT, ENAUSDT es exploratorio: no hay reconstrucción del universo histórico y los filtros de instrumento, cuando se usan, son metadatos actuales. La sincronización exige timestamps comunes; no se interpolan velas.

La existencia de estas clases y sus pruebas no acredita rentabilidad. La clasificación transversal queda EXPLORATORY_ONLY; mantener NO_TRADE hasta que los experimentos remotos reproduzcan resultados y CEREBRO revise el informe.
