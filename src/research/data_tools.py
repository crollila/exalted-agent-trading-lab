"""Allowlisted research/data tools (Part 9).

Agents get real, controlled research context here. Every datum is tagged with its
provenance (``live``/``delayed``/``fixture``/``unknown``) so agents can cite the
source and must never invent market facts. These tools are strictly read-only:
none of them can submit an order. There is no arbitrary scraping — only the
allowlisted sources below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.competition.proposals import DataProvenance

ALLOWLISTED_TOOLS = (
    "alpaca_account_status",
    "alpaca_positions",
    "alpaca_market_clock",
    "alpaca_latest_quote",
    "spy_benchmark",
    "local_runtime_history",
    "local_team_scorecards",
)


@dataclass(frozen=True)
class DataPoint:
    name: str
    value: Any
    provenance: DataProvenance
    source: str
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "provenance": self.provenance.value,
            "source": self.source,
            "note": self.note,
        }


@dataclass
class ResearchContext:
    points: list[DataPoint] = field(default_factory=list)

    def add(self, point: DataPoint) -> None:
        self.points.append(point)

    def sources_used(self) -> list[str]:
        return sorted({p.source for p in self.points})

    def as_dict(self) -> dict[str, object]:
        return {
            "points": [p.as_dict() for p in self.points],
            "sources_used": self.sources_used(),
        }


def _read_attr(obj: object, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def alpaca_account_status(client: AlpacaClientWrapper | None) -> DataPoint:
    if client is None or not client.has_credentials():
        return DataPoint(
            name="account_status",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_account_status",
            note="Alpaca paper credentials unavailable.",
        )
    try:
        account = client.get_account()
    except (RuntimeError, ValueError) as exc:
        return DataPoint(
            name="account_status",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_account_status",
            note=f"unavailable: {exc}",
        )
    value = {
        "equity": _read_attr(account, "equity"),
        "cash": _read_attr(account, "cash"),
        "buying_power": _read_attr(account, "buying_power"),
    }
    return DataPoint(
        name="account_status",
        value=value,
        provenance=DataProvenance.LIVE,
        source="alpaca_account_status",
    )


def alpaca_positions(client: AlpacaClientWrapper | None) -> DataPoint:
    if client is None or not client.has_credentials():
        return DataPoint(
            name="positions",
            value=[],
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_positions",
            note="Alpaca paper credentials unavailable.",
        )
    try:
        positions = client.get_positions()
    except (RuntimeError, ValueError) as exc:
        return DataPoint(
            name="positions",
            value=[],
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_positions",
            note=f"unavailable: {exc}",
        )
    return DataPoint(
        name="positions",
        value=[
            {"symbol": _read_attr(p, "symbol"), "qty": _read_attr(p, "qty")} for p in positions
        ],
        provenance=DataProvenance.LIVE,
        source="alpaca_positions",
    )


def alpaca_market_clock(client: AlpacaClientWrapper | None) -> DataPoint:
    if client is None or not client.has_credentials():
        return DataPoint(
            name="market_clock",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_market_clock",
            note="Alpaca paper credentials unavailable.",
        )
    try:
        is_open = client.is_market_open()
    except (RuntimeError, ValueError) as exc:
        return DataPoint(
            name="market_clock",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_market_clock",
            note=f"unavailable: {exc}",
        )
    return DataPoint(
        name="market_clock",
        value={"is_open": is_open},
        provenance=DataProvenance.LIVE,
        source="alpaca_market_clock",
    )


def alpaca_latest_quote(
    symbol: str,
    quote_fn: Callable[[str], Any] | None = None,
) -> DataPoint:
    """Latest quote via an injected quote function. Unknown when unavailable.

    No quote function is wired by default; this keeps the boundary explicit and
    test-friendly and avoids accidental network calls.
    """

    if quote_fn is None:
        return DataPoint(
            name=f"quote:{symbol.upper()}",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_latest_quote",
            note="No quote provider configured; price is unknown.",
        )
    try:
        quote = quote_fn(symbol)
    except (RuntimeError, ValueError) as exc:
        return DataPoint(
            name=f"quote:{symbol.upper()}",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="alpaca_latest_quote",
            note=f"unavailable: {exc}",
        )
    return DataPoint(
        name=f"quote:{symbol.upper()}",
        value=quote,
        provenance=DataProvenance.LIVE,
        source="alpaca_latest_quote",
    )


def spy_benchmark(
    return_pct: float | None = None,
    provenance: DataProvenance = DataProvenance.UNKNOWN,
) -> DataPoint:
    if return_pct is None:
        return DataPoint(
            name="spy_benchmark",
            value=None,
            provenance=DataProvenance.UNKNOWN,
            source="spy_benchmark",
            note="SPY benchmark return unavailable.",
        )
    return DataPoint(
        name="spy_benchmark",
        value={"return_pct": return_pct},
        provenance=provenance,
        source="spy_benchmark",
    )


def local_runtime_history(records: list[dict] | None) -> DataPoint:
    return DataPoint(
        name="local_runtime_history",
        value=records or [],
        provenance=DataProvenance.FIXTURE,
        source="local_runtime_history",
        note="Local persisted runtime history; not live market data.",
    )


def local_team_scorecards(scorecards: list[dict] | None) -> DataPoint:
    return DataPoint(
        name="local_team_scorecards",
        value=scorecards or [],
        provenance=DataProvenance.FIXTURE,
        source="local_team_scorecards",
        note="Local persisted scorecards; not live market data.",
    )


def gather_research_context(
    client: AlpacaClientWrapper | None = None,
    *,
    spy_return_pct: float | None = None,
    spy_provenance: DataProvenance = DataProvenance.UNKNOWN,
    history: list[dict] | None = None,
    scorecards: list[dict] | None = None,
) -> ResearchContext:
    context = ResearchContext()
    context.add(alpaca_account_status(client))
    context.add(alpaca_positions(client))
    context.add(alpaca_market_clock(client))
    context.add(spy_benchmark(spy_return_pct, spy_provenance))
    context.add(local_runtime_history(history))
    context.add(local_team_scorecards(scorecards))
    return context
