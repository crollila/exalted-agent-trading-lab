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

Status: complete for safe paper account/status wrapper and approved paper-order submission path.

Requirements:

- Fetch account.
- Fetch positions.
- Fetch market clock.
- Submit approved paper orders only.
- Never submit live orders.
- Tests must not require real credentials.
- Tests mock Alpaca completely.

## Phase 3 - Benchmark reports

Goal: compare the strategy to SPY every day.

Status: complete for deterministic SQLite-backed daily benchmark reports.

Metrics:

- starting equity
- current equity
- strategy return
- SPY return
- excess return
- max drawdown
- trade count
- rejected trade count

## Phase 3.5 - Run-aware reports and run records

Goal: make each dry-run or future paper-trading session independently reportable.

Status: complete.

Requirements:

- Create formal run records.
- Link dry-run portfolio snapshots, benchmark snapshots, trade proposals, risk decisions, orders, and daily reports to a run ID.
- Make `python -m src.main report` default to the latest run instead of the full database.
- Support explicit run reports with `python -m src.main report --run-id <id>`.
- Keep all Phase 3 metrics isolated by run.
- Tests must not require internet access, Alpaca credentials, or real market data.

## Phase 4 - Simple momentum strategy

Goal: add a deterministic, non-LLM baseline strategy.

Status: complete.

Reason:

If Hermes cannot beat a simple dumb baseline, Hermes is not useful.

Requirements:

- Rank symbols by deterministic recent close-price returns.
- Generate stock-only long buy proposals for positive-momentum symbols.
- Skip flat, negative-momentum, non-stock, and already-held symbols.
- Keep per-symbol target weights compatible with the current risk policy.
- Support safe local CLI selection with `python -m src.main dry-run --strategy momentum_v1`.
- Tests must not require internet access, Alpaca credentials, or real market data.

## Phase 5 - Hermes structured proposal agent

Goal: add Hermes as a proposal generator only.

Status: complete for strict local JSON parsing into `TradeProposal` objects.

Rules:

- Hermes outputs strict JSON.
- Hermes cannot place orders.
- Hermes cannot override risk.
- Invalid JSON is rejected.
- Every proposal must be logged before any future Hermes execution integration processes it.
- Phase 5 does not connect to Ollama, LM Studio, hosted LLMs, Alpaca, or dry-run execution.
- Phase 5 parser tests use local fixtures only.

## Phase 6 - Multi-strategy tournament

Goal: run multiple strategies side by side.

### Phase 6A - Local multi-strategy comparison

Status: complete for safe local dry-run comparison.

Included:

- Cash-only baseline strategy.
- Local comparison command: `python -m src.main compare-strategies`.
- Separate run records for `cash_only`, `spy_buy_hold`, and `momentum_v1`.
- Run-aware report generation for each compared strategy.
- Beginner-readable comparison table with Phase 3 metrics.
- Tests for zero-proposal cash behavior, separate run creation, strategy inclusion, run isolation, comparison output, and no credential requirement.

Not included:

- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External LLM/API calls.

### Phase 6B - Deterministic multi-day simulation fixtures

Status: complete for local deterministic comparison fixtures.

Included:

- Deterministic local multi-day price fixture for SPY and strategy symbols.
- Approved-trade-only simulation snapshots for comparison runs.
- Multi-day portfolio snapshots and benchmark snapshots per run.
- Non-zero strategy return, SPY return, excess return, and max drawdown where fixture movement creates them.
- Cash-only remains a zero-return strategy baseline with no modeled cash yield.
- `compare-strategies` uses the `multi_day` fixture by default and supports `--fixture flat` or `--fixture multi_day`.
- Tests for fixture SPY movement, simulated strategy movement, excess return, drawdown, cash-only baseline, CLI output, run isolation, and no credential requirement.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- Hermes runtime wiring.
- Cash yield modeling.

### Phase 6C - Comparison artifacts and experiment logs

Status: complete for durable local research artifacts.

Included:

- `compare-strategies --save` writes JSON, CSV, and Markdown artifacts.
- `compare-strategies --output-dir` selects the artifact directory, defaulting to `data/experiments`.
- Artifact rows include experiment timestamp, fixture name, strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
- Missing output directories are created automatically.
- Runtime artifacts under `data/experiments` and `data/reports` are ignored by git.
- Tests cover JSON fields, CSV columns, Markdown summary, missing output directory creation, no-credential operation, unchanged terminal-only comparison behavior, and saved artifacts for `multi_day` and `flat` fixtures.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Hermes runtime wiring.

### Phase 6D - Hermes fixture strategies in local comparison

Status: complete for parser-only local Hermes fixture strategies.

Included:

- `hermes_conservative_fixture` and `hermes_aggressive_fixture` strategies.
- Hardcoded local Hermes-shaped JSON payloads.
- Existing strict Hermes parser reused to convert fixture payloads into `TradeProposal` objects.
- Stock-only, long-only, buy-only fixture proposal generation.
- Conservative low target weights within current policy limits.
- Aggressive higher target weights that remain within current position and turnover policy limits.
- `compare-strategies --include-hermes-fixtures` adds Hermes fixture strategies to local comparison output.
- `compare-strategies --include-hermes-fixtures --save` includes Hermes fixture rows in JSON, CSV, and Markdown artifacts.
- Tests for valid fixture proposals, parser usage, invalid fixture rejection, comparison inclusion, saved artifact inclusion, and no credential requirement.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Hermes runtime wiring.
- Options, margin, shorting, or sell proposals.

### Phase 6E - Tournament scoring and ranking

Status: complete for deterministic local tournament scoring.

Included:

- `compare-strategies` assigns each compared strategy a deterministic score.
- Score formula: `score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)`.
- Ranking sorts best score first.
- Deterministic tie-breakers use higher excess return, lower drawdown, fewer rejected trades, then strategy ID alphabetical.
- Trade count is shown for context but does not automatically reward overtrading.
- Beginner-readable terminal output includes rank, score, score formula, strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
- Saved JSON, CSV, and Markdown comparison artifacts include rank, score, score formula, score explanation, and prior comparison metrics.
- Tests cover score calculation, drawdown penalty behavior, rejected trade penalty behavior, ranking order, deterministic tie-breakers, CLI output, and saved JSON/CSV/Markdown scoring fields.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Hermes runtime wiring.
- Options, margin, shorting, or sell proposals.

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
