from src.config.permissions import TradingPermissions


def test_advanced_permissions_default_off():
    perms = TradingPermissions.from_env(env={})
    assert perms.is_paper is True
    assert perms.stocks_enabled() is True
    assert perms.shorting_enabled() is False
    assert perms.margin_enabled() is False
    assert perms.options_enabled() is False
    assert perms.allow_naked_options is False


def test_caps_have_spec_defaults():
    perms = TradingPermissions.from_env(env={})
    assert perms.max_daily_orders_per_team == 3
    assert perms.max_daily_loss_pct_per_team == 0.02
    assert perms.max_position_weight == 0.20
    assert perms.max_gross_exposure == 1.50
    assert perms.max_net_exposure == 1.20
    assert perms.max_short_exposure == 0.30
    assert perms.max_single_short_weight == 0.10
    assert perms.max_options_premium_at_risk == 0.02
    assert perms.max_options_contracts_per_trade == 2
    assert perms.min_options_dte == 7


def test_non_paper_mode_disables_all_levels():
    perms = TradingPermissions.from_env(
        env={
            "TRADING_MODE": "live",
            "ENABLE_PAPER_STOCKS": "true",
            "ENABLE_PAPER_SHORTING": "true",
            "ENABLE_PAPER_MARGIN": "true",
            "ENABLE_PAPER_OPTIONS": "true",
        }
    )
    assert perms.is_paper is False
    assert perms.stocks_enabled() is False
    assert perms.shorting_enabled() is False
    assert perms.margin_enabled() is False
    assert perms.options_enabled() is False
    assert perms.enabled_levels() == ()


def test_explicit_enable_unlocks_levels_in_paper_mode():
    perms = TradingPermissions.from_env(
        env={
            "ENABLE_PAPER_SHORTING": "true",
            "ENABLE_PAPER_MARGIN": "true",
            "ENABLE_PAPER_OPTIONS": "true",
        }
    )
    assert perms.shorting_enabled() is True
    assert perms.margin_enabled() is True
    assert perms.options_enabled() is True
    assert perms.enabled_levels() == (1, 2, 3, 4)
