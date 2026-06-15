from pathlib import Path

from src.ui.ui_templates import (
    DEFAULT_UI_TEMPLATE_ID,
    UI_TEMPLATES,
    normalize_template_id,
    save_template_selection,
    selected_template_id,
    template_options,
    read_template_config,
    reset_template_selection,
    template_landing_metadata,
)


def test_default_template_is_portfolio_cockpit_when_no_setting(tmp_path):
    assert DEFAULT_UI_TEMPLATE_ID == "portfolio_cockpit"
    assert selected_template_id(tmp_path / "missing.json") == "portfolio_cockpit"


def test_template_selection_persists_to_runtime_path(tmp_path):
    path = tmp_path / "data" / "runtime" / "ui_template.json"
    saved = save_template_selection("ai_team_room", path)

    assert saved == path
    assert read_template_config(path) == {"selected_template": "ai_team_room"}
    assert selected_template_id(path) == "ai_team_room"


def test_unknown_template_normalizes_to_default(tmp_path):
    path = tmp_path / "ui_template.json"
    save_template_selection("does_not_exist", path)

    assert normalize_template_id("nope") == "portfolio_cockpit"
    assert selected_template_id(path) == "portfolio_cockpit"


def test_template_options_cover_required_layouts():
    labels = [template.label for template in template_options()]
    assert labels == ["Portfolio Cockpit", "AI Team Room", "Command Center"]
    assert set(UI_TEMPLATES) == {"command_center", "portfolio_cockpit", "ai_team_room"}


def test_template_landing_metadata_changes_selected_home_view():
    assert template_landing_metadata("portfolio_cockpit")["landing_view_id"] == "portfolio_cockpit"
    assert template_landing_metadata("command_center")["landing_view_id"] == "operator_console"
    assert template_landing_metadata("ai_team_room")["landing_view_id"] == "ai_team_room"
    assert len(
        {
            template_landing_metadata("portfolio_cockpit")["landing_title"],
            template_landing_metadata("command_center")["landing_title"],
            template_landing_metadata("ai_team_room")["landing_title"],
        }
    ) == 3


def test_reset_template_selection_returns_to_portfolio_default(tmp_path):
    path = tmp_path / "ui_template.json"
    save_template_selection("command_center", path)
    reset_template_selection(path)

    assert selected_template_id(path) == "portfolio_cockpit"
