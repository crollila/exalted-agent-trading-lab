"""Phase 7S.3 — live per-team paper account equity snapshots for the scoreboard.

The weekly competition status and the Discord Alpha-vs-Beta scoreboard used to
read only the *cached* local weekly state (the last persisted scorecard equity).
This module lets them refresh each team's **current** Alpaca paper account equity
using ONLY that team's own credentials, and clearly fall back to the cached value
(labelled as such) when a live read is not possible.

Safety properties (do not weaken):

* Read-only: only ``get_account()`` is ever called here. No orders, no risk
  bypass, no PortfolioManager involvement.
* Paper-only: ``client_for_source`` refuses any non-paper endpoint, so a live
  read can never touch a live account.
* Team-isolated: a team uses only its ``TEAM_*_ALPACA_*`` credentials. The
  global ``ALPACA_API_KEY`` is never used for the Alpha/Beta scoreboard, so a
  global auth failure (e.g. 401) can never block a team refresh.
* Never raises: any failure for a team (missing creds, 401, network, SDK error)
  falls back to that team's cached equity and records a short, secret-free
  reason. Secret values are never returned, logged, or printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

# Source labels surfaced verbatim in the status output and Discord scoreboard.
LIVE_SOURCE_LABEL = "live team Alpaca paper snapshot"
CACHED_SOURCE_LABEL = "cached local weekly state"

SOURCE_LIVE = "live"
SOURCE_CACHED = "cached"

WEEK_TEAMS = ("team_alpha", "team_beta")


def _read_value(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_reason(exc: Exception) -> str:
    """A short, secret-free classification of why a live read failed.

    Reuses the broker auth classifier so 401/403/network/SDK errors are labelled
    consistently. Never returns raw secret-bearing text.
    """

    from src.brokers.paper_auth import _classify_exception

    if isinstance(exc, ValueError):
        # Wrapper-level safety refusal (paper flag / base URL) or unconfigured creds.
        return "credentials not configured for paper endpoint"
    if isinstance(exc, RuntimeError) and "credentials" in str(exc).lower():
        return "credentials not configured"
    classification, _message = _classify_exception(exc)
    return classification


@dataclass(frozen=True)
class TeamEquitySnapshot:
    """One team's equity, either freshly read (live) or the cached fallback."""

    team_id: str
    source: str  # SOURCE_LIVE | SOURCE_CACHED
    snapshot_time: str
    equity: float | None
    starting_equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    error: str | None = None

    @property
    def is_live(self) -> bool:
        return self.source == SOURCE_LIVE

    @property
    def source_label(self) -> str:
        return LIVE_SOURCE_LABEL if self.is_live else CACHED_SOURCE_LABEL

    @property
    def team_return(self) -> float | None:
        if self.equity is None or not self.starting_equity:
            return None
        return (self.equity - self.starting_equity) / self.starting_equity

    def excess_return_vs_spy(self, spy_return: float | None) -> float | None:
        team_return = self.team_return
        if team_return is None or spy_return is None:
            return None
        return team_return - spy_return


def refresh_team_paper_equity(
    team_id: str,
    *,
    cached_equity: float | None = None,
    starting_equity: float | None = None,
    cached_cash: float | None = None,
    cached_buying_power: float | None = None,
    client_factory: Callable[[Any], Any] | None = None,
    base_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> TeamEquitySnapshot:
    """Read ``team_id``'s current paper equity, or fall back to its cached value.

    Uses only the team's own credentials (never the global key). Any failure
    returns a cached-source snapshot with a short, secret-free reason.
    """

    from src.brokers.paper_auth import client_for_source

    now = now or datetime.now(timezone.utc)
    timestamp = now.isoformat()
    try:
        client = client_for_source(
            team_id,
            base_settings=base_settings,
            env=env,
            client_factory=client_factory,
        )
        account = client.get_account()
        equity = _coerce_float(_read_value(account, "equity"))
        if equity is None:
            raise ValueError("account equity unavailable")
        return TeamEquitySnapshot(
            team_id=team_id,
            source=SOURCE_LIVE,
            snapshot_time=timestamp,
            equity=equity,
            starting_equity=starting_equity,
            cash=_coerce_float(_read_value(account, "cash")),
            buying_power=_coerce_float(_read_value(account, "buying_power")),
        )
    except Exception as exc:  # noqa: BLE001 - a refresh must never crash the scoreboard
        return TeamEquitySnapshot(
            team_id=team_id,
            source=SOURCE_CACHED,
            snapshot_time=timestamp,
            equity=cached_equity,
            starting_equity=starting_equity,
            cash=cached_cash,
            buying_power=cached_buying_power,
            error=_safe_reason(exc),
        )


@dataclass(frozen=True)
class CompetitionEquityView:
    """The refreshed (or cached) equity snapshots for the competing teams."""

    snapshots: dict[str, TeamEquitySnapshot]
    snapshot_time: str
    teams: tuple[str, ...] = WEEK_TEAMS

    def get(self, team_id: str) -> TeamEquitySnapshot | None:
        return self.snapshots.get(team_id)

    @property
    def all_live(self) -> bool:
        snaps = [self.snapshots[t] for t in self.teams if t in self.snapshots]
        return bool(snaps) and all(snap.is_live for snap in snaps)

    @property
    def any_live(self) -> bool:
        return any(snap.is_live for snap in self.snapshots.values())

    @property
    def source_label(self) -> str:
        """Overall label: live only when EVERY competing team read live."""

        return LIVE_SOURCE_LABEL if self.all_live else CACHED_SOURCE_LABEL


def refresh_competition_equity(
    teams: tuple[str, ...] = WEEK_TEAMS,
    *,
    cards: Mapping[str, Any] | None = None,
    client_factory: Callable[[Any], Any] | None = None,
    base_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> CompetitionEquityView:
    """Refresh live paper equity for each team, falling back to cached per team.

    ``cards`` maps ``team_id`` -> a scorecard-like object/dict exposing
    ``current_equity`` and ``starting_equity`` (used for the cached fallback and
    to compute live return against the team's starting equity).
    """

    now = now or datetime.now(timezone.utc)
    cards = cards or {}
    snapshots: dict[str, TeamEquitySnapshot] = {}
    for team_id in teams:
        card = cards.get(team_id)
        cached_equity = _coerce_float(_read_value(card, "current_equity")) if card is not None else None
        starting_equity = _coerce_float(_read_value(card, "starting_equity")) if card is not None else None
        snapshots[team_id] = refresh_team_paper_equity(
            team_id,
            cached_equity=cached_equity,
            starting_equity=starting_equity,
            client_factory=client_factory,
            base_settings=base_settings,
            env=env,
            now=now,
        )
    return CompetitionEquityView(snapshots=snapshots, snapshot_time=now.isoformat(), teams=tuple(teams))


__all__ = [
    "CACHED_SOURCE_LABEL",
    "LIVE_SOURCE_LABEL",
    "SOURCE_CACHED",
    "SOURCE_LIVE",
    "CompetitionEquityView",
    "TeamEquitySnapshot",
    "refresh_competition_equity",
    "refresh_team_paper_equity",
]
