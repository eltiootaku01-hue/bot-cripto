# PASSIVBOT — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/enarjord/passivbot

Licencia observada: Unlicense

Referencia auditada: master

## OBSERVED

El repositorio contiene módulos y documentos explícitos para:
- risk limits;
- backtesting;
- live event bus;
- fill events;
- restart executor;
- position fill synchronization;
- order churn gate;
- replay/minimal fake-live;
- validación de backtest data integrity.

## LO MÁS VALIOSO

1. Tratar eventos de fills como datos de primera clase.
2. Mantener sincronización entre estado live y fills.
3. Diseñar replay/fake-live para probar lifecycle sin exchange real.
4. Controlar order churn.
5. Tratar restart/recovery como parte de la arquitectura.
6. Auditar integridad del dataset de backtest.

## CONEXIÓN CON BOT-CRIPTO

Encaja especialmente con:
- idempotency;
- reconciliation;
- unknown execution outcomes;
- order lifecycle;
- futura capa Replay/Backtest.

## DECISIÓN

ESTUDIAR:
- replay;
- fill-event contract;
- restart/recovery;
- order churn gates;
- dataset integrity.

NO copiar su estrategia de trading ni su modelo de derivados como arquitectura económica general.

Fuentes:
https://github.com/enarjord/passivbot/tree/master/docs
https://github.com/enarjord/passivbot/tree/master/src/live
