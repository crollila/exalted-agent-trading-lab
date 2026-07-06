# Exalted Agent Trading Lab

Two AI teams trade real Alpaca **paper** accounts and compete every market day,
forever: beat the S&P 500 (SPY), beat the other team, and learn from every day's
results — including from each other.

**Paper trading only.** There is no live-trading code path anywhere in this
project, and a file-based kill switch can pause all order submission at any time.

## The teams

Each team has its own $1M Alpaca paper account and three AI agents that talk to
each other every cycle:

| Agent | Job |
|---|---|
| **Researcher** | Digests live prices, market movers, news, the earnings calendar — and searches the live web — into an honest research brief. Curates the team's own watchlist. |
| **Strategy developer** | Turns the brief + portfolio state into trade decisions under the team's self-chosen charter (or an explicit "no trade today"). |
| **Risk analyst** | Reviews every proposal; can veto or shrink it — never enlarge it. |

- **Team Alpha** starts as a momentum / catalyst hunter (fast cycles, higher gross).
- **Team Beta** starts contrarian / risk-adjusted (slower cycles, lower gross).

"Starts as", because **each team runs itself**. Its charter
(`data/charter/<team>.json`) holds its self-chosen parameters — position size,
gross exposure (margin), cycle speed (5–120 min), which instruments it uses
(stocks, shorts, long options, margin) and its style statement — and the
strategist may change any of it on any cycle, with the reason announced to
Discord. Teams can trade momentum, news, mean reversion, trend lines, whatever
they decide works.

After the agents, a **deterministic risk engine** (plain code, no LLM) has final
say. It enforces the tighter of the team's charter and the immutable
**platform caps** teams can never touch: max 30% per position, 2x gross
exposure, daily order/notional limits, options premium caps, live-price and
ticker-existence checks, and **long calls/puts only — selling/writing options
is never allowed**.

## How they learn

Every agent has a persistent memory file (`data/memory/<team>/<role>.json`):

1. At the end of each trading day, each agent reflects on the day's trades,
   theses, and the result vs SPY, and writes dated **lessons**.
2. Lessons are injected into that agent's prompts the next day.
3. When lessons pile up, they are distilled into a compact **playbook** of
   durable principles — knowledge accumulates without prompts growing forever.
4. Each team then reads the **rival's debrief** and answers with a public
   rebuttal (posted to Discord) plus private lessons-from-the-rival written
   into its own memory — the teams learn from each other's wins and mistakes.

The scoreboard (`data/scoreboard.json`) records every day's Alpha vs Beta vs SPY
result: daily wins, head-to-head record, cumulative returns, plus **Sharpe
ratio, volatility, and max drawdown** so a steady hand gets credit, not just a
hot one.

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

While the market is open, each team runs a full cycle on its own charter-chosen
cadence. After the close the day is scored, every agent reflects, the debriefs
and rebuttals go out, and the loop sleeps until the next open. Weekends and
holidays are handled automatically.

### Other commands

```bash
python -m src.main status                 # accounts, charters, watchlists, memory, errors
python -m src.main scoreboard             # who is winning (incl. Sharpe / drawdown)
python -m src.main cycle --dry-run --force  # test a full cycle without submitting orders
python -m src.main cycle --team team_alpha  # one real cycle for one team
python -m src.main eod                    # run the end-of-day pass now
python -m src.main kill on --reason "pause"  # block all order submission
python -m src.main kill off               # resume
```

## What's on disk

```
src/            ~20 small modules; start with cycle.py (the trading cycle)
data/
  cycles/       one JSON audit per cycle — the full story of every decision
  ledger/       every order attempt, tied to the thesis that produced it
  memory/       each agent's lessons + playbook (the "getting smarter" state)
  charter/      each team's self-chosen parameters + change history
  watchlist/    each team's self-curated symbol universe
  reports/      end-of-day markdown reports (debriefs + rebuttals)
  scoreboard.json
  runtime/      kill switch, errors.log, earnings cache, loop markers
```

## Configuration

Everything is set in `.env` — see [.env.example](.env.example). The risk values
there are the **platform walls**; each team picks its own settings inside them
via its charter.

## Safety rules

- Paper accounts only; the Alpaca client is constructed with `paper=True`, always.
- Every order passes the deterministic risk engine; LLMs never size or submit anything.
- Teams set their own risk appetite, but platform caps clamp every charter value.
- Options are long calls/puts only (max loss = premium paid). No naked selling, ever.
- The kill switch (`python -m src.main kill on`) blocks every submission instantly.
- Web research is read-only input; a malicious page can at worst *suggest* a bad
  idea, which the risk engine sizes and caps like any other.
- Secrets live only in `.env`, which is gitignored.
