from src.ui import competition_view


def test_permissions_levels_data_shape():
    data = competition_view.permissions_levels_data()
    assert data["is_paper"] is True
    assert "caps" in data


def test_advanced_levels_default_off(monkeypatch):
    # Neutralize .env loading so this reflects defaults, not the developer's local .env.
    import src.config.permissions as permissions

    monkeypatch.setattr(permissions, "load_dotenv", lambda *a, **k: None)
    for name in ("ENABLE_PAPER_SHORTING", "ENABLE_PAPER_MARGIN", "ENABLE_PAPER_OPTIONS", "TRADING_MODE"):
        monkeypatch.delenv(name, raising=False)
    levels = competition_view.advanced_paper_levels_data()
    by_level = {level["level"]: level["enabled"] for level in levels}
    assert by_level[1] is True
    assert by_level[2] is False
    assert by_level[3] is False
    assert by_level[4] is False


def test_model_provider_data_masks_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    data = competition_view.model_provider_data()
    # Never returns the actual key — only whether it is set.
    assert data["openai_api_key_set"] is True
    assert "super-secret-value" not in str(data)


def test_kill_switch_state_data_shape():
    data = competition_view.kill_switch_state_data()
    assert "engaged" in data


def test_auth_statuses_data_has_three_sources_and_masks_secrets(monkeypatch):
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_SECRET_KEY", "DO_NOT_LEAK_ME")
    data = competition_view.auth_statuses_data(attempt_auth=False)
    assert set(data) == {"global", "team_alpha", "team_beta"}
    assert "DO_NOT_LEAK_ME" not in str(data)

