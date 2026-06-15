# Risk Policy

This project is paper-trading only.

## Hard rules

- No live trading, ever. The broker wrapper refuses live endpoints.
- Paper-only. `TRADING_MODE` must be `paper`; any other value disables every advanced surface.
- Advanced paper trading (shorting, margin, options) is explicitly unlockable but **disabled by default**.
- Shorting/margin/options require explicit env/UI permission flags.
- LLMs/agents never place trades. They produce proposals only.
- Every order must pass the deterministic risk engine and the broker wrapper.
- Paper trading does not prove live profitability.
- Max 20% of portfolio in one stock (default).
- Minimum 10% cash reserve.
- Max 5 new positions per day (legacy local runner); competition cap is `MAX_DAILY_ORDERS_PER_TEAM`.
- Max 30% daily turnover (legacy local runner).

## Advanced paper permission levels

All advanced permissions default to `false` (fail-closed) and are paper-only. They are
controlled by explicit config (`.env` or UI) — never inferred from broker buying power.

- **Level 1 — Paper Stocks** (`ENABLE_PAPER_STOCKS`, default true): long stock paper trades.
- **Level 2 — Paper Shorting** (`ENABLE_PAPER_SHORTING`, default false): short stock paper trades.
  Logs borrow/availability assumptions; enforces `MAX_SHORT_EXPOSURE`, `MAX_SINGLE_SHORT_WEIGHT`,
  and per-position max loss; requires a stop/invalidation level.
- **Level 3 — Paper Margin** (`ENABLE_PAPER_MARGIN`, default false): leverage. Enforces
  `MAX_GROSS_EXPOSURE`, `MAX_NET_EXPOSURE`, `MAX_DAILY_LOSS_PCT_PER_TEAM`, and forced
  deleveraging (rejects new exposure when already over caps).
- **Level 4 — Paper Options** (`ENABLE_PAPER_OPTIONS`, default false): defined-risk options only.
  No 0DTE, no DTE below `MIN_OPTIONS_DTE`, no naked/uncovered short options
  (`ALLOW_NAKED_OPTIONS` default false). Enforces `MAX_OPTIONS_PREMIUM_AT_RISK` and
  `MAX_OPTIONS_CONTRACTS_PER_TRADE`; requires a calculable max loss and an
  assignment/exercise risk note; logs Greeks if available or marks them unavailable.

### Paper options execution

Approved options reach Alpaca paper through `OptionsExecutionAdapter`, a second
deterministic gate after the risk engine:

- Single-leg long calls/puts execute (OCC symbol built from the approved leg) using the
  deterministic risk-approved contract quantity. The LLM never sizes or submits.
- Multileg spreads are disabled by default (`ENABLE_PAPER_OPTION_SPREADS=false`) and are
  refused with a logged reason until multileg paper support is verified.
- 0DTE, naked/uncovered short legs, single short legs, unapproved quantity, and missing
  contract data are refused outright — never submitted.
- No fake fills. Broker/permission rejections are logged and the cycle continues.
- Paper-only and team-credential enforcement are unchanged: team orders use only that
  team's `TEAM_<NAME>_ALPACA_*` credentials with no global fallback, on the paper endpoint.

### Deterministic routing

The router produces three buckets:

- `execution_eligible` — permission enabled AND deterministic risk passed.
- `simulation_only` — permission flag disabled (researched, never executed), or eligible but
  over the team's daily order cap.
- `rejected` — malformed or violates a hard deterministic rule.

The deterministic risk engine — never the LLM — computes the approved quantity / contract count.

### Kill switch

A global kill switch (`data/runtime/kill_switch.json`) is checked immediately before every
broker submission. While engaged, all new broker submissions are blocked and autonomous cycles
skip execution; status/report commands continue. Toggle with `kill-switch-on` / `kill-switch-off`
(CLI), `!kill_switch on|off` (Discord), or the UI Kill Switch page.

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
