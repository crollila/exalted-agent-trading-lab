# Phase 6N - Fixture Sweep Leaderboard Export

Continue ExaltedFable Agent Trading Lab.

## Goal

Add a local Markdown export command for fixture-sweep robustness results, similar to `export-leaderboard` but focused on cross-regime robustness across deterministic fixtures.

Add command:

```bash
python -m src.main export-fixture-sweep-leaderboard
```

Also support:

```bash
python -m src.main export-fixture-sweep-leaderboard --output-dir data/experiments --report-path data/reports/fixture_sweep_leaderboard.md
```

## Required behavior

- Read saved fixture sweep JSON artifacts from the selected output/artifact directory.
- Generate a Markdown report at the selected report path.
- Create the report directory if missing.
- Print the saved report path.
- Do not write anything if there are no valid fixture sweep artifacts; print a clear beginner-readable message and no stack trace.
- Skip malformed artifacts safely and report skipped count if useful.
- Reuse existing fixture-sweep parsing/aggregation logic where reasonable.

## Markdown report

Include:

- Title, for example `# Fixture Sweep Leaderboard`.
- Generated timestamp.
- Source artifact directory.
- Current robust champion summary.
- Fixture list included in the sweep.
- Score formula/explanation.
- Safety disclaimer:
  - local deterministic research
  - not live trading
  - no options
  - no margin
  - no shorting
  - Hermes runtime disabled
- Per-fixture winner table:
  - fixture
  - winning strategy
  - winning score
- Strategy robustness aggregate table:
  - strategy ID
  - fixture appearances
  - fixture wins
  - win rate
  - average score
  - average excess return
  - worst max drawdown
- Caveats:
  - deterministic fixtures are not proof of real edge
  - cross-fixture robustness is still simulated
  - results should guide research, not trading decisions
- Most recent sweep artifact path.
- Skipped/malformed artifact count if any.

## Do not

- Start Hermes runtime.
- Add LLM calls.
- Add live trading.
- Add options.
- Add margin.
- Add shorting.
- Add real API keys.
- Require internet or Alpaca credentials.
- Submit paper orders.
- Change scoring formula.
- Change risk/execution/broker/Hermes behavior.
- Change `docs/risk_policy.md`.
- Commit unless explicitly asked.

## Tests

Add tests for:

- report generation from one fixture sweep artifact
- report generation from multiple fixture sweep artifacts
- champion summary included
- per-fixture winner table included
- strategy robustness aggregate table included
- score explanation included
- safety disclaimer included
- caveats included
- output directory creation
- no-artifact behavior
- malformed artifact skip behavior
- CLI output includes saved report path
- no external services or credentials required

## Docs

- Update `README.md` with the new command.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6N only if tests pass.
- Add this prompt as `docs/codex_prompts/021_phase_6n_fixture_sweep_leaderboard_export.md`.
- Do not change `docs/risk_policy.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main fixture-sweep --save
python -m src.main export-fixture-sweep-leaderboard
```

Also test export against an empty temp directory if practical.
