# Phase 6L - Fixture Scenario Expansion

## Goal

Add more deterministic local market scenarios so tournaments test strategies across multiple regimes, not only the existing default fixture.

Add support for these `compare-strategies --fixture` names:

- `bull_trend`
- `bear_trend`
- `sideways_chop`
- `volatile_reversal`
- `spy_outperformance`
- `momentum_crash`

## Requirements

- Keep existing fixture behavior backward compatible.
- Keep default fixture unchanged unless there is a strong reason.
- All fixtures must be local, deterministic, small, and beginner-readable.
- Each fixture should include deterministic SPY benchmark movement and strategy-symbol movement.
- Fixtures should create meaningful differences in strategy return, SPY return, excess return, and max drawdown where possible.
- `compare-strategies --fixture <new_fixture> --save` must save JSON/CSV/Markdown artifacts with the fixture name.
- Existing commands should continue working with new fixture artifacts:
  - `tournament-history`
  - `tournament-champion`
  - `export-leaderboard`
  - `create-analysis-note`

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

- every new fixture is accepted
- every new fixture is deterministic
- every new fixture includes SPY benchmark movement
- one fixture is bad/challenging for momentum
- one fixture has SPY outperformance
- saved artifact includes selected fixture name
- history/champion/leaderboard/analysis-note work with new fixture artifacts
- no external services or credentials required

## Docs

- Update `README.md` with fixture options.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6L only if tests pass.
- Add this prompt as `docs/codex_prompts/019_phase_6l_fixture_scenario_expansion.md`.
- Do not change `docs/risk_policy.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies --fixture bull_trend
python -m src.main compare-strategies --fixture bear_trend
python -m src.main compare-strategies --fixture sideways_chop
python -m src.main compare-strategies --fixture volatile_reversal
python -m src.main compare-strategies --fixture spy_outperformance
python -m src.main compare-strategies --fixture momentum_crash
python -m src.main compare-strategies --fixture momentum_crash --save
python -m src.main tournament-history
python -m src.main tournament-champion
python -m src.main export-leaderboard
python -m src.main create-analysis-note
```
