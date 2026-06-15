"""Safe market/account data tool descriptions for the dashboard and agent prompts.

This module does not browse the internet and does not submit orders. It describes which
local or Alpaca-backed data sources appear configured, and formats already-collected
portfolio snapshots for prompt context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from src.ui.dashboard_state import redact_secret_like_text
from src.ui.portfolio_view import TeamPortfolioSnapshot


@dataclass(frozen=True)
class DataSourceStatus:
    source: str
    configured: bool
    access: str
    note: str


def _present(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def build_data_source_statuses(env: Mapping[str, str]) -> list[DataSourceStatus]:
    """Summarize configured data sources without revealing credential values."""

    alpha_keys = _present(env, "TEAM_ALPHA_ALPACA_API_KEY") and _present(
        env, "TEAM_ALPHA_ALPACA_SECRET_KEY"
    )
    beta_keys = _present(env, "TEAM_BETA_ALPACA_API_KEY") and _present(
        env, "TEAM_BETA_ALPACA_SECRET_KEY"
    )
    market_data_hint = _present(env, "ALPACA_MARKET_DATA_FEED") or alpha_keys or beta_keys
    return [
        DataSourceStatus(
            source="Alpaca paper account",
            configured=alpha_keys or beta_keys,
            access="read-only account/positions in UI; paper orders only through gated Run Cycle",
            note="Configured for at least one team." if alpha_keys or beta_keys else "Paper keys not configured.",
        ),
        DataSourceStatus(
            source="Alpaca market data",
            configured=market_data_hint,
            access="market clock/snapshots only when existing Alpaca wrapper provides them",
            note="Availability depends on Alpaca API access and local keys.",
        ),
        DataSourceStatus(
            source="Alpaca news",
            configured=False,
            access="not wired in this phase",
            note="Future allowlisted adapter; agents must not claim news without provided context.",
        ),
        DataSourceStatus(
            source="Local runtime files",
            configured=True,
            access="read-only proposals, notes, ledgers, reports under ignored data/",
            note="Primary evidence source for what agents did and learned.",
        ),
        DataSourceStatus(
            source="Future RSS/news/SEC adapters",
            configured=False,
            access="planned allowlist + cache only",
            note="No uncontrolled web browsing is enabled.",
        ),
    ]


def data_source_rows(statuses: Sequence[DataSourceStatus]) -> list[dict[str, object]]:
    return [
        {
            "source": status.source,
            "configured": "yes" if status.configured else "no",
            "access": status.access,
            "note": status.note,
        }
        for status in statuses
    ]


def market_snapshot_context(snapshots: Sequence[TeamPortfolioSnapshot]) -> str:
    """Format already-collected paper snapshots for prompt context."""

    lines = [
        "Market/account context supplied by the app. Agents may only state market facts listed here.",
    ]
    if not snapshots:
        lines.append("No account or market snapshot was provided.")
        return "\n".join(lines)
    for snapshot in snapshots:
        market = (
            "open"
            if snapshot.market_open is True
            else "closed"
            if snapshot.market_open is False
            else "unknown"
        )
        lines.append(
            f"{snapshot.team_id}: available={snapshot.available}, equity={snapshot.equity}, "
            f"cash={snapshot.cash}, buying_power={snapshot.buying_power}, "
            f"market={market}, positions={snapshot.positions_count}, message={snapshot.message}"
        )
    return redact_secret_like_text("\n".join(lines))


def agent_market_data_rules(data_context: str | None = None) -> str:
    """Rules injected into agent prompts to prevent invented market/news claims."""

    context = (data_context or "").strip() or "No market/account/news data context was supplied."
    return (
        "Market/news rules:\n"
        "- Claim market, account, or news facts only when they appear in the tool context below.\n"
        "- If context is missing or stale, say so plainly.\n"
        "- Do not scrape arbitrary websites or invent current prices, news, market status, or catalysts.\n"
        "- No live trading, short execution, margin execution, or options execution.\n\n"
        f"Tool context:\n{redact_secret_like_text(context)}"
    )
