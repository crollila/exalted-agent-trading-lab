# Exalted Agent Trading Lab

Two AI teams trade real Alpaca **paper** accounts and compete every market day,
forever: beat the S&P 500 (SPY), beat the other team, and learn from every day's
results.

**Paper trading only.** There is no live-trading code path anywhere in this
project, and a file-based kill switch can pause all order submission at any time.

## The teams

Each team has its own $1M Alpaca paper account and three AI agents that talk to
each other every cycle:

| Agent | Job |
|---|---|
| **Researcher** | Digests live prices and news into an honest research brief. |
| **Strategy developer** | Turns the brief + portfolio state into trade proposals (or an explicit "no trade today"). |
| **Risk analyst** | Reviews every proposal; can veto or shrink it — never enlarge it. |

- **Team Alpha** — momentum / catalyst hunter. Higher variance, rotates fast.
- **Team Beta** — contrarian / risk-adjusted. Lower variance, hates churn.

After the agents, a **deterministic risk engine** (plain code, no LLM) has final
say: it verifies the ticker exists, checks live prices, sizes every order, and
enforces hard caps (position size, gross exposure, cash floor, buying power,
daily order/notional limits). The agents cannot bypass it.

## How they learn

Every agent has a persistent memory file (`data/memory/<team>/<role>.json`):

1. At the end of each trading day, each agent reflects on the day's trades,
   theses, and the result vs SPY, and writes dated **lessons**.
2. Lessons are injected into that agent's prompts the next day.
3. When lessons pile up, they are distilled into a compact **playbook** of
   durable principles — knowledge accumulates without prompts growing forever.

The scoreboard (`data/scoreboard.json`) records every day's Alpha vs Beta vs SPY
result: daily wins, head-to-head record, and cumulative returns.

After the close, each team also writes a **debrief for you** — what we did today,
why we did it, what we expected, what we've observed so far, what we learned,
and how we intend to go forward. It lands in the daily report
(`data/reports/<date>.md`) and is posted to Discord if configured.

Anything that goes wrong (a failed LLM call, a broker rejection, a loop crash)
is appended to `data/runtime/errors.log` **and** posted to Discord, so problems
are never only visible in a terminal nobody is watching. `status` shows the
last five.

## Run it

```bash
# one-time setup
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then fill in your keys

# the competition (runs forever; Ctrl+C to stop)
python -m src.main run
```

While the market is open it runs a full cycle for each team every
`CYCLE_MINUTES` (default 30). After the close it scores the day, runs each
agent's reflection, writes a report to `data/reports/`, and sleeps until the
next open. Weekends and holidays are handled automatically.

### Other commands

```bash
python -m src.main status                 # accounts, positions, market clock, memory
python -m src.main scoreboard             # who is winning
python -m src.main cycle --dry-run --force  # test a full cycle without submitting orders
python -m src.main cycle --team team_alpha  # one real cycle for one team
python -m src.main eod                    # run the end-of-day pass now
python -m src.main kill on --reason "pause"  # block all order submission
python -m src.main kill off               # resume
```

## What's on disk

```
src/            ~15 small modules; start with cycle.py (the trading cycle)
data/
  cycles/       one JSON audit per cycle — the full story of every decision
  ledger/       every order attempt, tied to the thesis that produced it
  memory/       each agent's lessons + playbook (the "getting smarter" state)
  reports/      end-of-day markdown reports
  scoreboard.json
  runtime/      kill switch + loop markers
```

## Configuration

Everything is set in `.env` — see [.env.example](.env.example) for every knob
(risk caps, models per agent, cycle cadence, optional Discord notifications).

## Safety rules

- Paper accounts only; the Alpaca client is constructed with `paper=True`, always.
- Every order passes the deterministic risk engine; LLMs never size or submit anything.
- The kill switch (`python -m src.main kill on`) blocks every submission instantly.
- Secrets live only in `.env`, which is gitignored.
