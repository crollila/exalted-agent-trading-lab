# Phase 6O - Fixture Sweep Analysis Notes

Continue ExaltedFable Agent Trading Lab.

## Goal

Add a local human-review note workflow specifically for saved fixture sweep artifacts. This is different from `create-analysis-note`, which works on single comparison artifacts.

Add command:

```bash
python -m src.main create-sweep-analysis-note
```

Also support:

```bash
python -m src.main create-sweep-analysis-note --output-dir data/experiments --notes-dir data/notes
python -m src.main create-sweep-analysis-note --force
```

## Required behavior

- Read saved `fixture_sweep_*.json` artifacts from the selected output directory.
- Select the most recent valid fixture sweep artifact by default.
- Generate a Markdown analysis note under the selected notes directory.
- Create the notes directory if missing.
- Print the saved note path.
- Do not overwrite existing note unless `--force` is provided.
- If no valid fixture sweep artifacts exist, print a clear beginner-readable message and no stack trace.
- Skip/report malformed artifacts safely.

## Markdown note

Include:

- Title: `# Fixture Sweep Analysis Note`
- Generated timestamp
- Source sweep artifact path
- Sweep timestamp
- Fixtures included
- Overall robust champion
- Champion wins
- Champion average score
- Champion average excess return
- Champion worst max drawdown
- Per-fixture winner table
- Strategy robustness table
- Score formula/explanation
- Safety disclaimer:
  - local deterministic research
  - not live trading
  - no options
  - no margin
  - no shorting
  - Hermes runtime disabled
- Human review prompts:
  - Which strategy was most robust?
  - Did cash winning indicate strategy weakness?
  - Which strategy failed in hostile regimes?
  - Which fixture exposed the biggest weakness?
  - Is the champion robust enough to promote, or should it be retested?
  - What scenario should be added next?
- Decision checklist:
  - promote
  - modify
  - retest
  - retire
  - no decision yet

## Filename

Use a deterministic readable filename from sweep timestamp, for example:

```text
sweep_analysis_note_20260613T015152211185Z.md
```

Use Windows-safe characters only.

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

- note generation from one valid sweep artifact
- most recent valid sweep artifact selected
- malformed sweep artifacts skipped safely
- no-valid-artifact behavior
- notes directory creation
- no-overwrite behavior
- force overwrite behavior
- note includes source artifact path
- note includes robust champion
- note includes per-fixture winner table
- note includes strategy robustness table
- note includes human review prompts
- note includes decision checklist
- CLI output includes saved note path
- no external services or credentials required

## Docs

- Update `README.md` with command.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6O only if tests pass.
- Add this prompt as `docs/codex_prompts/022_phase_6o_fixture_sweep_analysis_notes.md`.
- Do not change `docs/risk_policy.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main fixture-sweep --save
python -m src.main create-sweep-analysis-note
python -m src.main create-sweep-analysis-note --force
```

Also test against an empty temp directory if practical.
