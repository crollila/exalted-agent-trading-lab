# Risk Policy

This project is paper-trading only.

## Hard rules

- No live trading.
- No executable options.
- No executable margin.
- No executable shorting.
- Stocks only.
- Max 20% of portfolio in one stock.
- Minimum 10% cash reserve.
- Max 5 new positions per day.
- Max 30% daily turnover.

## LLM rule

The LLM cannot override these rules.

If Hermes proposes a bad trade, the risk engine rejects it.

Hermes may generate structured research proposals for future stock short, margin, and option review. Those proposals are routing inputs only. They are not execution approval, and they do not change the current executable risk policy.

Discord commands cannot override risk. Natural Discord team chat, `!ask_team`, `!ask_agent`, scheduled updates, and `!run_tournament` cannot submit orders.

The explicit Discord command path allowed to submit paper orders is `!paper_trade_team <team_id> <proposal_path> <risk_approval_note_path> <review_approval_note_path>`, and it may submit only stock-long paper orders that have risk/review approval notes and pass deterministic Python risk validation. Stock short, margin, and options proposals are rejected from execution until separate deterministic paper risk gates and mocked broker support are implemented and tested.

The autonomous paper-cycle scaffold may call the same paper order path only when all of these are true:

- Team autonomy is explicitly enabled for that team.
- A research proposal JSON file exists and passes sandbox review.
- The risk agent note includes `RISK_AGENT_APPROVED: true`.
- The review agent note includes `REVIEW_AGENT_APPROVED: true`.
- The deterministic Python risk engine approves a stock-long order quantity.
- The team is in `paper_stocks_only` autonomy mode.
- The team has remaining daily paper order count and daily notional capacity.
- The Alpaca wrapper is in paper mode with the exact paper endpoint.

A risk agent approval is not enough by itself. A review agent approval is not enough by itself. LLM output remains advisory until deterministic Python risk approves.

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
