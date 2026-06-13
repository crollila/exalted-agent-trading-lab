# Phase 6Q - Status-Aware Research Reports

Continue ExaltedFable Agent Trading Lab.

## Goal

Make existing local research reports display each strategy's current research status from the strategy status registry, without changing which strategies run yet.

Important:

- Do not filter out retired/retest strategies in this phase.
- Do not change tournament execution behavior.
- Only annotate/report statuses.

## Required behavior

- Read the local strategy status registry if it exists: `data/notes/strategy_status.md`.
- If the registry does not exist, treat strategy status as unknown or active by default, whichever is already more natural in the codebase.
- Add status display to beginner-readable outputs where useful:
  - `fixture-sweep`
  - `tournament-champion`
  - `export-leaderboard`
  - `export-fixture-sweep-leaderboard`
- Also include status in saved/exported Markdown reports where strategy aggregate tables are shown.
- If practical, include status in saved JSON/CSV artifacts from `fixture-sweep --save`; otherwise leave as TODO and document it.
- Keep outputs readable and deterministic.

Suggested display:

- strategy ID
- status
- wins
- average score
- average excess return
- worst drawdown

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
- Filter or exclude strategies based on status.
- Commit unless explicitly asked.

## Tests

Add tests for:

- status registry parsing/reuse
- missing status registry behavior
- fixture-sweep output includes status
- tournament-champion output includes status if applicable
- exported leaderboard includes status
- exported fixture sweep leaderboard includes status
- saved fixture sweep artifact includes status if implemented
- no strategy execution filtering happens because of status
- no external services or credentials required

## Docs

- Update `README.md` with note that reports can show strategy status.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6Q only if tests pass.
- Add this prompt as `docs/codex_prompts/024_phase_6q_status_aware_reports.md`.
- Ensure `data/notes` remains ignored in `.gitignore`.
- Do not change `docs/risk_policy.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main set-strategy-status --strategy-id momentum_v1 --status retest --reason "Needs cross-fixture improvement"
python -m src.main fixture-sweep
python -m src.main tournament-champion
python -m src.main export-leaderboard
python -m src.main export-fixture-sweep-leaderboard
```
