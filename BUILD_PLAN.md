# BUILD PLAN

## Phase 0 - Skeleton, config, database

Goal: create project structure, basic models, schema, docs, and tests.

Status: complete.

## Phase 1 - Risk engine and dry-run execution

Goal: all trade proposals must be validated before any simulated execution.

Status: complete for the first milestone.

Requirements:

- Reject options.
- Reject non-stock asset classes.
- Reject shorting.
- Reject max position violations.
- Reject low-cash violations.
- Reject excessive daily turnover.
- Reject more than 5 new positions in a day.
- Log all risk decisions.
- Dry-run execution logs approved orders without submission.

## Phase 2 - Alpaca paper integration

Goal: connect to Alpaca paper account only.

Requirements:

- Fetch account.
- Fetch positions.
- Fetch market clock.
- Submit approved paper orders only.
- Never submit live orders.
- Tests must not require real credentials.

## Phase 3 - Benchmark reports

Goal: compare the strategy to SPY every day.

Metrics:

- starting equity
- current equity
- strategy return
- SPY return
- excess return
- max drawdown
- trade count
- rejected trade count

## Phase 4 - Simple momentum strategy

Goal: add a deterministic, non-LLM baseline strategy.

Reason:

If Hermes cannot beat a simple dumb baseline, Hermes is not useful.

## Phase 5 - Hermes structured proposal agent

Goal: add Hermes as a proposal generator only.

Rules:

- Hermes outputs strict JSON.
- Hermes cannot place orders.
- Hermes cannot override risk.
- Invalid JSON is rejected.
- Every proposal is logged.

## Phase 6 - Multi-strategy tournament

Goal: run multiple strategies side by side.

Strategies:

- SPY buy-and-hold
- cash-only
- simple momentum
- Hermes conservative
- Hermes aggressive
- news/sentiment strategy
- political trade tracker, later
- wheel strategy, much later

## Phase 7 - Shadow live mode

Goal: observe real-time market conditions without sending orders.

Record:

- quote at decision time
- proposed order
- estimated fill
- estimated slippage
- whether paper fills were realistic

## Phase 8 - Optional live trading

Only after:

- 90+ paper trading days
- realistic slippage modeling
- walk-forward testing
- no critical bugs
- clear risk cap
- tiny capital only

This phase is intentionally not part of the current build.
