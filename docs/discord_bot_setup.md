# Discord Bot Setup

This bot is a local command center and team-chat surface for safe lab commands. It can summarize status, Hermes teams, proposal routing, local tournament routing scores, natural team-channel chat, gated paper-cycle scaffolds, and team Alpaca paper account state. It cannot live trade, and it cannot bypass the risk engine.

The `!ask_team` command can ask a configured Hermes runtime to generate proposal JSON. The flow is:

```text
Discord -> bot -> Hermes runtime -> proposal JSON -> sandbox review
```

It is not:

```text
Discord -> Alpaca
```

The only Discord path that may submit paper orders is:

```text
!paper_trade_team <team_id> <proposal_path> <risk_approval_note_path> <review_approval_note_path>
```

`!ask_team`, `!ask_agent`, `!run_tournament`, and natural team-channel chat never auto-submit orders.

Autonomous paper cycles are opt-in per team and still require:

```text
research proposal JSON
risk agent note ending with RISK_AGENT_APPROVED: true
review agent note ending with REVIEW_AGENT_APPROVED: true
deterministic Python risk approval
Alpaca paper-only wrapper
```

If any gate is missing, the cycle stops with no paper orders submitted.

## 1. Create a Discord bot

1. Open the Discord Developer Portal.
2. Create an application and add a bot.
3. Copy the bot token into your local `.env` or terminal environment.
4. Invite the bot to your server with bot and application command permissions.

Do not commit the token, guild ID, or channel IDs.

## 2. Configure local environment

PowerShell example:

```powershell
$env:DISCORD_BOT_TOKEN="<your bot token>"
$env:DISCORD_GUILD_ID="<optional server id>"
$env:DISCORD_ALLOWED_CHANNEL_IDS="<optional channel id,another channel id>"
$env:DISCORD_DEFAULT_REGISTRY="docs/examples/hermes_team_registry_example.json"
$env:DISCORD_DEFAULT_PROPOSAL="docs/examples/hermes_strategy_sandbox_example.json"
$env:DISCORD_TEAM_ALPHA_CHANNEL_ID="<optional #team-alpha channel id>"
$env:DISCORD_TEAM_BETA_CHANNEL_ID="<optional #team-beta channel id>"
$env:DISCORD_TOURNAMENT_RESULTS_CHANNEL_ID="<optional tournament results channel id>"
$env:DISCORD_STRATEGY_LAB_CHANNEL_ID="<optional strategy lab channel id>"
$env:DISCORD_PAPER_TRADING_LOG_CHANNEL_ID="<optional paper trading log channel id>"
$env:DISCORD_SCHEDULED_TEAM_UPDATES_ENABLED="false"
$env:DISCORD_SCHEDULED_TEAM_UPDATE_MINUTES="360"
$env:DISCORD_AUTONOMY_CONFIG_PATH="data/notes/team_autonomy_config.json"
$env:TEAM_ALPHA_AUTONOMY_ENABLED="false"
$env:TEAM_BETA_AUTONOMY_ENABLED="false"
$env:TEAM_ALPHA_AUTONOMY_MODE="paper_stocks_only"
$env:TEAM_BETA_AUTONOMY_MODE="paper_stocks_only"
$env:TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY="3"
$env:TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY="3"
$env:TEAM_ALPHA_MAX_DAILY_NOTIONAL="50000"
$env:TEAM_BETA_MAX_DAILY_NOTIONAL="50000"
$env:TEAM_ALPHA_REQUIRE_RISK_AGENT_APPROVAL="true"
$env:TEAM_BETA_REQUIRE_RISK_AGENT_APPROVAL="true"
$env:TEAM_ALPHA_REQUIRE_REVIEW_AGENT_APPROVAL="true"
$env:TEAM_BETA_REQUIRE_REVIEW_AGENT_APPROVAL="true"
```

If `DISCORD_ALLOWED_CHANNEL_IDS` is unset, the bot allows commands in all channels and prints a startup warning.

If `DISCORD_TEAM_ALPHA_CHANNEL_ID` or `DISCORD_TEAM_BETA_CHANNEL_ID` is set, normal non-command messages in that channel are routed to that team's active research, risk, and review agents. Responses are saved under ignored runtime notes and are team chat only.

To use `!ask_team`, also configure the existing Hermes runtime adapter:

```powershell
$env:HERMES_ENABLED="true"
$env:HERMES_BASE_URL="http://127.0.0.1:11434/v1"
$env:HERMES_MODEL="<your model>"
```

Generated proposal JSON is saved under ignored `data/agent_runs/`, then immediately validated by the local Hermes sandbox router.

For future paper-account competition status, configure team-specific Alpaca paper placeholders locally:

```powershell
$env:TEAM_ALPHA_ALPACA_API_KEY="<team alpha paper key>"
$env:TEAM_ALPHA_ALPACA_SECRET_KEY="<team alpha paper secret>"
$env:TEAM_ALPHA_ALPACA_PAPER="true"
$env:TEAM_ALPHA_ALPACA_BASE_URL="https://paper-api.alpaca.markets"

$env:TEAM_BETA_ALPACA_API_KEY="<team beta paper key>"
$env:TEAM_BETA_ALPACA_SECRET_KEY="<team beta paper secret>"
$env:TEAM_BETA_ALPACA_PAPER="true"
$env:TEAM_BETA_ALPACA_BASE_URL="https://paper-api.alpaca.markets"
```

When using team-specific Alpaca keys, the generic `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` values are optional. Team-specific keys are preferred for the Discord competition flow.

For $1,000,000 Alpaca paper accounts, use:

```powershell
$env:STARTING_EQUITY="1000000"
$env:MIN_CASH_PCT="0.10"
$env:MAX_POSITION_PCT="0.20"
$env:MAX_DAILY_TURNOVER_PCT="0.30"
$env:MAX_NEW_POSITIONS_PER_DAY="10"
```

Keep `MAX_NEW_POSITIONS_PER_DAY=10` for early autonomy, not broad values like 200. Alpaca paper buying power may show 4x equity, but project risk caps should keep exposure lower until margin gates are fully implemented and tested.

The bot never prints these secrets and never passes them to Hermes prompts. If they are missing or unsafe, `!status` reports a clear configuration message instead of crashing.

## 3. Run the bot

```powershell
python -m src.main discord-bot
```

If `DISCORD_BOT_TOKEN` is missing, startup refuses clearly and exits without connecting to Discord.

## 4. Commands

Prefix commands:

```text
!status
!teams
!team_paper_status team_alpha
!team_positions team_alpha
!review_proposals
!review_proposals docs/examples/hermes_strategy_sandbox_example.json
!run_tournament
!run_tournament latest
!run_tournament docs/examples/hermes_team_registry_example.json docs/examples/hermes_strategy_sandbox_example.json
!ask_team team_alpha alpha_research_1 research_agent team_alpha_discord_v1 Find a high-conviction strategy for tomorrow
!ask_agent team_alpha alpha_risk_01 Review the latest proposal risk
!latest_agent_run
!paper_trade_team team_alpha data/agent_runs/example.json data/notes/paper_cycles/team_alpha/risk.md data/notes/paper_cycles/team_alpha/review.md
!team_report team_alpha
!autonomy_status team_alpha
!team_autonomy_status team_alpha
!enable_autonomy team_alpha
!disable_autonomy team_alpha
!run_team_cycle team_alpha Prepare the next conservative paper-cycle proposal
!schedule_reports_status
!daily_team_report_now
```

Slash commands are also registered when Discord command sync succeeds:

```text
/status
/teams
/team_paper_status
/team_positions
/review_proposals
/run_tournament
/latest_agent_run
/ask_team
/ask_agent
/paper_trade_team
/team_report
/autonomy_status
/team_autonomy_status
/enable_autonomy
/disable_autonomy
/run_team_cycle
/schedule_reports_status
/daily_team_report_now
```

All responses are summaries of generated or local files and local routing logic. Tournament scores are routing scores only, not profitability and not execution approval. `!ask_team` responses include the saved proposal path and route counts, and always remain proposal-only with no trades placed.

Hermes may propose `stock_long`, `stock_short`, `stock_margin_long`, `stock_margin_short`, `option_long_call`, `option_long_put`, `covered_call`, and `cash_secured_put`. Unknown types are rejected. Options proposals are review/tournament-only in this phase; paper options execution is not enabled yet.

`!paper_trade_team` currently submits only stock-long paper orders that have risk/review approval notes and pass the existing deterministic stock risk gates. Stock short, margin, and options proposals are logged and rejected with clear reasons until their deterministic paper risk gates and mocked paper broker support are implemented.

`!enable_autonomy` and `!disable_autonomy` write only to the ignored local runtime file at `data/notes/team_autonomy_config.json` by default. Autonomy defaults off, and enabling it does not grant live trading or advanced paper permissions.

`!run_team_cycle` is the autonomous-cycle scaffold. It asks the research agent for proposal JSON, asks the risk and review agents for explicit approval tokens, and only calls the existing paper execution path when the team's autonomy is enabled, both approval tokens are present, stock-long-only mode is active, daily order/notional caps are still available, and deterministic Python risk approves. The deterministic Python risk engine remains the final hard gate.

`!schedule_reports_status` and `!daily_team_report_now` are manual report scaffolds in this phase. They do not start an external scheduler. `!daily_team_report_now` summarizes both team paper statuses, positions, latest saved team proposals, latest routing summary when available, and the paper-only/no-live-trading reminder.

## Phase 7S: per-iteration team-thought updates from the cheap loop

The cheap competition loop (`python -m src.main run-cheap-competition-loop`) can post a concise
"team room briefing" to each team's channel every iteration so you can watch what Team Alpha and
Team Beta are doing and why. This is **off by default** and is read-only: it summarizes local
artifacts (cheap-gate decision, PortfolioManager stance, latest thesis, attribution/learning,
SPY-relative performance, broker outcomes) and a paper-only safety badge. It never submits orders,
never posts secrets, and Discord problems (missing token/channel, rate limit, network down) only
print a warning — the trading loop keeps running.

Reuses your existing `DISCORD_BOT_TOKEN` and the same `DISCORD_TEAM_ALPHA_CHANNEL_ID` /
`DISCORD_TEAM_BETA_CHANNEL_ID` channel IDs. The optional Alpha-vs-Beta scoreboard posts to the
channel named by `DISCORD_COMPETITION_SUMMARY_CHANNEL` (`tournament_results` or `paper_trading_log`).

Enable in `.env`:

```
ENABLE_DISCORD_ITERATION_UPDATES=true
DISCORD_POST_WHEN_MARKET_CLOSED=false   # stay quiet overnight (recommended)
DISCORD_POST_REVIEW_ONLY=true
DISCORD_POST_FULL_CYCLE=true
DISCORD_POST_CHEAP_SKIP=false           # set true only if you want every-iteration "no change" posts
DISCORD_POST_COMPETITION_SUMMARY=true
DISCORD_COMPETITION_SUMMARY_CHANNEL=tournament_results
DISCORD_UPDATE_MIN_INTERVAL_SECONDS=300 # throttle per team
DISCORD_ITERATION_UPDATE_MAX_CHARS=1800
```

Preview without sending (no Discord API call, no secrets printed):

```
python -m src.main discord-iteration-update --team team_alpha --dry-run
python -m src.main discord-iteration-update --team team_beta --dry-run
python -m src.main discord-iteration-update --team both --summary --dry-run
```

Without `--dry-run`, the command sends only if `ENABLE_DISCORD_ITERATION_UPDATES=true` and the bot
token + target channel ID are configured; otherwise it reports a safe skip. The same posting happens
automatically each iteration when you run `run-cheap-competition-loop` with updates enabled.
