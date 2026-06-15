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

The Alpaca wrapper only accepts risk-approved stock `OrderRequest` objects. Dry-run orders, options, non-stock assets, margin fields, and short fields are rejected before submission.

Tests mock Alpaca completely and do not send paper orders.
