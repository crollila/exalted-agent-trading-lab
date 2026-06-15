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

### Phase 6N - Fixture sweep leaderboard export

Status: complete for local Markdown robustness leaderboard export.

Included:

- `export-fixture-sweep-leaderboard` CLI command reads saved `fixture-sweep --save` JSON artifacts.
- `export-fixture-sweep-leaderboard --output-dir` selects the artifact directory, defaulting to `data/experiments`.
- `export-fixture-sweep-leaderboard --report-path` selects the Markdown report path, defaulting to `data/reports/fixture_sweep_leaderboard.md`.
- Missing report directories are created automatically.
- No report is written when no valid fixture sweep artifacts exist.
- Malformed or incomplete fixture sweep artifacts are skipped and reported when valid artifacts also exist.
- The Markdown report includes title, generated timestamp, source artifact directory, current robust champion summary, fixture list, score formula/explanation, safety disclaimer, per-fixture winner table, strategy robustness aggregate table, caveats, most recent sweep artifact path, and skipped/malformed artifact count.
- Strategy robustness aggregates combine saved sweeps by fixture appearances, fixture wins, win rate, weighted average score, weighted average excess return, and worst max drawdown.
- Tests use temporary fixture files and do not depend on real `data/experiments` or `data/reports`.
- Tests cover one artifact, multiple artifacts, champion summary, per-fixture winner table, strategy robustness aggregate table, score explanation, safety disclaimer, caveats, output directory creation, no-artifact behavior, malformed-artifact skip behavior, CLI output, and no external service or credential requirement.

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

### Phase 6O - Fixture sweep analysis notes

Status: complete for local fixture sweep human-review notes.

Included:

- `create-sweep-analysis-note` CLI command reads saved `fixture_sweep_*.json` artifacts.
- `create-sweep-analysis-note --output-dir` selects the artifact directory, defaulting to `data/experiments`.
- `create-sweep-analysis-note --notes-dir` selects the Markdown note directory, defaulting to `data/notes`.
- `create-sweep-analysis-note --force` explicitly overwrites the deterministic note file when needed.
- Most recent valid fixture sweep artifact is selected by default.
- Notes directory is created when missing.
- Existing sweep notes are not overwritten unless `--force` is passed.
- Missing, empty, malformed, and mixed valid/malformed artifact directories are handled without tracebacks.
- Note filenames are deterministic and Windows-safe, using the sweep timestamp.
- Markdown notes include generated timestamp, source sweep artifact path, sweep timestamp, fixtures included, overall robust champion, champion metrics, per-fixture winner table, strategy robustness table, score formula/explanation, safety disclaimer, human review prompts, and decision checklist.
- Runtime notes under `data/notes` remain ignored by git.
- Tests cover one valid sweep artifact, most recent artifact selection, malformed-artifact skipping, no-valid-artifact behavior, notes directory creation, no-overwrite behavior, force overwrite behavior, source artifact path, robust champion, per-fixture winner table, strategy robustness table, human review prompts, decision checklist, CLI output, and no external service or credential requirement.

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

### Phase 6P - Strategy status registry

Status: complete for local append-only research status tracking.

Included:

- `set-strategy-status` CLI command appends local Markdown status entries.
- Default registry path is `data/notes/strategy_status.md`.
- `set-strategy-status --strategy-id`, `--status`, `--reason`, `--source-note`, `--next-action`, and `--registry-path` support.
- Valid statuses are `active`, `promoted`, `retest`, `modified`, and `retired`.
- Notes directory is created when missing.
- `strategy-status` CLI command prints current latest statuses and preserved history.
- `strategy-status --registry-path` selects a non-default registry for tests or alternate local ledgers.
- Entries include timestamp, strategy ID, status, reason, optional source note path, optional next action, and safety reminder.
- Status history is preserved in the same append-only Markdown file.
- Latest status per strategy is shown clearly before the history section.
- `data/notes` remains ignored by git.
- Tests cover new registry creation, valid statuses, invalid status rejection, updating the same strategy, latest-status display, history preservation, optional source note path, optional next action, safety reminder, no-file behavior, read behavior, CLI output, and no external service or credential requirement.

Deferred:

- Retired-strategy filtering for `compare-strategies` or `fixture-sweep` is left as a TODO because it would change tournament execution behavior and needs a separate careful pass.

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

### Phase 6Q - Status-aware research reports

Status: complete for local status annotations in research reports.

Included:

- Reports read latest strategy statuses from `data/notes/strategy_status.md` when present.
- Missing registry or missing strategy entries display as `unknown`.
- `fixture-sweep` terminal output includes strategy status in the robustness table.
- `fixture-sweep --save` JSON artifacts include a `strategy_statuses` map.
- `fixture-sweep --save` CSV artifacts include a status column.
- `fixture-sweep --save` Markdown artifacts include status in the strategy robustness table.
- `tournament-champion` terminal output includes the current champion strategy status.
- `export-leaderboard` Markdown reports include champion status and strategy status in aggregate tables.
- `export-fixture-sweep-leaderboard` Markdown reports include robust champion status and strategy status in aggregate tables.
- Strategy status annotations are deterministic reporting metadata only.
- Tests cover status registry parsing/reuse, missing registry behavior, fixture-sweep status output, tournament champion status output, exported leaderboard status output, exported fixture sweep leaderboard status output, saved sweep artifact status annotations, no execution filtering from retired status, and no external service or credential requirement.

Not included:

- Filtering, excluding, or changing which strategies run based on status.
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

### Phase 6R - Status-aware filtering controls

Status: complete for opt-in local research strategy filtering.

Included:

- `compare-strategies --exclude-retired` support for excluding only strategies whose latest local research status is exactly `retired`.
- `fixture-sweep --exclude-retired` support for excluding only strategies whose latest local research status is exactly `retired`.
- `compare-strategies --status active,promoted,retest` support for including only strategies whose latest local status is in the requested comma-separated list.
- `fixture-sweep --status active,promoted,retest` support for including only strategies whose latest local status is in the requested comma-separated list.
- `unknown` is an allowed explicit `--status` value for strategies missing from the local registry or when the registry is absent.
- Missing status registry files do not crash; missing strategy statuses resolve deterministically to `unknown`.
- Default `compare-strategies` behavior is unchanged and still runs the same default local strategies.
- Default `fixture-sweep` behavior is unchanged and still runs the same default local strategies across the same fixtures.
- Retired strategies are not excluded unless filtering is explicitly requested.
- Beginner-readable filter output lists included statuses and excluded strategy IDs with latest statuses.
- If a filter selects no strategies, the command prints a clear skip message without running a tournament.
- Saved comparison JSON, CSV, and Markdown artifacts include status-filter metadata when saved through the CLI.
- Saved fixture sweep JSON, CSV, and Markdown artifacts include status-filter metadata when saved through the CLI.
- Artifact readers remain compatible with older artifacts that do not have filter metadata.

Not included:

- Trading behavior changes.
- Scoring formula changes.
- Risk policy changes.
- Risk engine changes.
- Execution risk-path changes.
- Broker changes.
- Alpaca behavior changes.
- Hermes runtime wiring.
- Live trading.
- Alpaca calls.
- External market data.
- External LLM/API calls.
- Options, margin, shorting, or sell proposals.

### Phase 6S - Advanced permissions architecture plan

Status: complete for documentation-only future permissions planning.

Included:

- Dedicated advanced permissions architecture plan at `docs/advanced_permissions_plan.md`.
- Staged future permission roadmap: paper shorting design, paper shorting dry-run simulation, paper margin design, paper margin dry-run simulation, paper options design, paper options dry-run simulation, broker-paper implementation only after simulator/risk tests, and live trading remaining out of scope until long-term validation.
- Future deterministic permission model covering environment, asset classes, trade directions, leverage, order authority, strategy allowlists, and account allowlists.
- Gate order for future advanced features: model proposals, expand logging, add fail-closed deterministic risk checks, simulate locally, add paper-only abstractions, then consider paper execution only after review.
- Future paper-shorting design notes for explicit strategy and CLI/user permission flags, max short exposure, max gross exposure, max net exposure, max loss per short position, forced-cover behavior, borrow availability assumption logging, hard permission bans, and no live shorting.
- Future margin design notes for explicit permission levels, max gross exposure, max net exposure, max daily loss, margin call simulation, forced deleveraging, no live margin, and no silent margin implied by buying power.
- Future options design notes for explicit option contract models, underlying symbol, call/put, expiration, strike, contracts, premium, max premium at risk, max contracts, Greeks when available, liquidity/open-interest assumptions, assignment/exercise risk notes, no 0DTE at first, no naked short options at first, and no live options.
- Future reporting requirements so saved artifacts can identify stock-only, short-enabled, margin-enabled, options-enabled, simulated, paper, or shadow-live conditions.
- Future testing requirements for disabled-permission rejection, fail-closed defaults, paper-only boundaries, broker wrapper rejection, Hermes parser boundaries, and no external-service requirements.
- Documentation note that future phases changing permissions must update `STATUS.md`, `BUILD_PLAN.md`, `README.md`, and `docs/risk_policy.md`.
- Future Codex prompt added at `docs/codex_prompts/phase_6t_shorting_design_models.md` for shorting proposal/risk model design without enabling execution.

Not included:

- Trading permission changes.
- Risk policy changes.
- Risk limit changes.
- Proposal model changes.
- Risk engine changes.
- Execution changes.
- Broker/order submission changes.
- Alpaca shorting, margin, or options calls.
- Hermes runtime wiring.
- Live trading.
- Paper shorting.
- Paper margin.
- Paper options.
- External market data.
- External LLM/API calls.
- Options, margin, shorting, or sell proposals.

### Phase 6T - Shorting design models

Status: complete for future-facing model definitions and disabled-flow tests.

Goal: design shorting proposal and risk input models without enabling short execution.

Included:

- Separate inert shorting model definitions: `ShortProposal`, `ShortRiskLimits`, and `ShortRiskDecision`.
- Strict shorting model validation for stock-only asset class, short action, non-empty symbol, non-empty thesis, confidence range, positive estimated price, positive bounded short exposure, explicit borrow availability assumption, and forbidden extra fields.
- Future shorting fields for strategy ID, symbol, action, target short weight, notional exposure, estimated price, thesis, confidence, borrow availability assumption, borrow fee assumption, max-loss exit price, and forced-cover threshold.
- Tests proving valid short models work and invalid fields are rejected.
- Tests proving current `TradeProposal` behavior is unchanged.
- Tests proving the current executable risk flow still rejects sell-over-position shorting attempts.
- Tests proving `compare-strategies` and `fixture-sweep` behavior remains unchanged.

Required future controls documented but not enabled:

- Explicit strategy permission flag.
- Explicit CLI/user permission flag.
- Max short exposure.
- Max gross exposure.
- Max net exposure.
- Max loss per short position.
- Forced-cover rule.
- Borrow availability assumption logging.
- Hard ban on shorting without a specific permission level.
- No live shorting.

Not included:

- Shorting execution.
- Shorting simulation.
- Broker shorting calls.
- Risk engine behavior changes.
- Order execution behavior changes.
- Trading permission changes.
- Margin.
- Options.
- Live trading.
- Hermes runtime wiring.

### Phase 6U - Shorting dry-run simulator design

Status: complete for isolated local-only short simulation foundation.

Goal: simulate future-facing `ShortProposal` objects with deterministic local prices only, without enabling executable shorting.

Included:

- Isolated simulation-only shorting module at `src/simulation/shorting_simulator.py`.
- Local-only `simulate_short_proposal` function that accepts inert `ShortProposal` objects and deterministic local price tuples.
- Simulation result models for short position results, risk events, gross exposure, net exposure, and short exposure.
- Deterministic calculations for opening short notional, cover price, unrealized P/L, realized P/L, optional borrow fee estimate, forced-cover detection, gross exposure, net exposure, and short exposure.
- Tests for profitable short simulation when price falls.
- Tests for losing short simulation when price rises.
- Tests for forced-cover trigger detection.
- Tests for borrow fee impact.
- Tests for deterministic gross/net/short exposure.
- Tests proving invalid `ShortProposal` objects are rejected by model validation.
- Tests proving the simulator requires only local deterministic inputs and no Alpaca credentials.
- Tests proving `compare-strategies` and `fixture-sweep` behavior remains unchanged.
- Tests proving the executable risk engine still rejects shorting.

Not included:

- Executable shorting.
- Broker shorting calls.
- Alpaca shorting calls.
- Order executor changes.
- Risk engine behavior changes.
- Existing dry-run execution changes.
- Real market data.
- Runtime artifacts.
- CLI command.
- Margin.
- Options.
- Live trading.
- Hermes runtime wiring.

### Phase 6V - Shorting simulation report export

Status: complete for local-only short simulation report export.

Goal: make the isolated shorting simulator reviewable through deterministic local Markdown reporting without enabling shorting in normal execution.

Included:

- Local-only report module at `src/reporting/shorting_simulation_report.py`.
- Deterministic hardcoded `ShortProposal` example and deterministic local price tuple.
- Markdown report export to ignored runtime path `data/reports/shorting_simulation_report.md` by default.
- `export-short-simulation-report` CLI command with `--report-path` support.
- Missing report directories are created automatically.
- Report includes generated timestamp, simulation-only disclaimer, executable-shorting-disabled statement, proposal symbol/action/target short weight, entry price, cover price, gross exposure, net exposure, short exposure, realized/unrealized P/L, borrow fee estimate, forced-cover status, and risk event status.
- CLI prints `simulation only` and does not require credentials.
- Tests cover disclaimer wording, key short metrics, forced-cover/risk event status, output directory creation, CLI operation without credentials, unchanged comparison/sweep behavior, and existing executable risk-engine rejection of shorting.

Not included:

- Executable shorting.
- Broker shorting calls.
- Alpaca shorting calls.
- Order executor changes.
- Risk engine behavior changes.
- Existing dry-run execution changes.
- Real market data.
- Strategy shorting integration.
- Margin.
- Options.
- Live trading.
- Hermes runtime wiring.

### Phase 6W - Options design models

Status: complete for future-facing model definitions and disabled-flow tests.

Goal: design option proposal and risk input models without enabling options execution.

Included:

- Separate inert options model definitions: `OptionContract`, `OptionProposal`, `OptionRiskLimits`, and `OptionRiskDecision`.
- Strict option validation for required underlying symbol, call/put option type, buy-to-open or buy-to-close actions only, no sell-to-open actions, future expiration only, positive strike, positive contract count, positive premium, positive estimated total premium, non-empty thesis, confidence range, non-empty liquidity/open-interest assumption, non-empty assignment/exercise risk note, optional Greeks, and forbidden extra fields.
- Future option risk defaults for max premium at risk, max contracts per trade, max portfolio option exposure, no 0DTE, no naked short options, no live options, and broker option execution disabled by default.
- Inert `check_option_risk` helper for model-level future design checks, including excessive contracts, excessive premium, projected portfolio option exposure, and fail-closed disabled options permission.
- Tests proving valid option proposals work and invalid option type, sell-to-open/naked-short actions, 0DTE expiration, invalid strike/contracts/premium, missing thesis, missing risk note, invalid confidence, extra fields, and excessive contracts/premium are rejected.
- Tests proving current `TradeProposal` behavior is unchanged.
- Tests proving the current executable risk flow still rejects options.
- Tests proving `compare-strategies` and `fixture-sweep` behavior remains unchanged.

Not included:

- Options execution.
- Broker options calls.
- Alpaca options calls.
- Order executor changes.
- Existing risk engine behavior changes.
- Existing dry-run execution changes.
- Strategy options integration.
- Executable shorting.
- Margin.
- Live trading.
- Hermes runtime wiring.

### Phase 6X - Options dry-run simulator foundation

Status: complete for isolated local-only options simulation foundation.

Goal: simulate future-facing `OptionProposal` objects with deterministic local premium inputs only, without enabling options execution.

Included:

- Isolated simulation-only options module at `src/simulation/options_simulator.py`.
- Local-only `simulate_option_proposal` function that accepts inert `OptionProposal` objects and deterministic local premium inputs.
- Simulation result models for option position results, premium-at-risk risk events, and simulation-only outputs.
- Deterministic calculations for entry premium, exit premium, contracts, contract multiplier, premium paid, exit value, realized P/L, max premium at risk, return on premium, optional intrinsic value at expiration, and optional expiration outcome.
- Risk event detection when simulated premium at risk exceeds configured option risk limits.
- Tests for profitable long-call simulation when premium rises.
- Tests for losing long-call simulation when premium falls.
- Tests for profitable long-put simulation when premium rises.
- Tests for deterministic premium-at-risk calculation, contract multiplier handling, return-on-premium calculation, and premium-at-risk risk events.
- Tests proving invalid `OptionProposal` objects are rejected by model validation.
- Tests proving the simulator requires only local deterministic inputs and no Alpaca credentials.
- Tests proving `compare-strategies`, `fixture-sweep`, and `export-short-simulation-report` behavior remains unchanged.
- Tests proving the executable risk engine still rejects options.

Not included:

- Options execution.
- Broker options calls.
- Alpaca options calls.
- Order executor changes.
- Risk engine behavior changes.
- Existing dry-run execution changes.
- Runtime artifacts.
- CLI command.
- Strategy options integration.
- Executable shorting.
- Margin.
- Live trading.
- Hermes runtime wiring.

### Future Phase 6Y - Paper margin design

Status: planned, not implemented.

Goal: design explicit margin permission, exposure accounting, margin call simulation, daily loss limits, and forced deleveraging without enabling margin.

### Future Phase 6Z - Paper margin dry-run simulation

Status: planned, not implemented.

Goal: simulate margin exposure and forced deleveraging locally after design and tests, with no broker calls.

### Phase 7A - Hermes multi-agent strategy sandbox router

Status: complete for local-only sandbox review.

Goal: let Hermes-style agents propose advanced ideas while the lab routes each idea into a safe non-executing lane.

Included:

- New local-only sandbox module at `src/agents/hermes_strategy_sandbox.py`.
- Strict top-level request model with `agent_id`, `team_id`, `strategy_id`, `agent_role`, `proposals`, and optional `strategy_notes` and `learning_goal`.
- Strict proposal routing for `stock_long`, `short_stock`, `option_long`, and `margin`.
- `stock_long` maps to the existing `TradeProposal` model and routes to `paper_eligible_stock_long`.
- `short_stock` maps to the existing inert `ShortProposal` model and routes to `simulation_only_short`.
- `option_long` maps to the existing inert `OptionProposal` model and routes to `simulation_only_option`.
- `margin` maps to a strict simulation-only placeholder and routes to `simulation_only_margin`.
- Invalid JSON, missing required request fields, empty proposals, unknown proposal types, malformed stock/short/option proposals, and forbidden extra fields are rejected.
- `review-hermes-sandbox --file` CLI command reads strict local JSON and prints team, agent, strategy, route counts, proposal routes, and the warning that Hermes proposals are not execution approval.
- Example mixed payload at `docs/examples/hermes_strategy_sandbox_example.json`.
- Tests cover valid mixed parsing, route mapping, unknown type rejection, malformed advanced proposals, CLI operation without credentials, and no Alpaca/settings/database calls from review.

Not included:

- Hermes runtime wiring.
- Real LLM/API calls.
- Alpaca calls from review.
- Broker calls.
- Order submission or order writes.
- Portfolio state changes.
- Live trading.
- Real options, short, or margin broker execution.
- Risk bypasses.

### Phase 7B - Hermes agent team registry

Status: complete for local-only identity registry review.

Goal: create the team and agent identity layer for future Hermes-style strategy tournaments.

Included:

- New local-only registry module at `src/agents/hermes_team_registry.py`.
- Strict models for `HermesAgentProfile`, `HermesTeamProfile`, `HermesTeamRegistry`, and `HermesAgentRole`.
- Allowed roles: `research_agent`, `risk_agent`, `execution_agent`, `review_agent`, `strategy_mutator`, and `portfolio_manager`.
- Agent fields for IDs, team ID, name, role, description, active status, optional model hint, strengths, weaknesses, latest strategy ID, and learning notes.
- Team fields for ID, name, description, agents, active status, optional strategy family, and learning notes.
- Registry validation rejects missing IDs, duplicate team IDs, duplicate agent IDs across teams, invalid roles, empty team agent lists, mismatched agent/team IDs, and extra unknown fields.
- Example local registry at `docs/examples/hermes_team_registry_example.json` with `team_alpha`, `team_beta`, distinct roles, active/inactive agents, learning notes, and no secrets.
- `hermes-teams --file` CLI command reads a local JSON file and prints teams, agents, active/inactive status, roles, and the warning `registry only; no trading or LLM calls`.
- Tests cover valid parsing, registry validation failures, credential-free CLI operation, no settings/database/Alpaca usage from the command, and unchanged sandbox/comparison/sweep commands.

Not included:

- Hermes runtime calls.
- Real LLM/API calls.
- Alpaca calls.
- Broker calls.
- Order submission or order writes.
- Portfolio state changes.
- Trading permission changes.
- Risk bypasses.

### Phase 7C - Hermes tournament round runner

Status: complete for local-only routing-score tournaments.

Goal: run a Nate-style local tournament round from a Hermes team registry and one or more strict local Hermes proposal files.

Included:

- New local-only tournament module at `src/agents/hermes_tournament_round.py`.
- `hermes-tournament-round` CLI command with `--registry`, repeatable or comma-separated `--proposal`, `--output-dir`, and `--save`.
- Registry loading through the existing Hermes team registry validation.
- Proposal loading through the existing Hermes strategy sandbox router.
- Safe handling for malformed proposal files and unknown proposal team IDs.
- Per-proposal/team rows with team ID, agent ID, strategy ID, total proposals, route counts, rejected count, score, and warnings.
- Deterministic score formula: `score = paper_eligible_count * 2 + simulation_only_count * 1 - rejected_count * 1`.
- Deterministic team ranking by score descending, fewer rejected proposals, then team ID alphabetical.
- CLI output with winner, proposal rows, rankings, warnings, and the disclaimer that routing score is not profitability.
- Optional `--save` writes local JSON and Markdown artifacts under `data/experiments` by default.
- Second local proposal example for `team_beta` at `docs/examples/hermes_strategy_sandbox_team_beta_example.json`.

Not included:

- Hermes runtime calls.
- Real LLM/API calls.
- Alpaca calls.
- Broker calls.
- Order execution or order writes.
- Portfolio state changes.
- Profitability scoring.
- Trading permission changes.
- Risk bypasses.

### Phase 7D - Hermes runtime adapter

Status: complete for opt-in local/OpenAI-compatible proposal generation.

Goal: allow a configured Hermes-compatible chat endpoint to generate strict sandbox proposal JSON files, then save and validate them locally.

Included:

- New runtime adapter at `src/agents/hermes_runtime.py`.
- Strict `HermesRuntimeConfig`, `HermesGenerationRequest`, and `HermesGenerationResult` models.
- Runtime refuses unless `HERMES_ENABLED=true`.
- Runtime requires `HERMES_BASE_URL` and `HERMES_MODEL`.
- Supports generic OpenAI-compatible `/chat/completions` calls with optional `HERMES_API_KEY` and configurable timeout.
- Prompt builder requires JSON-only output matching the Hermes sandbox schema.
- Prompt explicitly bans secrets, execution claims, broker access, and prose/Markdown outside JSON.
- `hermes-generate-proposals` CLI writes raw generated JSON to a local output file, creates the output directory, validates the saved file through the sandbox router, and prints the route summary.
- Tests mock HTTP completely and require no real Hermes endpoint, real LLM, network, credentials, Alpaca, or broker access.

Not included:

- Live trading.
- Real order execution.
- Alpaca calls.
- Broker calls.
- Broker credential access.
- Runtime Hermes order authority.
- Risk bypasses.
- Automatic tournament submission.

### Phase 7E - Discord bot skeleton

Status: complete for safe local Discord command-center commands.

Goal: run a local Discord bot for quick lab status, Hermes team, proposal review, Hermes ask-team proposal generation, and tournament summaries without adding any trading authority.

Included:

- New Discord bot module at `src/discord_bot/bot.py`.
- `discord-bot` CLI command with clear refusal when `DISCORD_BOT_TOKEN` is missing.
- Optional `DISCORD_GUILD_ID`, optional comma-separated `DISCORD_ALLOWED_CHANNEL_IDS`, and default local registry/proposal file environment variables.
- Startup warning when `DISCORD_ALLOWED_CHANNEL_IDS` is unset and all channels are allowed.
- Prefix commands `!status`, `!teams`, `!review_proposals`, and `!run_tournament`.
- Prefix command `!ask_team <team_id> <agent_id> <agent_role> <strategy_id> <prompt text>` for configured Hermes runtime proposal generation.
- Slash command registration for `/status`, `/teams`, `/review_proposals`, `/run_tournament`, and `/ask_team` when Discord command sync succeeds.
- Discord-friendly summaries using the existing Hermes team registry, sandbox review, and tournament round logic.
- `!ask_team` uses the existing Hermes runtime adapter, saves generated proposal JSON under ignored `data/agent_runs/`, validates it through the sandbox router, and returns saved path plus route counts.
- Beginner setup guide at `docs/discord_bot_setup.md`.
- Tests cover missing-token refusal, allowlist parsing, summaries, ask-team mocked runtime generation, saved proposal validation, local-only behavior, and CLI refusal without connecting to Discord.

Not included:

- Live trading.
- Alpaca calls.
- Broker calls.
- Order execution or order writes.
- Portfolio state changes.
- Risk bypasses.
- Real Discord network calls in tests.
- Real Hermes network calls in tests.

### Phase 7F - Two-team Discord Alpaca paper competition foundations

Status: complete for registry, team paper config validation, expanded proposal routing, safe Discord team account commands, and explicit stock-long paper execution. Advanced short/margin/options paper execution remains gated.

Goal: shape the Discord/Hermes competition center around exactly two teams, team-specific Alpaca paper accounts, and strict proposal-first advanced strategy routing without allowing Discord or Hermes to bypass Python risk controls.

Included:

- Default registry at `docs/examples/hermes_team_registry_example.json` with exactly two active teams and three active agents per team.
- Team Alpha agents: `alpha_research_01`, `alpha_risk_01`, and `alpha_review_01`.
- Team Beta agents: `beta_research_01`, `beta_risk_01`, and `beta_review_01`.
- Team-specific Alpaca paper configuration loader for `TEAM_ALPHA_*` and `TEAM_BETA_*` env vars.
- Paper enforcement requires `*_ALPACA_PAPER=true` and base URL exactly `https://paper-api.alpaca.markets`.
- Safe config status messages that do not print secrets and do not crash the bot when credentials are missing.
- Expanded Hermes sandbox proposal types for `stock_long`, `stock_short`, `stock_margin_long`, `stock_margin_short`, `option_long_call`, `option_long_put`, `covered_call`, and `cash_secured_put`.
- Strict rejection for unknown proposal types, missing symbols/underlying symbols, missing thesis, missing confidence, 0DTE options, and uncovered/collateral-missing short-option shapes.
- Runtime prompt updated to request the Phase 7F proposal vocabulary.
- Discord `!team_paper_status` and `!team_positions` commands for team-specific Alpaca paper account summaries.
- Discord `!ask_agent` for role-aware paper-only agent chat saved under ignored runtime notes.
- Discord `!latest_agent_run` and `!run_tournament latest` helpers for the newest saved proposal JSON.
- Discord `!paper_trade_team` as the only explicit paper-submitting command path.
- `!paper_trade_team` requires risk/review approval notes, logs proposals, risk decisions, paper order attempts, and portfolio snapshots, and submits only approved stock-long paper orders.
- Discord `!team_report` reports clearly when benchmark data is not available.

Not included:

- Paper short execution.
- Paper margin execution.
- Paper options execution.
- Alpaca calls from Hermes.
- Live trading.
- Risk bypasses.

### Phase 7G - Natural Discord team chat and autonomous paper-cycle scaffolding

Status: complete for natural team-channel chat, explicit autonomy status, scheduled update scaffolding, and gated stock-long autonomous paper-cycle scaffolding.

Goal: make Discord feel like a Team Alpha / Team Beta agent workspace instead of a command-only bot, while keeping Python risk and paper-only broker boundaries as hard gates.

Included:

- Natural team chat routing from configured Discord channels with `DISCORD_TEAM_ALPHA_CHANNEL_ID` and `DISCORD_TEAM_BETA_CHANNEL_ID`.
- Normal non-command team-channel messages are sent to that team's active research, risk, and review agents.
- Agent team-chat responses are saved under ignored runtime notes and marked as no-trade chat.
- Explicit per-team autonomy flags with `TEAM_ALPHA_AUTONOMY_ENABLED` and `TEAM_BETA_AUTONOMY_ENABLED`, defaulting to disabled.
- Per-team autonomy mode, daily paper order cap, daily notional cap, and risk/review approval requirement env vars.
- Local ignored runtime autonomy overrides through `enable_autonomy` and `disable_autonomy`.
- Discord team autonomy status helpers that list the full paper-cycle gates.
- Scheduled team progress update scaffolding with `DISCORD_SCHEDULED_TEAM_UPDATES_ENABLED` and `DISCORD_SCHEDULED_TEAM_UPDATE_MINUTES`.
- `run_team_cycle` helper that asks the research agent for proposal JSON, asks the risk agent for `RISK_AGENT_APPROVED: true`, asks the review agent for `REVIEW_AGENT_APPROVED: true`, and stops unless all gates are present.
- Autonomous paper execution reuses the gated `paper_trade_team` path only when team autonomy is enabled, both approval tokens are present, stock-long-only mode is active, and daily order/notional caps pass.
- Manual `schedule_reports_status` and `daily_team_report_now` report scaffolds without adding an external scheduler.
- Deterministic Python risk validation remains the final hard gate before stock-long paper submission.
- Hermes prompt and sandbox validation improvements for expired options, missing theses, covered-call/cash-secured-put side consistency, stale option expirations, and benchmark-like SPY warnings for beat-SPY goals.
- Tests cover team channel parsing, autonomy parsing, natural chat fan-out, scheduled update text, disabled-autonomy refusal, enabled-autonomy approved paper submission, daily cap enforcement, manual report summaries, and Hermes validation warnings/rejections.

Not included:

- Live trading.
- Paper short execution.
- Paper margin execution.
- Paper options execution.
- Alpaca calls from Hermes prompts.
- Natural chat order placement.
- `ask_team`, `ask_agent`, or tournament order placement.
- Risk bypasses.

### Future Phase 7H - Advanced broker-paper implementation gate

Status: planned, not implemented.

Goal: consider advanced paper broker implementation only after simulator and risk tests pass, docs are updated, and the user explicitly approves a later phase.

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

## Phase 8 - Alpha vs Beta weekly paper competition + advanced paper permissions

Goal: unlock paper-only shorting, margin, and options for two autonomous research teams
without removing safeguards, by building the missing deterministic infrastructure.

Delivered:

- `src/config/permissions.py` — explicit, paper-only permission levels and risk caps; all
  advanced levels default off (fail-closed).
- `src/safety/kill_switch.py` — persisted global kill switch checked before every broker submit.
- `src/competition/proposals.py` — strict schema for all 8 proposal types with provenance.
- `src/competition/risk_engine.py` — deterministic advanced risk for shorting/margin/options;
  computes approved quantity/contracts (never the LLM).
- `src/competition/router.py` — routes to execution_eligible / simulation_only / rejected.
- `src/competition/execution.py` — the single gated broker bridge; logs every attempt.
- `src/brokers/options_adapter.py` — options adapter boundary (clear error if unconfigured).
- `src/brokers/alpaca_client.py` — gated short/margin/option submit methods, live-endpoint refusal.
- `src/competition/week_competition.py` + `scorecard.py` — competition state, 11-stage cycle,
  scorecards with SPY comparison and ranking.
- `src/learning/team_memory.py` — runtime learning ledger (memory/scorecards/prompt feedback).
- `src/research/data_tools.py` — allowlisted, provenance-tagged, read-only research tools.
- `src/agents/llm_provider.py` — OpenAI/Anthropic/Ollama abstraction, structured JSON only.
- UI pages, Discord commands, CLI commands, comprehensive mocked tests, and docs.
- `src/competition/attribution.py` — proposal/trade attribution + an outcome refresh system
  (`refresh-proposal-attribution`). Refresh re-scores pending outcomes against the latest paper
  prices + the SPY benchmark (safe market-data wrapper; team credentials only), persisting
  `current_price`, `unrealized_pnl`, `return_pct`, `spy_start_price`/`spy_current_price`,
  `spy_return_pct`, `excess_return_pct`, `outcome_status` (pending/worked/failed/mixed), and
  `refreshed_at`. SPY-relative verdict around a small configurable threshold; missing
  entry/price, unavailable SPY, or options stay pending with a skip reason. Backward-compatible
  JSONL (old rows load; new fields default), rewritten atomically. A compact "recent outcome
  feedback" block feeds the next LLM cycle as research feedback only (never bypasses risk).

Non-goals (unchanged): no live trading, no model-weight training, no LLM broker access, no
order submission from chat/Agent Hub/ask/UI surfaces.

## Phase 7M - Portfolio Manager / Capital Allocator

Goal: stop blindly generating new trades every cycle. Teams first review the portfolio and decide
whether to hold, trim, close, rotate, add, hedge, reduce exposure, request margin, or do nothing.

Delivered:

- `src/competition/portfolio_manager.py` — `PortfolioDecisionType`, `PortfolioDecision`,
  `PortfolioManagerConfig` (env-driven cost control), and a deterministic `review_portfolio`. Team
  personalities (alpha = higher-variance exploration; beta = conservative conservation), dynamic
  proposal caps (0–3), buying-power-aware gating, and no-trade as a first-class outcome. An optional
  LLM intent can only narrow behavior — never widen caps, unblock low-BP buys, or bypass hard caps.
- `src/competition/week_competition.py` — Portfolio Manager stage (4b) + dynamic-cap gate (6b) wired
  into the 11-stage cycle; `apply_portfolio_gate` demotes over-cap/blocked opens to advisory
  `simulation_only`; `CycleResult.no_trade`; scorecard + strategy-memory (mode, avoid-next-cycle) fields.
- `src/competition/execution.py` — `classify_broker_error` + `broker_rejected`/`broker_reject_reason`/
  `broker_reject_code`/`failure_category` on `ExecutionRecord`, flowing into attribution + PM context.
- Prompt self-review questions + a `portfolio_decision` schema in `src/agents/llm_proposal_agent.py`.
- CLI: `run-week-cycle` prints the decision and "No trade decision"; `week-competition-status`,
  `proposal-attribution`, and `team-learning-status` surface PM/broker-rejection fields.
- Deterministic mocked tests (no network/credentials/market hours) and docs.

Non-goals (unchanged): no live trading, no model-weight training, no LLM broker access, no
order submission from chat/Agent Hub/ask/UI surfaces, no weakening of hard risk caps.

## Phase 7N - Strategy Debate, Daily SPY Attribution, and Cheap Cycle Gate

Goal: make teams behave like investment teams that review outcomes, explain SPY-relative results,
and only spend LLM/API calls when useful.

Delivered:

- `src/competition/cycle_gate.py` — `CheapCycleGateConfig` (env), `GateDecision`, deterministic
  `evaluate_cheap_cycle_gate` (interval per team, major-SPY-move/broker-rejection/research triggers,
  low-buying-power → review recommendation not forced orders). CLI `cheap-cycle-gate --team`.
- Review-only mode: `run_week_cycle(..., review_only=True)` + `run-week-cycle --review-only`. Runs the
  review, updates memory/scorecard, submits no new orders, never builds a broker client, and does not
  reset the full-cycle timer (`TeamLearningLedger.last_full_cycle_at`).
- `src/competition/daily_review.py` — symbol buckets, `compute_daily_spy_attribution` (returns/excess,
  long/short contribution, winners/losers, submitted/rejected, no-trade cycles, sector buckets, driver
  explanation), and `DailyTeamReview` strategy-debate artifact persisted under `data/reviews/`
  (atomic write). CLI `daily-spy-attribution` and `export-daily-team-review`.
- `daily_review_context` feeds a compact previous-review block into `build_llm_context` (research
  feedback only). `src/competition/scorecard.py` gains `load_scorecard_history` for no-trade counting.
- Deterministic mocked tests (no network/credentials/LLM), docs, and a cheaper suggested loop in README.

Non-goals (unchanged): no live trading, no model-weight training, no LLM broker access, no new external
web/search calls (OpenAI web search stays off; Alpaca news only), no weakening of hard risk caps.

## Phase 7O - LLM Model Routing and Cost-Saving Automation

Goal: spend strong models only on high-value strategy/proposal decisions; use cheaper models for the
rest; and replace the manual PowerShell loop with a single cheaper runner command.

Delivered:

- `src/agents/model_routing.py` — task → model resolution (`LLM_MODEL_<TASK>` → `LLM_MODEL` →
  `OPENAI_MODEL` → default), `build_routed_provider(task)`, and `routing_status` (model names +
  key-configured bool, never secrets). `LLM_PROVIDER` alias added to `LLMProviderConfig.from_env`.
- run-week-cycle's LLM proposal path now uses the routed `strategy` model and prints the provider +
  strategy model. Deterministic paths remain deterministic (not forced onto an LLM).
- CLI `llm-routing-status`; CLI `run-cheap-competition-loop` (`--once`, `--sleep-seconds`, `--team`,
  `--market-hours-only`/`--no-market-hours-only`, `--run-review-only-when-skipped`, `--dry-run-loop`).
  The loop refreshes + gates cheaply and runs a full cycle only when the gate says so; it never bypasses
  the kill switch and never submits unless `run-week-cycle` is invoked.
- `.env.example` adds the routing vars + cheaper defaults and de-duplicates `CHEAP_CYCLE_GATE_ENABLED`
  (now a single `true`). Mocked tests, docs.

Non-goals (unchanged): no live trading, no model-weight training, no LLM broker access, no new external
web/search calls, no weakening of hard risk caps, no real OpenAI calls in tests.
