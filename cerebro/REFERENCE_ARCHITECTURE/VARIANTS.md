# BOT-CRIPTO — VARIANTS DE REFERENCIA

Fecha: 2026-10-06

## Freqtrade — ccxt.pro_orderbook

Branch: ccxt.pro_orderbook
Tip observado: ea4f84ee41888266b80523c01f51c5d59405be2a

Comparación con develop:
- diverged
- 37 commits ahead
- 180 commits behind

Área relevante:
freqtrade/exchange/exchange_ws.py

Hallazgos:
- websocket dedicado;
- tareas background;
- timestamp de última actualización del orderbook;
- timeout del feed;
- lifecycle de watch/unwatch.

Lección para BOT-CRIPTO:
cuando exista orderbook real-time deberá existir freshness/staleness explícito y un estado seguro cuando el feed deje de actualizar.

## Jesse — add-backtest-checker

Branch: add-backtest-checker
Tip observado: a070636116d54e28cbf42364cc83f2552b8ade31

Comparación con master:
- diverged
- 3 commits ahead
- 1657 commits behind

Archivos relevantes modificados:
- jesse/helpers.py
- jesse/modes/backtest_mode.py
- jesse/services/redis.py

Lección:
el watchdog de un proceso de backtest puede mejorar operabilidad, pero no es sustituto de:
- replay determinista;
- dataset integrity;
- result fingerprint;
- evidence contract.

## OctoBot — user_action_created_id

Branch: user_action_created_id
Tip observado: 6b2685c62fedeffd281f5c59c035a60b07c78ecf

Comparación con master:
- diverged
- 8 commits ahead

Áreas:
- action dependencies;
- transitive dependency resolution;
- DSL/evaluator;
- tolerant state loading.

Lección positiva:
las dependencias explícitas permiten razonar sobre un workflow y sobre sus dependientes transitivos.

Lección negativa:
tolerar estado incompleto y reconstruirlo puede ser una característica de producto, pero no debe utilizarse para autoridad financiera.

## Passivbot — codex/tm-hsl-lean-one-side

Branch:
codex/tm-hsl-lean-one-side

Tip observado:
b7065e765ee67e52b8b8df5a58e8616297689dd3

Comparación con master actual:
- ahead_by = 0
- behind_by = 650

Conclusión:
es un ancestor histórico del master actual. No añade una familia arquitectónica independiente.

## Resultado

Los variants son fuentes secundarias:
- sirven para estudiar una decisión puntual;
- no elevan por sí solos una arquitectura a patrón recomendado;
- no deben incorporarse al roadmap si la rama principal ya explica mejor el concepto.
