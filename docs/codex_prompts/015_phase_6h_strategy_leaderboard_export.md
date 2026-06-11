# Phase 6H - Strategy Leaderboard README/Report Export

Continue ExaltedFable Agent Trading Lab.

Task: Phase 6H - Strategy leaderboard README/report export.

Goal:

Add a local command that generates a clean Markdown strategy leaderboard report from saved ranked tournament artifacts. This should make the project easier to review on GitHub/resumes while staying fully local and non-trading.

Add command:

```bash
python -m src.main export-leaderboard
```

Also support:

```bash
python -m src.main export-leaderboard --output-dir data/experiments --report-path data/reports/strategy_leaderboard.md
```

Current source state:

- Phase 6G tournament champion report is complete.
- Current local research loop:

  ```text
  compare-strategies --save -> tournament-history -> tournament-champion
  ```

- Hermes runtime remains disabled.
- Local Hermes fixture strategies are parser-only and hardcoded.
- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys.
- No LLM direct execution.

Required behavior:

- Read saved ranked comparison JSON artifacts from the selected output/artifact directory.
- Reuse or build on tournament-history and tournament-champion logic where reasonable.
- Generate a Markdown report at the selected report path.
- Create the report directory if missing.
- Print the saved report path.
- Do not write anything if there are no valid artifacts; instead print a clear beginner-readable message and no stack trace.

Markdown report should include:

- title, for example `# Strategy Leaderboard`
- generated timestamp
- current champion summary
- score formula
- beginner-readable safety disclaimer:
  - dry-run/local research
  - not live trading
  - no options
  - no margin
  - no shorting
  - Hermes runtime disabled
- recent tournament summary table
- strategy aggregate table with:
  - strategy ID
  - appearances
  - wins
  - win rate
  - best score
  - average score
  - average excess return
  - worst max drawdown
- fixture caveats explaining that deterministic fixtures are not proof of real trading edge
- artifact source directory

Graceful behavior:

- If no artifacts exist, print a clear no-artifacts or no-valid-artifacts message with no stack trace.
- If some artifacts are malformed, skip/report them safely without crashing and include skipped count if useful.
- Runtime report output should live under ignored `data/reports` by default and must not be committed.

Safety:

- Do not start Hermes runtime.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Do not require internet.
- Do not require Alpaca credentials.
- Do not submit paper orders.
- Do not change scoring formula.
- Do not change risk, execution, broker, or Hermes behavior.
- Do not change `docs/risk_policy.md`.
- Do not commit unless explicitly asked.

Tests:

- report generation with one artifact
- report generation with multiple artifacts
- champion summary included
- score formula included
- safety disclaimer included
- strategy aggregate table included
- recent tournament table included
- output directory creation
- no-artifact behavior
- malformed artifact skip behavior
- CLI output includes saved report path
- no external services, credentials, or order submission required

Update:

- `README.md` with command
- `STATUS.md` only if tests pass
- `BUILD_PLAN.md` with Phase 6H only if tests pass
- add this prompt as `docs/codex_prompts/015_phase_6h_strategy_leaderboard_export.md`

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies --fixture multi_day --save
python -m src.main tournament-history
python -m src.main tournament-champion
python -m src.main export-leaderboard
```

Also test `export-leaderboard` against an empty temporary directory if practical.
