# FRANKESTAIN — AUDITORÍA Y MAPA DE INTEGRACIÓN

Base: `main@118c437f7fd12fc0e59d4c36afd3933c9c7700af`

Esta rama es un laboratorio temporal. No debe mergearse directamente a `main`.

## Fuentes auditadas

| Fuente | Licencia | Valor principal | Decisión |
|---|---|---|---|
| NautilusTrader | LGPL-3.0 | event-driven, replay/backtest/live, execution/risk | usar patrones; no copiar núcleo Rust |
| Hummingbot | Apache-2.0 | executors, order lifecycle, connectors | estudiar/adaptar; no duplicar ExecutionBoundary existente |
| Freqtrade | GPL-3.0 | Strategy interface, protections, backtesting, hyperopt | usar patrones, no copiar código GPL |
| LEAN | Apache-2.0 | RiskManagementModel, drawdown, portfolio/risk/execution separation | candidato directo para adaptación original |
| Jesse | MIT | indicadores, candle pipelines, backtesting | candidato para ampliar research/indicators |
| OctoBot | GPL-3.0 | backtesting, strategy designer, multi-exchange product | referencia de producto; no copiar código GPL |
| Passivbot | Unlicense | replay/backtesting, risk limits, live event model | candidato directo para conceptos de replay/risk |

## Hallazgo crítico de las ramas ref/*

Las ramas de referencia creadas desde PowerShell contienen el manifiesto `REFERENCE_IMPORT.md`, pero el diff auditado de esos commits no contiene el código del ZIP.

Por tanto:

- las ramas `ref/*` existen;
- el código fuente de los ZIP NO quedó realmente versionado en ellas;
- no se debe declarar que esas ramas son una copia auditable de los ZIP;
- los repositorios upstream reales fueron auditados directamente cuando fue posible.

Las variantes locales:
- `freqtrade-ccxt.pro_orderbook.zip`
- `jesse-add-backtest-checker.zip`
- `OctoBot-user_action_created_id.zip`
- `passivbot-codex-tm-hsl-lean-one-side.zip`

quedan como `UNKNOWN` respecto de su contenido fuente dentro de GitHub hasta volver a importar sus archivos correctamente.

## Qué YA tenemos

BOT-CRIPTO ya posee:

- canonical MarketData;
- temporal/evidence availability;
- Analysis contracts y AnalysisEngine;
- EMA/RSI/SMA;
- Strategy contracts;
- TradeProposal;
- Risk contracts;
- Reservation + EffectiveCapacity;
- Financial Admission;
- ExecutionBoundary/ExecutionOrchestrator;
- idempotency;
- reconciliation;
- Murphy/fail-closed.

Por eso no se copian conectores, motores de ejecución ni grandes frameworks externos.

## Candidatos seleccionados para Frankenstein

### 1. Drawdown / portfolio risk
Inspiración: LEAN + Passivbot.

Adaptación propia y provider-neutral:
- drawdown absoluto o trailing;
- gross exposure fraction;
- resultados descriptivos, no RiskDecision;
- sin I/O.

### 2. Replay timeline
Inspiración: NautilusTrader + Passivbot.

Adaptación propia:
- eventos inmutables;
- orden determinista;
- timestamps aware;
- availability explícita;
- selección hasta un evaluation timestamp.

### 3. Backtest metrics
Inspiración: Jesse + Freqtrade + OctoBot.

Adaptación propia:
- total return;
- max drawdown;
- wins/losses;
- win rate;
- profit factor cuando sea definible.

## Lo que NO entra

- conectores completos externos;
- clientes exchange duplicados;
- SQLite/Redis/servicios;
- Strategy engines completos;
- execution engines alternativos;
- Rust core de Nautilus;
- código GPL/AGPL copiado;
- cualquier integración automática con Binance;
- cualquier orden real.

## Regla de integración

Todo elemento de esta rama es candidato hasta que CEREBRO lo valide contra los contratos canónicos.

`frankestain` no es producción.
