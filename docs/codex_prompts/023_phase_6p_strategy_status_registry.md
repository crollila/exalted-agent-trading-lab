# Phase 6P - Strategy Status Registry

Continue ExaltedFable Agent Trading Lab.

## Goal

Add a local research status registry so each strategy can be marked active, promoted, retest, modified, or retired. This prevents the project from endlessly testing strategies that already failed robustness checks, while keeping all behavior local and non-trading.

Add command:

```bash
python -m src.main set-strategy-status
```

Example:

```bash
python -m src.main set-strategy-status --strategy-id momentum_v1 --status retest --reason "Failed cross-fixture robustness sweep"
```

Also add read command:

```bash
python -m src.main strategy-status
```

## Required behavior

- Store status registry as local Markdown by default: `data/notes/strategy_status.md`.
- Create notes directory if missing.
- Append or update strategy status entries safely.
- Print saved registry path after setting status.
- `strategy-status` should print current known statuses or a clear message if none exists.
- Valid statuses:
  - active
  - promoted
  - retest
  - modified
  - retired
- Include:
  - timestamp
  - strategy ID
  - status
  - reason
  - optional source note path
  - optional next action
  - safety reminder that this is research status only and not live trading approval
- If a strategy status is updated multiple times, the read command should show the latest status clearly.
- Preserve history either in the same Markdown file or in a simple append-only format.

## Optional

Only if clean and low-risk:

- Add `--include-retired` or `--exclude-retired` support to `compare-strategies` and/or `fixture-sweep`.
- If this is not simple, skip it and leave as TODO. Do not risk destabilizing comparison logic.

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

- creating new strategy status registry
- setting valid status
- rejecting invalid status
- updating same strategy status and showing latest clearly
- preserving status history
- optional source note path included
- optional next action included
- safety reminder included
- strategy-status no-file behavior
- strategy-status read behavior
- CLI output includes saved registry path
- no external services or credentials required
- if retired filtering is added, test it carefully

## Docs

- Update `README.md` with commands.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6P only if tests pass.
- Add this prompt as `docs/codex_prompts/023_phase_6p_strategy_status_registry.md`.
- Ensure `data/notes` remains ignored in `.gitignore`.
- Do not change `docs/risk_policy.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main set-strategy-status --strategy-id momentum_v1 --status retest --reason "Failed cross-fixture robustness sweep" --next-action "Modify or replace momentum logic"
python -m src.main strategy-status
```
