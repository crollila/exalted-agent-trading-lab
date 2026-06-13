from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from src.agents.hermes_runtime import (
    HermesGenerationRequest,
    HermesRuntimeConfig,
    generate_hermes_proposals,
)
from src.agents.hermes_strategy_sandbox import (
    PAPER_ELIGIBLE_STOCK_LONG,
    REJECTED,
    SIMULATION_ONLY_MARGIN,
    SIMULATION_ONLY_OPTION,
    SIMULATION_ONLY_SHORT,
    load_hermes_sandbox_file,
)
from src.agents.hermes_team_registry import load_hermes_team_registry_file
from src.agents.hermes_tournament_round import run_hermes_tournament_round


DEFAULT_REGISTRY_ENV = "DISCORD_DEFAULT_REGISTRY"
DEFAULT_PROPOSAL_ENV = "DISCORD_DEFAULT_PROPOSAL"
DEFAULT_REGISTRY_PATH = Path("docs/examples/hermes_team_registry_example.json")
DEFAULT_PROPOSAL_PATH = Path("docs/examples/hermes_strategy_sandbox_example.json")
DEFAULT_ASK_TEAM_OUTPUT_DIR = Path("data/agent_runs")
TOKEN_ENV = "DISCORD_BOT_TOKEN"
GUILD_ID_ENV = "DISCORD_GUILD_ID"
ALLOWED_CHANNELS_ENV = "DISCORD_ALLOWED_CHANNEL_IDS"


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str | None
    guild_id: int | None
    allowed_channel_ids: frozenset[int] | None
    default_registry_path: Path
    default_proposal_path: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DiscordBotConfig":
        env = environ if environ is not None else os.environ
        token = _clean_optional(env.get(TOKEN_ENV))
        guild_id = parse_optional_int(env.get(GUILD_ID_ENV), GUILD_ID_ENV)
        return cls(
            token=token,
            guild_id=guild_id,
            allowed_channel_ids=parse_allowed_channel_ids(env.get(ALLOWED_CHANNELS_ENV)),
            default_registry_path=Path(env.get(DEFAULT_REGISTRY_ENV, str(DEFAULT_REGISTRY_PATH))),
            default_proposal_path=Path(env.get(DEFAULT_PROPOSAL_ENV, str(DEFAULT_PROPOSAL_PATH))),
        )

    @property
    def channel_scope_description(self) -> str:
        if self.allowed_channel_ids is None:
            return "all channels"
        return f"{len(self.allowed_channel_ids)} allowed channel(s)"


def parse_optional_int(raw_value: str | None, env_name: str) -> int | None:
    value = _clean_optional(raw_value)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer.") from exc


def parse_allowed_channel_ids(raw_value: str | None) -> frozenset[int] | None:
    value = _clean_optional(raw_value)
    if value is None:
        return None
    channel_ids: set[int] = set()
    for item in value.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        try:
            channel_ids.add(int(cleaned))
        except ValueError as exc:
            raise ValueError(f"{ALLOWED_CHANNELS_ENV} must contain only comma-separated integers.") from exc
    return frozenset(channel_ids) if channel_ids else None


def is_channel_allowed(channel_id: int | None, allowed_channel_ids: frozenset[int] | None) -> bool:
    if allowed_channel_ids is None:
        return True
    return channel_id in allowed_channel_ids


def build_status_summary(config: DiscordBotConfig) -> str:
    return "\n".join(
        [
            "ExaltedFable command center: online.",
            "Safe lab mode only. Trading, Alpaca calls, and order execution are disabled.",
            f"Default registry: {config.default_registry_path}",
            f"Default proposal: {config.default_proposal_path}",
            f"Channel scope: {config.channel_scope_description}",
        ]
    )


def build_teams_summary(registry_path: Path | str) -> str:
    registry = load_hermes_team_registry_file(registry_path)
    active_teams = sum(1 for team in registry.teams if team.active)
    total_agents = sum(len(team.agents) for team in registry.teams)
    active_agents = sum(1 for team in registry.teams for agent in team.agents if agent.active)
    lines = [
        "Hermes teams",
        f"{len(registry.teams)} team(s), {active_teams} active. {total_agents} agent(s), {active_agents} active.",
    ]
    for team in registry.teams:
        status = "active" if team.active else "inactive"
        agent_roles = ", ".join(f"{agent.agent_id}:{agent.role.value}" for agent in team.agents)
        lines.append(f"- {team.team_id} ({status}) - {agent_roles}")
    lines.append("Registry only; no trading or LLM calls.")
    return _truncate_discord_message("\n".join(lines))


def build_review_proposals_summary(file_path: Path | str) -> str:
    result = load_hermes_sandbox_file(file_path)
    if result.request is None:
        errors = "; ".join(result.errors) if result.errors else "unknown parse error"
        return _truncate_discord_message(
            "\n".join(
                [
                    "Hermes proposal review: rejected.",
                    errors,
                    "No execution approval.",
                ]
            )
        )

    counts = result.route_counts()
    request = result.request
    return "\n".join(
        [
            f"Hermes proposal review: {request.team_id}/{request.agent_id}",
            f"Strategy: {request.strategy_id}",
            (
                f"Routes: paper {counts[PAPER_ELIGIBLE_STOCK_LONG]}, "
                f"short sim {counts[SIMULATION_ONLY_SHORT]}, "
                f"option sim {counts[SIMULATION_ONLY_OPTION]}, "
                f"margin sim {counts[SIMULATION_ONLY_MARGIN]}, "
                f"rejected {counts[REJECTED]}."
            ),
            "No execution approval.",
        ]
    )


def build_run_tournament_summary(registry_path: Path | str, proposal_paths: Sequence[Path | str]) -> str:
    result = run_hermes_tournament_round(registry_path=registry_path, proposal_paths=list(proposal_paths))
    winner = result.winner
    lines = ["Hermes tournament round"]
    if winner is None:
        lines.append("Winner: none.")
    else:
        lines.append(f"Winner: {winner.team_id} with score {winner.score}.")
    lines.append("Rankings:")
    for ranking in result.rankings:
        lines.append(
            f"- #{ranking.rank} {ranking.team_id}: score {ranking.score}, "
            f"total {ranking.total_proposals}, rejected {ranking.rejected_count}"
        )
    if result.errors:
        lines.append(f"Warnings: {len(result.errors)} routing warning(s).")
    lines.append("Routing score only; no trading or execution approval.")
    return _truncate_discord_message("\n".join(lines))


def build_ask_team_summary(
    team_id: str,
    agent_id: str,
    agent_role: str,
    strategy_id: str,
    prompt_text: str,
    *,
    output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
    runtime_config: HermesRuntimeConfig | None = None,
    generator=generate_hermes_proposals,
) -> str:
    prompt = prompt_text.strip()
    if not prompt:
        raise ValueError("prompt text is required.")

    config = runtime_config or HermesRuntimeConfig.from_env()
    config.validate_ready()
    request = HermesGenerationRequest(
        team_id=team_id,
        agent_id=agent_id,
        agent_role=agent_role,
        strategy_id=strategy_id,
        learning_goal=prompt,
        strategy_notes=f"Discord ask_team prompt: {prompt}",
    )
    output_file = _ask_team_output_file(output_dir, strategy_id)
    result = generator(config, request, output_file)
    sandbox = result.sandbox_result
    if sandbox.request is None:
        errors = "; ".join(sandbox.errors) if sandbox.errors else "unknown sandbox validation error"
        return _truncate_discord_message(
            "\n".join(
                [
                    "Hermes ask_team generated proposal JSON, but sandbox validation rejected it.",
                    f"Saved file: {result.output_file}",
                    errors,
                    "proposal only; no trades placed",
                ]
            )
        )

    counts = sandbox.route_counts()
    request_summary = sandbox.request
    return _truncate_discord_message(
        "\n".join(
            [
                "Hermes ask_team complete.",
                f"Saved file: {result.output_file}",
                f"Team ID: {request_summary.team_id}",
                f"Agent ID: {request_summary.agent_id}",
                f"Strategy ID: {request_summary.strategy_id}",
                (
                    f"Routes: paper {counts[PAPER_ELIGIBLE_STOCK_LONG]}, "
                    f"short sim {counts[SIMULATION_ONLY_SHORT]}, "
                    f"option sim {counts[SIMULATION_ONLY_OPTION]}, "
                    f"margin sim {counts[SIMULATION_ONLY_MARGIN]}, "
                    f"rejected {counts[REJECTED]}."
                ),
                f"Rejected count: {counts[REJECTED]}",
                "proposal only; no trades placed",
            ]
        )
    )


def run_discord_bot(config: DiscordBotConfig | None = None) -> None:
    config = config or DiscordBotConfig.from_env()
    if not config.token:
        print(f"Discord bot unavailable: {TOKEN_ENV} is required.", file=sys.stderr)
        raise SystemExit(1)

    try:
        import discord
        from discord import app_commands
        from discord.ext import commands
    except ImportError as exc:
        print("Discord bot unavailable: install dependency discord.py.", file=sys.stderr)
        raise SystemExit(1) from exc

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    async def send_prefix_response(ctx, message: str) -> None:
        if not is_channel_allowed(getattr(ctx.channel, "id", None), config.allowed_channel_ids):
            return
        await ctx.send(message)

    async def send_slash_response(interaction, message: str) -> None:
        if not is_channel_allowed(interaction.channel_id, config.allowed_channel_ids):
            await interaction.response.send_message("This channel is not allowed for lab commands.", ephemeral=True)
            return
        await interaction.response.send_message(message[:1900])

    @bot.event
    async def on_ready() -> None:
        if config.allowed_channel_ids is None:
            print(f"Warning: {ALLOWED_CHANNELS_ENV} is unset; Discord bot will allow all channels.")
        else:
            print(f"Discord bot channel allowlist: {sorted(config.allowed_channel_ids)}")
        if config.guild_id is not None:
            guild = discord.Object(id=config.guild_id)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
        print(f"Discord bot ready as {bot.user}. Safe lab mode only.")

    @bot.command(name="status")
    async def prefix_status(ctx) -> None:
        await send_prefix_response(ctx, build_status_summary(config))

    @bot.command(name="teams")
    async def prefix_teams(ctx) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_teams_summary(config.default_registry_path)))

    @bot.command(name="review_proposals")
    async def prefix_review_proposals(ctx, file_path: str | None = None) -> None:
        path = Path(file_path) if file_path else config.default_proposal_path
        await send_prefix_response(ctx, _safe_command(lambda: build_review_proposals_summary(path)))

    @bot.command(name="run_tournament")
    async def prefix_run_tournament(ctx, registry: str | None = None, proposal: str | None = None) -> None:
        registry_path = Path(registry) if registry else config.default_registry_path
        proposal_path = Path(proposal) if proposal else config.default_proposal_path
        await send_prefix_response(
            ctx,
            _safe_command(lambda: build_run_tournament_summary(registry_path, [proposal_path])),
        )

    @bot.command(name="ask_team")
    async def prefix_ask_team(ctx, team_id: str, agent_id: str, agent_role: str, strategy_id: str, *, prompt_text: str):
        await send_prefix_response(
            ctx,
            _safe_command(
                lambda: build_ask_team_summary(
                    team_id=team_id,
                    agent_id=agent_id,
                    agent_role=agent_role,
                    strategy_id=strategy_id,
                    prompt_text=prompt_text,
                )
            ),
        )

    @bot.tree.command(name="status", description="Show safe lab Discord bot status.")
    async def slash_status(interaction) -> None:
        await send_slash_response(interaction, build_status_summary(config))

    @bot.tree.command(name="teams", description="Summarize the configured Hermes team registry.")
    async def slash_teams(interaction) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_teams_summary(config.default_registry_path)))

    @bot.tree.command(name="review_proposals", description="Review a local Hermes proposal JSON file.")
    @app_commands.describe(file_path="Optional local proposal JSON path.")
    async def slash_review_proposals(interaction, file_path: str | None = None) -> None:
        path = Path(file_path) if file_path else config.default_proposal_path
        await send_slash_response(interaction, _safe_command(lambda: build_review_proposals_summary(path)))

    @bot.tree.command(name="run_tournament", description="Run a local Hermes routing-score tournament.")
    @app_commands.describe(registry="Optional registry JSON path.", proposal="Optional proposal JSON path.")
    async def slash_run_tournament(interaction, registry: str | None = None, proposal: str | None = None) -> None:
        registry_path = Path(registry) if registry else config.default_registry_path
        proposal_path = Path(proposal) if proposal else config.default_proposal_path
        await send_slash_response(
            interaction,
            _safe_command(lambda: build_run_tournament_summary(registry_path, [proposal_path])),
        )

    @bot.tree.command(name="ask_team", description="Ask Hermes to generate local proposal JSON for sandbox review.")
    @app_commands.describe(
        team_id="Hermes team ID.",
        agent_id="Hermes agent ID.",
        agent_role="Hermes agent role.",
        strategy_id="Strategy ID for the generated proposal JSON.",
        prompt_text="Prompt/context for Hermes proposal generation.",
    )
    async def slash_ask_team(
        interaction,
        team_id: str,
        agent_id: str,
        agent_role: str,
        strategy_id: str,
        prompt_text: str,
    ) -> None:
        await send_slash_response(
            interaction,
            _safe_command(
                lambda: build_ask_team_summary(
                    team_id=team_id,
                    agent_id=agent_id,
                    agent_role=agent_role,
                    strategy_id=strategy_id,
                    prompt_text=prompt_text,
                )
            ),
        )

    bot.run(config.token)


def _safe_command(command) -> str:
    try:
        return command()
    except Exception as exc:  # Discord command responses should fail closed and stay concise.
        return f"Command unavailable: {exc}"


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _truncate_discord_message(message: str, limit: int = 1900) -> str:
    if len(message) <= limit:
        return message
    return f"{message[: limit - 14].rstrip()}\n...truncated"


def _ask_team_output_file(output_dir: Path | str, strategy_id: str) -> Path:
    safe_strategy_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", strategy_id.strip()) or "strategy"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(output_dir) / f"discord_{safe_strategy_id}_{timestamp}.json"


__all__ = [
    "ALLOWED_CHANNELS_ENV",
    "DEFAULT_PROPOSAL_PATH",
    "DEFAULT_REGISTRY_PATH",
    "DiscordBotConfig",
    "TOKEN_ENV",
    "build_ask_team_summary",
    "build_review_proposals_summary",
    "build_run_tournament_summary",
    "build_status_summary",
    "build_teams_summary",
    "is_channel_allowed",
    "parse_allowed_channel_ids",
    "run_discord_bot",
]
