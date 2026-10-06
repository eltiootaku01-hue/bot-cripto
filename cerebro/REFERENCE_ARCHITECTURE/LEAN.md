# QUANTCONNECT LEAN — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/QuantConnect/Lean

Licencia observada: Apache-2.0

Referencia auditada: master

## OBSERVED

LEAN separa explícitamente:
- Alpha;
- Portfolio Construction;
- Risk Management;
- Execution.

El PortfolioConstructionModel transforma insights en targets de portfolio.
El RiskManagementModel transforma targets mediante reglas de riesgo.

Un ejemplo observado es MaximumDrawdownPercentPortfolio:
- mantiene un máximo de portfolio;
- calcula drawdown;
- puede usar trailing high;
- reduce targets a cero cuando se supera el límite.

## LO MÁS VALIOSO

1. La señal no debe decidir directamente el tamaño final de posición.
2. Portfolio construction y Risk Management son responsabilidades distintas.
3. El riesgo puede transformar una intención de portfolio sin convertirse en execution.
4. Rebalance cadence debe ser explícita.

## BOT-CRIPTO

Esto coincide muy bien con la frontera que estamos diseñando:

Signal
→ TradeProposal
→ Risk Evaluation
→ Authorization
→ Admission
→ Execution

## DECISIÓN

ADOPTAR COMO PATRÓN:
- separación Alpha/Portfolio/Risk/Execution;
- modelos de drawdown;
- portfolio targets;
- rebalance semantics.

REIMPLEMENTAR con Decimal, evidence y fail-closed propios.

Fuente de ejemplo:
https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/MaximumDrawdownPercentPortfolio.py
