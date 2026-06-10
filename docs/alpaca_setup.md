# Alpaca Setup

## Goal

Use Alpaca paper trading only.

## Steps

1. Create or log into an Alpaca account.
2. Go to the paper trading dashboard.
3. Generate a paper API key and secret.
4. Copy `.env.example` to `.env`.
5. Paste keys into `.env`.

Example:

```env
ALPACA_API_KEY=your_paper_key_here
ALPACA_SECRET_KEY=your_paper_secret_here
ALPACA_PAPER=true
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

## Rules

- Never commit `.env`.
- Never paste real keys into ChatGPT, Codex, GitHub, Discord, or screenshots.
- Never set `ALPACA_PAPER=false`.
- Live trading is intentionally out of scope.
