# Research Setup

Agents can research current market context using **allowlisted** providers only —
the Alpaca News API and the OpenAI web-search tool. There is no arbitrary scraping,
no broker/order access from research, and no secrets in prompts or logs.

## Providers

```env
ENABLE_LIVE_NEWS_RESEARCH=true
NEWS_PROVIDER=alpaca            # none | alpaca | openai_web | hybrid
ENABLE_OPENAI_WEB_RESEARCH=true # required for openai_web / hybrid
OPENAI_WEB_RESEARCH_MODEL=gpt-5.4-mini
MAX_RESEARCH_QUERIES_PER_TEAM_PER_CYCLE=5
MAX_RESEARCH_RESULTS_PER_QUERY=5
RESEARCH_LOOKBACK_HOURS=24
RESEARCH_WATCHLIST=SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMD,META,GOOGL,AMZN
```

- `none` — research disabled (default).
- `alpaca` — Alpaca News only (team-specific credentials).
- `openai_web` — OpenAI web-search tool only.
- `hybrid` — Alpaca News first, then OpenAI web; results de-duplicated.

Caps (queries/results/lookback) are enforced deterministically by the app, not the model.

## How it flows

1. A team-specific **research planner** builds queries (Alpha = momentum/catalyst,
   Beta = contrarian/risk).
2. The app executes them through the configured provider(s), tagging each result with
   `provider`, `query`, `title`, `url`, `published_at`, `tickers`, `freshness`, and a
   `source_id`.
3. Results + prior performance feedback are injected into the LLM proposal prompt with the
   instruction: **do not invent news beyond the provided sources**.
4. The model cites `research_source_ids` per proposal and sets `research_changed_proposal`.
5. Every research run is logged (see below); citations flow into attribution.

## Research logs (ignored runtime path)

```
data/research/research_log.jsonl                 # append-only, one entry per run
data/research/latest_team_alpha_research.json    # latest snapshot per team
data/research/latest_team_beta_research.json
```

Each entry records timestamp, team, cycle, provider, queries, results, errors, and the
proposal ids that used it. No secrets are written.

## Effectiveness attribution

Per-proposal attribution is stored under `data/attribution/<team>_attribution.jsonl` and
tracks data sources, research source ids, routing, broker submission, entry price, return %,
SPY return, excess vs SPY, thesis outcome (pending/worked/failed/mixed), and lessons. View it:

```bash
python -m src.main research-status
python -m src.main proposal-attribution --team team_alpha
python -m src.main proposal-attribution --team team_beta
```

Recent winners/losers and best/worst symbols/strategies are fed back into the next cycle's
prompt. The LLM still never sizes or submits — the deterministic risk engine and the
kill-switch-guarded broker wrapper remain the only path to a paper order.
