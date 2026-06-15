# Local Dashboard Setup (Phase 7J)

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
python -m src.main app
python -m src.main dashboard
# or
streamlit run src/ui/dashboard.py
```

`python -m src.main app` starts Streamlit on `127.0.0.1` and opens a desktop window when
`pywebview` is installed. If the wrapper is missing, it opens your browser and explains
`pip install pywebview`. Stop it with `Ctrl+C` in the terminal.

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
- A simple team status table and a placeholder equity chart section.

## Command Center (Phase 7H.2)

The console is branded **ExaltedFable Command Center** and uses sidebar navigation:
Overview, Teams, Agents, Agent Hub, Run Cycle, Paper Accounts, Discord Bot, Reports,
Runtime Files, Settings, Setup / Secrets, Help / Safety.

### Persistent notifications

Saving settings, secrets, or team config now shows a notification near the top of the page
that stays visible for a few seconds (no one-frame flash) and can be dismissed with the
"Dismiss notifications" button.

### Discord Bot page

Start/stop/inspect the local Discord bot without a terminal:

- Shows whether the bot appears running, stopped, or has a **stale PID** (PID file present
  but process gone).
- **Start** launches `python -m src.main discord-bot` via a subprocess. No secrets are passed
  on the command line — the bot reads your local `.env` as usual.
- **Stop** terminates by saved PID (on Windows it force-closes the process tree). If a stop
  seems unreliable, close the bot's own window.
- **Restart** stops then starts.
- The bot log tail is shown read-only and secret-redacted.
- Runtime files live under `data/runtime/` (`discord_bot.pid`, `discord_bot.log`) and are
  never committed.
- Changing `.env` or Discord settings requires a **bot restart** to take effect.

### Agent Hub page

Talk to agents from the UI, outside Discord, with a chat-style transcript. It has four
modes (it **defaults to Team Chat**):

- **Team Chat** — natural conversation with the whole team ("hey guys", "what are you working
  on?", "summarize your latest idea in plain English"). Conversational only; no proposals, no
  sandbox routing, no trades.
- **Agent Chat** — natural conversation with one selected agent, in that agent's role/persona.
  Conversational only; no proposals, no trades.
- **Ask Team for Proposal** — the structured `build_ask_team_summary` path. Labeled
  "Structured proposal-only; no trades placed."
- **Ask Agent for Proposal** — the structured `build_ask_agent_summary` path. Labeled
  "Structured proposal-only; no trades placed."

Chat and proposal modes are separate so casual messages get friendly replies instead of
route/rejection counts. Conversational modes use the existing Hermes/Ollama conversational
adapter with a chat prompt (not the proposal parser); if no runtime is configured or a model
call fails, you get a clear fallback message instead of an error. **No Agent Hub mode submits
orders, and none calls `build_team_paper_cycle_summary`.** Chat history is kept separately per
team/mode/agent; Clear chat and Save transcript (to git-ignored `data/notes/agent_hub/`,
secret-redacted) work in every mode.

**Grounded answers.** Conversational replies are grounded in actual saved runtime evidence —
latest proposal path + routing split, risk/review note paths + parsed approvals, stock_long
eligibility, paper order status, and recent files. The prompt instructs the model to use only
that evidence and not invent topics, symbols, or market views. An **"Evidence available to
this chat"** expander shows exactly what the chat is grounded on. For common status questions
("what are you working on?", "what's the latest proposal?", "what happened last cycle?"), if
the model is unavailable you still get a deterministic, evidence-only answer rather than a
guess — and if there's no saved evidence, it says so plainly.

**Proposal modes** require a non-blank prompt: your typed text becomes the proposal request's
`learning_goal`, and a blank prompt is blocked with a friendly error before any helper runs.

Secrets always remain only in your local `.env`; the dashboard never displays or logs them.

## Phase 7I productization

### UI templates

Use the sidebar **UI template** selector:

- **Portfolio Cockpit** is the default for normal daily use. Its Home page is broker-style:
  Alpha/Beta equity, cash, buying power, positions, market status, approvals, positions,
  charts where data exists, and a "next safe action" summary. Advanced details live in
  expanders.
- **Command Center** is the operator/debug layout. Its Home page shows process state, bot
  PID detection, runtime files, logs, warnings, raw paths, agent status, and a prominent
  kill switch.
- **AI Team Room** is chat/team-first. Its Home page shows Team Alpha and Team Beta rooms,
  agent cards, current focus, goals, hypotheses, recent lessons, and "what are we working
  on?" panels grounded in saved runtime evidence.

The selected template is saved locally under `data/runtime/ui_template.json`, which is
ignored by git. Use **Reset template** in the sidebar to return to Portfolio Cockpit.

### First-run onboarding

Use **Setup Wizard** for a non-coder first run:

1. Confirm the paper-only warning.
2. Check local requirements: Python, dependencies, Streamlit, optional Ollama, and Alpaca paper keys.
3. Use **Setup / Secrets** to write local `.env` values. Blank secret fields preserve existing values.
4. Save recommended first-test caps: Alpha one paper order/day and $250,000 notional, Beta zero orders and zero notional, both autonomy flags off.
5. Validate `.env`, Alpaca paper endpoint, Ollama/Hermes status, and optional Discord status.
6. Start from Daily Lab with a disabled-autonomy smoke cycle.

### Daily Lab

Daily Lab is the repeatable feedback loop:

- editable agent/team goals under `data/notes/agent_goals/`
- morning checklist for account status, caps, autonomy, and positions
- disabled-autonomy team cycle runner
- end-of-day report/cycle review
- learning ledger at `data/notes/learning_ledger.md`
- strategy scorecards and improvement counters derived from latest runtime status
- manual loop scaffold: morning plan, market-hours paper test, end-of-day review,
  learning update, and tomorrow's hypothesis

The ledger is runtime memory only. It can be included in future prompt context, but it does not train the model, modify code, change prompts automatically, or change trading permissions.

### Data Tools

The **Data Tools** page shows which sources are configured: Alpaca paper account, Alpaca
market data hints, local runtime files, and future RSS/news/SEC adapters. Agents may only
claim market/news facts when the app provides them in tool context. Hermes/Ollama does not
have internet by default, and arbitrary website scraping is not enabled in this phase.

### Hermes / Ollama / Local AI

The **Hermes / Ollama / Local AI** page explains that Hermes is the adapter layer and Ollama is a common local model runtime. Local Ollama has no per-message API fee, but uses local compute/electricity. It does not know current market/news unless data is fed to it, and it does not train itself.

### Optional Windows launcher

Run:

```bash
python scripts/launch_desktop_app.py
```

Optional EXE scaffold:

```powershell
pip install pyinstaller pywebview
powershell -ExecutionPolicy Bypass -File scripts/build_windows_launcher.ps1
```

Generated `dist/`, `build/`, and `*.spec` files are ignored. The launcher only starts
Streamlit; it is not the trading engine and does not contain secrets.
