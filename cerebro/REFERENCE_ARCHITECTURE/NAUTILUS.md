# NAUTILUSTRADER — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/nautechsystems/nautilus_trader

Licencia observada: LGPL-3.0

Referencia auditada: develop

## OBSERVED

NautilusTrader documenta explícitamente:

DDD + Event-Driven Architecture + Messaging Patterns + Ports and Adapters + Crash-only design.

Su kernel separa:

- DataEngine
- RiskEngine
- ExecutionEngine
- Portfolio
- Trader
- MessageBus
- Cache

El DataEngine procesa datos, los cachea y publica eventos.
El RiskEngine valida campos de órdenes, balances, cantidades, notionals, reduce-only y estado de trading.
El ExecutionEngine administra lifecycle de órdenes, fills y reconciliación.
Portfolio mantiene balances, posiciones, PnL y exposure.
Trader coordina actores y estrategias.
Backtest, Sandbox y Live comparten el mismo kernel conceptual.

## LO MÁS VALIOSO PARA BOT-CRIPTO

1. Separar explícitamente Risk de Execution.
2. Mantener una semántica común entre backtest y live.
3. Tratar la reconciliación como parte de execution, no como parche.
4. Usar Ports & Adapters para proveedores.
5. Tratar el tiempo como parte de la arquitectura.

## LO QUE NO DEBEMOS COPIAR

- Rust core completo.
- Message bus global por defecto.
- Cache distribuida.
- Redis.
- Multi-asset/multi-venue completo.
- complejidad del kernel si una versión pequeña basta.

## DECISIÓN

ADOPTAR PATRONES:
- runtime determinista;
- replay/backtest/live con semántica compartida;
- separación Risk/Execution;
- adapters;
- event model cuando realmente sea necesario.

NO COPIAR CÓDIGO como base del producto.

## Fuente adicional

https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/concepts/architecture.md
