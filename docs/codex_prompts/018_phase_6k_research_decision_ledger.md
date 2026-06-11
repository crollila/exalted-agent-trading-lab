# Phase 6K - Research Decision Ledger

## Goal

Add a local, non-trading workflow for recording structured research decisions after reviewing strategy analysis notes. This should turn analysis notes into an explicit decision history for each strategy.

Add command:

```bash
python -m src.main record-research-decision
```

Also support:

```bash
python -m src.main record-research-decision --strategy-id momentum_v1 --decision retest --reason "Won fixture but needs more scenarios"
```

## Required behavior

- Create or append to a local Markdown decision ledger, defaulting to `data/notes/research_decisions.md`.
- Create the notes directory if missing.
- Print the saved ledger path.
- Append one decision entry per command invocation.
- Do not require existing analysis notes.
- Allow optional source note path with `--source-note data/notes/example.md`.
- Include timestamp, strategy ID, decision, reason, optional source note path, and optional next action.
- Validate decision is one of:
  - `promote`
  - `modify`
  - `retest`
  - `retire`
  - `no_decision`
- If required fields are missing, fail gracefully with CLI help or beginner-readable error.
- Keep this local and deterministic.

## Ledger entry requirements

- Decision timestamp
- Strategy ID
- Decision
- Reason
- Source note path, if provided
- Next action, if provided
- Safety reminder:
  - research decision only
  - not live trading approval
  - no broker/order behavior changed

## Read behavior

Add a simple read command:

```bash
python -m src.main research-decisions
```

It should print the existing decision ledger or a clear message if none exists.

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

- creating a new decision ledger
- appending multiple decisions
- validating allowed decision values
- rejecting invalid decision values
- missing required field behavior
- optional source note path included
- optional next action included
- safety reminder included
- list/read behavior
- CLI output includes saved ledger path
- no external services/credentials/order submission required

## Docs

- Update `README.md` with command.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6K only if tests pass.
- Add this prompt as `docs/codex_prompts/018_phase_6k_research_decision_ledger.md`.
- Ensure `data/notes` remains ignored in `.gitignore`.
- Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main record-research-decision --strategy-id momentum_v1 --decision retest --reason "Won fixture but needs more scenarios" --next-action "Run more fixtures"
python -m src.main research-decisions
```
