# Discord Bot Setup

This bot is a local command center for safe lab commands only. It can summarize status, Hermes teams, proposal routing, and local tournament routing scores. It cannot trade, call Alpaca, place orders, approve execution, or bypass the risk engine.

The `!ask_team` command can ask a configured Hermes runtime to generate proposal JSON. The flow is:

```text
Discord -> bot -> Hermes runtime -> proposal JSON -> sandbox review
```

It is not:

```text
Discord -> Alpaca
```

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
```

If `DISCORD_ALLOWED_CHANNEL_IDS` is unset, the bot allows commands in all channels and prints a startup warning.

To use `!ask_team`, also configure the existing Hermes runtime adapter:

```powershell
$env:HERMES_ENABLED="true"
$env:HERMES_BASE_URL="http://127.0.0.1:11434/v1"
$env:HERMES_MODEL="<your model>"
```

Generated proposal JSON is saved under ignored `data/agent_runs/`, then immediately validated by the local Hermes sandbox router.

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
!review_proposals
!review_proposals docs/examples/hermes_strategy_sandbox_example.json
!run_tournament
!run_tournament docs/examples/hermes_team_registry_example.json docs/examples/hermes_strategy_sandbox_example.json
!ask_team team_alpha alpha_research_1 research_agent team_alpha_discord_v1 Find a high-conviction strategy for tomorrow
```

Slash commands are also registered when Discord command sync succeeds:

```text
/status
/teams
/review_proposals
/run_tournament
/ask_team
```

All responses are summaries of generated or local files and local routing logic. Tournament scores are routing scores only, not profitability and not execution approval. `!ask_team` responses include the saved proposal path and route counts, and always remain proposal-only with no trades placed.
