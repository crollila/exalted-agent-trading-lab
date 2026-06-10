# PROJECT RULES

ChatGPT is the project manager, architect, risk reviewer, and Codex prompt writer.

Codex is the coding worker.

The user will paste Codex results back to ChatGPT before moving to the next phase.

This project is an experimental trading research platform. The goal is to test whether AI-assisted strategies can beat SPY after realistic costs, slippage, drawdowns, and risk controls.

Default mode:
- Dry-run first.
- Alpaca paper trading second.
- No live-money trading until explicitly approved after long-term validation.

Core architecture:
- Strategies and LLM agents create trade proposals.
- A deterministic risk engine validates every proposal.
- Execution code submits only approved orders.
- Every proposal, approval, rejection, order, fill, and portfolio snapshot must be logged.

The LLM should not directly bypass the risk engine or broker wrapper.

Phase permissions:

Level 0 — Dry Run
Allowed:
- generate trade proposals
- log decisions
- benchmark against SPY
Not allowed:
- submit orders

Level 1 — Paper Stocks
Allowed:
- Alpaca paper stock trades
- long-only positions
- SPY benchmark tracking
Not allowed:
- live trading
- margin
- shorting
- options

Level 2 — Paper Shorting
Allowed:
- short stock trades in paper mode
Restrictions:
- strict max short exposure
- strict max loss per position
- borrow/availability assumptions must be logged
Still not allowed:
- live trading
- options
- margin unless separately approved

Level 3 — Paper Margin
Allowed:
- paper margin simulation or paper margin use
Restrictions:
- max gross exposure
- max net exposure
- max daily loss
- forced deleveraging rules
Still not allowed:
- live trading
- options unless separately approved

Level 4 — Paper Options
Allowed:
- paper options strategies
Restrictions:
- no 0DTE at first
- no naked short options at first
- max premium at risk
- max contract count
- Greeks and expiration must be logged
- strategy must explain assignment/exercise risk
Still not allowed:
- live trading

Level 5 — Shadow Live
Allowed:
- watch live market data
- log what the system would do
- estimate realistic fills and slippage
Not allowed:
- submit live orders

Level 6 — Tiny Live Stocks
Allowed only after explicit approval:
- tiny live capital
- stock-only trades
- strict daily loss limit
- immediate kill switch

Level 7 — Advanced Live Tools
Allowed only after strong proof:
- live shorting
- live margin
- live options
This requires separate approval, strong logs, and risk review.

Hard safety requirements:
- No real API keys in source files.
- Never commit .env.
- Every order must be linked to a proposal.
- Every proposal must include a thesis.
- Every trade must have post-trade analysis.
- Every strategy must be compared to SPY.
- Every strategy must be tested against a dumb baseline.
- Any new permission level requires STATUS.md and risk_policy.md updates.

After every Codex coding session:
- Update STATUS.md.
- Run tests.
- Report changed files.
- Report test output.

Update BUILD_PLAN.md when:
- a phase is completed
- a phase changes
- new scope is added
- a phase is delayed or removed

Update docs/risk_policy.md whenever:
- trading permissions change
- risk limits change
- margin rules change
- shorting rules change
- options rules change
- live/paper/shadow mode rules change