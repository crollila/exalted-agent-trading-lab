# STATUS

## Current state

Phase 6P strategy status registry completed.

Included:

- Python source tree.
- SQLite schema.
- Pydantic models with basic validation.
- Stateful deterministic risk validator with approved quantity and estimated trade value outputs.
- Strategy interface.
- Cash-only baseline strategy.
- SPY buy-and-hold baseline.
- Simple deterministic momentum baseline strategy.
- Strict Hermes JSON proposal parser that converts valid local payloads into `TradeProposal` objects only.
- Parser-only local Hermes fixture strategies: `hermes_conservative_fixture` and `hermes_aggressive_fixture`.
- Safe Hermes parser rejection for invalid JSON, missing fields, empty symbols, non-buy actions, non-stock assets, options, invalid target weights, empty theses, out-of-range confidence, extra fields, and missing local estimated prices.
- Dry-run order executor that only uses risk-approved quantities.
- `dry-run --strategy` CLI selection for known local deterministic strategies.
- `compare-strategies` CLI command that runs `cash_only`, `spy_buy_hold`, and `momentum_v1` in separate dry-run records.
- Deterministic local `multi_day` comparison fixture for SPY, SPY buy-and-hold, and momentum strategy symbols.
- `compare-strategies --fixture multi_day` explicit fixture selection, with `multi_day` as the default and `flat` available for the old placeholder behavior.
- `compare-strategies --save` local artifact output for JSON, CSV, and Markdown experiment summaries.
- `compare-strategies --include-hermes-fixtures` support for adding local Hermes-shaped JSON fixture strategies to dry-run comparison and saved artifacts.
- `compare-strategies --output-dir` support, defaulting to ignored runtime output under `data/experiments`.
- Deterministic tournament scoring for `compare-strategies`.
- Ranked comparison output sorted by best score first.
- Beginner-readable score formula: `score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)`.
- Deterministic ranking tie-breakers: higher excess return, lower drawdown, fewer rejected trades, then strategy ID alphabetical.
- Saved comparison artifacts include experiment timestamp, fixture name, strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
- Saved comparison artifacts now also include rank, score, score formula, and score explanation.
- `tournament-history` CLI command for reviewing saved `compare-strategies --save` JSON artifacts over time.
- `tournament-history --output-dir` support, defaulting to ignored runtime output under `data/experiments`.
- Beginner-readable tournament history output with artifact timestamp, fixture name, strategy count, winning strategy ID, winning score, winning strategy return, winning SPY return, winning excess return, winning max drawdown, and artifact path.
- Tournament history sorts valid artifacts newest first and reports malformed artifacts without crashing.
- `tournament-champion` CLI command for summarizing the current champion strategy across saved ranked tournament artifacts.
- `tournament-champion --output-dir` support, defaulting to ignored runtime output under `data/experiments`.
- Beginner-readable champion output with champion strategy ID, valid tournament count, wins, win rate, best score, average score, average excess return, worst max drawdown, most recent win timestamp, fixtures where the champion appeared, and skipped/malformed artifact count.
- Champion selection uses most rank-1 wins, then deterministic tie-breakers for average score, best score, average excess return, worst drawdown, and strategy ID.
- `export-leaderboard` CLI command for generating a clean Markdown strategy leaderboard report from saved ranked tournament artifacts.
- `export-leaderboard --output-dir` and `--report-path` support, defaulting to ignored runtime paths under `data/experiments` and `data/reports/strategy_leaderboard.md`.
- Leaderboard report includes generated timestamp, current champion summary, score formula, safety disclaimer, recent tournament table, strategy aggregate table, fixture caveats, artifact source directory, and skipped/malformed artifact count when applicable.
- Leaderboard export creates missing report directories and does not write a report when no valid artifacts exist.
- Top-level README now includes a concise project summary, portfolio/recruiting review notes, architecture flow, safety disclaimer, current capabilities, beginner command workflow, and a portfolio note.
- `create-analysis-note` CLI command for generating local Markdown human review templates from the latest valid saved ranked tournament artifact.
- `create-analysis-note --output-dir` and `--notes-dir` support, defaulting to ignored runtime paths under `data/experiments` and `data/notes`.
- `create-analysis-note --force` support for explicit safe overwrites when the deterministic note filename already exists.
- Analysis notes include generated timestamp, source artifact path, tournament timestamp, fixture name, winner strategy ID, winner score, ranking table, score formula, safety disclaimer, human review prompts, and decision checkboxes.
- Malformed tournament artifacts are skipped safely during analysis-note generation, and empty or missing artifact directories produce beginner-readable messages without tracebacks.
- `record-research-decision` CLI command for appending structured local research decisions to `data/notes/research_decisions.md`.
- `record-research-decision --strategy-id`, `--decision`, `--reason`, `--source-note`, `--next-action`, and `--ledger-path` support.
- Research decision validation for `promote`, `modify`, `retest`, `retire`, and `no_decision`.
- `research-decisions` CLI command for reading the local Markdown decision ledger or printing a clear no-ledger message.
- Research decision entries include timestamp, strategy ID, decision, reason, optional source note path, optional next action, and a safety reminder that the entry is not live trading approval and changes no broker/order behavior.
- Expanded deterministic local comparison fixtures: `bull_trend`, `bear_trend`, `sideways_chop`, `volatile_reversal`, `spy_outperformance`, and `momentum_crash`.
- Existing `flat` and `multi_day` fixture behavior remains backward compatible, with `multi_day` still the default for strategy comparison.
- New fixture artifacts flow through tournament history, tournament champion, leaderboard export, and analysis-note generation.
- `fixture-sweep` CLI command for running local strategy comparison across all deterministic non-flat fixtures.
- `fixture-sweep --include-hermes-fixtures` support for adding parser-only local Hermes fixture strategies to the sweep.
- `fixture-sweep --save` and `--output-dir` support for ignored runtime JSON, CSV, and Markdown sweep artifacts under `data/experiments`.
- Fixture sweep output includes per-fixture winners, strategy aggregate wins, average score, average excess return, worst max drawdown, overall robust champion, score formula, score explanation, and safety disclaimer.
- Overall robust champion tie-breakers use most fixture wins, higher average score, higher average excess return, lower worst drawdown severity, then strategy ID alphabetical.
- `export-fixture-sweep-leaderboard` CLI command for generating a clean Markdown robustness leaderboard report from saved fixture sweep artifacts.
- `export-fixture-sweep-leaderboard --output-dir` and `--report-path` support, defaulting to ignored runtime paths under `data/experiments` and `data/reports/fixture_sweep_leaderboard.md`.
- Fixture sweep leaderboard export creates missing report directories and does not write a report when no valid fixture sweep artifacts exist.
- Fixture sweep leaderboard report includes generated timestamp, source artifact directory, current robust champion summary, fixture list, score formula/explanation, safety disclaimer, per-fixture winner table, strategy robustness aggregate table, caveats, most recent sweep artifact path, and skipped/malformed artifact count.
- Malformed fixture sweep artifacts are skipped safely during leaderboard export and reported without crashing.
- `create-sweep-analysis-note` CLI command for generating local Markdown human review templates from the latest valid saved fixture sweep artifact.
- `create-sweep-analysis-note --output-dir`, `--notes-dir`, and `--force` support, defaulting to ignored runtime paths under `data/experiments` and `data/notes`.
- Sweep analysis notes use deterministic Windows-safe filenames derived from the sweep timestamp.
- Sweep analysis notes include generated timestamp, source sweep artifact path, sweep timestamp, fixtures included, overall robust champion, champion metrics, per-fixture winner table, strategy robustness table, score formula/explanation, safety disclaimer, human review prompts, and decision checklist.
- Malformed fixture sweep artifacts are skipped safely during sweep analysis-note generation, and empty or missing artifact directories produce beginner-readable messages without tracebacks.
- `set-strategy-status` CLI command for appending local Markdown research status entries to `data/notes/strategy_status.md`.
- `set-strategy-status --strategy-id`, `--status`, `--reason`, `--source-note`, `--next-action`, and `--registry-path` support.
- Strategy status validation for `active`, `promoted`, `retest`, `modified`, and `retired`.
- `strategy-status` CLI command for printing current latest strategy statuses plus append-only history.
- Strategy status entries include timestamp, strategy ID, status, reason, optional source note path, optional next action, and a safety reminder that the entry is research status only and not live trading approval.
- Retired-strategy filtering for comparison/sweep execution was intentionally left as a TODO to avoid changing tournament behavior in this phase.
- Multi-day simulated portfolio and benchmark snapshots that produce non-zero strategy return, SPY return, excess return, and max drawdown where appropriate.
- Cash-only comparison baseline remains zero-return with no cash yield modeled.
- Beginner-readable comparison output with rank, strategy ID, run ID, score, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
- Alpaca paper client wrapper for account status, positions, market clock, and approved paper-order submission.
- `paper-status` CLI command with safe failure when credentials or paper settings are missing.
- SQLite-backed benchmark and daily report generator.
- Formal run records for dry-run sessions.
- Run-linked portfolio snapshots, benchmark snapshots, trade proposals, risk decisions, orders, and daily reports.
- `report` CLI command for beginner-readable SPY comparison metrics, defaulting to the latest run.
- Explicit run-id reports via `python -m src.main report --run-id <id>`.
- Expanded tests for risk rules, validation, sizing, execution logging, approved quantities, mocked Alpaca paper integration, benchmark reporting, run-isolated reports, deterministic momentum behavior, cash-only behavior, local strategy comparison, deterministic multi-scenario simulation fixtures, comparison artifacts, Hermes fixture strategies, tournament history, tournament champion reporting, leaderboard export, fixture sweep, fixture sweep leaderboard export, analysis notes, fixture sweep analysis notes, research decisions, strategy status registry, and performance.
- Beginner docs.
- Codex prompt workflow.

## Trading safety state

Current allowed mode:

- Dry-run only by default.
- Alpaca paper integration is wrapped and requires `ALPACA_PAPER=true`.
- Alpaca base URL must be exactly `https://paper-api.alpaca.markets`.
- No live trading.
- No options.
- No shorting.
- No margin.
- No LLM direct execution.
- Hermes runtime is not wired into dry-run, paper trading, Alpaca, or any order path.
- Hermes fixture strategies are local parser-only dry-run proposal generators.
- Hermes parser tests require no network, credentials, Ollama, LM Studio, hosted LLM, or real market data.
- Local strategy comparison and saved artifacts are dry-run only and do not call Alpaca, Hermes, external LLMs, market data APIs, or network services.

## Next step

Review Phase 6P strategy status registry, then continue with broader non-live tournament variants or explicit Hermes runtime prompting when ready.

## Project manager rule

ChatGPT acts as:

- project manager
- prompt writer
- architecture reviewer
- risk reviewer

Codex acts as:

- coding worker
- file editor
- test runner
