# Phase 6I - README Project Showcase Polish

## Goal

Improve the top-level README so the GitHub repo is immediately understandable to recruiters, technical reviewers, and future contributors.

This is documentation-only polish. Do not change code behavior.

## Source of truth

Use these files as project truth:

- `PROJECT_RULES.md`
- `STATUS.md`
- `BUILD_PLAN.md`
- `README.md`
- `docs/risk_policy.md`
- `docs/hermes_setup.md`
- `docs/codex_workflow.md`
- `docs/codex_prompts/`

Current safety state:

- Hermes runtime remains disabled.
- Local Hermes fixture strategies are parser-only and hardcoded.
- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys.
- No LLM direct execution.

## Required README improvements

- Add a concise project summary near the top.
- Add a "What this project demonstrates" section suitable for portfolio/recruiting review.
- Add or improve an architecture section showing:

```text
Strategy / Hermes fixture -> TradeProposal -> Risk Engine -> Dry-run/Paper Execution -> SQLite Logs -> Reports
```

- Clearly explain that this is a research lab, not a live-money trading bot.
- Add a safety disclaimer:
  - dry-run by default
  - Alpaca paper only behind wrapper
  - no live trading
  - no options
  - no margin
  - no shorting
  - no LLM direct execution
  - no real keys in source
- Add a "Current capabilities" section covering:
  - deterministic risk engine
  - Alpaca paper account/status wrapper
  - benchmark reports against SPY
  - run-aware reporting
  - deterministic strategy comparison
  - multi-day fixtures
  - Hermes parser-only fixtures
  - tournament scoring/ranking
  - tournament history
  - tournament champion report
  - leaderboard Markdown export
- Add a beginner-friendly command workflow:
  - setup/install
  - run tests
  - dry-run
  - compare strategies
  - save comparison artifacts
  - review tournament history
  - view champion
  - export leaderboard
- Add a short "Portfolio note" or "Why this matters" section explaining the engineering skills shown:
  - Python
  - SQLite
  - testing
  - CLI design
  - deterministic risk controls
  - paper-trading safety
  - reporting/analytics
  - AI-agent safety boundaries

## Constraints

- Keep claims accurate and do not imply proven trading edge or live profitability.
- Keep generated artifacts under `data/` described as ignored runtime outputs, not committed files.
- Keep README readable and not overly long.
- Do not change source code behavior.
- Do not change tests unless strictly needed for docs checks.
- Do not change `docs/risk_policy.md`.
- Do not change trading permissions.
- Do not start Hermes runtime.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Do not commit unless explicitly asked.

## Optional docs

- Update `STATUS.md` with a brief note that Phase 6I README polish is complete.
- Update `BUILD_PLAN.md` to record Phase 6I under Phase 6 as documentation polish.
- Add this prompt as `docs/codex_prompts/016_phase_6i_readme_showcase_polish.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main --help
```

Also run lightweight CLI help commands as useful to confirm README examples still match the CLI.
