# Continuous operation, bounded memory & learning (Phase 7W / 7X)

This document describes how the paper-only competition loop runs for long periods
without uncontrolled runtime bloat, learns from verified outcomes in a bounded
way, reports end-of-day, and stays alive via a watchdog.

**Phase 7X wired the three live-loop integrations** that were previously manual:

- **Bounded memory in the live prompt.** `build_llm_context` now assembles the
  deterministic bounded-memory block (`build_bounded_prompt_memory`): refreshed
  working memory, current positions + active theses, account/risk/cap constraints,
  the last N daily summaries, the top K active playbook lessons, and the latest
  scorecard snapshot — excluding raw audit JSONL, unbounded agent responses, and
  chat history. Each iteration's audit logs the memory metadata (daily summaries,
  lesson IDs, scorecard flag, bounded-context char count, malformed sources) — never
  raw prompt text or secrets.
- **Portfolio review + safe sell-to-close in the loop.** Every market-hours
  iteration refreshes the account/positions, runs deterministic health checks,
  reviews existing positions **before** new-entry research, executes only eligible
  long trims/exits (capped to refreshed held qty; never shorts; gated by
  `ENABLE_PAPER_SELL_TO_CLOSE`) **before** any new buys, and blocks new buys when
  health requires a reduction. The audit records `portfolio_action_recommended`,
  `portfolio_action_eligible`, `portfolio_action_submitted`,
  `portfolio_action_rejected_reason`, and `new_buys_blocked_reason`.
- **Automatic EOD + weekly delivery.** The running loop sends the once-per-team/
  trading-date EOD report after the regular close and runs the once-per-team/week
  synthesis after the last session of the week — both Alpaca clock/calendar gated,
  restart-safe (durable delivery record written before and after send), and
  retried on Discord failure. Status: `eod-report-status`, `weekly-review-status`.

> **Safety:** Learning here is research feedback only. It **never** auto-edits
> `.env`, risk limits, strategy code, broker permissions, or the database schema,
> and it never enables live trading, options/short/margin execution, or LLM order
> placement. Deterministic Python remains the final authority for execution and
> retention.

## Memory layers (per team)

| Layer | What | Where | Lifecycle |
|---|---|---|---|
| **A. Working memory** | account, positions + theses, session state, pending proposals, today's watchlist/constraints/usage | rebuilt each cycle (not durably stored) | replaced every cycle |
| **B. Daily memory** | one compact summary per team per trading day (orders, holds/trims/exits, reasons, P&L vs SPY, what went right/wrong, lessons, next-day plan/watchlist) | `data/runtime/eod_reports/`, `data/runtime/daily_learning/` | retained `MEMORY_DAILY_SUMMARY_RETENTION_DAYS` |
| **C. Long-term playbook** | curated, validated lessons (strengths, mistakes, risk lessons, strategy observations, preferred regimes, failure modes) with evidence count, confidence, last-validated, retirement/supersession markers | `data/runtime/playbook/<team>_playbook.json` | durable; never auto-deleted; capped at `MEMORY_MAX_PLAYBOOK_LESSONS_PER_TEAM` |
| **D. Scorecards** | equity history, SPY-relative return, outcomes, win/loss, thesis accuracy | `data/scorecards/` | existing |

Raw LLM prompt history is **not** stored as memory.

## Learning from outcomes

At end of day, each decision (entry/hold/trim/exit/rejected) is linked to its
thesis, confidence, and later realized/unrealized outcome. Deterministic Python
generates **learning candidates** and promotes one to the durable playbook only
when it clears the evidence gate:

* non-empty supporting evidence references, **and**
* explicit confidence > 0, **and**
* repeated across ≥ 2 decisions **or** a single high-impact documented success/failure,
* and it is not contradicted by newer evidence (otherwise it is *superseded*, not deleted).

An LLM may phrase a candidate; it can never invent a permanent lesson without
evidence.

## Bounded retrieval

Before research/review prompts, only a small, capped context is supplied: current
working memory, current positions + active theses, the most recent
`MEMORY_MAX_DAILY_SUMMARIES_IN_PROMPT` daily summaries, the top
`MEMORY_MAX_LESSONS_IN_PROMPT` relevant non-retired playbook lessons (ranked by
symbol/sector match, action type, regime, recency, confidence, evidence count),
the latest scorecard snapshot, and current constraints. Raw audit logs, old
chats, and unbounded reports are **excluded by design**.

## Inspect memory

```bash
python -m src.main memory-status --team both
```
Shows storage paths, file counts/sizes by category, oldest/newest, daily
summaries retained, raw-audit status, playbook size, scorecard availability, next
cleanup eligibility, and any malformed files. Read-only; no secrets.

## Retention & cleanup

Defaults live in `.env.example` (`MEMORY_*`). Cleanup is dry-run by default:

```bash
python -m src.main memory-maintenance --team both --dry-run   # plan only
python -m src.main memory-maintenance --team both --apply      # archive + delete
```

Rules: never deletes today's data, the current/latest daily summary, current
position-thesis (portfolio-review) records, or durable playbook lessons (stale
lessons are *marked superseded*, not deleted). Eligible old raw files are gzipped
into weekly archives under `data/runtime/memory_archives/` before deletion (when
`MEMORY_KEEP_WEEKLY_ARCHIVES=true`), recorded in a manifest so re-runs are
idempotent and an interrupted run resumes safely. A JSON + Markdown maintenance
report is written for every run. It never touches `.env`, source, DB migrations,
Git files, or user notes outside runtime directories.

## Weekly learning

```bash
python -m src.main weekly-team-review --team both        # add --send to post a short summary
```
Once a week (non-trading), summarizes the week's daily reports + scorecard
changes, identifies recurring successes/failures, and promotes/demotes/supersedes
playbook lessons **only** through the deterministic evidence gate. Saves a weekly
report under `data/runtime/weekly_reviews/`. Never trades or changes settings.

## End-of-day Discord report

```bash
python -m src.main export-eod-report --team both          # build + save (no post)
python -m src.main export-eod-report --team both --send   # post once, after close
```
Sent once per team per US trading date (Alpaca clock), after the regular session
closes. Prefers the configured **paper-trading log channel**
(`DISCORD_PAPER_TRADING_LOG_CHANNEL_ID`) and falls back to the team channel. A
delivery-state file prevents duplicate sends across restarts. The report covers
equity/P&L, SPY-relative performance, cash/BP/exposure/positions, what it did and
why, strongest/weakest positions, what it learned, thesis confirmations/
weakenings/invalidations, next-day plan/watchlist/conditions, and ends with
"Paper-only research summary. No live trading." Full Markdown + JSON are saved
under `data/runtime/eod_reports/`. Inputs are grounded in local order logs,
Alpaca account/position state, daily reports, and saved review records — no
fabricated outcomes.

## Watchdog / eternal operation

The loop writes a heartbeat every iteration (`data/runtime/loop_heartbeat.json`):
PID + timestamp + market state. Liveness requires **both** a live PID **and** a
fresh heartbeat — a stale PID file alone never counts as running. A normal exit
sets a graceful-shutdown flag so the watchdog does not restart an intentional
stop.

```bash
python -m src.main loop-health                                   # read-only status
python -m src.main loop-watchdog --team both --sleep-seconds 900 # keep the loop alive
```

`loop-health` reports PID, alive/dead, last heartbeat + age, last iteration and
last exception per team, market state, and whether a restart is recommended.

`loop-watchdog` restarts the loop only when it is dead or the heartbeat is stale
beyond `--stale-threshold-seconds` (default 1800). It:

* never launches a duplicate loop (defers to the tracked PID + a process scan),
* never restarts during a known graceful shutdown,
* never starts while the kill switch is engaged,
* uses the **same project Python** (`sys.executable`) to spawn the gated loop,
* writes logs to `data/runtime/watchdog.log`,
* **never submits a paper order itself** (its only action is respawning the gated
  loop process). Use `--dry-run` to assess + log without restarting, or `--once`
  for a single check.

### Windows Task Scheduler (start at logon, stay active)

Prefer a **single** scheduled watchdog over multiple loop tasks. Create a small
launcher `run_watchdog.bat` in the project root so the working directory is correct
for the relative `data/` paths:

```bat
@echo off
cd /d "C:\Users\croll\Desktop\Coding Projects\exalted-agent-trading-lab"
".venv\Scripts\python.exe" -m src.main loop-watchdog --team both --sleep-seconds 900
```

Register it to run at logon (runs the watchdog, which in turn keeps the loop alive):

```bat
schtasks /Create /TN "ExaltedLoopWatchdog" ^
  /TR "\"C:\Users\croll\Desktop\Coding Projects\exalted-agent-trading-lab\run_watchdog.bat\"" ^
  /SC ONLOGON /RL LIMITED /F
```

Optional hardening (restart the task if it ever stops, check every 5 min):

```bat
schtasks /Change /TN "ExaltedLoopWatchdog" /RI 5 /DU 9999:59
```

Manage it:

```bat
schtasks /Run    /TN "ExaltedLoopWatchdog"   :: start now
schtasks /Query  /TN "ExaltedLoopWatchdog"   :: status
schtasks /End    /TN "ExaltedLoopWatchdog"   :: stop the running instance
schtasks /Delete /TN "ExaltedLoopWatchdog" /F
```

The kill switch remains authoritative: `python -m src.main kill-switch-on` stops
new orders, and the watchdog will not start a loop while it is engaged.
