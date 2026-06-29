"""Tests for the non-trading loop diagnostic classifier + formatter (Phase 7U)."""

from __future__ import annotations

from dataclasses import replace

from src.competition import loop_diagnostics as ld
from src.competition.loop_diagnostics import (
    TeamLoopFacts,
    classify_diagnosis,
    classify_last_cycle,
    format_team_report,
)


def base_facts(**overrides) -> TeamLoopFacts:
    """A healthy, market-open, fully-cleared team with a successful last cycle."""

    facts = TeamLoopFacts(
        team_id="team_alpha",
        local_iso="2026-06-29T09:35:00-04:00",
        ny_iso="2026-06-29T09:35:00-04:00",
        market_is_open=True,
        clock_next_open="2026-06-30T09:30:00-04:00",
        clock_next_close="2026-06-29T16:00:00-04:00",
        account_ok=True,
        account_classification="ok",
        equity=1_000_000.0,
        cash=500_000.0,
        buying_power=800_000.0,
        open_positions=3,
        low_buying_power=False,
        orders_today=1,
        max_daily_orders_per_team=200,
        cheap_gate_enabled=True,
        gate_should_run_full_cycle=True,
        proposals_count=3,
        approved_count=2,
        simulation_only_count=1,
        rejected_count=0,
        orders_submitted=2,
        broker_rejected_count=0,
        portfolio_decision_type="add",
        portfolio_no_trade=False,
    )
    return replace(facts, **overrides)


def test_ready_when_market_open_and_clear():
    diag = classify_diagnosis(base_facts())
    assert diag.diagnosis == ld.READY


def test_market_closed_when_clock_closed_and_no_persistent_blocker():
    facts = base_facts(market_is_open=False, low_buying_power=False)
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.MARKET_CLOSED


def test_config_disabled_kill_switch():
    diag = classify_diagnosis(base_facts(kill_switch_engaged=True))
    assert diag.diagnosis == ld.CONFIG_DISABLED


def test_config_disabled_dry_run():
    diag = classify_diagnosis(base_facts(dry_run=True))
    assert diag.diagnosis == ld.CONFIG_DISABLED


def test_config_disabled_stocks_off():
    diag = classify_diagnosis(base_facts(stocks_enabled=False))
    assert diag.diagnosis == ld.CONFIG_DISABLED


def test_cap_reached_takes_precedence_over_market_closed():
    facts = base_facts(orders_today=200, max_daily_orders_per_team=200, market_is_open=False)
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.CAP_REACHED


def test_python_risk_rejected_low_buying_power_even_when_closed():
    # team_alpha real-world case: BP exhausted; surfaced even with market closed.
    facts = base_facts(market_is_open=False, buying_power=0.0, low_buying_power=True)
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.PYTHON_RISK_REJECTED


def test_broker_error_when_account_unreachable():
    facts = base_facts(account_ok=False, account_classification="unauthorized_401")
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.BROKER_ERROR


def test_no_executable_proposals_when_open_and_empty():
    facts = base_facts(
        proposals_count=0, approved_count=0, orders_submitted=0,
        portfolio_no_trade=True, portfolio_decision_type="no_trade",
    )
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.NO_EXECUTABLE_PROPOSALS


def test_agent_gate_failed_when_gate_holds_back():
    facts = base_facts(
        cheap_gate_enabled=True,
        gate_should_run_full_cycle=False,
        gate_reason="interval not elapsed",
        # last cycle was a healthy submit so only the live gate blocks now.
        orders_submitted=2, proposals_count=3, approved_count=2,
    )
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.AGENT_GATE_FAILED


def test_loop_not_running_when_nothing_recorded():
    facts = base_facts(proposals_count=None, last_audit_iso=None, orders_submitted=None)
    diag = classify_diagnosis(facts)
    assert diag.diagnosis == ld.LOOP_NOT_RUNNING


def test_classify_last_cycle_variants():
    assert classify_last_cycle(base_facts(broker_rejected_count=1)) == ld.BROKER_ERROR
    assert classify_last_cycle(base_facts(orders_submitted=2)) == ld.READY
    assert classify_last_cycle(
        base_facts(orders_submitted=0, proposals_count=0)
    ) == ld.NO_EXECUTABLE_PROPOSALS
    assert classify_last_cycle(
        base_facts(orders_submitted=0, proposals_count=3, approved_count=0, rejected_count=3)
    ) == ld.PYTHON_RISK_REJECTED


def test_report_contains_no_secret_like_values():
    facts = base_facts()
    diag = classify_diagnosis(facts)
    report = format_team_report(facts, diag)
    lowered = report.lower()
    # No API-key/secret/token material should ever appear in the rendered report.
    for needle in ("secret", "api_key", "apikey", "authorization", "bearer", "password"):
        assert needle not in lowered
    assert "DIAGNOSIS [team_alpha]" in report


def test_all_diagnoses_are_known_strings():
    facts = base_facts()
    diag = classify_diagnosis(facts)
    assert diag.diagnosis in ld.ALL_DIAGNOSES
