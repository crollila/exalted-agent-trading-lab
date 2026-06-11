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

### Phase 6F - Tournament history / experiment ledger

Status: complete for local saved-artifact history review.

Included:

- `tournament-history` CLI command reads saved `compare-strategies --save` JSON artifacts.
- `tournament-history --output-dir` selects the artifact directory, defaulting to `data/experiments`.
- Beginner-readable history output includes artifact timestamp, fixture name, strategy count, winning strategy ID, winning score, winning strategy return, winning SPY return, winning excess return, winning max drawdown, and artifact path.
- Valid artifacts are sorted deterministically newest first.
- Malformed or incomplete artifacts are skipped and reported without a traceback.
- Empty or missing artifact directories print a clear no-artifacts message.
- Tests use temporary fixture files and do not depend on real `data/experiments`.
- Tests cover one artifact, multiple artifacts, rank-based winner detection, deterministic sorting, no-artifact behavior, malformed-artifact behavior, CLI winner/score output, and no external service or credential requirement.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Hermes runtime wiring.
- Options, margin, shorting, or sell proposals.

### Phase 6G - Tournament champion report

Status: complete for local champion summary reporting.

Included:

- `tournament-champion` CLI command reads saved ranked `compare-strategies --save` JSON artifacts.
- `tournament-champion --output-dir` selects the artifact directory, defaulting to `data/experiments`.
- Champion is the strategy with the most rank-1 wins across valid saved tournaments.
- Deterministic champion tie-breakers use more wins, higher average score, higher best score, higher average excess return, lower worst drawdown, then strategy ID alphabetical.
- Beginner-readable champion output includes champion strategy ID, number of valid tournaments reviewed, champion wins, win rate, best score, average score, average excess return, worst max drawdown, most recent win timestamp, fixtures where the champion appeared, and skipped/malformed artifact count.
- Empty, missing, malformed, and mixed valid/malformed artifact directories are handled without tracebacks.
- Tests use temporary fixture files and do not depend on real `data/experiments`.
- Tests cover one artifact, multiple artifacts, most-wins champion selection, deterministic tie-breakers, average score, best score, average excess return, worst drawdown, no-artifact behavior, all-malformed behavior, mixed valid/malformed behavior, CLI output, and no external service or credential requirement.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Hermes runtime wiring.
- Options, margin, shorting, or sell proposals.

### Phase 6H - Strategy leaderboard README/report export

Status: complete for local Markdown leaderboard export.

Included:

- `export-leaderboard` CLI command reads saved ranked `compare-strategies --save` JSON artifacts.
- `export-leaderboard --output-dir` selects the artifact directory, defaulting to `data/experiments`.
- `export-leaderboard --report-path` selects the Markdown report path, defaulting to `data/reports/strategy_leaderboard.md`.
- Missing report directories are created automatically.
- No report is written when no valid artifacts exist.
- The Markdown report includes title, generated timestamp, current champion summary, score formula, safety disclaimer, recent tournament table, strategy aggregate table, fixture caveats, and artifact source directory.
- Malformed or incomplete artifacts are skipped and reported in the export when valid artifacts also exist.
- Tests use temporary fixture files and do not depend on real `data/experiments` or `data/reports`.
- Tests cover one artifact, multiple artifacts, champion summary, score formula, safety disclaimer, strategy aggregate table, recent tournament table, output directory creation, no-artifact behavior, malformed-artifact skip behavior, CLI output, and no external service or credential requirement.

Not included:

- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Hermes runtime wiring.
- Options, margin, shorting, or sell proposals.

### Phase 6I - README project showcase polish

Status: complete for documentation-only README polish.

Included:

- Concise top-level project summary for GitHub visitors.
- Portfolio/recruiting-focused "What this project demonstrates" section.
- Architecture flow from strategy or Hermes fixture through risk engine, execution, SQLite logs, and reports.
- Clear research-lab framing that avoids claims of proven trading edge or live profitability.
- Safety disclaimer covering dry-run default, Alpaca paper wrapper only, no live trading, no options, no margin, no shorting, no LLM direct execution, and no real API keys in source.
- Current capabilities section covering deterministic risk, Alpaca paper wrapper, SPY benchmark reports, run-aware reporting, strategy comparison, multi-day fixtures, Hermes parser-only fixtures, tournament reports, and leaderboard export.
- Beginner command workflow for setup, tests, dry-run, comparison, saved artifacts, tournament history, champion report, and leaderboard export.
- Portfolio note describing Python, SQLite, testing, CLI design, deterministic risk controls, paper-trading safety, reporting/analytics, and AI-agent safety boundaries.

Not included:

- Source code behavior changes.
- Test changes.
- Trading permission changes.
- Risk policy changes.
- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Options, margin, shorting, or sell proposals.

### Phase 6J - Strategy notes / post-run analysis templates

Status: complete for local Markdown analysis-note generation.

Included:

- `create-analysis-note` CLI command for creating a human review note from saved ranked tournament artifacts.
- `create-analysis-note --output-dir` selects the saved artifact directory, defaulting to `data/experiments`.
- `create-analysis-note --notes-dir` selects the Markdown note directory, defaulting to `data/notes`.
- `create-analysis-note --force` explicitly overwrites the deterministic note file when needed.
- Most recent valid tournament artifact is selected by default.
- Notes directory is created when missing.
- Existing notes are not overwritten unless `--force` is passed.
- Missing, empty, malformed, and mixed valid/malformed artifact directories are handled without tracebacks.
- Markdown note includes generated timestamp, source artifact path, tournament timestamp, fixture name, winner strategy ID, winner score, strategy ranking table, score formula, safety disclaimer, human review prompts, and decision checkboxes.
- Runtime notes under `data/notes` are ignored by git.
- Tests cover note generation, most recent artifact selection, malformed-artifact skipping, no-valid-artifact behavior, notes directory creation, no-overwrite behavior, force overwrite behavior, source artifact path, winner/ranking table, prompts, decision checkboxes, CLI output, and no external service or credential requirement.

Not included:

- Trading behavior changes.
- Scoring formula changes.
- Risk policy changes.
- Risk engine changes.
- Execution changes.
- Broker changes.
- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Options, margin, shorting, or sell proposals.

### Phase 6K - Research decision ledger

Status: complete for local Markdown research-decision logging.

Included:

- `record-research-decision` CLI command for appending one structured decision entry per invocation.
- Default local ledger path: `data/notes/research_decisions.md`.
- Notes directory creation when missing.
- Required `--strategy-id`, `--decision`, and `--reason` fields.
- Optional `--source-note`, `--next-action`, and `--ledger-path` fields.
- Decision validation for `promote`, `modify`, `retest`, `retire`, and `no_decision`.
- `research-decisions` CLI command for printing the existing ledger or a clear no-ledger message.
- Ledger entries include timestamp, strategy ID, decision, reason, optional source note path, optional next action, and safety reminder.
- Runtime decision ledgers remain under ignored `data/notes`.
- Tests cover new ledger creation, multiple appends, allowed decisions, invalid decisions, missing fields, optional source note path, optional next action, safety reminder, read behavior, CLI output, and no external service or credential requirement.

Not included:

- Trading behavior changes.
- Scoring formula changes.
- Risk policy changes.
- Risk engine changes.
- Execution changes.
- Broker changes.
- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Options, margin, shorting, or sell proposals.

### Phase 6L - Fixture scenario expansion

Status: complete for additional deterministic local market scenarios.

Included:

- New `compare-strategies --fixture` choices: `bull_trend`, `bear_trend`, `sideways_chop`, `volatile_reversal`, `spy_outperformance`, and `momentum_crash`.
- Existing `flat` and `multi_day` behavior remains backward compatible.
- Default comparison fixture remains `multi_day`.
- All new fixtures are local, deterministic, small, and beginner-readable.
- Each new fixture includes deterministic SPY benchmark movement and strategy-symbol movement.
- `momentum_crash` is intentionally challenging for the momentum strategy.
- `spy_outperformance` models a regime where SPY beats the local momentum strategy.
- Saved JSON, CSV, and Markdown comparison artifacts include the selected new fixture name.
- New fixture artifacts continue to work with `tournament-history`, `tournament-champion`, `export-leaderboard`, and `create-analysis-note`.
- Tests cover fixture acceptance, determinism, SPY movement, challenging momentum regime, SPY outperformance, saved fixture names, downstream reporting compatibility, and no external service or credential requirement.

Not included:

- Trading behavior changes.
- Scoring formula changes.
- Risk policy changes.
- Risk engine changes.
- Execution risk-path changes.
- Broker changes.
- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Options, margin, shorting, or sell proposals.

### Phase 6M - Fixture sweep tournament

Status: complete for local cross-fixture robustness summaries.

Included:

- `fixture-sweep` CLI command for running local strategy comparisons across deterministic non-flat fixtures.
- Sweep fixtures: `multi_day`, `bull_trend`, `bear_trend`, `sideways_chop`, `volatile_reversal`, `spy_outperformance`, and `momentum_crash`.
- `flat` is excluded from fixture sweeps by default.
- `fixture-sweep --include-hermes-fixtures` adds parser-only local Hermes fixture strategies.
- `fixture-sweep --save` writes JSON, CSV, and Markdown sweep artifacts.
- `fixture-sweep --output-dir` selects the artifact directory, defaulting to ignored runtime output under `data/experiments`.
- Beginner-readable CLI output includes fixture winners, winning scores, strategy wins, average score, average excess return, worst max drawdown, overall robust champion, score formula, score explanation, and safety disclaimer.
- Overall robust champion tie-breakers use most fixture wins, higher average score, higher average excess return, lower worst drawdown severity, then strategy ID alphabetical.
- Saved sweep artifacts include timestamp, fixtures included, per-fixture winners, per-strategy aggregate robustness metrics, overall champion, score formula, and score explanation.
- Tests cover fixture inclusion, flat exclusion, per-fixture winners, aggregate wins, average score, average excess return, worst drawdown, deterministic tie-breakers, Hermes fixture inclusion, saved artifacts, CLI output, and no external service or credential requirement.

Not included:

- Trading behavior changes.
- Scoring formula changes.
- Risk policy changes.
- Risk engine changes.
- Execution risk-path changes.
- Broker changes.
- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
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
