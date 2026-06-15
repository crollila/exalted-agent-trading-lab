# User Quickstart

This is a local paper-trading research lab. It is not live trading, and it should not be exposed to the public internet.

## First run

1. Download or clone the repository.
2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Create local config:

   ```bash
   copy .env.example .env
   ```

5. Initialize the database:

   ```bash
   python -m src.main init-db
   ```

6. Open the desktop-style app:

   ```bash
   python -m src.main app
   ```

   If `pywebview` is not installed, it falls back to your normal browser and tells you how
   to install the optional wrapper. Browser-only mode also works:

   ```bash
   python -m src.main dashboard
   ```

## Setup inside the dashboard

1. Open **Setup Wizard**.
2. Open **Setup / Secrets** and add only local values.
3. Validate Alpaca paper account settings. Use the paper endpoint only: `https://paper-api.alpaca.markets`.
4. Configure Hermes/Ollama only if you want local agent model calls.
5. Start Discord Bot only if you want Discord commands. No-Discord mode is supported; use
   Agent Hub, Daily Lab, and Run Cycle in the dashboard.
6. Save recommended first-test caps:
   - `TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY=1`
   - `TEAM_ALPHA_MAX_DAILY_NOTIONAL=250000`
   - `TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY=0`
   - `TEAM_BETA_MAX_DAILY_NOTIONAL=0`
   - both autonomy flags off
7. Use **Daily Lab** to run a disabled-autonomy smoke test.
8. For the first market-hours paper test, enable Alpha only for one controlled run, then disable autonomy again.

## Safety rules

- Paper only.
- No live trading.
- No short execution.
- No margin execution.
- No options execution.
- Keys stay local in `.env`.
- Runtime data under `data/` is ignored and should not be committed.
- Start with conservative caps.
- Do not enable Beta initially.

Paper orders, if any, can only flow through the existing Run Cycle path after autonomy, risk-agent approval, review-agent approval, deterministic Python risk, daily caps, and the Alpaca paper-only wrapper all pass.

## Optional launcher

The desktop launcher starts Streamlit locally and opens a desktop window when `pywebview`
is installed:

```bash
python scripts/launch_desktop_app.py
```

Optional Windows EXE wrapper:

```powershell
pip install pyinstaller pywebview
powershell -ExecutionPolicy Bypass -File scripts/build_windows_launcher.ps1
```

The EXE wrapper is only a launcher. It is not the trading engine, does not include secrets,
and still needs your local `.env`, Alpaca paper keys, and optional Ollama setup.

## How to know it is working

- The app opens as **ExaltedFable Command Center** or falls back to a local browser tab.
- Setup Wizard validation shows your local `.env` status without printing secrets.
- Portfolio Cockpit can show paper account status when Alpaca paper keys are configured.
- Daily Lab can run a disabled-autonomy smoke test with no paper orders submitted.
- Agent Hub can answer status questions from saved runtime evidence.

## Troubleshooting

- Dashboard opens in browser: install optional wrapper with `pip install pywebview`.
- Bot already running: use the Discord Bot page to inspect or stop detected processes.
- Keys configured but fields blank: secret fields intentionally do not redisplay values.
- Market closed: account views may work, but paper tests should wait for market hours.
- Model unavailable: check Ollama is running and `HERMES_BASE_URL` / `HERMES_MODEL` are set.
- Discord missing: ignore it unless you want Discord commands; the dashboard works without it.
