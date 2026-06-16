"""Grouped navigation + mode selection for the Arena Command Center (Phase 7Q).

Pure and Streamlit-free so it can be unit-tested without launching a UI. This module
only defines the navigation grouping and the persisted local UI mode (Demo/Operator,
Simple/Expert). It never trades, reads secrets, or bypasses any safety gate.

Modes:

* ``audience``: ``demo`` (safe for GitHub / interview / presentation; no risky controls;
  clearly-labeled sample data when real data is missing) or ``operator`` (real local
  runtime state + operational controls — still paper-only and fully gated).
* ``density``: ``simple`` (polished cards, scoreboard, brief summaries; no raw logs) or
  ``expert`` (tables, raw paths, runtime files, logs, advanced controls).

The selection persists under the ignored runtime path ``data/runtime/arena_ui.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DEFAULT_ARENA_UI_CONFIG_PATH = Path("data/runtime/arena_ui.json")

Audience = Literal["demo", "operator"]
Density = Literal["simple", "expert"]

AUDIENCE_VALUES: tuple[str, ...] = ("demo", "operator")
DENSITY_VALUES: tuple[str, ...] = ("simple", "expert")

# Safe defaults: Demo + Simple is presentation-safe out of the box.
DEFAULT_AUDIENCE: Audience = "demo"
DEFAULT_DENSITY: Density = "simple"


@dataclass(frozen=True)
class NavPage:
    """A single navigable page mapped to an existing dashboard render target."""

    label: str
    page_id: str
    operator_only: bool = False  # hidden unless audience == operator
    expert_only: bool = False  # hidden unless density == expert


@dataclass(frozen=True)
class NavGroup:
    """A labeled group of pages shown in the redesigned sidebar."""

    key: str
    label: str
    pages: tuple[NavPage, ...] = field(default_factory=tuple)


# The new Arena home replaces the giant flat page list. Every legacy page is still
# reachable through one of these groups so no feature is lost.
NAVIGATION_GROUPS: tuple[NavGroup, ...] = (
    NavGroup(
        key="arena",
        label="Arena",
        pages=(
            NavPage("Arena", "Arena"),
            NavPage("Alpha vs Beta Scoreboard", "Alpha vs Beta Scoreboard"),
            NavPage("Weekly Competition", "Weekly Competition"),
        ),
    ),
    NavGroup(
        key="agents",
        label="Agents",
        pages=(
            NavPage("Agents", "Agents"),
            NavPage("Agent Hub", "Agent Hub"),
            NavPage("Team Learning", "Team Learning"),
        ),
    ),
    NavGroup(
        key="portfolio",
        label="Portfolio",
        pages=(
            NavPage("Portfolio Cockpit", "Portfolio Cockpit"),
            NavPage("Paper Accounts", "Paper Accounts"),
            NavPage("Reports", "Reports"),
        ),
    ),
    NavGroup(
        key="research_lab",
        label="Research Lab",
        pages=(
            NavPage("Research", "Research"),
            NavPage("Proposal Attribution", "Proposal Attribution"),
            NavPage("Daily Lab", "Daily Lab"),
            NavPage("Data Tools", "Data Tools", expert_only=True),
        ),
    ),
    NavGroup(
        key="operator",
        label="Operator",
        pages=(
            NavPage("Operator", "Operator"),
            NavPage("Run Cycle", "Run Cycle", operator_only=True, expert_only=True),
            NavPage("Runtime Files", "Runtime Files", expert_only=True),
            NavPage("Discord Bot", "Discord Bot", operator_only=True),
        ),
    ),
    NavGroup(
        key="setup_safety",
        label="Setup & Safety",
        pages=(
            NavPage("Kill Switch", "Kill Switch"),
            NavPage("Permissions / Risk Levels", "Permissions / Risk Levels"),
            NavPage("Advanced Paper Trading", "Advanced Paper Trading"),
            NavPage("Model Provider Setup", "Model Provider Setup"),
            NavPage("Setup / Secrets", "Setup / Secrets"),
            NavPage("Setup Wizard", "Setup Wizard"),
            NavPage("Settings", "Settings", expert_only=True),
            NavPage("Hermes / Ollama / Local AI", "Hermes / Ollama / Local AI", expert_only=True),
            NavPage("Help / Safety", "Help / Safety"),
        ),
    ),
)

DEFAULT_PAGE_ID = "Arena"


def navigation_groups() -> tuple[NavGroup, ...]:
    """Return the grouped navigation structure (stable order)."""

    return NAVIGATION_GROUPS


def all_pages() -> list[NavPage]:
    """Flatten every page across all groups."""

    return [page for group in NAVIGATION_GROUPS for page in group.pages]


def default_page_id() -> str:
    """The Arena home is always the default landing page."""

    return DEFAULT_PAGE_ID


def visible_pages(group: NavGroup, *, audience: str, density: str) -> list[NavPage]:
    """Return the pages of a group visible for the current mode.

    ``operator_only`` pages are hidden in Demo audience; ``expert_only`` pages are
    hidden in Simple density. Demo + Simple therefore shows only the safe, polished
    subset — ideal for a presentation.
    """

    result: list[NavPage] = []
    for page in group.pages:
        if page.operator_only and audience != "operator":
            continue
        if page.expert_only and density != "expert":
            continue
        result.append(page)
    return result


def visible_groups(*, audience: str, density: str) -> list[NavGroup]:
    """Return only groups that have at least one visible page for the mode."""

    groups: list[NavGroup] = []
    for group in NAVIGATION_GROUPS:
        if visible_pages(group, audience=audience, density=density):
            groups.append(group)
    return groups


# ---------------------------------------------------------------------------
# Persisted UI mode
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ArenaMode:
    audience: Audience = DEFAULT_AUDIENCE
    density: Density = DEFAULT_DENSITY

    @property
    def is_demo(self) -> bool:
        return self.audience == "demo"

    @property
    def is_operator(self) -> bool:
        return self.audience == "operator"

    @property
    def is_simple(self) -> bool:
        return self.density == "simple"

    @property
    def is_expert(self) -> bool:
        return self.density == "expert"

    def as_dict(self) -> dict[str, str]:
        return {"audience": self.audience, "density": self.density}


def normalize_audience(value: str | None) -> Audience:
    text = (value or "").strip().lower()
    return text if text in AUDIENCE_VALUES else DEFAULT_AUDIENCE  # type: ignore[return-value]


def normalize_density(value: str | None) -> Density:
    text = (value or "").strip().lower()
    return text if text in DENSITY_VALUES else DEFAULT_DENSITY  # type: ignore[return-value]


def read_arena_mode(path: Path | str = DEFAULT_ARENA_UI_CONFIG_PATH) -> ArenaMode:
    """Read the persisted UI mode; missing/bad files fall back to safe defaults."""

    file_path = Path(path)
    if not file_path.is_file():
        return ArenaMode()
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ArenaMode()
    if not isinstance(payload, dict):
        return ArenaMode()
    return ArenaMode(
        audience=normalize_audience(payload.get("audience")),
        density=normalize_density(payload.get("density")),
    )


def save_arena_mode(
    mode: ArenaMode,
    path: Path | str = DEFAULT_ARENA_UI_CONFIG_PATH,
) -> Path:
    """Persist the UI mode to the local (ignored) runtime config file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(mode.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
