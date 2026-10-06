# BOT-CRIPTO — BITÁCORA DE ARQUITECTURA DE REFERENCIA

Fecha de apertura: 2026-10-06
Base de BOT-CRIPTO auditada: main @ 118c437f7fd12fc0e59d4c36afd3933c9c7700af

## Propósito

Esta carpeta registra aprendizajes arquitectónicos obtenidos de proyectos externos de trading.

NO es código de producción.
NO es una lista de dependencias.
NO autoriza copiar código.
NO sustituye los contratos de BOT-CRIPTO.

La fuente de verdad del producto continúa siendo el código y los contratos de BOT-CRIPTO.

## Proyectos estudiados

| Proyecto | Licencia | Referencia principal |
|---|---|---|
| NautilusTrader | LGPL-3.0 | arquitectura event-driven, risk, execution, backtest/live |
| Hummingbot | Apache-2.0 | connectors, executors, order lifecycle |
| Freqtrade | GPL-3.0 | strategy, protections, backtesting, hyperopt |
| QuantConnect LEAN | Apache-2.0 | Alpha/Portfolio/Risk/Execution |
| Jesse | MIT | indicadores, research, backtesting |
| OctoBot | GPL-3.0 | backtesting, strategy designer, producto |
| Passivbot | Unlicense | risk limits, replay, live events, backtesting |

## Regla de lectura

Cada hallazgo se clasifica como:

- OBSERVED — visto en código/documentación real.
- TESTED — respaldado por ejecución o CI observada.
- INFERRED — conclusión arquitectónica derivada.
- UNKNOWN — evidencia insuficiente.
- PROPOSED — decisión propia de BOT-CRIPTO, todavía no autorizada como contrato.

## Conclusiones iniciales

### OBSERVED
1. Los proyectos maduros separan, con nombres y responsabilidades distintas, datos, estrategia, portfolio/risk y ejecución.
2. NautilusTrader lleva esa separación al extremo con DataEngine, RiskEngine, ExecutionEngine, Portfolio, Trader, MessageBus y Cache dentro de un kernel común.
3. Hummingbot utiliza una familia de Executors para encapsular ciclos de ejecución concretos; esto es una referencia útil para order lifecycle, pero BOT-CRIPTO ya posee ExecutionBoundary/ExecutionOrchestrator.
4. Freqtrade expone una Strategy interface rica y un backtester que fuerza dry-run durante backtesting y separa el cálculo de estrategia de la infraestructura de persistencia.
5. LEAN separa Portfolio Construction, Risk Management y Execution como modelos independientes.
6. Jesse ofrece un catálogo amplio de indicadores y un modo de backtesting con métricas, charts y sesiones.
7. Passivbot contiene una arquitectura explícita de replay/live events, límites de riesgo, backtesting y sincronización de fills.
8. OctoBot trata backtesting y diseño de estrategias como una capacidad de producto separada del trading live.

### INFERRED
La arquitectura de BOT-CRIPTO debe conservar su núcleo contractual propio y adoptar patrones selectivos, no importar un framework completo.

### PROPOSED
La ruta de evolución recomendada es:

MarketData
→ Analysis
→ Strategy
→ Signal
→ TradeProposal
→ RiskEvaluationContext
→ RiskDecision
→ RiskAuthorization
→ Financial Admission
→ Reservation
→ Execution
→ Reconciliation

y, en paralelo:

MarketData/Analysis/Strategy
→ Replay
→ Backtest
→ Metrics
→ Parameter Optimization

## Decisión global

NO reemplazar BOT-CRIPTO por NautilusTrader, Hummingbot, Freqtrade, LEAN, Jesse, OctoBot o Passivbot.

USAR sus arquitecturas como mapa de referencia.

ADAPTAR solo ideas compatibles con:
- provider-neutral;
- fail-closed;
- evidencia temporal;
- snapshot identity;
- idempotencia;
- reservation;
- execution boundary.

## Estado

Esta bitácora debe actualizarse después de cada auditoría importante o descubrimiento que cambie el mapa de arquitectura.
