# STATUS

## Current state

Phase 8 — Alpha vs Beta weekly paper competition with explicit, paper-only advanced
permission levels (shorting, margin, options), a deterministic advanced risk engine,
proposal routing, a kill switch, a team learning loop, allowlisted research tools, and
an LLM provider abstraction (OpenAI/Anthropic/Ollama). LLM-driven proposals
(`run-week-cycle --proposal-source llm`) and paper options execution are wired:
single-leg long calls/puts submit to Alpaca paper via the OCC-symbol options adapter
(multileg spreads off by default), with team-credential-only, no-fake-fill, kill-switch
guarantees intact.

Key safety properties (unchanged in spirit, now broader):

- Paper-only. No live trading. The broker wrapper refuses live endpoints.
- Advanced paper trading (shorting/margin/options) is unlockable but **off by default**.
- LLMs/agents never place trades; the deterministic risk engine computes approved size.
- Chat, Agent Hub, `!ask_team`, `!ask_agent`, and tournament/research commands cannot submit orders.
- The UI never submits orders; it calls the same safe functions and shows masked secrets only.
- A global kill switch is checked immediately before every broker submission.
- `.env` and `data/` are never committed; API keys are never printed, logged, or committed.

New commands: `paper-permissions`, `start-week-competition`, `run-week-cycle --team`,
`week-competition-status`, `stop-week-competition`, `team-learning-status --team`,
`export-team-scorecards`, `kill-switch-on|off|status`. Discord: `!start_week_competition`,
`!run_week_cycle`, `!week_competition_status`, `!stop_week_competition`, `!kill_switch`.

Proposal attribution outcome refresh (`refresh-proposal-attribution [--team] [--threshold]`):
re-reads pending proposal/trade outcomes against the latest paper prices + the SPY benchmark
(via the safe market-data wrapper, team credentials only — global keys are not required) and
persists `current_price`, `unrealized_pnl`, `return_pct`, `spy_start_price`,
`spy_current_price`, `spy_return_pct`, `excess_return_pct`, `outcome_status`
(`pending`/`worked`/`failed`/`mixed`), and `refreshed_at`. The verdict is SPY-relative around a
small configurable threshold (default 0.5%); missing entry/price, an unavailable SPY benchmark,
or options leave the row `pending` with a printed skip reason. The JSONL schema is
backward-compatible (old rows load; new fields default) and is rewritten atomically. A compact
"recent outcome feedback" block feeds the next LLM cycle as **research feedback only** — it
never authorizes bypassing risk, sizing, credentials, or the kill switch. `proposal-attribution`
and `week-competition-status` surface the refreshed outcomes. Refresh reads prices only; it
submits no orders and prints no secrets.

Phase 7M — Portfolio Manager / Capital Allocator. Before proposing trades, each team runs a
deterministic Portfolio Manager review of the current book, buying power, prior theses, attribution
outcomes, and SPY-relative performance, then decides to hold, trim, close, rotate, add, hedge,
reduce exposure, request margin, or do nothing. Behavior:

- No-trade / hold is a first-class successful outcome (still records scorecard, memory, attribution;
  the CLI prints "No trade decision" with rationale).
- Low buying power triggers a review instead of hard-stopping the cycle; new-money buys are blocked
  (demoted to advisory `simulation_only`) unless the team frees room (trim/close/rotate) or makes an
  explicit margin request.
- Dynamic proposal cap (`max_new_proposals_this_cycle`, 0–3): `team_alpha` is higher-variance
  (exploration, slightly higher cap, more willing to rotate); `team_beta` is conservative
  (conservation, lower cap, more hold/trim). Both stay within the platform hard cap. Config:
  `PORTFOLIO_MANAGER_ENABLED` (true), `MAX_NEW_PROPOSALS_ALPHA` (3), `MAX_NEW_PROPOSALS_BETA` (2),
  `LOW_BUYING_POWER_REVIEW_THRESHOLD_PCT` (0.15), `ALLOW_NO_TRADE_DECISIONS` (true),
  `CHEAP_CYCLE_GATE_ENABLED` (false).
- An LLM `portfolio_decision` is advisory only: it may narrow behavior but can never widen the cap,
  unblock low-BP buys, or bypass hard risk caps. Prompts now require a compact self-review.
- Broker submission failures are recorded distinctly (`broker_rejected`, `broker_reject_reason`,
  `broker_reject_code`, `failure_category`: insufficient_buying_power / wash_trade / broker_error /
  unknown) and flow into attribution + the next cycle's Portfolio Manager context. Each team keeps a
  compact strategy-memory note (mode = exploration/conservation, what to avoid next cycle).

Phase 7N — Strategy Debate, Daily SPY Attribution, and Cheap Cycle Gate. Teams behave more like
investment teams that review outcomes and only spend LLM/API calls when useful:

- `cheap-cycle-gate --team <team>` decides (no LLM, local data only) whether a full
  `run-week-cycle` is worth running. It returns `should_run_full_cycle`, `reason`,
  `recommended_wait_minutes`, `recommend_review_only`, and `trigger_flags`, using the last full-cycle
  time, scorecard, attribution, buying power, and broker rejections. Config: `CHEAP_CYCLE_GATE_ENABLED`
  (false), `MIN_FULL_CYCLE_INTERVAL_MINUTES_ALPHA` (30), `MIN_FULL_CYCLE_INTERVAL_MINUTES_BETA` (45),
  `FORCE_FULL_CYCLE_ON_MAJOR_MOVE` (true), `MAJOR_SPY_MOVE_THRESHOLD_PCT` (0.5),
  `FORCE_FULL_CYCLE_ON_LOW_BUYING_POWER` (false). Alpha has a shorter interval (more exploratory);
  low buying power recommends a review, never forces new orders.
- `run-week-cycle --review-only` runs the portfolio/strategy review and updates memory/scorecard but
  submits NO new broker orders (advisory hold/trim/close only); it never builds a broker client. It
  does not reset the full-cycle timer.
- `daily-spy-attribution [--team]` explains why each team beat or lost to SPY (team/SPY/excess return,
  begin/end equity, long/short contribution estimates, top winners/losers, submitted vs broker-rejected,
  no-trade cycles, best-effort symbol/sector buckets, and a concise driver explanation: stock selection /
  sector / short exposure / missed beta / leverage / broker rejections / too much cash / bad timing).
- `export-daily-team-review [--team]` writes a compact strategy-debate artifact (the standard
  self-review questions + recommended exploration/conservation mode) under the ignored path
  `data/reviews/`. A compact version feeds the next LLM cycle's context as research feedback only.

Cost control: the cheap gate can recommend skipping full cycles; review-only updates learning without
trading; the daily review reuses local data only (no new external web/search calls; Alpaca news remains
the only live research provider; OpenAI web search stays off).

Phase 7O — LLM Model Routing and Cost-Saving Automation. Stronger models run only the high-value
strategy/proposal path; cheaper models are configured for review/critique/summary/research-synthesis.

- `src/agents/model_routing.py` resolves a model per task via `LLM_MODEL_<TASK>` → `LLM_MODEL` →
  `OPENAI_MODEL` → built-in default, for tasks: strategy, portfolio_manager, review, critique, summary,
  research_synthesis, default. `build_routed_provider(task)` builds a provider whose model is the routed
  one. The only live LLM call path today — run-week-cycle proposal generation — uses the `strategy`
  model; deterministic paths (PM decision, critique, daily review, research synthesis, summaries) stay
  deterministic and are not forced onto an LLM. `LLM_PROVIDER` is accepted as an alias for
  `EXALTED_LLM_PROVIDER`.
- `llm-routing-status` prints provider + per-task model names and `API key configured: true/false`
  (never key contents).
- `run-cheap-competition-loop` is a one-command all-day runner: each iteration refreshes attribution,
  prints cheap status, runs the cheap gate per team, and runs a full `run-week-cycle` **only** when the
  gate says so (optionally a review-only cycle when skipped). Args: `--once`, `--sleep-seconds` (900),
  `--team team_alpha|team_beta|both`, `--market-hours-only`/`--no-market-hours-only`,
  `--run-review-only-when-skipped`, `--dry-run-loop`. It never bypasses the kill switch, never submits
  unless `run-week-cycle` is actually invoked, and never prints secrets.

Self-improvement here means runtime memory, scorecards, and prompt feedback — **not**
model-weight training. Paper trading does not prove live profitability.

### Prior state

Phase 7G natural Discord team chat and autonomous paper-cycle scaffolding completed.

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
- Hermes multi-agent strategy sandbox router was added for strict local JSON review.
- `src/agents/hermes_strategy_sandbox.py` defines `HermesSandboxRequest`, `RoutedHermesProposal`, and `HermesSandboxResult`.
- Sandbox requests require `agent_id`, `team_id`, `strategy_id`, `agent_role`, and a non-empty `proposals` list, with optional `strategy_notes` and `learning_goal`.
- Sandbox proposal routing maps `stock_long` to existing `TradeProposal` objects with route `paper_eligible_stock_long`.
- Sandbox proposal routing maps `short_stock` to existing inert `ShortProposal` objects with route `simulation_only_short`.
- Sandbox proposal routing maps `option_long` to existing inert `OptionProposal` objects with route `simulation_only_option`.
- Sandbox proposal routing maps strict margin placeholders to `simulation_only_margin`.
- Invalid JSON, missing required request fields, empty proposals, unknown proposal types, malformed stock/short/option proposals, and extra fields are rejected.
- `review-hermes-sandbox --file` reads a local JSON file only and prints team ID, agent ID, strategy ID, route summary counts, proposal routes, and the warning that Hermes proposals are not execution approval.
- Example local sandbox payload was added at `docs/examples/hermes_strategy_sandbox_example.json`.
- Phase 7A does not call Hermes, LLM APIs, Alpaca, brokers, market data, or network services.
- Phase 7A does not submit or write orders, change portfolio state, enable advanced execution, weaken risk policy, or allow broker/order/risk bypasses.
- Hermes agent team registry was added for strict local team/agent identity review.
- `src/agents/hermes_team_registry.py` defines `HermesAgentProfile`, `HermesTeamProfile`, `HermesTeamRegistry`, and `HermesAgentRole`.
- Allowed agent roles are `research_agent`, `risk_agent`, `execution_agent`, `review_agent`, `strategy_mutator`, and `portfolio_manager`.
- Registry validation rejects missing team IDs, missing agent IDs, duplicate team IDs, duplicate agent IDs across teams, invalid roles, empty team agent lists, mismatched agent/team IDs, and extra unknown fields.
- Agent profiles track team ID, agent name, role, description, active status, optional model hint, strengths, weaknesses, latest strategy ID, and learning notes.
- Team profiles track team name, description, agents, active status, optional strategy family, and learning notes.
- Example local registry was added at `docs/examples/hermes_team_registry_example.json` with `team_alpha`, `team_beta`, distinct roles, active/inactive agents, learning notes, and no secrets.
- `hermes-teams --file` reads a local JSON file only and prints teams, agents, active/inactive status, roles, and `registry only; no trading or LLM calls`.
- Phase 7B does not call Hermes, LLM APIs, Alpaca, brokers, market data, or network services.
- Phase 7B does not submit or write orders, change portfolio state, enable advanced execution, weaken risk policy, or allow broker/order/risk bypasses.
- Hermes tournament round runner was added for local-only team proposal route scoring.
- `src/agents/hermes_tournament_round.py` loads a Hermes team registry plus one or more Hermes sandbox proposal JSON files.
- `hermes-tournament-round --registry --proposal` runs local routing-score tournaments and supports repeatable or comma-separated proposal paths.
- Tournament rows include team ID, agent ID, strategy ID, total proposals, route counts for paper-eligible stock longs and simulation-only short/option/margin ideas, rejected count, score, and warnings.
- Tournament scoring uses `score = paper_eligible_count * 2 + simulation_only_count * 1 - rejected_count * 1`.
- Team rankings sort by score descending, fewer rejected proposals, then team ID alphabetical.
- Malformed proposal files and unknown proposal team IDs are handled as safe warning/rejection rows without traceback.
- `hermes-tournament-round --save` writes local JSON and Markdown artifacts under `data/experiments` by default.
- The CLI prints a winner, rankings, and the disclaimer that routing score is not profitability.
- A second local proposal example was added at `docs/examples/hermes_strategy_sandbox_team_beta_example.json`.
- Phase 7C does not call Hermes, LLM APIs, Alpaca, brokers, market data, or network services.
- Phase 7C does not submit or write orders, change portfolio state, enable advanced execution, score profitability, weaken risk policy, or allow broker/order/risk bypasses.
- Hermes runtime adapter was added for opt-in proposal JSON generation through a local/OpenAI-compatible chat endpoint.
- `src/agents/hermes_runtime.py` defines `HermesRuntimeConfig`, `HermesGenerationRequest`, and `HermesGenerationResult`.
- Runtime configuration uses only `HERMES_ENABLED`, `HERMES_BASE_URL`, `HERMES_MODEL`, optional `HERMES_API_KEY`, and optional `HERMES_TIMEOUT_SECONDS`.
- Runtime refuses unless `HERMES_ENABLED=true`, and fails clearly when base URL or model is missing.
- Runtime calls only generic OpenAI-compatible `/chat/completions`, saves the returned raw JSON locally, then validates the saved file through the existing Hermes sandbox router.
- The generation prompt requires strict JSON matching the sandbox schema and bans secrets, execution claims, broker credentials, order placement, live trading, Markdown, and prose outside JSON.
- `hermes-generate-proposals` CLI creates the output directory, saves raw generated proposal JSON, prints the sandbox route summary, and treats Hermes output as proposal JSON only.
- `data/agent_runs/` is ignored for local generated proposal files.
- Runtime tests mock HTTP completely and require no real Hermes endpoint, real LLM, network, credentials, Alpaca, or broker access.
- Phase 7D does not call Alpaca, submit orders, write orders, change portfolio state, enable live trading, grant Hermes broker access, or allow broker/order/risk bypasses.
- Local Discord command-center bot was added for safe lab summaries.
- `src/discord_bot/bot.py` defines environment parsing, channel allowlist handling, Discord-friendly summary builders, prefix commands, and slash command registration.
- `discord-bot` CLI refuses clearly when `DISCORD_BOT_TOKEN` is missing.
- Discord configuration supports optional `DISCORD_GUILD_ID`, optional comma-separated `DISCORD_ALLOWED_CHANNEL_IDS`, and local default registry/proposal paths.
- Bot commands support status, teams, proposal review, tournament routing summaries, and `ask_team` proposal generation using existing Hermes registry, runtime, sandbox, and tournament logic.
- `ask_team` requires the existing Hermes runtime configuration, saves generated proposal JSON under ignored `data/agent_runs/`, validates the saved file through the sandbox router, and returns route counts to Discord.
- `docs/discord_bot_setup.md` documents beginner setup and run steps without real tokens or IDs.
- Phase 7E does not call Alpaca, submit orders, write orders, change portfolio state, enable live trading, grant Discord broker access, or allow broker/order/risk bypasses.
- Default Team Alpha/Team Beta registry now has exactly two active teams and three active agents per team.
- Team Alpha agents are `alpha_research_01`, `alpha_risk_01`, and `alpha_review_01`.
- Team Beta agents are `beta_research_01`, `beta_risk_01`, and `beta_review_01`.
- Team-specific Alpaca paper config reads `TEAM_ALPHA_*` and `TEAM_BETA_*` env vars, enforces paper mode and the exact Alpaca paper base URL, and never prints secrets.
- Hermes sandbox routing supports Phase 7F proposal types: `stock_long`, `stock_short`, `stock_margin_long`, `stock_margin_short`, `option_long_call`, `option_long_put`, `covered_call`, and `cash_secured_put`.
- Options proposals are accepted for review/tournament routing only; paper options execution is not enabled yet.
- Discord now has team paper status, team positions, role-aware ask-agent, latest-agent-run, run-tournament-latest, explicit paper-trade-team, and team-report helpers.
- `paper_trade_team` is the only explicit Discord command path that can submit paper orders. It requires risk/review approval notes, logs proposals, risk decisions, paper order attempts, and portfolio snapshots, and submits only approved stock-long paper orders.
- Phase 7F does not add paper short execution, paper margin execution, paper options execution, live trading, Alpaca calls from Hermes, or broker/order/risk bypasses.
- Natural Discord team chat can be configured with `DISCORD_TEAM_ALPHA_CHANNEL_ID` and `DISCORD_TEAM_BETA_CHANNEL_ID`.
- Normal non-command messages in configured team channels are routed to that team's active research, risk, and review agents, with responses saved under ignored runtime notes.
- Team autonomy is explicitly opt-in with `TEAM_ALPHA_AUTONOMY_ENABLED=true` or `TEAM_BETA_AUTONOMY_ENABLED=true`; defaults remain disabled.
- Team autonomy config includes `paper_stocks_only` mode, max paper orders per day, max daily notional, and risk/review approval requirements.
- Discord now has autonomy status, enable-autonomy, disable-autonomy, scheduled report status, daily team report, and `run_team_cycle` helpers.
- `run_team_cycle` generates research proposal JSON, asks the risk agent for `RISK_AGENT_APPROVED: true`, asks the review agent for `REVIEW_AGENT_APPROVED: true`, and stops with no paper orders unless team autonomy and both approval tokens are present.
- When all autonomous gates are present, `run_team_cycle` reuses the gated `paper_trade_team` execution path, so daily order/notional caps, deterministic Python risk validation, and the Alpaca paper-only wrapper remain the final hard gates.
- Natural chat, `ask_team`, `ask_agent`, `run_tournament`, and scheduled updates do not submit orders.
- Hermes sandbox validation now rejects expired option dates, missing theses, covered-call/cash-secured-put side inconsistencies, and stale option expirations.
- SPY stock-long proposals remain allowed but are warned as benchmark-like when the learning goal is to beat SPY.
- Phase 7G does not add live trading, paper short execution, paper margin execution, paper options execution, Alpaca calls from Hermes prompts, or broker/order/risk bypasses.
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
- Expanded tests for opt-in status-aware comparison and fixture sweep filtering, unknown status behavior, filter output, saved filter metadata, and Discord bot local command summaries.
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
- Phase 7A adds local-only Hermes sandbox review only; Hermes can propose advanced ideas, but the review command cannot place orders directly, call Alpaca, call LLMs, call brokers, enable live trading, or bypass broker/order/risk controls.
- Phase 7B adds local-only Hermes team registry review only; agent identities, roles, and learning notes do not grant broker, order, LLM, Alpaca, or execution authority.
- Phase 7C adds local-only Hermes tournament round route scoring only; tournament winners are based on proposal routing counts, not profitability or trading approval.
- Phase 7D adds opt-in Hermes proposal generation only; generated output is local sandbox JSON and must still pass local review/routing before any research use.
- Phase 7E adds a local Discord command center only; Discord commands summarize local lab state and do not call Alpaca, submit orders, approve execution, or change portfolio state.
- Phase 7F adds team paper credential validation, expanded proposal routing, and explicit stock-long paper execution only; advanced short/margin/options paper execution remains disabled until deterministic risk gates and mocked broker support are implemented and tested.
- Phase 7G adds natural Discord team chat and autonomous paper-cycle scaffolding only; normal chat cannot trade, and autonomous paper cycles require explicit team autonomy, paper-stocks-only mode, research proposal JSON, risk and review approval tokens, daily cap checks, deterministic Python risk approval, and the Alpaca paper-only wrapper.

## Next step

Run the Discord team-chat loop in the real server, observe Team Alpha and Team Beta paper-cycle behavior, and only then design deterministic paper short/margin/options risk gates before allowing any advanced paper order path.

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
