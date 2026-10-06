# PLAN DE USO DE LA BITÁCORA

## Orden recomendado de futuras investigaciones

### 1. Strategy Runtime
Estudiar:
- Freqtrade;
- Jesse;
- LEAN.

Objetivo:
definir StrategyRuntime de BOT-CRIPTO sin romper Strategy contracts.

### 2. Portfolio Construction / Sizing
Estudiar:
- LEAN;
- NautilusTrader;
- Passivbot.

Objetivo:
separar:
Signal
→ intención económica
→ sizing
→ RiskAuthorization.

### 3. Replay
Estudiar:
- NautilusTrader;
- Passivbot.

Objetivo:
construir replay determinista, con availability y timestamp evidence.

### 4. Backtest
Estudiar:
- NautilusTrader;
- Freqtrade;
- Jesse;
- OctoBot.

Objetivo:
unificar:
dataset
→ replay
→ strategy
→ risk
→ simulated execution
→ metrics.

### 5. Optimización
Estudiar:
- Freqtrade Hyperopt;
- Jesse hyperparameters.

Objetivo:
parameter space reproducible y búsqueda controlada.

### 6. Execution hardening
Estudiar:
- Hummingbot;
- NautilusTrader;
- Passivbot.

Objetivo:
mejorar execution/reconciliation sin duplicar nuestra boundary.

## Qué NO hacer

- No copiar frameworks enteros.
- No añadir Docker/Redis/Kafka por moda.
- No crear multi-exchange antes de necesitarlo.
- No convertir estrategia en autoridad financiera.
- No permitir que backtest y live usen contratos incompatibles.
- No introducir código GPL en producción por comodidad.

## Estado actual

Esta bitácora es una fuente de diseño para CEREBRO.
No es una autorización de implementación.
Cada futura integración debe entrar mediante una fase explícita.
