"""Shared test fixtures. No network, no real keys, no global state.

The kill switch and all data files are pointed at tmp_path so tests can never
be affected by (or affect) the real runtime state — engaging the kill switch
on the desk must not fail the suite.
"""

from __future__ import annotations

import pytest

from src.broker import AccountInfo, AssetInfo, PositionInfo
from src.config import RiskLimits, Settings, TeamConfig, TEAM_STANCES


@pytest.fixture
def team_alpha() -> TeamConfig:
    return TeamConfig(
        team_id="team_alpha",
        display_name="Team Alpha",
        stance=TEAM_STANCES["team_alpha"],
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
    )


@pytest.fixture
def settings(tmp_path, team_alpha) -> Settings:
    beta = TeamConfig(
        team_id="team_beta",
        display_name="Team Beta",
        stance=TEAM_STANCES["team_beta"],
        alpaca_api_key="test-key-b",
        alpaca_secret_key="test-secret-b",
    )
    return Settings(
        teams=(team_alpha, beta),
        llm_provider="openai",
        openai_api_key="test-openai-key",
        model_default="test-model",
        data_dir=tmp_path / "data",
        risk=RiskLimits(),
    )


@pytest.fixture
def account() -> AccountInfo:
    return AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=1_000_000.0, buying_power=2_000_000.0)


def make_position(symbol: str, qty: float, side: str = "long", price: float = 100.0) -> PositionInfo:
    signed = qty * price if side == "long" else -qty * price
    return PositionInfo(
        symbol=symbol,
        qty=qty,
        side=side,
        avg_entry_price=price,
        current_price=price,
        market_value=signed,
        unrealized_plpc=0.0,
    )


class StaticAssets:
    """asset_of stub: every symbol tradable+shortable unless listed."""

    def __init__(self, missing=(), unshortable=()):
        self.missing = {s.upper() for s in missing}
        self.unshortable = {s.upper() for s in unshortable}

    def __call__(self, symbol: str) -> AssetInfo | None:
        symbol = symbol.upper()
        if symbol in self.missing:
            return None
        return AssetInfo(symbol=symbol, tradable=True, shortable=symbol not in self.unshortable)


class StaticPrices:
    def __init__(self, prices: dict[str, float]):
        self.prices = {k.upper(): v for k, v in prices.items()}

    def __call__(self, symbol: str) -> float | None:
        return self.prices.get(symbol.upper())
