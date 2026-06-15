import math
from datetime import date, timedelta

from competition_helpers import (
    margin_long,
    option_long_call,
    naked_short_call,
    stock_long,
    stock_short,
)
from src.competition.risk_engine import AccountContext, Route, evaluate_proposal
from src.config.permissions import TradingPermissions

EQUITY = 1_000_000.0


def acct(**overrides) -> AccountContext:
    values = dict(equity=EQUITY, cash=EQUITY, buying_power=EQUITY * 2)
    values.update(overrides)
    return AccountContext(**values)


def perms(**overrides) -> TradingPermissions:
    return TradingPermissions.from_env(env={k: str(v) for k, v in overrides.items()})


# --- non-paper / live ---


def test_live_mode_rejects_everything():
    decision = evaluate_proposal(stock_long(), perms(TRADING_MODE="live"), acct())
    assert decision.route == Route.REJECTED
    assert decision.approved is False


# --- shorting ---


def test_short_rejected_when_disabled():
    decision = evaluate_proposal(stock_short(), perms(ENABLE_PAPER_SHORTING="false"), acct())
    assert decision.approved is False
    assert decision.route == Route.SIMULATION_ONLY


def test_short_approved_when_enabled_within_caps():
    decision = evaluate_proposal(stock_short(), perms(ENABLE_PAPER_SHORTING="true"), acct())
    assert decision.approved is True
    assert decision.route == Route.EXECUTION_ELIGIBLE
    assert decision.approved_quantity is not None and decision.approved_quantity > 0
    assert decision.borrow_assumption_logged is not None


def test_short_rejected_over_short_exposure_cap():
    decision = evaluate_proposal(
        stock_short(),
        perms(ENABLE_PAPER_SHORTING="true", MAX_SHORT_EXPOSURE="0.02"),
        acct(current_short_exposure=0.0),
    )
    assert decision.approved is False
    assert decision.route == Route.REJECTED


def test_short_rejected_when_daily_loss_breached():
    decision = evaluate_proposal(
        stock_short(),
        perms(ENABLE_PAPER_SHORTING="true"),
        acct(daily_loss_pct=0.05),
    )
    assert decision.approved is False


# --- margin ---


def test_margin_rejected_when_disabled():
    decision = evaluate_proposal(margin_long(), perms(ENABLE_PAPER_MARGIN="false"), acct())
    assert decision.approved is False
    assert decision.route == Route.SIMULATION_ONLY


def test_margin_approved_when_enabled_within_caps():
    decision = evaluate_proposal(margin_long(), perms(ENABLE_PAPER_MARGIN="true"), acct())
    assert decision.approved is True
    assert decision.approved_quantity is not None


def test_margin_rejected_over_gross_exposure():
    decision = evaluate_proposal(
        margin_long(),
        perms(ENABLE_PAPER_MARGIN="true", MAX_GROSS_EXPOSURE="1.0"),
        acct(current_gross_exposure=0.95),
    )
    assert decision.approved is False
    assert decision.route == Route.REJECTED


def test_margin_rejected_over_net_exposure():
    decision = evaluate_proposal(
        margin_long(),
        perms(ENABLE_PAPER_MARGIN="true", MAX_NET_EXPOSURE="1.0"),
        acct(current_net_exposure=0.95),
    )
    assert decision.approved is False


def test_margin_requires_buying_power():
    decision = evaluate_proposal(
        margin_long(),
        perms(ENABLE_PAPER_MARGIN="true"),
        acct(buying_power=None),
    )
    assert decision.approved is False


def test_margin_forced_deleveraging_flagged():
    decision = evaluate_proposal(
        margin_long(),
        perms(ENABLE_PAPER_MARGIN="true", MAX_GROSS_EXPOSURE="1.0"),
        acct(current_gross_exposure=1.5),
    )
    assert decision.approved is False
    assert decision.forced_deleveraging_required is True


# --- options ---


def test_options_rejected_when_disabled():
    decision = evaluate_proposal(option_long_call(), perms(ENABLE_PAPER_OPTIONS="false"), acct())
    assert decision.approved is False
    assert decision.route == Route.SIMULATION_ONLY


def test_options_approved_when_enabled_and_defined_risk():
    decision = evaluate_proposal(option_long_call(), perms(ENABLE_PAPER_OPTIONS="true"), acct())
    assert decision.approved is True
    assert decision.approved_contracts == 1
    assert decision.premium_at_risk == 400.0


def test_options_rejected_0dte():
    decision = evaluate_proposal(
        option_long_call(expiration=date.today()),
        perms(ENABLE_PAPER_OPTIONS="true"),
        acct(),
    )
    assert decision.approved is False
    assert any("0DTE" in r for r in decision.reasons)


def test_options_rejected_below_min_dte():
    decision = evaluate_proposal(
        option_long_call(expiration=date.today() + timedelta(days=3)),
        perms(ENABLE_PAPER_OPTIONS="true", MIN_OPTIONS_DTE="7"),
        acct(),
    )
    assert decision.approved is False
    assert any("DTE" in r for r in decision.reasons)


def test_options_rejected_naked_short():
    decision = evaluate_proposal(naked_short_call(), perms(ENABLE_PAPER_OPTIONS="true"), acct())
    assert decision.approved is False
    assert any("naked" in r.lower() or "uncovered" in r.lower() for r in decision.reasons)


def test_options_rejected_when_max_loss_missing():
    decision = evaluate_proposal(
        option_long_call(max_loss=None),
        perms(ENABLE_PAPER_OPTIONS="true"),
        acct(),
    )
    assert decision.approved is False
    assert any("max loss" in r.lower() for r in decision.reasons)


def test_options_rejected_premium_over_cap():
    decision = evaluate_proposal(
        option_long_call(),
        perms(ENABLE_PAPER_OPTIONS="true"),
        acct(equity=1000.0, cash=1000.0, buying_power=2000.0),
    )
    assert decision.approved is False
    assert any("premium at risk" in r.lower() for r in decision.reasons)


def test_options_rejected_contracts_over_cap():
    decision = evaluate_proposal(
        option_long_call(contracts=5),
        perms(ENABLE_PAPER_OPTIONS="true", MAX_OPTIONS_CONTRACTS_PER_TRADE="2"),
        acct(),
    )
    assert decision.approved is False


def test_greeks_logged_as_unavailable_when_missing():
    decision = evaluate_proposal(option_long_call(), perms(ENABLE_PAPER_OPTIONS="true"), acct())
    assert decision.greeks_available is False
    assert any("unavailable" in note.lower() for note in decision.notes)


# --- deterministic sizing / LLM cannot set quantity ---


def test_deterministic_quantity_is_bounded_not_llm_driven():
    # The proposal requests a 90% weight but max_position_weight caps it at 20%.
    decision = evaluate_proposal(
        stock_long(target_weight=0.90),
        perms(MAX_POSITION_WEIGHT="0.20"),
        acct(),
    )
    expected = math.floor(0.20 * EQUITY / 500.0)
    assert decision.approved is True
    assert decision.approved_quantity == float(expected)


def test_llm_cannot_inflate_short_size_beyond_single_short_cap():
    decision = evaluate_proposal(
        stock_short(target_weight=0.90),
        perms(ENABLE_PAPER_SHORTING="true", MAX_SINGLE_SHORT_WEIGHT="0.10"),
        acct(),
    )
    expected = math.floor(0.10 * EQUITY / 50.0)
    assert decision.approved_quantity == float(expected)
