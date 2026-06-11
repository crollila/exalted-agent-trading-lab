# Phase 6J - Strategy Notes / Post-Run Analysis Templates

## Goal

Add a local, non-trading workflow for structured human review notes after strategy tournaments. The project should help turn ranked tournament output into a repeatable research process.

Add command:

```bash
python -m src.main create-analysis-note
```

Also support:

```bash
python -m src.main create-analysis-note --output-dir data/experiments --notes-dir data/notes
```

## Required behavior

- Read saved ranked comparison JSON artifacts from the selected output/artifact directory.
- Select the most recent valid tournament artifact by default.
- Generate a Markdown analysis note template under the selected notes directory.
- Create the notes directory if missing.
- Print the saved note path.
- Do not overwrite an existing note unless an explicit safe flag is added, such as `--force`.
- If no valid artifacts exist, print a clear beginner-readable message and no stack trace.
- If malformed artifacts exist, skip/report them safely without crashing.

## Markdown note requirements

- Title, for example: `# Strategy Tournament Analysis Note`
- Generated timestamp
- Source artifact path
- Tournament timestamp
- Fixture name
- Winner strategy ID
- Winner score
- Strategy ranking table with rank, strategy ID, score, strategy return, SPY return, excess return, max drawdown, trade count, rejected trade count
- Score formula
- Safety disclaimer:
  - local/dry-run research
  - not live trading
  - no options
  - no margin
  - no shorting
  - Hermes runtime disabled
- Human review prompts:
  - What won?
  - Why did it win?
  - Was the edge real or fixture-specific?
  - What risks showed up?
  - What should be tested next?
  - Should this strategy be promoted, modified, or retired?
- Decision section with checkboxes:
  - promote
  - modify
  - retest
  - retire
  - no decision yet

## Filename requirement

Use a deterministic readable filename based on tournament timestamp/fixture when available, for example:

```text
analysis_note_multi_day_20260611T014633789491Z.md
```

Avoid characters invalid on Windows.

## Do not

- Start Hermes runtime.
- Add LLM calls.
- Add live trading.
- Add options.
- Add margin.
- Add shorting.
- Add real API keys.
- Require internet.
- Require Alpaca credentials.
- Submit paper orders.
- Change scoring formula.
- Change risk/execution/broker/Hermes behavior.
- Change `docs/risk_policy.md`.
- Commit unless explicitly asked.

## Tests

Add tests for:

- note generation from one valid artifact
- most recent valid artifact selected by default
- malformed artifacts skipped safely
- no-valid-artifact behavior
- notes directory creation
- no-overwrite behavior
- optional force overwrite behavior if implemented
- note includes source artifact path
- note includes winner and ranking table
- note includes human review prompts
- note includes decision checkboxes
- CLI output includes saved note path
- no external services/credentials/order submission required

## Docs

- Update `README.md` with command.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6J only if tests pass.
- Add this prompt as `docs/codex_prompts/017_phase_6j_strategy_analysis_notes.md`.
- Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies --fixture multi_day --save
python -m src.main create-analysis-note
```

Also test `create-analysis-note` against an empty temp directory if practical.
