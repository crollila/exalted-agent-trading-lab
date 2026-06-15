from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import load_dotenv

from src.brokers.alpaca_client import PAPER_BASE_URL
from src.config.settings import Settings


TEAM_ALPACA_ENV_PREFIXES = {
    "team_alpha": "TEAM_ALPHA",
    "team_beta": "TEAM_BETA",
}


class TeamAlpacaConfigError(ValueError):
    """Raised when a team's Alpaca paper configuration is missing or unsafe."""


@dataclass(frozen=True)
class TeamAlpacaPaperConfig:
    team_id: str
    api_key: str | None
    secret_key: str | None
    paper: bool | None
    base_url: str | None
    env_prefix: str

    @classmethod
    def from_env(
        cls,
        team_id: str,
        env: Mapping[str, str] | None = None,
    ) -> "TeamAlpacaPaperConfig":
        if env is None:
            load_dotenv()
            env = os.environ
        env_prefix = _env_prefix_for_team(team_id)
        paper_raw = _clean_optional(env.get(f"{env_prefix}_ALPACA_PAPER"))
        return cls(
            team_id=team_id,
            api_key=_clean_optional(env.get(f"{env_prefix}_ALPACA_API_KEY")),
            secret_key=_clean_optional(env.get(f"{env_prefix}_ALPACA_SECRET_KEY")),
            paper=None if paper_raw is None else paper_raw.lower() == "true",
            base_url=_clean_optional(env.get(f"{env_prefix}_ALPACA_BASE_URL")),
            env_prefix=env_prefix,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key and self.paper is not None and self.base_url)

    def validate_ready(self) -> None:
        if not self.configured:
            raise TeamAlpacaConfigError(
                f"{self.team_id} Alpaca paper credentials are not configured. "
                f"Set {self.env_prefix}_ALPACA_API_KEY, {self.env_prefix}_ALPACA_SECRET_KEY, "
                f"{self.env_prefix}_ALPACA_PAPER, and {self.env_prefix}_ALPACA_BASE_URL."
            )
        if self.paper is not True:
            raise TeamAlpacaConfigError(f"{self.team_id} must set {self.env_prefix}_ALPACA_PAPER=true.")
        if self.base_url != PAPER_BASE_URL:
            raise TeamAlpacaConfigError(f"{self.team_id} base URL must be exactly {PAPER_BASE_URL}.")

    def safe_status(self) -> str:
        try:
            self.validate_ready()
        except TeamAlpacaConfigError as exc:
            return str(exc)
        return f"{self.team_id} Alpaca paper credentials configured."

    def to_settings(self, base_settings: Settings | None = None) -> Settings:
        self.validate_ready()
        settings = base_settings or Settings.from_env()
        return Settings(
            alpaca_api_key=self.api_key,
            alpaca_secret_key=self.secret_key,
            alpaca_paper=self.paper,
            alpaca_base_url=self.base_url or "",
            database_path=settings.database_path,
            dry_run=settings.dry_run,
            starting_equity=settings.starting_equity,
            min_cash_pct=settings.min_cash_pct,
            max_position_pct=settings.max_position_pct,
            max_daily_turnover_pct=settings.max_daily_turnover_pct,
            max_new_positions_per_day=settings.max_new_positions_per_day,
        )


def load_team_alpaca_paper_config(
    team_id: str,
    env: Mapping[str, str] | None = None,
) -> TeamAlpacaPaperConfig:
    return TeamAlpacaPaperConfig.from_env(team_id, env=env)


def _env_prefix_for_team(team_id: str) -> str:
    try:
        return TEAM_ALPACA_ENV_PREFIXES[team_id]
    except KeyError as exc:
        raise TeamAlpacaConfigError(f"Unknown team_id for Alpaca paper config: {team_id}.") from exc


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
