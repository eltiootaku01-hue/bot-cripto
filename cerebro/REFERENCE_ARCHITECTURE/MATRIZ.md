# MATRIZ MAESTRA — BOT-CRIPTO VS REFERENCIAS

Estado de apertura: 2026-10-06

| Área | BOT-CRIPTO | Mejor referencia | Decisión |
|---|---|---|---|
| Market Data canónico | FUERTE | Nautilus | conservar propio |
| Availability temporal | FUERTE | Nautilus/Passivbot como inspiración | conservar propio |
| Analysis contracts | EXISTE | Jesse/Freqtrade | ampliar sin copiar |
| Indicators | SMA/EMA/RSI | Jesse | estudiar catálogo y casos borde |
| Strategy contract | EXISTE | Freqtrade/Jesse | estudiar runtime |
| Parameter optimization | PENDIENTE | Freqtrade/Jesse | candidato |
| Portfolio construction | PENDIENTE | LEAN/Nautilus | diseñar |
| Risk evaluation | EN DISEÑO | LEAN/Nautilus/Passivbot | adaptar |
| Drawdown | PENDIENTE | LEAN | candidato |
| Exposure limits | PARCIAL | LEAN/Passivbot | candidato |
| Reservation | FUERTE | propio | conservar |
| Financial Admission | FUERTE | propio | conservar |
| Execution boundary | FUERTE | Nautilus/Hummingbot | conservar |
| Order executor lifecycle | PARCIAL | Hummingbot | estudiar, no duplicar |
| Reconciliation | EXISTE | Nautilus/Passivbot | comparar |
| Replay | PENDIENTE | Nautilus/Passivbot | candidato |
| Backtest engine | PENDIENTE | Nautilus/Freqtrade/Jesse/OctoBot | diseñar propio |
| Backtest metrics | PENDIENTE | Jesse/Freqtrade | candidato |
| Walk-forward | PENDIENTE | estudiar | posterior |
| Multi-venue | NO PRIORITARIO | Nautilus/Hummingbot | no ampliar todavía |
| UI/Web | NO PRIORITARIO | Freqtrade/OctoBot | posterior |
| Exchange connectors | Binance Spot existe | Hummingbot/Nautilus | extender solo cuando haya necesidad |
| Persistence | SQLite selectiva | Passivbot/Nautilus | conservar enfoque mínimo |
| Event bus | NO IMPLEMENTAR por defecto | Nautilus/Passivbot | estudiar cuando Replay/Runtime lo exija |
| Redis/Kafka/Docker | NO | Nautilus/otros | no introducir por prestigio |
| AI/ML | NO necesario en V1 | varios | no introducir sin necesidad |

## Regla

Una fila no significa "copiar esa implementación".

Significa "ésta es la mejor fuente de ideas que debemos estudiar".
