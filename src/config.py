"""All configuration in one place, loaded from .env / environment.

Two teams, one LLM provider, one set of deterministic risk limits. Anything the
system does is controlled from here — if a knob is not in this file, it does
not exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

TEAM_IDS = ("team_alpha", "team_beta")

# Symbols the researcher always gets prices/news for (held symbols are added).
DEFAULT_WATCHLIST = (
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL", "AMZN",
)

TEAM_STANCES = {
    "team_alpha": (
        "Team Alpha hunts momentum, breakouts, and catalysts. Higher variance: "
        "rotate quickly out of weak positions into stronger ideas, press winners, "
        "and act decisively when research shows leadership."
    ),
    "team_beta": (
        "Team Beta is contrarian and risk-adjusted. Lower variance: favor quality, "
        "mean reversion, and capital preservation; trade less, avoid churn, and only "
        "add exposure with strong justification."
    ),
}

TEAM_DISPLAY_NAMES = {"team_alpha": "Team Alpha", "team_beta": "Team Beta"}

# Agent roles. Each is a separate LLM call with its own memory.
ROLE_RESEARCHER = "researcher"
ROLE_STRATEGIST = "strategist"
ROLE_RISK = "risk"
ROLES = (ROLE_RESEARCHER, ROLE_STRATEGIST, ROLE_RISK)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class RiskLimits:
    """Deterministic hard caps. The risk-analyst agent may only veto or shrink
    trades; these caps are enforced in code afterwards and always win."""

    max_position_pct: float = 0.15      # max single-position weight of equity
    max_gross_exposure: float = 1.00    # sum(|position notional|) / equity
    min_cash_pct: float = 0.05          # cash floor that buys may not break
    max_orders_per_day: int = 40        # per team, ET-scoped
    max_daily_notional: float = 500_000.0
    allow_shorts: bool = True
    max_proposals_per_cycle: int = 3

    @classmethod
    def from_env(cls) -> "RiskLimits":
        return cls(
            max_position_pct=_env_float("MAX_POSITION_PCT", cls.max_position_pct),
            max_gross_exposure=_env_float("MAX_GROSS_EXPOSURE", cls.max_gross_exposure),
            min_cash_pct=_env_float("MIN_CASH_PCT", cls.min_cash_pct),
            max_orders_per_day=_env_int("MAX_ORDERS_PER_DAY", cls.max_orders_per_day),
            max_daily_notional=_env_float("MAX_DAILY_NOTIONAL", cls.max_daily_notional),
            allow_shorts=_env_bool("ALLOW_SHORTS", cls.allow_shorts),
            max_proposals_per_cycle=_env_int("MAX_PROPOSALS_PER_CYCLE", cls.max_proposals_per_cycle),
        )


@dataclass(frozen=True)
class TeamConfig:
    team_id: str
    display_name: str
    stance: str
    alpaca_api_key: str
    alpaca_secret_key: str

    @property
    def has_credentials(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)


@dataclass(frozen=True)
class Settings:
    teams: tuple[TeamConfig, ...]
    llm_provider: str = "openai"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    model_default: str = "gpt-5.4-nano"
    model_overrides: dict = field(default_factory=dict)  # role -> model name
    cycle_minutes: int = 30
    risk: RiskLimits = field(default_factory=RiskLimits)
    dry_run: bool = False
    data_dir: Path = Path("data")
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST
    news_items_per_cycle: int = 12
    discord_bot_token: str | None = None
    discord_channel_id: str | None = None

    def team(self, team_id: str) -> TeamConfig:
        for team in self.teams:
            if team.team_id == team_id:
                return team
        raise KeyError(f"Unknown team: {team_id!r}. Known: {[t.team_id for t in self.teams]}")

    def model_for(self, role: str) -> str:
        return self.model_overrides.get(role) or self.model_default

    @classmethod
    def from_env(cls, dotenv: bool = True) -> "Settings":
        if dotenv:
            load_dotenv()

        teams = tuple(
            TeamConfig(
                team_id=team_id,
                display_name=TEAM_DISPLAY_NAMES[team_id],
                stance=TEAM_STANCES[team_id],
                alpaca_api_key=(os.getenv(f"{team_id.upper()}_ALPACA_API_KEY") or "").strip(),
                alpaca_secret_key=(os.getenv(f"{team_id.upper()}_ALPACA_SECRET_KEY") or "").strip(),
            )
            for team_id in TEAM_IDS
        )

        overrides = {}
        for role, env_name in (
            (ROLE_RESEARCHER, "LLM_MODEL_RESEARCHER"),
            (ROLE_STRATEGIST, "LLM_MODEL_STRATEGIST"),
            (ROLE_RISK, "LLM_MODEL_RISK"),
        ):
            value = (os.getenv(env_name) or "").strip()
            if value:
                overrides[role] = value

        watchlist_raw = (os.getenv("WATCHLIST") or "").strip()
        watchlist = (
            tuple(s.strip().upper() for s in watchlist_raw.split(",") if s.strip())
            if watchlist_raw
            else DEFAULT_WATCHLIST
        )

        return cls(
            teams=teams,
            llm_provider=(os.getenv("LLM_PROVIDER") or "openai").strip().lower(),
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            anthropic_api_key=(os.getenv("ANTHROPIC_API_KEY") or "").strip() or None,
            ollama_base_url=(os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1").strip(),
            model_default=(os.getenv("LLM_MODEL") or "gpt-5.4-nano").strip(),
            model_overrides=overrides,
            cycle_minutes=_env_int("CYCLE_MINUTES", 30),
            risk=RiskLimits.from_env(),
            dry_run=_env_bool("DRY_RUN", False),
            data_dir=Path(os.getenv("DATA_DIR") or "data"),
            watchlist=watchlist,
            news_items_per_cycle=_env_int("NEWS_ITEMS_PER_CYCLE", 12),
            discord_bot_token=(os.getenv("DISCORD_BOT_TOKEN") or "").strip() or None,
            discord_channel_id=(
                (os.getenv("DISCORD_CHANNEL_ID") or os.getenv("DISCORD_PAPER_TRADING_LOG_CHANNEL_ID") or "").strip()
                or None
            ),
        )
