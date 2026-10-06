# HUMMINGBOT — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/hummingbot/hummingbot

Licencia observada: Apache-2.0

Referencia auditada: master

## OBSERVED

Hummingbot contiene una capa extensa de connectors y un modelo de Strategy V2 con Executors.

El PositionExecutor rastrea explícitamente:
- orden de apertura;
- orden de cierre;
- take profit;
- órdenes fallidas;
- cantidad ejecutada;
- precio medio;
- fees;
- PnL;
- expiración;
- barreras de cierre.

Esto crea una referencia útil para representar un "ejecutor de una intención" como máquina de lifecycle.

También existe ClientOrderTracker y BudgetChecker dentro de connectors.

## LO MÁS VALIOSO

1. Un executor puede tener responsabilidad limitada y observable.
2. El lifecycle de una posición puede modelarse con estado explícito.
3. El connector debe aislar detalles del exchange.
4. Cálculos de cantidad/precio deben respetar trading rules.

## BOT-CRIPTO YA TIENE

BOT-CRIPTO ya posee:
- OrderIntent;
- ExecutionBoundary;
- ExecutionOrchestrator;
- order lifecycle;
- idempotencia;
- reconciliación;
- exchange adapter.

Por eso NO debemos introducir otro sistema de execution paralelo.

## DECISIÓN

ADOPTAR COMO REFERENCIA:
- state machines explícitas para ejecución;
- tracking de órdenes y fills;
- separación connector/executor;
- validación de trading rules.

NO adoptar:
- arquitectura de connectors completa;
- executor paralelo que compita con ExecutionOrchestrator.
