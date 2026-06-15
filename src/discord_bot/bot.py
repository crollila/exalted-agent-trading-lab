from __future__ import annotations

import os
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from src.agents.hermes_runtime import (
    HermesAgentChatRequest,
    HermesGenerationRequest,
    HermesRuntimeConfig,
    ask_hermes_agent,
    generate_hermes_proposals,
)
from src.agents.hermes_strategy_sandbox import (
    HermesSandboxResult,
    PAPER_ELIGIBLE_STOCK_LONG,
    REJECTED,
    RoutedHermesProposal,
    SIMULATION_ONLY_MARGIN,
    SIMULATION_ONLY_OPTION,
    SIMULATION_ONLY_SHORT,
    load_hermes_sandbox_file,
)
from src.agents.hermes_team_registry import load_hermes_team_registry_file
from src.agents.hermes_tournament_round import run_hermes_tournament_round
from src.brokers.alpaca_client import AlpacaClientWrapper
from src.brokers.order_models import (
    AssetClass,
    OrderRequest,
    PortfolioSnapshot,
    RiskDecision,
    TradeAction,
    TradeProposal,
)
from src.brokers.team_alpaca_config import TEAM_ALPACA_ENV_PREFIXES, load_team_alpaca_paper_config
from src.config.settings import Settings
from src.db.database import (
    complete_run,
    create_run,
    get_connection,
    initialize_database,
    insert_order,
    insert_portfolio_snapshot,
    insert_risk_decision,
    insert_trade_proposal,
)
from src.portfolio.portfolio_state import PortfolioState, Position
from src.reporting.report_generator import format_report, generate_daily_report
from src.risk.risk_rules import RiskRules
from src.risk.trade_validator import TradeValidator


DEFAULT_REGISTRY_ENV = "DISCORD_DEFAULT_REGISTRY"
DEFAULT_PROPOSAL_ENV = "DISCORD_DEFAULT_PROPOSAL"
DEFAULT_REGISTRY_PATH = Path("docs/examples/hermes_team_registry_example.json")
DEFAULT_PROPOSAL_PATH = Path("docs/examples/hermes_strategy_sandbox_example.json")
DEFAULT_ASK_TEAM_OUTPUT_DIR = Path("data/agent_runs")
DEFAULT_AGENT_RESPONSE_DIR = Path("data/notes/agent_responses")
DEFAULT_TEAM_CHAT_DIR = Path("data/notes/team_chats")
DEFAULT_TEAM_CYCLE_DIR = Path("data/notes/paper_cycles")
DEFAULT_AUTONOMY_CONFIG_PATH = Path("data/notes/team_autonomy_config.json")
TOKEN_ENV = "DISCORD_BOT_TOKEN"
GUILD_ID_ENV = "DISCORD_GUILD_ID"
ALLOWED_CHANNELS_ENV = "DISCORD_ALLOWED_CHANNEL_IDS"
TEAM_CHANNEL_ENVS = {
    "team_alpha": "DISCORD_TEAM_ALPHA_CHANNEL_ID",
    "team_beta": "DISCORD_TEAM_BETA_CHANNEL_ID",
}
SPECIAL_CHANNEL_ENVS = {
    "tournament_results": "DISCORD_TOURNAMENT_RESULTS_CHANNEL_ID",
    "strategy_lab": "DISCORD_STRATEGY_LAB_CHANNEL_ID",
    "paper_trading_log": "DISCORD_PAPER_TRADING_LOG_CHANNEL_ID",
}
TEAM_AUTONOMY_ENVS = {
    "team_alpha": "TEAM_ALPHA_AUTONOMY_ENABLED",
    "team_beta": "TEAM_BETA_AUTONOMY_ENABLED",
}
TEAM_AUTONOMY_MODE_ENVS = {
    "team_alpha": "TEAM_ALPHA_AUTONOMY_MODE",
    "team_beta": "TEAM_BETA_AUTONOMY_MODE",
}
TEAM_MAX_PAPER_ORDERS_PER_DAY_ENVS = {
    "team_alpha": "TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY",
    "team_beta": "TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY",
}
TEAM_MAX_DAILY_NOTIONAL_ENVS = {
    "team_alpha": "TEAM_ALPHA_MAX_DAILY_NOTIONAL",
    "team_beta": "TEAM_BETA_MAX_DAILY_NOTIONAL",
}
TEAM_REQUIRE_RISK_APPROVAL_ENVS = {
    "team_alpha": "TEAM_ALPHA_REQUIRE_RISK_AGENT_APPROVAL",
    "team_beta": "TEAM_BETA_REQUIRE_RISK_AGENT_APPROVAL",
}
TEAM_REQUIRE_REVIEW_APPROVAL_ENVS = {
    "team_alpha": "TEAM_ALPHA_REQUIRE_REVIEW_AGENT_APPROVAL",
    "team_beta": "TEAM_BETA_REQUIRE_REVIEW_AGENT_APPROVAL",
}
SCHEDULED_TEAM_UPDATES_ENABLED_ENV = "DISCORD_SCHEDULED_TEAM_UPDATES_ENABLED"
SCHEDULED_TEAM_UPDATE_MINUTES_ENV = "DISCORD_SCHEDULED_TEAM_UPDATE_MINUTES"
RISK_APPROVAL_TOKEN = "RISK_AGENT_APPROVED"
REVIEW_APPROVAL_TOKEN = "REVIEW_AGENT_APPROVED"
AUTONOMY_MODE_PAPER_STOCKS_ONLY = "paper_stocks_only"


@dataclass(frozen=True)
class TeamAutonomyConfig:
    team_id: str
    enabled: bool = False
    mode: str = AUTONOMY_MODE_PAPER_STOCKS_ONLY
    max_paper_orders_per_day: int = 3
    max_daily_notional: float = 50000.0
    require_risk_agent_approval: bool = True
    require_review_agent_approval: bool = True

    @property
    def stock_paper_only(self) -> bool:
        return self.mode == AUTONOMY_MODE_PAPER_STOCKS_ONLY


@dataclass(frozen=True)
class PaperDailyUsage:
    submitted_order_count: int = 0
    submitted_notional: float = 0.0


@dataclass(frozen=True)
class AgentApprovalGate:
    risk_agent_approved: bool
    review_agent_approved: bool
    source: str

    @property
    def approved(self) -> bool:
        return self.risk_agent_approved and self.review_agent_approved


@dataclass(frozen=True)
class ProposalRoutingSplit:
    execution_eligible_proposals: tuple[RoutedHermesProposal, ...]
    simulation_only_proposals: tuple[RoutedHermesProposal, ...]
    rejected_proposals: tuple[RoutedHermesProposal, ...]


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str | None
    guild_id: int | None
    allowed_channel_ids: frozenset[int] | None
    default_registry_path: Path
    default_proposal_path: Path
    team_channel_ids: Mapping[str, int]
    special_channel_ids: Mapping[str, int]
    team_autonomy: Mapping[str, TeamAutonomyConfig]
    autonomy_config_path: Path
    scheduled_team_updates_enabled: bool
    scheduled_team_update_minutes: float

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
            team_channel_ids=parse_team_channel_ids(env),
            special_channel_ids=parse_special_channel_ids(env),
            team_autonomy=parse_team_autonomy_config(env),
            autonomy_config_path=Path(env.get("DISCORD_AUTONOMY_CONFIG_PATH", str(DEFAULT_AUTONOMY_CONFIG_PATH))),
            scheduled_team_updates_enabled=parse_bool_env(env.get(SCHEDULED_TEAM_UPDATES_ENABLED_ENV), default=False),
            scheduled_team_update_minutes=parse_positive_float_env(
                env.get(SCHEDULED_TEAM_UPDATE_MINUTES_ENV),
                SCHEDULED_TEAM_UPDATE_MINUTES_ENV,
                default=360.0,
            ),
        )

    @property
    def channel_scope_description(self) -> str:
        if self.allowed_channel_ids is None:
            return "all channels"
        return f"{len(self.allowed_channel_ids)} allowed channel(s)"

    def team_for_channel(self, channel_id: int | None) -> str | None:
        if channel_id is None:
            return None
        for team_id, configured_channel_id in self.team_channel_ids.items():
            if channel_id == configured_channel_id:
                return team_id
        return None

    def autonomy_enabled_for(self, team_id: str) -> bool:
        return self.autonomy_for(team_id).enabled

    def autonomy_for(self, team_id: str) -> TeamAutonomyConfig:
        _validate_known_team_id(team_id)
        base = self.team_autonomy.get(team_id, default_team_autonomy_config(team_id))
        override = load_runtime_team_autonomy_config(self.autonomy_config_path).get(team_id)
        if override is None:
            return base
        return TeamAutonomyConfig(
            team_id=team_id,
            enabled=override.enabled,
            mode=override.mode or base.mode,
            max_paper_orders_per_day=override.max_paper_orders_per_day,
            max_daily_notional=override.max_daily_notional,
            require_risk_agent_approval=override.require_risk_agent_approval,
            require_review_agent_approval=override.require_review_agent_approval,
        )


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


def parse_team_channel_ids(env: Mapping[str, str]) -> dict[str, int]:
    channel_ids: dict[str, int] = {}
    for team_id, env_name in TEAM_CHANNEL_ENVS.items():
        channel_id = parse_optional_int(env.get(env_name), env_name)
        if channel_id is not None:
            channel_ids[team_id] = channel_id
    return channel_ids


def parse_special_channel_ids(env: Mapping[str, str]) -> dict[str, int]:
    channel_ids: dict[str, int] = {}
    for channel_name, env_name in SPECIAL_CHANNEL_ENVS.items():
        channel_id = parse_optional_int(env.get(env_name), env_name)
        if channel_id is not None:
            channel_ids[channel_name] = channel_id
    return channel_ids


def parse_team_autonomy_flags(env: Mapping[str, str]) -> dict[str, bool]:
    return {
        team_id: parse_bool_env(env.get(env_name), default=False)
        for team_id, env_name in TEAM_AUTONOMY_ENVS.items()
    }


def parse_team_autonomy_config(env: Mapping[str, str]) -> dict[str, TeamAutonomyConfig]:
    return {
        team_id: TeamAutonomyConfig(
            team_id=team_id,
            enabled=parse_bool_env(env.get(TEAM_AUTONOMY_ENVS[team_id]), default=False),
            mode=parse_autonomy_mode(env.get(TEAM_AUTONOMY_MODE_ENVS[team_id])),
            max_paper_orders_per_day=parse_positive_int_env(
                env.get(TEAM_MAX_PAPER_ORDERS_PER_DAY_ENVS[team_id]),
                TEAM_MAX_PAPER_ORDERS_PER_DAY_ENVS[team_id],
                default=3,
            ),
            max_daily_notional=parse_positive_float_env(
                env.get(TEAM_MAX_DAILY_NOTIONAL_ENVS[team_id]),
                TEAM_MAX_DAILY_NOTIONAL_ENVS[team_id],
                default=50000.0,
            ),
            require_risk_agent_approval=parse_bool_env(
                env.get(TEAM_REQUIRE_RISK_APPROVAL_ENVS[team_id]),
                default=True,
            ),
            require_review_agent_approval=parse_bool_env(
                env.get(TEAM_REQUIRE_REVIEW_APPROVAL_ENVS[team_id]),
                default=True,
            ),
        )
        for team_id in TEAM_AUTONOMY_ENVS
    }


def default_team_autonomy_config(team_id: str) -> TeamAutonomyConfig:
    _validate_known_team_id(team_id)
    return TeamAutonomyConfig(team_id=team_id)


def parse_autonomy_mode(raw_value: str | None) -> str:
    mode = _clean_optional(raw_value) or AUTONOMY_MODE_PAPER_STOCKS_ONLY
    if mode != AUTONOMY_MODE_PAPER_STOCKS_ONLY:
        raise ValueError(f"Autonomy mode must be {AUTONOMY_MODE_PAPER_STOCKS_ONLY}.")
    return mode


def parse_bool_env(raw_value: str | None, *, default: bool) -> bool:
    value = _clean_optional(raw_value)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def parse_positive_float_env(raw_value: str | None, env_name: str, *, default: float) -> float:
    value = _clean_optional(raw_value)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a positive number.") from exc
    if parsed <= 0:
        raise ValueError(f"{env_name} must be a positive number.")
    return parsed


def parse_positive_int_env(raw_value: str | None, env_name: str, *, default: int) -> int:
    value = _clean_optional(raw_value)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{env_name} must be a positive integer.")
    return parsed


def is_channel_allowed(channel_id: int | None, allowed_channel_ids: frozenset[int] | None) -> bool:
    if allowed_channel_ids is None:
        return True
    return channel_id in allowed_channel_ids


def load_runtime_team_autonomy_config(config_path: Path | str = DEFAULT_AUTONOMY_CONFIG_PATH) -> dict[str, TeamAutonomyConfig]:
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    teams = payload.get("teams", {}) if isinstance(payload, dict) else {}
    if not isinstance(teams, dict):
        return {}
    configs: dict[str, TeamAutonomyConfig] = {}
    for team_id, raw_config in teams.items():
        if team_id not in TEAM_AUTONOMY_ENVS or not isinstance(raw_config, dict):
            continue
        base = default_team_autonomy_config(team_id)
        configs[team_id] = TeamAutonomyConfig(
            team_id=team_id,
            enabled=bool(raw_config.get("enabled", base.enabled)),
            mode=str(raw_config.get("mode", base.mode)),
            max_paper_orders_per_day=int(raw_config.get("max_paper_orders_per_day", base.max_paper_orders_per_day)),
            max_daily_notional=float(raw_config.get("max_daily_notional", base.max_daily_notional)),
            require_risk_agent_approval=bool(
                raw_config.get("require_risk_agent_approval", base.require_risk_agent_approval)
            ),
            require_review_agent_approval=bool(
                raw_config.get("require_review_agent_approval", base.require_review_agent_approval)
            ),
        )
    return configs


def save_runtime_team_autonomy_config(
    team_config: TeamAutonomyConfig,
    config_path: Path | str = DEFAULT_AUTONOMY_CONFIG_PATH,
) -> Path:
    path = Path(config_path)
    existing = load_runtime_team_autonomy_config(path)
    existing[team_config.team_id] = team_config
    payload = {
        "teams": {
            team_id: {
                "enabled": config.enabled,
                "mode": config.mode,
                "max_paper_orders_per_day": config.max_paper_orders_per_day,
                "max_daily_notional": config.max_daily_notional,
                "require_risk_agent_approval": config.require_risk_agent_approval,
                "require_review_agent_approval": config.require_review_agent_approval,
            }
            for team_id, config in sorted(existing.items())
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_status_summary(config: DiscordBotConfig) -> str:
    paper_config_lines = [
        load_team_alpaca_paper_config(team_id).safe_status()
        for team_id in TEAM_ALPACA_ENV_PREFIXES
    ]
    return "\n".join(
        [
            "ExaltedFable command center: online.",
            (
                "Safe lab mode only. No live trading. Alpaca paper calls are allowed only through explicit "
                "team paper commands. Paper order submission is allowed only through !paper_trade_team or "
                "explicitly enabled autonomous paper cycles after deterministic risk approval."
            ),
            f"Default registry: {config.default_registry_path}",
            f"Default proposal: {config.default_proposal_path}",
            f"Channel scope: {config.channel_scope_description}",
            f"Natural team chat channels: {len(config.team_channel_ids)} configured.",
            f"Special channels: {len(config.special_channel_ids)} configured.",
            (
                "Scheduled team updates: "
                f"{'enabled' if config.scheduled_team_updates_enabled else 'disabled'} "
                f"({config.scheduled_team_update_minutes:g} minute interval)."
            ),
            "Team autonomy:",
            *[
                (
                    f"- {team_id}: {'enabled' if (team_config := config.autonomy_for(team_id)).enabled else 'disabled'}, "
                    f"mode {team_config.mode}, max {team_config.max_paper_orders_per_day} order(s)/day, "
                    f"${team_config.max_daily_notional:,.2f} notional/day"
                )
                for team_id in TEAM_AUTONOMY_ENVS
            ],
            "Team paper account config:",
            *paper_config_lines,
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


def build_team_paper_status_summary(
    team_id: str,
    *,
    settings: Settings | None = None,
    client_factory=None,
) -> str:
    try:
        wrapper = _team_alpaca_client(team_id, settings=settings, client_factory=client_factory)
    except Exception as exc:
        return f"{team_id} paper status unavailable: {exc}"
    account = wrapper.get_account()
    positions = wrapper.get_positions()
    market_open = wrapper.is_market_open()
    return "\n".join(
        [
            f"{team_id} Alpaca paper status",
            f"Equity: {_money_or_unknown(_read_value(account, 'equity'))}",
            f"Cash: {_money_or_unknown(_read_value(account, 'cash'))}",
            f"Buying power: {_money_or_unknown(_read_value(account, 'buying_power'))}",
            f"Market: {'open' if market_open else 'closed'}",
            f"Positions count: {len(positions)}",
            "paper only; no trades placed",
        ]
    )


def build_team_positions_summary(
    team_id: str,
    *,
    settings: Settings | None = None,
    client_factory=None,
) -> str:
    try:
        wrapper = _team_alpaca_client(team_id, settings=settings, client_factory=client_factory)
    except Exception as exc:
        return f"{team_id} paper positions unavailable: {exc}"
    positions = wrapper.get_positions()
    if not positions:
        return f"{team_id} has no current Alpaca paper positions."

    lines = [f"{team_id} Alpaca paper positions"]
    for position in positions:
        symbol = _read_value(position, "symbol")
        quantity = _read_value(position, "qty", _read_value(position, "quantity"))
        market_value = _read_value(position, "market_value")
        cost_basis = _read_value(position, "cost_basis", _read_value(position, "avg_entry_price"))
        unrealized_pl = _read_value(position, "unrealized_pl")
        lines.append(
            f"- {symbol}: qty {quantity}, market value {_money_or_unknown(market_value)}, "
            f"cost basis {_money_or_unknown(cost_basis)}, unrealized P/L {_money_or_unknown(unrealized_pl)}"
        )
    lines.append("paper only; no trades placed")
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
    lines = [
        f"Hermes proposal review: {request.team_id}/{request.agent_id}",
        f"Strategy: {request.strategy_id}",
        (
            f"Routes: paper {counts[PAPER_ELIGIBLE_STOCK_LONG]}, "
            f"short sim {counts[SIMULATION_ONLY_SHORT]}, "
            f"option sim {counts[SIMULATION_ONLY_OPTION]}, "
            f"margin sim {counts[SIMULATION_ONLY_MARGIN]}, "
            f"rejected {counts[REJECTED]}."
        ),
    ]
    if counts[SIMULATION_ONLY_OPTION]:
        lines.append("Paper options execution not enabled yet.")
    lines.append("No execution approval.")
    return "\n".join(
        lines
    )


def build_run_tournament_summary(registry_path: Path | str, proposal_paths: Sequence[Path | str]) -> str:
    if len(proposal_paths) == 1 and str(proposal_paths[0]).strip().lower() == "latest":
        proposal_paths = [latest_agent_run_path()]
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


def build_latest_agent_run_summary(output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR) -> str:
    latest_path = latest_agent_run_path(output_dir=output_dir)
    return f"Latest saved proposal: {latest_path}"


def latest_agent_run_path(output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR) -> Path:
    paths = list(Path(output_dir).glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No saved proposal JSON files found under {output_dir}.")
    return max(paths, key=lambda path: path.stat().st_mtime)


def latest_agent_run_path_for_team(team_id: str, output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR) -> Path:
    candidates: list[Path] = []
    for path in Path(output_dir).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("team_id") == team_id:
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No saved proposal JSON files found for {team_id} under {output_dir}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def build_latest_team_cycle_summary(
    team_id: str,
    config: DiscordBotConfig,
    *,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    proposal_output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
    notes_output_dir: Path | str = DEFAULT_TEAM_CYCLE_DIR,
    settings: Settings | None = None,
) -> str:
    _validate_known_team_id(team_id)
    agents = _team_agents_by_role(registry_path, team_id)
    risk_agent = agents.get("risk_agent")
    review_agent = agents.get("review_agent")
    if risk_agent is None or review_agent is None:
        raise ValueError(f"{team_id} must have active risk_agent and review_agent profiles.")

    proposal_path: Path | None
    try:
        proposal_path = latest_agent_run_path_for_team(team_id, output_dir=proposal_output_dir)
        proposal_text = str(proposal_path)
    except FileNotFoundError:
        proposal_path = None
        proposal_text = "none saved yet"

    routing_split: ProposalRoutingSplit | None = None
    if proposal_path is not None:
        try:
            routing_split = _proposal_routing_split(load_hermes_sandbox_file(proposal_path))
        except OSError:
            routing_split = None

    risk_note = _latest_team_cycle_note_path(notes_output_dir, team_id, risk_agent.agent_id)
    review_note = _latest_team_cycle_note_path(notes_output_dir, team_id, review_agent.agent_id)
    risk_approved = _approval_file_is_true(risk_note, RISK_APPROVAL_TOKEN)
    review_approved = _approval_file_is_true(review_note, REVIEW_APPROVAL_TOKEN)
    has_execution_eligible = routing_split is not None and bool(routing_split.execution_eligible_proposals)
    eligible_to_proceed = risk_approved and review_approved and has_execution_eligible
    autonomy = "enabled" if config.autonomy_enabled_for(team_id) else "disabled"

    if routing_split is not None:
        split_counts = _proposal_routing_split_counts_text(routing_split)
    else:
        split_counts = (
            "Proposal routing split: execution_eligible_proposals 0, "
            "simulation_only_proposals 0, rejected_proposals 0."
        )

    return _truncate_discord_message(
        "\n".join(
            [
                f"{team_id} latest team cycle",
                f"Latest proposal: {proposal_text}",
                split_counts,
                f"Latest risk note: {risk_note if risk_note is not None else 'none saved yet'}",
                f"Latest review note: {review_note if review_note is not None else 'none saved yet'}",
                f"Parsed risk approval: {'yes' if risk_approved else 'no'}",
                f"Parsed review approval: {'yes' if review_approved else 'no'}",
                (
                    "stock_long subset eligible to proceed to deterministic Python risk review: "
                    f"{'yes' if eligible_to_proceed else 'no'}"
                ),
                f"Autonomy: {autonomy}",
                f"Paper order submission status: {_latest_paper_order_status(team_id, settings=settings)}",
                "No paper order can be submitted unless both agent approvals and deterministic Python risk approval pass.",
            ]
        )
    )


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


def build_ask_agent_summary(
    team_id: str,
    agent_id: str,
    prompt_text: str,
    *,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    output_dir: Path | str = DEFAULT_AGENT_RESPONSE_DIR,
    runtime_config: HermesRuntimeConfig | None = None,
    asker=ask_hermes_agent,
) -> str:
    registry = load_hermes_team_registry_file(registry_path)
    agent_role = None
    for team in registry.teams:
        if team.team_id == team_id:
            for agent in team.agents:
                if agent.agent_id == agent_id:
                    agent_role = agent.role.value
                    break
    if agent_role is None:
        raise ValueError(f"Unknown agent '{agent_id}' for team '{team_id}'.")

    config = runtime_config or HermesRuntimeConfig.from_env()
    config.validate_ready()
    request = HermesAgentChatRequest(
        team_id=team_id,
        agent_id=agent_id,
        agent_role=agent_role,
        prompt_text=prompt_text,
    )
    output_file = _agent_response_output_file(output_dir, team_id, agent_id)
    result = asker(config, request, output_file)
    return _truncate_discord_message(
        "\n".join(
            [
                f"{agent_id} ({agent_role})",
                result.response_text,
                f"Saved response: {result.output_file}",
                "proposal only; no trades placed",
            ]
        )
    )


def build_natural_team_chat_summary(
    team_id: str,
    author_name: str,
    message_text: str,
    *,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    output_dir: Path | str = DEFAULT_TEAM_CHAT_DIR,
    runtime_config: HermesRuntimeConfig | None = None,
    asker=ask_hermes_agent,
) -> str:
    prompt = message_text.strip()
    if not prompt:
        raise ValueError("message text is required.")

    agents = _active_team_agents(registry_path, team_id)
    config = runtime_config or HermesRuntimeConfig.from_env()
    config.validate_ready()

    lines = [f"{_team_display_name(team_id)} agent team"]
    for agent in agents:
        request = HermesAgentChatRequest(
            team_id=team_id,
            agent_id=agent.agent_id,
            agent_role=agent.role.value,
            prompt_text=_natural_team_chat_prompt(author_name, prompt),
        )
        output_file = _team_chat_output_file(output_dir, team_id, agent.agent_id)
        result = asker(config, request, output_file)
        lines.append(f"{agent.agent_name}: {_single_line_response(result.response_text)}")
    lines.append("Reminder: proposal only; no trades placed")
    return _truncate_discord_message("\n".join(lines))


def build_natural_message_response_for_channel(
    config: DiscordBotConfig,
    channel_id: int | None,
    author_name: str,
    message_text: str,
    *,
    output_dir: Path | str = DEFAULT_TEAM_CHAT_DIR,
    runtime_config: HermesRuntimeConfig | None = None,
    asker=ask_hermes_agent,
) -> str | None:
    if not is_channel_allowed(channel_id, config.allowed_channel_ids):
        return None
    if message_text.strip().startswith("!") or not message_text.strip():
        return None
    team_id = config.team_for_channel(channel_id)
    if team_id is None:
        return None
    return build_natural_team_chat_summary(
        team_id=team_id,
        author_name=author_name,
        message_text=message_text,
        registry_path=config.default_registry_path,
        output_dir=output_dir,
        runtime_config=runtime_config,
        asker=asker,
    )


def build_team_autonomy_status_summary(team_id: str, config: DiscordBotConfig) -> str:
    _validate_known_team_id(team_id)
    autonomy_config = config.autonomy_for(team_id)
    autonomy = "enabled" if autonomy_config.enabled else "disabled"
    channel_id = config.team_channel_ids.get(team_id)
    channel_summary = str(channel_id) if channel_id is not None else "not configured"
    return "\n".join(
        [
            f"{team_id} autonomy status",
            f"Autonomy: {autonomy}",
            f"Mode: {autonomy_config.mode}",
            f"Max paper orders/day: {autonomy_config.max_paper_orders_per_day}",
            f"Max daily notional: ${autonomy_config.max_daily_notional:,.2f}",
            f"Require risk agent approval: {autonomy_config.require_risk_agent_approval}",
            f"Require review agent approval: {autonomy_config.require_review_agent_approval}",
            f"Natural chat channel: {channel_summary}",
            f"Runtime config: {config.autonomy_config_path}",
            "Autonomous paper cycle gate:",
            "- research proposal JSON required",
            f"- risk agent note must include {RISK_APPROVAL_TOKEN}: true",
            f"- review agent note must include {REVIEW_APPROVAL_TOKEN}: true",
            "- deterministic Python risk approval required",
            "- Alpaca paper-only wrapper required",
            "No live trading.",
        ]
    )


def build_enable_autonomy_summary(team_id: str, config: DiscordBotConfig) -> str:
    current = config.autonomy_for(team_id)
    updated = TeamAutonomyConfig(
        team_id=team_id,
        enabled=True,
        mode=current.mode,
        max_paper_orders_per_day=current.max_paper_orders_per_day,
        max_daily_notional=current.max_daily_notional,
        require_risk_agent_approval=current.require_risk_agent_approval,
        require_review_agent_approval=current.require_review_agent_approval,
    )
    path = save_runtime_team_autonomy_config(updated, config.autonomy_config_path)
    return "\n".join(
        [
            f"{team_id} autonomy enabled.",
            f"Mode: {updated.mode}",
            f"Max paper orders/day: {updated.max_paper_orders_per_day}",
            f"Max daily notional: ${updated.max_daily_notional:,.2f}",
            f"Runtime config: {path}",
            "Paper orders still require research proposal JSON, agent approvals, deterministic Python risk approval, and Alpaca paper mode.",
        ]
    )


def build_disable_autonomy_summary(team_id: str, config: DiscordBotConfig) -> str:
    current = config.autonomy_for(team_id)
    updated = TeamAutonomyConfig(
        team_id=team_id,
        enabled=False,
        mode=current.mode,
        max_paper_orders_per_day=current.max_paper_orders_per_day,
        max_daily_notional=current.max_daily_notional,
        require_risk_agent_approval=current.require_risk_agent_approval,
        require_review_agent_approval=current.require_review_agent_approval,
    )
    path = save_runtime_team_autonomy_config(updated, config.autonomy_config_path)
    return "\n".join(
        [
            f"{team_id} autonomy disabled.",
            f"Runtime config: {path}",
            "No autonomous paper orders will be submitted.",
        ]
    )


def build_scheduled_team_update_summary(
    team_id: str,
    config: DiscordBotConfig,
    *,
    output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
) -> str:
    _validate_known_team_id(team_id)
    lines = [
        f"{team_id} scheduled progress update",
        f"Autonomy: {'enabled' if config.autonomy_enabled_for(team_id) else 'disabled'}",
    ]
    try:
        lines.append(f"Latest proposal: {latest_agent_run_path(output_dir=output_dir)}")
    except FileNotFoundError:
        lines.append("Latest proposal: none saved yet")
    lines.extend(
        [
            "Objective: develop paper-only strategies to beat SPY over time.",
            "Next cycle: research proposal, risk review, review-agent critique, Python risk gate.",
            "scheduled update only; no trades placed",
        ]
    )
    return _truncate_discord_message("\n".join(lines))


def build_schedule_reports_status_summary(config: DiscordBotConfig) -> str:
    report_channel = config.special_channel_ids.get("paper_trading_log")
    return "\n".join(
        [
            "Scheduled report scaffold",
            f"Scheduled team updates: {'enabled' if config.scheduled_team_updates_enabled else 'disabled'}",
            f"Interval minutes: {config.scheduled_team_update_minutes:g}",
            f"Paper trading log channel: {report_channel if report_channel is not None else 'not configured'}",
            "Use !daily_team_report_now for a manual report in this phase.",
            "paper only; no live trading",
        ]
    )


def build_daily_team_report_now_summary(
    config: DiscordBotConfig,
    *,
    settings: Settings | None = None,
    client_factory=None,
    output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
) -> str:
    lines = [
        "Daily team paper report",
        "Team Alpha paper status:",
        build_team_paper_status_summary("team_alpha", settings=settings, client_factory=client_factory),
        "Team Alpha positions:",
        build_team_positions_summary("team_alpha", settings=settings, client_factory=client_factory),
        "Team Beta paper status:",
        build_team_paper_status_summary("team_beta", settings=settings, client_factory=client_factory),
        "Team Beta positions:",
        build_team_positions_summary("team_beta", settings=settings, client_factory=client_factory),
        "Latest saved proposals:",
        f"- team_alpha: {_latest_agent_run_for_team_text('team_alpha', output_dir)}",
        f"- team_beta: {_latest_agent_run_for_team_text('team_beta', output_dir)}",
        "Latest routing summary:",
        _latest_routing_summary_text(config.default_registry_path, output_dir),
        "Reminder: paper only, no live trading",
    ]
    return _truncate_discord_message("\n".join(lines))


def build_team_paper_cycle_summary(
    team_id: str,
    prompt_text: str,
    *,
    config: DiscordBotConfig,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    proposal_output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
    notes_output_dir: Path | str = DEFAULT_TEAM_CYCLE_DIR,
    runtime_config: HermesRuntimeConfig | None = None,
    generator=generate_hermes_proposals,
    asker=ask_hermes_agent,
    settings: Settings | None = None,
    client_factory=None,
) -> str:
    prompt = prompt_text.strip() or "Run one paper-cycle research pass for beating SPY over time."
    agents = _team_agents_by_role(registry_path, team_id)
    research_agent = agents.get("research_agent")
    risk_agent = agents.get("risk_agent")
    review_agent = agents.get("review_agent")
    if research_agent is None or risk_agent is None or review_agent is None:
        raise ValueError(f"{team_id} must have active research_agent, risk_agent, and review_agent profiles.")

    runtime = runtime_config or HermesRuntimeConfig.from_env()
    runtime.validate_ready()
    strategy_id = research_agent.latest_strategy_id or _cycle_strategy_id(team_id)
    proposal_file = _ask_team_output_file(proposal_output_dir, strategy_id)
    generation = generator(
        runtime,
        HermesGenerationRequest(
            team_id=team_id,
            agent_id=research_agent.agent_id,
            agent_role=research_agent.role.value,
            strategy_id=strategy_id,
            learning_goal=prompt,
            strategy_notes=(
                "Paper-cycle scaffold: create reviewable proposals for beating SPY. "
                "Strongly prefer 1-3 high-conviction stock_long proposals, since only "
                "stock_long is execution-eligible for paper trading in this phase. "
                "Do not flood the file with short, margin, or options proposals unless the "
                "cycle prompt explicitly asks for that research; those routes stay simulation-only. "
                "Research output is not execution approval."
            ),
        ),
        proposal_file,
    )
    sandbox = generation.sandbox_result
    if sandbox.request is None:
        errors = "; ".join(sandbox.errors) if sandbox.errors else "unknown sandbox validation error"
        return _truncate_discord_message(
            "\n".join(
                [
                    f"{team_id} paper cycle stopped.",
                    f"Saved proposal file: {generation.output_file}",
                    f"Sandbox rejected research proposal: {errors}",
                    "no trades placed",
                ]
            )
        )

    routing_split = _proposal_routing_split(sandbox)
    route_summary = _route_summary_text(sandbox.route_counts())
    split_summary = _proposal_routing_split_summary(routing_split)
    reviewer_checklist = _deterministic_reviewer_checklist(routing_split)
    risk_result = asker(
        runtime,
        HermesAgentChatRequest(
            team_id=team_id,
            agent_id=risk_agent.agent_id,
            agent_role=risk_agent.role.value,
            prompt_text=_cycle_risk_approval_prompt(
                proposal_path=generation.output_file,
                route_summary=route_summary,
                split_summary=split_summary,
                checklist=reviewer_checklist,
                user_prompt=prompt,
            ),
        ),
        _team_cycle_note_output_file(notes_output_dir, team_id, risk_agent.agent_id),
    )
    has_execution_eligible_proposals = bool(routing_split.execution_eligible_proposals)
    risk_approved = (
        _approval_token_is_true(risk_result.response_text, RISK_APPROVAL_TOKEN)
        and has_execution_eligible_proposals
    )
    review_result = asker(
        runtime,
        HermesAgentChatRequest(
            team_id=team_id,
            agent_id=review_agent.agent_id,
            agent_role=review_agent.role.value,
            prompt_text=_cycle_review_approval_prompt(
                proposal_path=generation.output_file,
                risk_note_path=risk_result.output_file,
                risk_approved=risk_approved,
                route_summary=route_summary,
                split_summary=split_summary,
                checklist=reviewer_checklist,
                risk_note_text=risk_result.response_text,
                user_prompt=prompt,
            ),
        ),
        _team_cycle_note_output_file(notes_output_dir, team_id, review_agent.agent_id),
    )
    review_approved = (
        _approval_token_is_true(review_result.response_text, REVIEW_APPROVAL_TOKEN)
        and risk_approved
        and has_execution_eligible_proposals
    )
    autonomy_enabled = config.autonomy_enabled_for(team_id)
    autonomy_config = config.autonomy_for(team_id)

    lines = [
        f"{team_id} paper cycle scaffold",
        f"Saved proposal file: {generation.output_file}",
        route_summary,
        _proposal_routing_split_counts_text(routing_split),
        f"Risk agent approval: {'yes' if risk_approved else 'no'}",
        f"Review agent approval: {'yes' if review_approved else 'no'}",
        f"Risk note saved: {risk_result.output_file}",
        f"Review note saved: {review_result.output_file}",
        (
            "stock_long subset eligible to proceed to deterministic Python risk review: "
            f"{'yes' if (risk_approved and review_approved) else 'no'}"
        ),
        f"Autonomy: {'enabled' if autonomy_enabled else 'disabled'}",
    ]
    if autonomy_enabled and risk_approved and review_approved:
        paper_summary = build_paper_trade_team_summary(
            team_id,
            generation.output_file,
            approval_gate=AgentApprovalGate(
                risk_agent_approved=risk_approved,
                review_agent_approved=review_approved,
                source="run_team_cycle agent approvals",
            ),
            autonomy_config=autonomy_config,
            settings=settings,
            client_factory=client_factory,
        )
        lines.extend(["Deterministic paper gate result:", paper_summary])
    else:
        lines.append("No paper orders submitted.")
    lines.append(
        "No paper order can be submitted unless both agent approvals and deterministic Python risk approval pass."
    )
    lines.append("paper-cycle scaffold only; no live trading")
    return _truncate_discord_message("\n".join(lines))


def build_paper_trade_team_summary(
    team_id: str,
    proposal_path: Path | str,
    *,
    approval_gate: AgentApprovalGate | None = None,
    autonomy_config: TeamAutonomyConfig | None = None,
    settings: Settings | None = None,
    client_factory=None,
) -> str:
    if approval_gate is None:
        return _truncate_discord_message(
            "\n".join(
                [
                    f"{team_id} paper trade unavailable.",
                    "Risk and review agent approvals are required before paper execution.",
                    f"Risk note must include {RISK_APPROVAL_TOKEN}: true.",
                    f"Review note must include {REVIEW_APPROVAL_TOKEN}: true.",
                    "paper only; no trades placed",
                ]
            )
        )
    if not approval_gate.approved:
        return _truncate_discord_message(
            "\n".join(
                [
                    f"{team_id} paper trade unavailable.",
                    f"Risk agent approval: {'yes' if approval_gate.risk_agent_approved else 'no'}",
                    f"Review agent approval: {'yes' if approval_gate.review_agent_approved else 'no'}",
                    f"Approval source: {approval_gate.source}",
                    "paper only; no trades placed",
                ]
            )
        )

    settings = settings or Settings.from_env()
    autonomy_config = autonomy_config or default_team_autonomy_config(team_id)
    initialize_database(settings.database_path)
    try:
        wrapper = _team_alpaca_client(team_id, settings=settings, client_factory=client_factory)
    except Exception as exc:
        return f"{team_id} paper trade unavailable: {exc}"
    account = wrapper.get_account()
    positions = wrapper.get_positions()
    portfolio = _portfolio_from_alpaca(account, positions)
    result = load_hermes_sandbox_file(proposal_path)
    if result.request is None:
        return _truncate_discord_message(
            "\n".join(
                [
                    f"{team_id} paper trade unavailable.",
                    "Proposal file failed sandbox validation.",
                    *(result.errors or ["Unknown sandbox validation error."]),
                    "paper only; no trades placed",
                ]
            )
        )

    run_id = create_run(
        settings.database_path,
        strategy_id=f"{team_id}_paper",
        strategy_name=f"{team_id} Discord paper execution",
        starting_equity=portfolio.equity,
    )
    validator = TradeValidator(
        rules=RiskRules(
            min_cash_pct=settings.min_cash_pct,
            max_position_pct=settings.max_position_pct,
            max_daily_turnover_pct=settings.max_daily_turnover_pct,
            max_new_positions_per_day=settings.max_new_positions_per_day,
        )
    )
    approved_count = 0
    rejected_count = 0
    submitted_count = 0
    rejected_reasons: list[str] = []
    daily_usage = _paper_usage_today(settings.database_path, team_id)
    try:
        insert_portfolio_snapshot(
            settings.database_path,
            portfolio_snapshot := _portfolio_snapshot(team_id, portfolio),
            run_id=run_id,
        )
        for routed in result.routed_proposals:
            proposal = _trade_proposal_for_logging(result.request.strategy_id, routed)
            if proposal is None:
                rejected_count += 1
                rejected_reasons.extend(routed.errors or [f"Rejected: unsupported proposal {routed.proposal_type}."])
                continue

            if routed.route == PAPER_ELIGIBLE_STOCK_LONG and isinstance(routed.mapped_proposal, TradeProposal):
                decision = validator.validate(routed.mapped_proposal, portfolio)
                insert_trade_proposal(settings.database_path, routed.mapped_proposal, run_id=run_id)
                insert_risk_decision(settings.database_path, decision, run_id=run_id)
                if decision.approved and decision.approved_quantity is not None:
                    cap_reason = _paper_cap_rejection_reason(
                        autonomy_config=autonomy_config,
                        daily_usage=daily_usage,
                        next_order_notional=decision.estimated_trade_value,
                    )
                    if cap_reason is not None:
                        rejected_count += 1
                        rejected_reasons.append(f"{routed.mapped_proposal.symbol}: {cap_reason}")
                        continue
                    approved_count += 1
                    order = _order_from_decision(routed.mapped_proposal, decision)
                    try:
                        wrapper.submit_paper_order(order)
                        insert_order(settings.database_path, order, submitted=True, run_id=run_id)
                        submitted_count += 1
                        daily_usage = PaperDailyUsage(
                            submitted_order_count=daily_usage.submitted_order_count + 1,
                            submitted_notional=daily_usage.submitted_notional + decision.estimated_trade_value,
                        )
                    except Exception as exc:
                        insert_order(settings.database_path, order, submitted=False, run_id=run_id)
                        rejected_reasons.append(f"{routed.mapped_proposal.symbol}: paper submit failed: {exc}")
                else:
                    rejected_count += 1
                    rejected_reasons.extend(f"{routed.mapped_proposal.symbol}: {reason}" for reason in decision.reasons)
                continue

            reason = _execution_disabled_reason(routed.proposal_type)
            decision = RiskDecision(
                proposal_id=proposal.proposal_id,
                approved=False,
                reasons=[reason],
                approved_quantity=None,
                estimated_trade_value=0.0,
            )
            insert_trade_proposal(settings.database_path, proposal, run_id=run_id)
            insert_risk_decision(settings.database_path, decision, run_id=run_id)
            rejected_count += 1
            rejected_reasons.append(f"{proposal.symbol}: {reason}")
        complete_run(settings.database_path, run_id)
    except Exception:
        complete_run(settings.database_path, run_id, status="failed")
        raise

    lines = [
        f"{team_id} paper trade summary",
        f"Proposal file: {proposal_path}",
        f"Run ID: {run_id}",
        f"Approved count: {approved_count}",
        f"Rejected count: {rejected_count}",
        f"Submitted paper order count: {submitted_count}",
        (
            "Daily caps: "
            f"{autonomy_config.max_paper_orders_per_day} order(s), "
            f"${autonomy_config.max_daily_notional:,.2f} notional"
        ),
        "Rejected reasons:",
    ]
    lines.extend(f"- {reason}" for reason in rejected_reasons[:10])
    if not rejected_reasons:
        lines.append("- none")
    lines.append(f"Approval source: {approval_gate.source}")
    lines.append("paper only; explicit or autonomous gated path; no live trading")
    return _truncate_discord_message("\n".join(lines))


def build_agent_approval_gate_from_files(
    risk_approval_path: Path | str,
    review_approval_path: Path | str,
) -> AgentApprovalGate:
    risk_path = Path(risk_approval_path)
    review_path = Path(review_approval_path)
    risk_text = risk_path.read_text(encoding="utf-8")
    review_text = review_path.read_text(encoding="utf-8")
    return AgentApprovalGate(
        risk_agent_approved=_approval_token_is_true(risk_text, RISK_APPROVAL_TOKEN),
        review_agent_approved=_approval_token_is_true(review_text, REVIEW_APPROVAL_TOKEN),
        source=f"{risk_path} + {review_path}",
    )


def build_team_report_summary(team_id: str, *, settings: Settings | None = None) -> str:
    settings = settings or Settings.from_env()
    initialize_database(settings.database_path)
    strategy_id = f"{team_id}_paper"
    run_id = _latest_run_for_strategy(settings.database_path, strategy_id)
    if run_id is None:
        return (
            f"No local paper report data for {team_id} yet. "
            f"Run !paper_trade_team {team_id} <proposal_path> <risk_note_path> <review_note_path> first, "
            "then add benchmark snapshots for SPY."
        )
    report_result = generate_daily_report(settings.database_path, strategy_id=strategy_id, run_id=run_id)
    if not report_result.ok or report_result.report is None:
        return (
            f"Team report unavailable for {team_id}: {report_result.message}. "
            "Need portfolio snapshots and SPY benchmark snapshots before comparison."
        )
    return format_report(report_result.report)


def run_discord_bot(config: DiscordBotConfig | None = None) -> None:
    config = config or DiscordBotConfig.from_env()
    if not config.token:
        print(f"Discord bot unavailable: {TOKEN_ENV} is required.", file=sys.stderr)
        raise SystemExit(1)

    try:
        import discord
        from discord import app_commands
        from discord.ext import commands
        from discord.ext import tasks
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

    @tasks.loop(minutes=config.scheduled_team_update_minutes)
    async def scheduled_team_updates() -> None:
        if not config.scheduled_team_updates_enabled:
            return
        for team_id, channel_id in config.team_channel_ids.items():
            channel = bot.get_channel(channel_id)
            if channel is None or not is_channel_allowed(channel_id, config.allowed_channel_ids):
                continue
            await channel.send(_safe_command(lambda team_id=team_id: build_scheduled_team_update_summary(team_id, config)))

    @bot.event
    async def on_message(message) -> None:
        if getattr(message.author, "bot", False):
            return
        await bot.process_commands(message)
        content = getattr(message, "content", "")
        if content.strip().startswith("!"):
            return
        channel_id = getattr(message.channel, "id", None)
        author_name = getattr(message.author, "display_name", None) or getattr(message.author, "name", "Discord user")
        response = _safe_command(
            lambda: build_natural_message_response_for_channel(
                config=config,
                channel_id=channel_id,
                author_name=author_name,
                message_text=content,
            )
        )
        if response is not None:
            await message.channel.send(response)

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
        if config.scheduled_team_updates_enabled and not scheduled_team_updates.is_running():
            scheduled_team_updates.start()
        print(f"Discord bot ready as {bot.user}. Safe lab mode only.")

    @bot.command(name="status")
    async def prefix_status(ctx) -> None:
        await send_prefix_response(ctx, build_status_summary(config))

    @bot.command(name="teams")
    async def prefix_teams(ctx) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_teams_summary(config.default_registry_path)))

    @bot.command(name="team_paper_status")
    async def prefix_team_paper_status(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_team_paper_status_summary(team_id)))

    @bot.command(name="team_positions")
    async def prefix_team_positions(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_team_positions_summary(team_id)))

    @bot.command(name="review_proposals")
    async def prefix_review_proposals(ctx, file_path: str | None = None) -> None:
        path = Path(file_path) if file_path else config.default_proposal_path
        await send_prefix_response(ctx, _safe_command(lambda: build_review_proposals_summary(path)))

    @bot.command(name="run_tournament")
    async def prefix_run_tournament(ctx, registry: str | None = None, proposal: str | None = None) -> None:
        if registry == "latest" and proposal is None:
            registry_path = config.default_registry_path
            proposal_path = Path("latest")
        else:
            registry_path = Path(registry) if registry else config.default_registry_path
            proposal_path = Path(proposal) if proposal else config.default_proposal_path
        await send_prefix_response(
            ctx,
            _safe_command(lambda: build_run_tournament_summary(registry_path, [proposal_path])),
        )

    @bot.command(name="latest_agent_run")
    async def prefix_latest_agent_run(ctx) -> None:
        await send_prefix_response(ctx, _safe_command(build_latest_agent_run_summary))

    @bot.command(name="latest_team_cycle")
    async def prefix_latest_team_cycle(ctx, team_id: str) -> None:
        await send_prefix_response(
            ctx,
            _safe_command(
                lambda: build_latest_team_cycle_summary(
                    team_id,
                    config,
                    registry_path=config.default_registry_path,
                )
            ),
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

    @bot.command(name="ask_agent")
    async def prefix_ask_agent(ctx, team_id: str, agent_id: str, *, prompt_text: str) -> None:
        await send_prefix_response(
            ctx,
            _safe_command(
                lambda: build_ask_agent_summary(
                    team_id=team_id,
                    agent_id=agent_id,
                    prompt_text=prompt_text,
                    registry_path=config.default_registry_path,
                )
            ),
        )

    @bot.command(name="paper_trade_team")
    async def prefix_paper_trade_team(
        ctx,
        team_id: str,
        proposal_path: str,
        risk_approval_path: str | None = None,
        review_approval_path: str | None = None,
    ) -> None:
        await send_prefix_response(
            ctx,
            _safe_command(
                lambda: build_paper_trade_team_summary(
                    team_id,
                    proposal_path,
                    approval_gate=_approval_gate_from_optional_paths(risk_approval_path, review_approval_path),
                    autonomy_config=config.autonomy_for(team_id),
                )
            ),
        )

    @bot.command(name="team_report")
    async def prefix_team_report(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_team_report_summary(team_id)))

    @bot.command(name="team_autonomy_status")
    async def prefix_team_autonomy_status(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_team_autonomy_status_summary(team_id, config)))

    @bot.command(name="autonomy_status")
    async def prefix_autonomy_status(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_team_autonomy_status_summary(team_id, config)))

    @bot.command(name="enable_autonomy")
    async def prefix_enable_autonomy(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_enable_autonomy_summary(team_id, config)))

    @bot.command(name="disable_autonomy")
    async def prefix_disable_autonomy(ctx, team_id: str) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_disable_autonomy_summary(team_id, config)))

    @bot.command(name="schedule_reports_status")
    async def prefix_schedule_reports_status(ctx) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_schedule_reports_status_summary(config)))

    @bot.command(name="daily_team_report_now")
    async def prefix_daily_team_report_now(ctx) -> None:
        await send_prefix_response(ctx, _safe_command(lambda: build_daily_team_report_now_summary(config)))

    @bot.command(name="run_team_cycle")
    async def prefix_run_team_cycle(ctx, team_id: str, *, prompt_text: str = "") -> None:
        await send_prefix_response(
            ctx,
            _safe_command(
                lambda: build_team_paper_cycle_summary(
                    team_id=team_id,
                    prompt_text=prompt_text,
                    config=config,
                    registry_path=config.default_registry_path,
                )
            ),
        )

    @bot.tree.command(name="status", description="Show safe lab Discord bot status.")
    async def slash_status(interaction) -> None:
        await send_slash_response(interaction, build_status_summary(config))

    @bot.tree.command(name="teams", description="Summarize the configured Hermes team registry.")
    async def slash_teams(interaction) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_teams_summary(config.default_registry_path)))

    @bot.tree.command(name="team_paper_status", description="Show a team's Alpaca paper account status.")
    async def slash_team_paper_status(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_team_paper_status_summary(team_id)))

    @bot.tree.command(name="team_positions", description="Show a team's Alpaca paper positions.")
    async def slash_team_positions(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_team_positions_summary(team_id)))

    @bot.tree.command(name="review_proposals", description="Review a local Hermes proposal JSON file.")
    @app_commands.describe(file_path="Optional local proposal JSON path.")
    async def slash_review_proposals(interaction, file_path: str | None = None) -> None:
        path = Path(file_path) if file_path else config.default_proposal_path
        await send_slash_response(interaction, _safe_command(lambda: build_review_proposals_summary(path)))

    @bot.tree.command(name="run_tournament", description="Run a local Hermes routing-score tournament.")
    @app_commands.describe(registry="Optional registry JSON path.", proposal="Optional proposal JSON path.")
    async def slash_run_tournament(interaction, registry: str | None = None, proposal: str | None = None) -> None:
        if registry == "latest" and proposal is None:
            registry_path = config.default_registry_path
            proposal_path = Path("latest")
        else:
            registry_path = Path(registry) if registry else config.default_registry_path
            proposal_path = Path(proposal) if proposal else config.default_proposal_path
        await send_slash_response(
            interaction,
            _safe_command(lambda: build_run_tournament_summary(registry_path, [proposal_path])),
        )

    @bot.tree.command(name="latest_agent_run", description="Show the most recent saved proposal JSON path.")
    async def slash_latest_agent_run(interaction) -> None:
        await send_slash_response(interaction, _safe_command(build_latest_agent_run_summary))

    @bot.tree.command(name="latest_team_cycle", description="Show latest team-cycle proposal, notes, approvals, and order status.")
    async def slash_latest_team_cycle(interaction, team_id: str) -> None:
        await send_slash_response(
            interaction,
            _safe_command(
                lambda: build_latest_team_cycle_summary(
                    team_id,
                    config,
                    registry_path=config.default_registry_path,
                )
            ),
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

    @bot.tree.command(name="ask_agent", description="Ask one Hermes agent for a concise paper-only response.")
    async def slash_ask_agent(interaction, team_id: str, agent_id: str, prompt_text: str) -> None:
        await send_slash_response(
            interaction,
            _safe_command(
                lambda: build_ask_agent_summary(
                    team_id=team_id,
                    agent_id=agent_id,
                    prompt_text=prompt_text,
                    registry_path=config.default_registry_path,
                )
            ),
        )

    @bot.tree.command(name="paper_trade_team", description="Explicitly submit approved stock-long paper orders.")
    async def slash_paper_trade_team(
        interaction,
        team_id: str,
        proposal_path: str,
        risk_approval_path: str | None = None,
        review_approval_path: str | None = None,
    ) -> None:
        await send_slash_response(
            interaction,
            _safe_command(
                lambda: build_paper_trade_team_summary(
                    team_id,
                    proposal_path,
                    approval_gate=_approval_gate_from_optional_paths(risk_approval_path, review_approval_path),
                    autonomy_config=config.autonomy_for(team_id),
                )
            ),
        )

    @bot.tree.command(name="team_report", description="Report a team's paper equity versus SPY if data exists.")
    async def slash_team_report(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_team_report_summary(team_id)))

    @bot.tree.command(name="team_autonomy_status", description="Show a team's autonomous paper-cycle gates.")
    async def slash_team_autonomy_status(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_team_autonomy_status_summary(team_id, config)))

    @bot.tree.command(name="autonomy_status", description="Show a team's autonomous paper-cycle gates.")
    async def slash_autonomy_status(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_team_autonomy_status_summary(team_id, config)))

    @bot.tree.command(name="enable_autonomy", description="Enable local autonomous paper-cycle scaffolding for a team.")
    async def slash_enable_autonomy(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_enable_autonomy_summary(team_id, config)))

    @bot.tree.command(name="disable_autonomy", description="Disable local autonomous paper-cycle scaffolding for a team.")
    async def slash_disable_autonomy(interaction, team_id: str) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_disable_autonomy_summary(team_id, config)))

    @bot.tree.command(name="schedule_reports_status", description="Show the manual scheduled-report scaffold status.")
    async def slash_schedule_reports_status(interaction) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_schedule_reports_status_summary(config)))

    @bot.tree.command(name="daily_team_report_now", description="Build a manual daily team paper report.")
    async def slash_daily_team_report_now(interaction) -> None:
        await send_slash_response(interaction, _safe_command(lambda: build_daily_team_report_now_summary(config)))

    @bot.tree.command(name="run_team_cycle", description="Run a gated team paper-cycle scaffold.")
    async def slash_run_team_cycle(interaction, team_id: str, prompt_text: str = "") -> None:
        await send_slash_response(
            interaction,
            _safe_command(
                lambda: build_team_paper_cycle_summary(
                    team_id=team_id,
                    prompt_text=prompt_text,
                    config=config,
                    registry_path=config.default_registry_path,
                )
            ),
        )

    bot.run(config.token)


def _safe_command(command) -> str:
    try:
        return command()
    except Exception as exc:  # Discord command responses should fail closed and stay concise.
        return f"Command unavailable: {exc}"


def _team_alpaca_client(
    team_id: str,
    *,
    settings: Settings | None = None,
    client_factory=None,
) -> AlpacaClientWrapper:
    config = load_team_alpaca_paper_config(team_id)
    return AlpacaClientWrapper(settings=config.to_settings(settings), client_factory=client_factory)


def _approval_gate_from_optional_paths(
    risk_approval_path: str | None,
    review_approval_path: str | None,
) -> AgentApprovalGate:
    if not risk_approval_path or not review_approval_path:
        raise ValueError("risk_approval_path and review_approval_path are required.")
    return build_agent_approval_gate_from_files(risk_approval_path, review_approval_path)


def _validate_known_team_id(team_id: str) -> None:
    if team_id not in TEAM_ALPACA_ENV_PREFIXES:
        raise ValueError(f"Unknown team_id: {team_id}.")


def _active_team_agents(registry_path: Path | str, team_id: str):
    registry = load_hermes_team_registry_file(registry_path)
    for team in registry.teams:
        if team.team_id == team_id:
            return [agent for agent in team.agents if agent.active]
    raise ValueError(f"Unknown team_id: {team_id}.")


def _team_agents_by_role(registry_path: Path | str, team_id: str):
    return {agent.role.value: agent for agent in _active_team_agents(registry_path, team_id)}


def _natural_team_chat_prompt(author_name: str, message_text: str) -> str:
    return "\n".join(
        [
            f"{author_name} posted in the team's natural Discord channel:",
            message_text,
            "",
            "Respond as your assigned agent role in a concise team-chat style.",
            "You may suggest research, risk checks, or review next steps.",
            "Do not claim execution approval or order placement.",
            "If you mention trades, keep them proposal-only.",
        ]
    )


def _cycle_risk_approval_prompt(
    proposal_path: Path | str,
    route_summary: str,
    split_summary: str,
    checklist: str,
    user_prompt: str,
) -> str:
    return "\n".join(
        [
            "Risk-review this paper-cycle research output for the team.",
            f"Proposal file: {proposal_path}",
            route_summary,
            split_summary,
            "",
            checklist,
            "",
            f"Original cycle prompt: {user_prompt}",
            "",
            "Phase 7G.2 execution rule:",
            "- only execution_eligible_proposals may proceed toward paper execution",
            "- only stock_long proposals are execution-eligible in this phase",
            "- short, margin, options, and other non-stock-long ideas are simulation/research-only in this phase",
            "- you may approve the stock_long execution-eligible subset while explicitly rejecting simulation-only proposals from execution",
            "",
            "Phase 7G.3 reviewer guardrails:",
            "- Review only the execution-eligible stock_long subset.",
            "- Do not reject because simulation-only or rejected proposals exist.",
            "- The deterministic reviewer checklist above is computed by Python and is authoritative for field presence.",
            "- Do not say thesis is missing when the checklist says thesis present: yes.",
            "- Do not say confidence is missing when the checklist says confidence present: yes.",
            "- Approve only if at least one execution-eligible stock_long proposal has the required fields and no obviously invalid values.",
            "- This approval is not execution; deterministic Python risk still decides final order approval.",
            "",
            "Approval means: the stock_long execution-eligible subset may proceed to review; non-stock-long ideas are not approved for execution.",
            "Approve only if all of these are true for execution_eligible_proposals:",
            "- at least one stock_long proposal passed sandbox routing into the paper route",
            "- every execution-eligible proposal is paper-only stock_long",
            "- symbol exists",
            "- target_weight or quantity exists",
            "- thesis exists",
            "- confidence exists",
            "- no live-trading language",
            "- no direct execution claims",
            "- market-price/risk-engine check can still happen later",
            "- subset appears reasonable enough to pass to review",
            "",
            "Do not reject the executable stock_long subset merely because simulation_only_proposals or rejected_proposals exist.",
            "",
            "If not approved, include a short reason.",
            "End your response with exactly one of:",
            f"{RISK_APPROVAL_TOKEN}: true",
            f"{RISK_APPROVAL_TOKEN}: false",
            "Your token is agent handoff approval only; deterministic Python risk remains the hard gate.",
            "No live trading.",
        ]
    )


def _cycle_review_approval_prompt(
    proposal_path: Path | str,
    risk_note_path: Path | str,
    risk_approved: bool,
    route_summary: str,
    split_summary: str,
    checklist: str,
    risk_note_text: str,
    user_prompt: str,
) -> str:
    return "\n".join(
        [
            "Final-review this paper-cycle research output for the team.",
            f"Proposal file: {proposal_path}",
            f"Risk note file: {risk_note_path}",
            f"Parsed risk agent approval: {'yes' if risk_approved else 'no'}",
            route_summary,
            split_summary,
            "",
            checklist,
            "",
            "Risk note text:",
            _short_text(risk_note_text, 1200) if risk_note_text.strip() else "(risk note text unavailable)",
            "",
            f"Original cycle prompt: {user_prompt}",
            "",
            "Phase 7G.2 execution rule:",
            "- review only execution_eligible_proposals for paper execution",
            "- short, margin, options, and other non-stock-long ideas remain non-executing",
            "- do not reject the whole cycle just because simulation_only_proposals or rejected_proposals exist",
            "- reject if there is no valid stock_long execution-eligible proposal",
            "",
            "Phase 7G.3 reviewer guardrails:",
            "- The deterministic reviewer checklist above is computed by Python and is authoritative for field presence.",
            "- Do not invent missing fields; if the checklist says thesis present: yes or confidence present: yes, treat them as present.",
            "- Review approval requires parsed risk agent approval yes.",
            "- This approval is not execution; deterministic Python risk still decides final order approval.",
            "",
            "Approval means: the stock_long execution-eligible subset may proceed to deterministic Python risk review; short/margin/options remain non-executing.",
            "Approve only if all of these are true:",
            "- research proposal exists",
            f"- risk agent gave {RISK_APPROVAL_TOKEN}: true",
            "- at least one execution_eligible_proposals item exists",
            "- each execution_eligible_proposals item has symbol",
            "- each execution_eligible_proposals item has target_weight",
            "- each execution_eligible_proposals item has thesis",
            "- each execution_eligible_proposals item has confidence",
            "- stock_long subset has paper-only framing",
            "- stock_long subset has no live-trading language",
            "- stock_long subset has no direct execution claims",
            "- final plan is clear enough for deterministic Python risk review",
            "",
            "If not approved, include a short reason.",
            "End your response with exactly one of:",
            f"{REVIEW_APPROVAL_TOKEN}: true",
            f"{REVIEW_APPROVAL_TOKEN}: false",
            "Your token is agent handoff approval only; deterministic Python risk remains the hard gate.",
            "No live trading.",
        ]
    )


def parse_agent_approval_token(text: str, token: str) -> bool | None:
    """Parse an exact approval token from agent note text.

    Requires the exact label (e.g. ``REVIEW_AGENT_APPROVED``) followed by ``true`` or
    ``false``. The token may appear on any line and may be surrounded by markdown,
    whitespace, or other prose (LLMs sometimes lead with the verdict, then explain). The
    last matching ``true``/``false`` verdict wins. Returns ``None`` when no exact token is
    present so vague approvals are never treated as approval.
    """

    pattern = re.compile(rf"{re.escape(token)}\s*:\s*(true|false)\b", flags=re.IGNORECASE)
    verdict: bool | None = None
    for line in text.splitlines():
        match = pattern.search(line)
        if match is not None:
            verdict = match.group(1).lower() == "true"
    return verdict


def _approval_token_is_true(text: str, token: str) -> bool:
    return parse_agent_approval_token(text, token) is True


def _approval_file_is_true(path: Path | None, token: str) -> bool:
    if path is None:
        return False
    try:
        return _approval_token_is_true(path.read_text(encoding="utf-8"), token)
    except OSError:
        return False


def _latest_team_cycle_note_path(output_dir: Path | str, team_id: str, agent_id: str) -> Path | None:
    safe_team_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", team_id.strip()) or "team"
    safe_agent_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_id.strip()) or "agent"
    paths = list((Path(output_dir) / safe_team_id).glob(f"{safe_agent_id}_*.md"))
    if not paths:
        return None
    return max(paths, key=lambda path: path.stat().st_mtime)


def _latest_paper_order_status(team_id: str, *, settings: Settings | None = None) -> str:
    settings = settings or Settings.from_env()
    database_path = Path(settings.database_path)
    if not database_path.exists():
        return "none recorded"
    try:
        run_id = _latest_run_for_strategy(database_path, f"{team_id}_paper")
        if run_id is None:
            return "none recorded"
        with get_connection(database_path) as conn:
            row = conn.execute(
                '''
                SELECT
                    COUNT(*) AS order_count,
                    COALESCE(SUM(CASE WHEN submitted = 1 THEN 1 ELSE 0 END), 0) AS submitted_count
                FROM orders
                WHERE run_id = ?
                ''',
                (run_id,),
            ).fetchone()
    except Exception:
        return "unavailable"
    order_count = int(row["order_count"] or 0) if row is not None else 0
    submitted_count = int(row["submitted_count"] or 0) if row is not None else 0
    if order_count == 0:
        return f"latest run {run_id}: no paper orders recorded"
    return f"latest run {run_id}: submitted {submitted_count}/{order_count} paper order(s)"


def _route_summary_text(counts: Mapping[str, int]) -> str:
    return (
        f"Routes: paper {counts[PAPER_ELIGIBLE_STOCK_LONG]}, "
        f"short sim {counts[SIMULATION_ONLY_SHORT]}, "
        f"option sim {counts[SIMULATION_ONLY_OPTION]}, "
        f"margin sim {counts[SIMULATION_ONLY_MARGIN]}, "
        f"rejected {counts[REJECTED]}."
    )


def _proposal_routing_split(result: HermesSandboxResult) -> ProposalRoutingSplit:
    execution_eligible = []
    simulation_only = []
    rejected = []
    for routed in result.routed_proposals:
        if routed.route == PAPER_ELIGIBLE_STOCK_LONG:
            execution_eligible.append(routed)
        elif routed.route in {SIMULATION_ONLY_SHORT, SIMULATION_ONLY_OPTION, SIMULATION_ONLY_MARGIN}:
            simulation_only.append(routed)
        else:
            rejected.append(routed)
    return ProposalRoutingSplit(
        execution_eligible_proposals=tuple(execution_eligible),
        simulation_only_proposals=tuple(simulation_only),
        rejected_proposals=tuple(rejected),
    )


def _proposal_routing_split_counts_text(split: ProposalRoutingSplit) -> str:
    return (
        "Proposal routing split: "
        f"execution_eligible_proposals {len(split.execution_eligible_proposals)}, "
        f"simulation_only_proposals {len(split.simulation_only_proposals)}, "
        f"rejected_proposals {len(split.rejected_proposals)}."
    )


def _deterministic_reviewer_checklist(split: ProposalRoutingSplit) -> str:
    lines = [
        "Deterministic reviewer checklist (computed by Python sandbox; treat these facts as ground truth):",
        f"- execution-eligible stock_long count: {len(split.execution_eligible_proposals)}",
        f"- simulation-only count: {len(split.simulation_only_proposals)}",
        f"- rejected count: {len(split.rejected_proposals)}",
    ]
    if not split.execution_eligible_proposals:
        lines.append("- no execution-eligible stock_long proposals were found by the sandbox")
        return "\n".join(lines)
    lines.append("Execution-eligible stock_long subset facts:")
    for routed in split.execution_eligible_proposals:
        mapped = routed.mapped_proposal
        symbol = getattr(mapped, "symbol", None)
        target_weight = getattr(mapped, "target_weight", None)
        estimated_price = getattr(mapped, "estimated_price", None)
        thesis_text = getattr(mapped, "thesis", None)
        confidence = getattr(mapped, "confidence", None)
        thesis_present = "yes" if (isinstance(thesis_text, str) and thesis_text.strip()) else "no"
        confidence_present = "yes" if confidence is not None else "no"
        is_stock_long = "yes" if routed.proposal_type == "stock_long" else "no"
        route_is_paper = "yes" if routed.route == PAPER_ELIGIBLE_STOCK_LONG else "no"
        lines.append(
            f"- proposals.{routed.proposal_index}: symbol={symbol}; "
            f"target_weight={target_weight}; estimated_price={estimated_price}; "
            f"thesis present: {thesis_present}; confidence present: {confidence_present}; "
            f"proposal_type is stock_long: {is_stock_long}; sandbox route is paper: {route_is_paper}"
        )
    return "\n".join(lines)


def _proposal_routing_split_summary(split: ProposalRoutingSplit) -> str:
    lines = [
        "Proposal routing split for Phase 7G.2:",
        "execution_eligible_proposals:",
        *_formatted_routed_proposals(split.execution_eligible_proposals),
        "simulation_only_proposals:",
        *_formatted_routed_proposals(split.simulation_only_proposals),
        "rejected_proposals:",
        *_formatted_routed_proposals(split.rejected_proposals),
    ]
    return "\n".join(lines)


def _formatted_routed_proposals(proposals: Sequence[RoutedHermesProposal], limit: int = 8) -> list[str]:
    if not proposals:
        return ["- none"]
    lines = [_formatted_routed_proposal(proposal) for proposal in proposals[:limit]]
    remaining_count = len(proposals) - limit
    if remaining_count > 0:
        lines.append(f"- ... {remaining_count} additional proposal(s) omitted")
    return lines


def _formatted_routed_proposal(proposal: RoutedHermesProposal) -> str:
    mapped = proposal.mapped_proposal
    prefix = f"- proposals.{proposal.proposal_index} {proposal.proposal_type} route={proposal.route}"
    if proposal.errors:
        return f"{prefix}; errors={_short_text('; '.join(proposal.errors), 220)}"
    if isinstance(mapped, TradeProposal):
        sizing = (
            f"target_weight={mapped.target_weight}"
            if mapped.target_weight is not None
            else f"quantity={mapped.quantity}"
        )
        warning_detail = f"; warnings={_short_text('; '.join(proposal.warnings), 160)}" if proposal.warnings else ""
        return (
            f"{prefix}; symbol={mapped.symbol}; {sizing}; "
            f"confidence={mapped.confidence}; thesis={_short_text(mapped.thesis, 220)}{warning_detail}"
        )

    symbol = getattr(mapped, "symbol", None)
    if symbol is None:
        symbol = getattr(mapped, "underlying_symbol", None)
    if symbol is None and hasattr(mapped, "contract"):
        symbol = getattr(mapped.contract, "underlying_symbol", None)

    detail_parts = []
    if symbol is not None:
        detail_parts.append(f"symbol={symbol}")
    thesis = getattr(mapped, "thesis", None)
    if thesis is not None:
        detail_parts.append(f"thesis={_short_text(str(thesis), 160)}")
    if proposal.warnings:
        detail_parts.append(f"warnings={_short_text('; '.join(proposal.warnings), 160)}")
    if detail_parts:
        return f"{prefix}; {'; '.join(detail_parts)}"
    return prefix


def _short_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _latest_agent_run_for_team_text(team_id: str, output_dir: Path | str) -> str:
    try:
        return str(latest_agent_run_path_for_team(team_id, output_dir=output_dir))
    except FileNotFoundError:
        return "none saved yet"


def _latest_routing_summary_text(registry_path: Path | str, output_dir: Path | str) -> str:
    proposal_paths: list[Path] = []
    for team_id in TEAM_ALPACA_ENV_PREFIXES:
        try:
            proposal_paths.append(latest_agent_run_path_for_team(team_id, output_dir=output_dir))
        except FileNotFoundError:
            continue
    if not proposal_paths:
        return "No saved team proposals available for routing summary."
    return build_run_tournament_summary(registry_path, proposal_paths)


def _team_display_name(team_id: str) -> str:
    if team_id == "team_alpha":
        return "Team Alpha"
    if team_id == "team_beta":
        return "Team Beta"
    return team_id


def _single_line_response(response_text: str) -> str:
    lines = [line.strip() for line in response_text.splitlines() if line.strip()]
    return " ".join(lines) if lines else "No response."


def _cycle_strategy_id(team_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{team_id}_paper_cycle_{timestamp}"


def _portfolio_from_alpaca(account: object, positions: Sequence[object]) -> PortfolioState:
    return PortfolioState(
        equity=_float_value(_read_value(account, "equity"), default=0.0),
        cash=_float_value(_read_value(account, "cash"), default=0.0),
        positions={
            str(_read_value(position, "symbol")).upper(): Position(
                symbol=str(_read_value(position, "symbol")).upper(),
                quantity=_float_value(_read_value(position, "qty", _read_value(position, "quantity")), default=0.0),
                market_value=_float_value(_read_value(position, "market_value"), default=0.0),
                average_entry_price=_optional_float(_read_value(position, "avg_entry_price")),
            )
            for position in positions
            if _read_value(position, "symbol") not in (None, "unknown")
        },
        timestamp=datetime.now(timezone.utc),
    )


def _portfolio_snapshot(team_id: str, portfolio: PortfolioState) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        strategy_id=f"{team_id}_paper",
        equity=max(portfolio.equity, 0.01),
        cash=max(portfolio.cash, 0.0),
        timestamp=portfolio.timestamp,
    )


def _order_from_decision(proposal: TradeProposal, decision: RiskDecision) -> OrderRequest:
    if decision.approved_quantity is None or decision.approved_quantity <= 0:
        raise ValueError("Approved risk decision must include a positive approved quantity.")
    return OrderRequest(
        proposal_id=proposal.proposal_id,
        symbol=proposal.symbol,
        action=proposal.action,
        asset_class=proposal.asset_class,
        quantity=decision.approved_quantity,
        dry_run=False,
        risk_approved=True,
    )


def _trade_proposal_for_logging(strategy_id: str, routed) -> TradeProposal | None:
    if routed.errors:
        return None
    mapped = routed.mapped_proposal
    if isinstance(mapped, TradeProposal):
        return mapped
    symbol = getattr(mapped, "symbol", None) or getattr(mapped, "underlying_symbol", None)
    if symbol is None:
        return None
    proposal_type = str(routed.proposal_type)
    action = TradeAction.SELL if "short" in proposal_type or proposal_type in {"covered_call", "cash_secured_put"} else TradeAction.BUY
    asset_class = AssetClass.OPTION if "option" in proposal_type or proposal_type in {"covered_call", "cash_secured_put"} else AssetClass.STOCK
    estimated_price = (
        getattr(mapped, "estimated_price", None)
        or getattr(mapped, "max_premium", None)
        or getattr(mapped, "requested_gross_exposure", None)
        or 1.0
    )
    return TradeProposal(
        strategy_id=strategy_id,
        symbol=str(symbol).upper(),
        action=action,
        asset_class=asset_class,
        quantity=getattr(mapped, "contracts", None),
        estimated_price=float(estimated_price),
        thesis=getattr(mapped, "thesis", f"{proposal_type} rejected before execution."),
        confidence=float(getattr(mapped, "confidence", 0.0)),
    )


def _execution_disabled_reason(proposal_type: str) -> str:
    if "option" in proposal_type or proposal_type in {"covered_call", "cash_secured_put"}:
        return "Rejected: paper options execution not enabled yet."
    if "margin" in proposal_type:
        return "Rejected: paper margin execution not enabled yet."
    if "short" in proposal_type:
        return "Rejected: paper short execution not enabled yet."
    return "Rejected: proposal type is not executable through Discord paper trading."


def _paper_usage_today(database_path: Path | str, team_id: str) -> PaperDailyUsage:
    today = datetime.now(timezone.utc).date().isoformat()
    with get_connection(database_path) as conn:
        row = conn.execute(
            '''
            SELECT
                COUNT(o.rowid) AS submitted_order_count,
                COALESCE(SUM(d.estimated_trade_value), 0) AS submitted_notional
            FROM orders o
            JOIN runs r ON r.id = o.run_id
            LEFT JOIN risk_decisions d
                ON d.run_id = o.run_id
                AND d.proposal_id = o.proposal_id
            WHERE r.strategy_id = ?
              AND o.submitted = 1
              AND substr(o.created_at, 1, 10) = ?
            ''',
            (f"{team_id}_paper", today),
        ).fetchone()
    if row is None:
        return PaperDailyUsage()
    return PaperDailyUsage(
        submitted_order_count=int(row["submitted_order_count"] or 0),
        submitted_notional=float(row["submitted_notional"] or 0.0),
    )


def _paper_cap_rejection_reason(
    *,
    autonomy_config: TeamAutonomyConfig,
    daily_usage: PaperDailyUsage,
    next_order_notional: float,
) -> str | None:
    if not autonomy_config.stock_paper_only:
        return f"Rejected: autonomy mode {autonomy_config.mode} is not enabled for paper stock execution."
    if daily_usage.submitted_order_count + 1 > autonomy_config.max_paper_orders_per_day:
        return (
            "Rejected: team daily paper order cap reached "
            f"({autonomy_config.max_paper_orders_per_day} order(s))."
        )
    if daily_usage.submitted_notional + next_order_notional > autonomy_config.max_daily_notional:
        return (
            "Rejected: team daily paper notional cap would be exceeded "
            f"(${autonomy_config.max_daily_notional:,.2f})."
        )
    return None


def _latest_run_for_strategy(database_path: Path | str, strategy_id: str) -> str | None:
    with get_connection(database_path) as conn:
        row = conn.execute(
            '''
            SELECT id
            FROM runs
            WHERE strategy_id = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            ''',
            (strategy_id,),
        ).fetchone()
    return None if row is None else row["id"]


def _read_value(obj: object, name: str, default: object = "unknown") -> object:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _float_value(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money_or_unknown(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "unknown"


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


def _agent_response_output_file(output_dir: Path | str, team_id: str, agent_id: str) -> Path:
    safe_team_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", team_id.strip()) or "team"
    safe_agent_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_id.strip()) or "agent"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(output_dir) / f"{safe_team_id}_{safe_agent_id}_{timestamp}.md"


def _team_chat_output_file(output_dir: Path | str, team_id: str, agent_id: str) -> Path:
    safe_team_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", team_id.strip()) or "team"
    safe_agent_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_id.strip()) or "agent"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(output_dir) / safe_team_id / f"{safe_agent_id}_{timestamp}.md"


def _team_cycle_note_output_file(output_dir: Path | str, team_id: str, agent_id: str) -> Path:
    safe_team_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", team_id.strip()) or "team"
    safe_agent_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_id.strip()) or "agent"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(output_dir) / safe_team_id / f"{safe_agent_id}_{timestamp}.md"


__all__ = [
    "ALLOWED_CHANNELS_ENV",
    "AgentApprovalGate",
    "DEFAULT_PROPOSAL_PATH",
    "DEFAULT_REGISTRY_PATH",
    "DiscordBotConfig",
    "PaperDailyUsage",
    "REVIEW_APPROVAL_TOKEN",
    "RISK_APPROVAL_TOKEN",
    "TeamAutonomyConfig",
    "TOKEN_ENV",
    "build_agent_approval_gate_from_files",
    "build_ask_agent_summary",
    "build_ask_team_summary",
    "build_daily_team_report_now_summary",
    "build_disable_autonomy_summary",
    "build_enable_autonomy_summary",
    "build_latest_agent_run_summary",
    "build_latest_team_cycle_summary",
    "build_natural_message_response_for_channel",
    "build_natural_team_chat_summary",
    "build_paper_trade_team_summary",
    "build_review_proposals_summary",
    "build_run_tournament_summary",
    "build_scheduled_team_update_summary",
    "build_schedule_reports_status_summary",
    "build_status_summary",
    "build_team_autonomy_status_summary",
    "build_team_paper_cycle_summary",
    "build_team_paper_status_summary",
    "build_team_positions_summary",
    "build_team_report_summary",
    "build_teams_summary",
    "is_channel_allowed",
    "latest_agent_run_path",
    "latest_agent_run_path_for_team",
    "parse_allowed_channel_ids",
    "parse_special_channel_ids",
    "parse_agent_approval_token",
    "parse_team_autonomy_flags",
    "parse_team_autonomy_config",
    "parse_team_channel_ids",
    "run_discord_bot",
]
