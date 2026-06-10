# Risk Policy

This project is paper-trading only.

## Hard rules

- No live trading.
- No options.
- No margin.
- No shorting.
- Stocks only.
- Max 20% of portfolio in one stock.
- Minimum 10% cash reserve.
- Max 5 new positions per day.
- Max 30% daily turnover.

## LLM rule

The LLM cannot override these rules.

If Hermes proposes a bad trade, the risk engine rejects it.

## Logging rule

Every proposal is logged.

Every approval or rejection is logged.

Every order attempt is logged.

## Approval payload

The risk engine is the only component that converts a proposal into an executable quantity.

For each proposal, the risk decision records:

- `approved_quantity`: the exact quantity the executor may use, or null when rejected.
- `estimated_trade_value`: the proposal's estimated dollar value at validation time.

The order executor must not size orders itself. It may only create an order from an approved risk decision with a positive `approved_quantity`.
