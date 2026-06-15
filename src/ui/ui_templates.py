"""Persisted UI template selection for the local dashboard.

No Streamlit imports live here. The dashboard stores this tiny preference under
``data/runtime`` so it stays local and ignored by git.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_TEMPLATE_CONFIG_PATH = Path("data/runtime/ui_template.json")
DEFAULT_UI_TEMPLATE_ID = "portfolio_cockpit"


@dataclass(frozen=True)
class UiTemplate:
    template_id: str
    label: str
    description: str
    default_page: str
    landing_view_id: str
    landing_title: str
    show_debug_by_default: bool
    compact_safety_banner: bool


UI_TEMPLATES: dict[str, UiTemplate] = {
    "command_center": UiTemplate(
        template_id="command_center",
        label="Command Center",
        description="Dark operator console for debugging, runtime files, process state, and control.",
        default_page="Home",
        landing_view_id="operator_console",
        landing_title="Command Center",
        show_debug_by_default=True,
        compact_safety_banner=False,
    ),
    "portfolio_cockpit": UiTemplate(
        template_id="portfolio_cockpit",
        label="Portfolio Cockpit",
        description="Cleaner broker-style dashboard for daily use, portfolio cards, charts, and P/L.",
        default_page="Home",
        landing_view_id="portfolio_cockpit",
        landing_title="Portfolio Cockpit",
        show_debug_by_default=False,
        compact_safety_banner=True,
    ),
    "ai_team_room": UiTemplate(
        template_id="ai_team_room",
        label="AI Team Room",
        description="Agent/team-chat-first layout with Alpha/Beta rooms, roles, current tasks, and evidence.",
        default_page="Home",
        landing_view_id="ai_team_room",
        landing_title="AI Team Room",
        show_debug_by_default=False,
        compact_safety_banner=True,
    ),
}


def normalize_template_id(value: str | None) -> str:
    """Return a known template id, falling back to the product default."""

    if value in UI_TEMPLATES:
        return str(value)
    return DEFAULT_UI_TEMPLATE_ID


def template_options() -> list[UiTemplate]:
    """Return templates in the intended UI order."""

    return [
        UI_TEMPLATES["portfolio_cockpit"],
        UI_TEMPLATES["ai_team_room"],
        UI_TEMPLATES["command_center"],
    ]


def read_template_config(path: Path | str = DEFAULT_TEMPLATE_CONFIG_PATH) -> dict[str, str]:
    """Read the local template preference. Missing/bad files return an empty config."""

    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    value = payload.get("selected_template")
    return {"selected_template": normalize_template_id(str(value) if value is not None else None)}


def selected_template_id(
    path: Path | str = DEFAULT_TEMPLATE_CONFIG_PATH,
    *,
    existing_user_setting: str | None = None,
) -> str:
    """Return the selected template.

    The persisted local preference wins. If no preference exists, an explicit legacy/user
    setting can be honored. Otherwise Portfolio Cockpit is the default.
    """

    persisted = read_template_config(path).get("selected_template")
    if persisted:
        return persisted
    return normalize_template_id(existing_user_setting)


def selected_template(path: Path | str = DEFAULT_TEMPLATE_CONFIG_PATH) -> UiTemplate:
    """Return the selected template object."""

    return UI_TEMPLATES[selected_template_id(path)]


def save_template_selection(
    template_id: str,
    path: Path | str = DEFAULT_TEMPLATE_CONFIG_PATH,
) -> Path:
    """Persist a known template id to a local runtime JSON file."""

    normalized = normalize_template_id(template_id)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"selected_template": normalized}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def reset_template_selection(path: Path | str = DEFAULT_TEMPLATE_CONFIG_PATH) -> Path:
    """Reset the local UI preference to the product default."""

    return save_template_selection(DEFAULT_UI_TEMPLATE_ID, path)


def template_landing_metadata(template_id: str | None) -> dict[str, str]:
    """Return stable metadata for the selected template's landing view."""

    template = UI_TEMPLATES[normalize_template_id(template_id)]
    return {
        "template_id": template.template_id,
        "label": template.label,
        "default_page": template.default_page,
        "landing_view_id": template.landing_view_id,
        "landing_title": template.landing_title,
    }


def template_label_by_id(templates: Mapping[str, UiTemplate] = UI_TEMPLATES) -> dict[str, str]:
    """Small helper for Streamlit selectbox labels."""

    return {template_id: template.label for template_id, template in templates.items()}
