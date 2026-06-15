# Alpaca Setup

## Goal

Use Alpaca paper trading only.

## Steps

1. Create or log into an Alpaca account.
2. Go to the paper trading dashboard.
3. Generate a paper API key and secret.
4. Copy `.env.example` to `.env`.
5. Paste paper keys into `.env`.
6. Leave `ALPACA_PAPER=true`.
7. Leave `ALPACA_BASE_URL=https://paper-api.alpaca.markets`.

Example:

```env
ALPACA_API_KEY=your_paper_key_here
ALPACA_SECRET_KEY=your_paper_secret_here
ALPACA_PAPER=true
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

For Discord team competitions, team-specific keys are preferred and the generic `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` values are optional:

```env
TEAM_ALPHA_ALPACA_API_KEY=your_team_alpha_paper_key_here
TEAM_ALPHA_ALPACA_SECRET_KEY=your_team_alpha_paper_secret_here
TEAM_ALPHA_ALPACA_PAPER=true
TEAM_ALPHA_ALPACA_BASE_URL=https://paper-api.alpaca.markets

TEAM_BETA_ALPACA_API_KEY=your_team_beta_paper_key_here
TEAM_BETA_ALPACA_SECRET_KEY=your_team_beta_paper_secret_here
TEAM_BETA_ALPACA_PAPER=true
TEAM_BETA_ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

For $1,000,000 paper accounts, use:

```env
STARTING_EQUITY=1000000
MIN_CASH_PCT=0.10
MAX_POSITION_PCT=0.20
MAX_DAILY_TURNOVER_PCT=0.30
MAX_NEW_POSITIONS_PER_DAY=10
```

For early autonomy, keep `MAX_NEW_POSITIONS_PER_DAY=10`; do not raise it to broad values like 200. Alpaca paper buying power may show 4x equity, but this project should keep exposure lower through project risk caps until dedicated margin gates are fully implemented and tested.

## Check paper account status

After `.env` is configured with paper credentials, run:

```bash
python -m src.main paper-status
```

This prints:

- account equity
- cash
- buying power
- whether the market is open
- current positions count

If credentials are missing or paper safety settings are wrong, the command fails safely with a short message. It must not print API secrets.

## Rules

- Never commit `.env`.
- Never paste real keys into ChatGPT, Codex, GitHub, Discord, or screenshots.
- Never set `ALPACA_PAPER=false`.
- Never change `ALPACA_BASE_URL` away from `https://paper-api.alpaca.markets`.
- Live trading is intentionally out of scope.

## Order safety

The Alpaca wrapper only accepts risk-approved `OrderRequest` objects and refuses live
endpoints, dry-run orders, and unapproved orders before submission.

## Advanced paper order paths (paper-only, gated)

The wrapper exposes dedicated, gated methods, each checked against the kill switch
immediately before submission:

- `submit_paper_order` — long stock (Level 1). Rejects short/margin/option fields.
- `submit_paper_short_order` — short stock (Level 2). Requires `short=True` and SELL.
- `submit_paper_margin_order` — margin stock (Level 3). Requires `margin=True`.
- `submit_paper_option_order` — defined-risk options (Level 4) via an adapter boundary.

If no options execution adapter is configured, `submit_paper_option_order` raises a clear
runtime error (`OptionsAdapterNotConfigured`) instead of faking a fill. Every attempted
broker submission is logged; every skipped unsupported adapter path is logged.

These methods are reached only from the gated Run Cycle path — never from chat, the Agent
Hub, ask commands, or the UI. Advanced levels are paper-only and off by default; enable them
explicitly via `.env` (`ENABLE_PAPER_SHORTING`, `ENABLE_PAPER_MARGIN`, `ENABLE_PAPER_OPTIONS`).

Tests mock Alpaca completely and do not send real orders.

## Team-aware credentials (global / alpha / beta)

There are three independent paper credential sources. Global credentials may be invalid
without blocking the teams; **team execution never falls back to global keys**.

- `global` -> `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
- `team_alpha` -> `TEAM_ALPHA_ALPACA_API_KEY` / `TEAM_ALPHA_ALPACA_SECRET_KEY`
- `team_beta` -> `TEAM_BETA_ALPACA_API_KEY` / `TEAM_BETA_ALPACA_SECRET_KEY`

Commands (secrets are never printed):

```bash
python -m src.main paper-status --team global
python -m src.main paper-status --team team_alpha
python -m src.main paper-status --team team_beta
python -m src.main alpaca-auth-diagnose          # presence/length + auth classification per source
python -m src.main competition-readiness-check   # per-team readiness + exact blockers
```

`alpaca-auth-diagnose` classifies each source as one of: `missing_env`, `endpoint_mismatch`,
`unauthorized_401`, `forbidden_403`, `network_error`, `sdk_error`, `unknown`, or `ok`.
The weekly competition uses each team's own credentials for account context and order
submission; if a team's keys are missing/invalid, only that team is blocked.
