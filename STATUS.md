# STATUS

## Current state

Phase 6X options dry-run simulator foundation completed.

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
- Status-aware report annotations read the latest local strategy statuses from `data/notes/strategy_status.md` when present.
- Missing strategy statuses display as `unknown`.
- `fixture-sweep` terminal output now includes strategy status in the robustness table.
- `fixture-sweep --save` JSON, CSV, and Markdown artifacts include strategy status annotations.
- `tournament-champion` terminal output includes the current champion strategy status.
- `export-leaderboard` Markdown reports include champion status and strategy status in aggregate tables.
- `export-fixture-sweep-leaderboard` Markdown reports include robust champion status and strategy status in aggregate tables.
- Strategy status annotations alone do not filter, exclude, or change which strategies run.
- Opt-in status-aware filtering controls for local research selection.
- `compare-strategies --exclude-retired` excludes only strategies whose latest local research status is exactly `retired`.
- `fixture-sweep --exclude-retired` excludes only strategies whose latest local research status is exactly `retired`.
- `compare-strategies --status active,promoted,retest` includes only strategies whose latest status is in the requested comma-separated list.
- `fixture-sweep --status active,promoted,retest` includes only strategies whose latest status is in the requested comma-separated list.
- `unknown` is supported as an explicit `--status` filter value for strategies with no local status entry or when the status registry is missing.
- Missing status registries do not crash status filtering.
- Default comparison and fixture sweep behavior remains unchanged, including retired strategies unless filtering is explicitly requested.
- Status filtering applies only to local research comparison and fixture sweep strategy selection.
- Status filtering prints beginner-readable explanations with included statuses and excluded strategy IDs plus latest statuses.
- Status filters that match no selected strategies print a clear skip message instead of running a tournament.
- Saved comparison artifacts include status-filter metadata when saved through the CLI.
- Saved fixture sweep artifacts include status-filter metadata when saved through the CLI.
- Advanced permissions architecture plan for future shorting, margin, and options work.
- `docs/advanced_permissions_plan.md` documents a future staged permission roadmap without enabling any new behavior.
- Future stages are documented for paper shorting design, paper shorting dry-run simulation, paper margin design, paper margin dry-run simulation, paper options design, paper options dry-run simulation, broker-paper implementation only after simulator/risk tests, and live trading remaining out of scope until long-term validation.
- Future advanced permission gates are documented for proposal modeling, logging, deterministic risk rejection, simulation-only fixtures, paper-only wrappers, shadow-live observation, and later explicit live review.
- Future shorting plan covers explicit strategy and CLI/user permission flags, max short exposure, max gross exposure, max net exposure, max loss per short position, forced-cover rules, borrow availability assumption logging, hard permission bans, and no live shorting.
- Future margin plan covers explicit permission levels, max gross exposure, max net exposure, max daily loss, margin call simulation, forced deleveraging, no live margin, and no silent margin implied by buying power.
- Future options plan covers explicit contract models, underlying symbol, call/put, expiration, strike, contracts, premium, max premium at risk, max contracts, Greeks when available, liquidity/open-interest assumptions, assignment/exercise risk notes, no 0DTE at first, no naked short options at first, and no live options.
- Advanced permission reporting requirements are documented so future artifacts can identify stock-only, short-enabled, margin-enabled, options-enabled, simulation, paper, or shadow-live conditions.
- Advanced permission testing requirements are documented, including fail-closed defaults and no external service or credential requirements.
- Phase 6S is documentation-only and does not add proposal fields, risk behavior, execution behavior, broker behavior, Alpaca advanced calls, Hermes runtime wiring, or advanced trading paths.
- Future Codex prompt `docs/codex_prompts/phase_6t_shorting_design_models.md` was added for shorting proposal/risk model design without enabling execution.
- Future-facing shorting model definitions were added without wiring them into execution.
- `ShortProposal`, `ShortRiskLimits`, and `ShortRiskDecision` define inert model shapes for future paper-shorting research.
- Shorting model validation rejects non-stock asset classes, options, empty symbols, empty theses, out-of-range confidence, zero or negative prices, zero/negative/excessive short exposure, missing borrow availability assumptions, invalid short actions, and extra fields.
- Shorting model fields include strategy ID, symbol, stock asset class, short action, target short weight, notional exposure, estimated price, thesis, confidence, borrow availability assumption, optional borrow fee assumption, optional max-loss exit price, and optional forced-cover threshold.
- Tests prove current `TradeProposal` behavior is unchanged.
- Tests prove the current executable risk flow still rejects shorting attempts.
- Tests prove dry-run comparison and fixture sweep behavior is unchanged.
- Phase 6T does not enable shorting, margin, options, broker shorting calls, execution changes, risk engine behavior changes, live trading, or Hermes runtime wiring.
- Local-only shorting simulator foundation was added for future research simulation.
- `src/simulation/shorting_simulator.py` simulates inert `ShortProposal` objects against deterministic local price inputs only.
- Short simulation result models report opening short notional, cover price, unrealized P/L, realized P/L, optional borrow fee estimate, forced-cover detection, gross exposure, net exposure, short exposure, and simulation-only risk events.
- Simulator tests cover profitable falling-price shorts, losing rising-price shorts, forced-cover triggers, borrow fee impact, deterministic exposure calculations, invalid `ShortProposal` rejection, and local-only inputs without Alpaca credentials.
- Tests prove `compare-strategies` and `fixture-sweep` behavior remains unchanged.
- Tests prove the executable risk engine still rejects shorting.
- Phase 6U does not add a CLI command and does not write runtime artifacts.
- Phase 6U does not enable executable shorting, options, margin, broker calls, Alpaca shorting calls, order execution changes, risk engine behavior changes, live trading, or Hermes runtime wiring.
- Local-only shorting simulation report export was added for deterministic review of the isolated shorting simulator.
- `src/reporting/shorting_simulation_report.py` builds a Markdown report from one hardcoded local `ShortProposal` fixture and deterministic local prices.
- `export-short-simulation-report` CLI command writes to ignored runtime path `data/reports/shorting_simulation_report.md` by default and supports `--report-path`.
- The short simulation report includes generated timestamp, simulation-only disclaimer, proposal symbol/action/target short weight, entry price, cover price, gross exposure, net exposure, short exposure, gross P/L, realized/unrealized P/L, borrow fee estimate, forced-cover status, risk event status, and a statement that executable shorting remains disabled.
- Short simulation report export creates missing report directories, prints `simulation only`, and does not require credentials.
- Tests prove the report includes the simulation-only disclaimer, key metrics, forced-cover/risk event status, output directory creation, and CLI operation without credentials.
- Phase 6V does not change compare-strategies behavior, fixture-sweep behavior, broker/order execution behavior, existing risk-engine permissions, dry-run execution, Alpaca behavior, strategy wiring, or Hermes runtime wiring.
- Future-facing options model definitions were added without wiring them into execution.
- `OptionContract`, `OptionProposal`, `OptionRiskLimits`, and `OptionRiskDecision` define inert model shapes for future paper-options research.
- Options model validation rejects missing underlying symbols, invalid option types, sell-to-open or other naked-short option actions, 0DTE or past expiration, invalid strike/contracts/premium, missing thesis, missing liquidity/open-interest assumptions, missing assignment/exercise risk notes, invalid confidence, and extra fields.
- Options model fields include strategy ID, underlying symbol, call/put type, buy-to-open or buy-to-close action, expiration, strike, contract count, premium, estimated total premium, thesis, confidence, liquidity/open-interest assumption, assignment/exercise risk note, optional open interest, and optional Greeks.
- Option risk-limit defaults keep options permission disabled, no 0DTE enabled, naked short options disabled, live options disabled, and broker option execution disabled.
- Tests prove excessive option contracts and premium are rejected by the inert option risk checker.
- Tests prove current `TradeProposal` behavior is unchanged.
- Tests prove the current executable risk flow still rejects options.
- Tests prove dry-run comparison and fixture sweep behavior is unchanged.
- Phase 6W does not change compare-strategies behavior, fixture-sweep behavior, broker/order execution behavior, existing risk-engine permissions, dry-run execution, Alpaca behavior, strategy wiring, executable shorting, margin, live trading, or Hermes runtime wiring.
- Local-only options simulator foundation was added for future research simulation.
- `src/simulation/options_simulator.py` simulates inert `OptionProposal` objects against deterministic local premium inputs only.
- Option simulation result models report entry premium, exit premium, contracts, contract multiplier, premium paid, exit value, realized P/L, max premium at risk, return on premium, optional intrinsic value at expiration, optional expiration outcome, and simulation-only premium-at-risk risk events.
- Simulator tests cover profitable long calls when premium rises, losing long calls when premium falls, profitable long puts when premium rises, deterministic premium-at-risk calculations, contract multiplier handling, return-on-premium calculations, premium-at-risk limit events, invalid `OptionProposal` rejection, and local-only inputs without Alpaca credentials.
- Tests prove `compare-strategies`, `fixture-sweep`, and `export-short-simulation-report` behavior remains unchanged.
- Tests prove the executable risk engine still rejects options.
- Phase 6X does not add a CLI command and does not write runtime artifacts.
- Phase 6X does not enable options execution, executable shorting, margin, broker calls, Alpaca options calls, order execution changes, risk engine behavior changes, live trading, strategy options integration, or Hermes runtime wiring.
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
- Expanded tests for risk rules, validation, sizing, execution logging, approved quantities, mocked Alpaca paper integration, benchmark reporting, run-isolated reports, deterministic momentum behavior, cash-only behavior, local strategy comparison, deterministic multi-scenario simulation fixtures, comparison artifacts, Hermes fixture strategies, tournament history, tournament champion reporting, leaderboard export, fixture sweep, fixture sweep leaderboard export, status-aware reports, analysis notes, fixture sweep analysis notes, research decisions, strategy status registry, and performance.
- Expanded tests for opt-in status-aware comparison and fixture sweep filtering, unknown status behavior, filter output, and saved filter metadata.
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
- Phase 6S is only a future architecture plan; it does not change current trading permissions or risk limits.
- Phase 6T adds inert shorting design models only; it does not change current trading permissions or risk limits.
- Phase 6U adds local-only shorting simulation foundations only; it does not change current trading permissions or risk limits.
- Phase 6V adds local-only shorting simulation report export only; it does not change current trading permissions or risk limits.
- Phase 6W adds inert options design models only; it does not change current trading permissions or risk limits.
- Phase 6X adds local-only options simulation foundations only; it does not change current trading permissions or risk limits.

## Next step

Review Phase 6X options dry-run simulator foundation, then consider later permission-gated report/export work only after explicit approval.

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
