# Local Dashboard Setup (Phase 7H)

A local-only browser dashboard for monitoring and controlling the ExaltedFable Agent
Trading Lab. It is an **operator console for your own machine** — not a public web app, and
**not live trading**.

## Install

Streamlit is listed in `requirements.txt`. Install dependencies (or just Streamlit):

```bash
pip install -r requirements.txt
# or, minimally:
pip install streamlit
```

## Run

Either command works:

```bash
python -m src.main dashboard
# or
streamlit run src/ui/dashboard.py
```

Streamlit serves the dashboard locally (default http://localhost:8501) and opens your
browser. Stop it with `Ctrl+C` in the terminal.

## Local-only usage

- The dashboard is meant to run on your own machine against your local `.env` and local
  `data/` runtime files.
- Do **not** expose it to the public internet. It is an operator tool, not a hosted app.

## No live trading

- The dashboard is paper-only. It cannot place live trades.
- It does **not** call Alpaca order submission directly. Running a cycle from the UI goes
  through the exact same `build_team_paper_cycle_summary` path the Discord bot uses.
- Short, margin, and options remain non-executing. Natural chat and `!ask_team`,
  `!ask_agent`, `!run_tournament` never trade.

## `.env` is required locally but never committed

- The app reads configuration from your local `.env` (Discord, Alpaca, Hermes/Ollama,
  per-team autonomy caps). `.env` is git-ignored and must never be committed.
- Runtime files under `data/` (proposals, notes, autonomy config, database) are also
  git-ignored and never committed.
- The dashboard **never displays secrets**: API keys, Discord tokens, and Alpaca secrets
  are masked, and any file shown in a viewer is passed through a secret-redaction pass
  before rendering.

## The dashboard does not bypass risk gates

A paper order can only be submitted when **all** existing gates pass, unchanged by the UI:

1. autonomy enabled
2. risk agent approval token (`RISK_AGENT_APPROVED: true`)
3. review agent approval token (`REVIEW_AGENT_APPROVED: true`)
4. deterministic Python risk approval
5. daily caps (max paper orders/day, max daily notional)
6. Alpaca paper-only wrapper

As an extra UI speed bump, if a team's autonomy is **enabled**, the "Run cycle" button is
blocked until you tick:

> "I understand this may attempt Alpaca paper orders if all existing gates pass."

This checkbox is only an additional confirmation; it never weakens the gates above.

## Recommended first-test caps

Before any market-hours paper testing, start conservative. In your local `.env`:

```dotenv
TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY=1
TEAM_ALPHA_MAX_DAILY_NOTIONAL=250000
TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY=0
TEAM_BETA_MAX_DAILY_NOTIONAL=0
```

This lets Team Alpha attempt at most one small paper order per day while Team Beta stays
fully blocked for the first test.

## What the dashboard shows

- Title banner and safety notice (paper-only, no live trading, gates required).
- Two team cards (`team_alpha`, `team_beta`): autonomy state, mode, caps, natural-chat
  channel, Alpaca paper equity/cash/buying power and market open/closed (if credentials are
  configured), positions, latest proposal/risk/review paths, the execution-eligible /
  simulation-only / rejected split, parsed risk and review approvals, stock_long
  eligibility, and paper order submission status.
- Controls: Refresh, Enable/Disable autonomy per team, a "Disable all autonomy" kill
  switch, and a Run-cycle form with a default conservative single-stock prompt.
- Read-only viewers for the latest proposal JSON, risk note, review note, latest daily team
  report, and recent runtime files under `data/agent_runs` and `data/notes/paper_cycles`.
- A simple team status table and a placeholder equity chart section ("equity chart pending
  Phase 7H.1").
