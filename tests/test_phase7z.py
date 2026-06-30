"""Phase 7Z — candidate-generation integrity, fresh-state grounding, truthful SPY.

Mocked + deterministic. No real credentials, no broker network, no LLM calls, no
market hours. Proves:

* Fresh broker zero-position/full-cash state overrides stale XYZ/low-BP memory.
* Genuine current low buying power still blocks new entries.
* A broker read failure fails safe (account_state_unavailable) and never invents
  an empty account.
* A healthy current state reaches candidate generation without forcing a trade.
* Each no-trade reason class is persisted and visible, and model-zero / provider
  failure / invalid output / risk rejection are distinguishable.
* Same-period benchmark math is correct; missing anchors -> n/a (no false beat/loss).
* Paper-only / deterministic-risk / cap / kill-switch / no-LLM-execution guarantees
  remain unchanged.
"""

from __future__ import annotations

import inspect

import pytest

from competition_helpers import stock_long
from src.competition.benchmark import (
    TIMEFRAME_WEEKLY,
    BenchmarkAnchors,
    build_benchmark_anchors,
    safe_excess,
)
from src.competition.broker_snapshot import (
    STATUS_ACCOUNT_STATE_UNAVAILABLE,
    STATUS_OK,
    build_snapshot_from_parts,
    summarize_positions,
    unavailable_snapshot,
)
from src.competition.candidate_generation import (
    ACCOUNT_STATE_UNAVAILABLE,
    AUTONOMY_DISABLED,
    DAILY_CAP_REACHED,
    EXEC_BROKER_CLIENT_UNAVAILABLE,
    EXEC_DRY_RUN,
    EXEC_KILL_SWITCH_ENGAGED,
    EXEC_PAPER_SUBMISSION_NOT_ATTEMPTED,
    EXEC_REVIEW_ONLY,
    EXEC_TEAM_AUTONOMY_DISABLED,
    INVALID_MODEL_OUTPUT,
    LIVE_PORTFOLIO_HEALTH_BLOCK,
    MODEL_ZERO_CANDIDATES,
    NO_CURRENT_SIGNAL,
    PORTFOLIO_MANAGER_HOLD,
    PROVIDER_FAILURE,
    PROVIDER_OUTCOME_FAILURE,
    PROVIDER_OUTCOME_INVALID_OUTPUT,
    PROVIDER_OUTCOME_SUCCESS,
    PROVIDER_OUTCOME_ZERO_CANDIDATES,
    RISK_REJECTED,
    classify_candidate_outcome,
)
from src.competition.portfolio_manager import (
    PortfolioDecisionType,
    PortfolioManagerConfig,
    review_portfolio,
)
from src.competition.risk_engine import AccountContext
from src.competition.state_reconciliation import (
    ACCOUNT_STATE_UNAVAILABLE as RECON_ACCOUNT_UNAVAILABLE,
    CLEAN,
    CONFLICT_STALE_HOLDING,
    CONFLICT_STALE_LOW_BUYING_POWER,
    CONFLICT_STALE_SHORT_EXPOSURE,
    LIVE_PORTFOLIO_HEALTH_BLOCK as RECON_HEALTH_BLOCK,
    STALE_CONTEXT_CORRECTED,
    HistoricalSignals,
    reconcile_state,
)
from src.competition.week_competition import ProposalBundle, run_week_cycle
from src.config.permissions import TradingPermissions


# --- shared fixtures --------------------------------------------------------


def perms(**o):
    base = {"MAX_DAILY_ORDERS_PER_TEAM": 10}
    base.update(o)
    return TradingPermissions.from_env(env={k: str(v) for k, v in base.items()})


def healthy_account(**o):
    base = dict(equity=1_000_000.0, cash=1_000_000.0, buying_power=4_000_000.0)
    base.update(o)
    return AccountContext(**base)


def low_bp_account():
    return AccountContext(equity=1_000_000.0, cash=50_000.0, buying_power=50_000.0)


def _dirs(tmp_path):
    return {
        "competition_dir": tmp_path / "comp",
        "scorecard_dir": tmp_path / "sc",
        "learning_dir": tmp_path / "learn",
        "kill_switch_path": str(tmp_path / "ks.json"),
        "attribution_dir": tmp_path / "attr",
    }


def _pos(symbol, side="long", qty=10.0):
    return {"symbol": symbol, "side": side, "qty": qty, "market_value": 1000.0, "avg_entry_price": 100.0}


def healthy_snapshot(**o):
    base = dict(equity=1_000_000.0, cash=1_000_000.0, buying_power=4_000_000.0)
    base.update(o)
    return build_snapshot_from_parts(
        "team_alpha", account=base, raw_positions=[], account_read_ok=True
    )


# --- broker snapshot grounding ---------------------------------------------


def test_snapshot_available_reads_account_and_positions():
    snap = build_snapshot_from_parts(
        "team_alpha",
        account={"equity": 1_000_000.0, "cash": 1_000_000.0, "buying_power": 4_000_000.0},
        raw_positions=[_pos("NVDA"), _pos("XYZ", side="short")],
        account_read_ok=True,
    )
    assert snap.is_available is True
    assert snap.status == STATUS_OK
    assert snap.position_count == 2
    assert snap.short_position_count == 1
    assert "NVDA" in snap.held_symbols and "XYZ" in snap.short_symbols
    assert snap.buying_power_ratio() == pytest.approx(4.0)


def test_broker_read_failure_fails_safe_and_invents_nothing():
    snap = unavailable_snapshot("team_alpha", classification="unauthorized_401")
    assert snap.is_available is False
    assert snap.status == STATUS_ACCOUNT_STATE_UNAVAILABLE
    # Crucially: positions are UNKNOWN (None), never zero; no cash invented.
    assert snap.position_count is None
    assert snap.equity is None and snap.cash is None and snap.buying_power is None
    assert snap.is_flat is False  # we do NOT claim a flat book


def test_summarize_positions_counts_sides():
    summary = summarize_positions([_pos("AAPL"), _pos("MSFT"), _pos("TSLA", side="short")])
    assert summary["position_count"] == 3
    assert summary["short_position_count"] == 1
    assert summary["long_position_count"] == 2


# --- state reconciliation ---------------------------------------------------


def test_fresh_flat_account_overrides_stale_xyz_and_low_bp_memory():
    # Live: zero positions, full cash, healthy BP. Historical memory references an
    # XYZ holding, claims low buying power, and references short exposure.
    snap = healthy_snapshot()
    historical = HistoricalSignals(
        referenced_symbols=["XYZ", "SPY"],
        claims_low_buying_power=True,
        claims_short_exposure=True,
    )
    result = reconcile_state(snap, historical)
    assert result.status == STALE_CONTEXT_CORRECTED
    kinds = {c.kind for c in result.conflicts}
    assert CONFLICT_STALE_HOLDING in kinds
    assert CONFLICT_STALE_LOW_BUYING_POWER in kinds
    assert CONFLICT_STALE_SHORT_EXPOSURE in kinds
    # Current facts win; conflicts are surfaced as compact warnings.
    assert result.current_position_count == 0
    assert any("XYZ" in w for w in result.warnings())


def test_reconciliation_clean_when_memory_matches_live():
    snap = build_snapshot_from_parts(
        "team_alpha",
        account={"equity": 1_000_000.0, "cash": 500_000.0, "buying_power": 2_000_000.0},
        raw_positions=[_pos("NVDA")],
        account_read_ok=True,
    )
    result = reconcile_state(snap, HistoricalSignals(referenced_symbols=["NVDA"]))
    assert result.status == CLEAN
    assert result.conflicts == []


def test_reconciliation_account_unavailable_does_not_refute_history():
    snap = unavailable_snapshot("team_alpha")
    result = reconcile_state(snap, HistoricalSignals(referenced_symbols=["XYZ"], claims_low_buying_power=True))
    assert result.status == RECON_ACCOUNT_UNAVAILABLE
    # No stale-correction asserted; positions remain unknown.
    assert result.conflicts == []
    assert result.current_position_count is None


def test_reconciliation_live_health_block_is_not_stale():
    snap = build_snapshot_from_parts(
        "team_alpha",
        account={"equity": 1_000_000.0, "cash": 50_000.0, "buying_power": 50_000.0},
        raw_positions=[_pos("NVDA")],
        account_read_ok=True,
    )
    result = reconcile_state(
        snap, HistoricalSignals(referenced_symbols=["NVDA"]),
        current_health_block=True, current_health_reason="low BP now",
    )
    assert result.status == RECON_HEALTH_BLOCK
    assert result.current_low_buying_power is True


# --- candidate-generation outcome classification ---------------------------


def _classify(**o):
    base = dict(
        team_id="team_alpha",
        account_available=True,
        execution_config_enabled=True,
        health_block=False,
        portfolio_manager_allows_new=True,
        portfolio_manager_is_genuine_hold=False,
        provider_called=True,
        provider_failed=False,
        invalid_model_output=False,
        parsed_proposal_count=0,
        routed_execution_eligible=0,
        routed_simulation_only=0,
        routed_rejected=0,
        orders_submitted=0,
    )
    base.update(o)
    return classify_candidate_outcome(**base)


def test_healthy_full_cash_reaches_candidate_generation_without_forcing_trade():
    out = _classify(parsed_proposal_count=0)
    # A healthy zero-position/full-cash account REACHES generation...
    assert out.candidate_generation_allowed is True
    assert out.reached_candidate_generation is True
    # ...and is NOT forced to trade: zero candidates is a clean classified no-trade.
    assert out.no_trade_reason_class == MODEL_ZERO_CANDIDATES
    assert out.execution_block_reason is None
    assert out.orders_submitted == 0


def test_model_zero_vs_provider_failure_vs_invalid_vs_risk_distinguishable():
    zero = _classify(parsed_proposal_count=0)
    failure = _classify(provider_failed=True, parsed_proposal_count=0)
    invalid = _classify(invalid_model_output=True, parsed_proposal_count=0)
    risk = _classify(parsed_proposal_count=2, routed_execution_eligible=0, routed_rejected=2)

    assert zero.no_trade_reason_class == MODEL_ZERO_CANDIDATES
    assert zero.provider_outcome == PROVIDER_OUTCOME_ZERO_CANDIDATES
    assert failure.no_trade_reason_class == PROVIDER_FAILURE
    assert failure.provider_outcome == PROVIDER_OUTCOME_FAILURE
    assert invalid.no_trade_reason_class == INVALID_MODEL_OUTPUT
    assert invalid.provider_outcome == PROVIDER_OUTCOME_INVALID_OUTPUT
    assert risk.no_trade_reason_class == RISK_REJECTED
    # All four are distinct classes.
    assert len({zero.no_trade_reason_class, failure.no_trade_reason_class,
                invalid.no_trade_reason_class, risk.no_trade_reason_class}) == 4


def test_account_unavailable_and_config_and_health_take_precedence():
    assert _classify(account_available=False).no_trade_reason_class == ACCOUNT_STATE_UNAVAILABLE
    # Non-paper / stocks-off config => no execution-eligible route => AUTONOMY_DISABLED.
    assert _classify(execution_config_enabled=False).no_trade_reason_class == AUTONOMY_DISABLED
    assert _classify(health_block=True).no_trade_reason_class == LIVE_PORTFOLIO_HEALTH_BLOCK


def test_portfolio_manager_genuine_hold_and_daily_cap():
    hold = _classify(
        provider_called=True, parsed_proposal_count=3,
        portfolio_manager_allows_new=False, portfolio_manager_is_genuine_hold=True,
    )
    assert hold.no_trade_reason_class == PORTFOLIO_MANAGER_HOLD
    assert hold.execution_block_reason is None

    cap = _classify(parsed_proposal_count=3, routed_execution_eligible=0,
                    routed_simulation_only=3, daily_cap_reached=True)
    assert cap.no_trade_reason_class == DAILY_CAP_REACHED


def test_order_submitted_has_no_no_trade_reason():
    out = _classify(parsed_proposal_count=1, routed_execution_eligible=1, orders_submitted=1)
    assert out.no_trade_reason_class is None
    assert out.execution_block_reason is None
    assert out.provider_outcome == PROVIDER_OUTCOME_SUCCESS


def test_provider_not_called_default_source_no_signal():
    out = _classify(provider_called=False, parsed_proposal_count=0)
    assert out.no_trade_reason_class == NO_CURRENT_SIGNAL


# --- no-trade vs submission/execution block (requirement 2/3) ---------------


def test_approved_but_unsubmitted_is_execution_block_not_no_trade():
    # The Alpha-style case: proposals=3, approved=3, orders_submitted=0. This is a
    # submission block, NOT a no-trade — no_trade_reason_class must be None.
    out = _classify(parsed_proposal_count=3, routed_execution_eligible=3,
                    orders_submitted=0, dry_run=True)
    assert out.no_trade_reason_class is None
    assert out.execution_block_reason == EXEC_DRY_RUN
    assert out.is_no_trade is False
    assert out.is_submission_blocked is True


def test_execution_block_precedence_kill_switch_then_review_then_dry_run():
    ks = _classify(parsed_proposal_count=2, routed_execution_eligible=2, orders_submitted=0,
                   kill_switch_engaged=True, dry_run=True)
    assert ks.execution_block_reason == EXEC_KILL_SWITCH_ENGAGED
    rev = _classify(parsed_proposal_count=2, risk_approved_count=2, routed_execution_eligible=0,
                    routed_simulation_only=2, orders_submitted=0, review_only=True)
    assert rev.execution_block_reason == EXEC_REVIEW_ONLY
    assert rev.no_trade_reason_class is None  # review-only demotes eligible -> still a block


def test_team_autonomy_disabled_is_execution_block():
    out = _classify(parsed_proposal_count=2, routed_execution_eligible=2, orders_submitted=0,
                    team_autonomy_enabled=False)
    assert out.execution_block_reason == EXEC_TEAM_AUTONOMY_DISABLED
    assert out.no_trade_reason_class is None


def test_autonomy_enabled_but_broker_client_unavailable_is_execution_block():
    out = _classify(parsed_proposal_count=2, routed_execution_eligible=2, orders_submitted=0,
                    team_autonomy_enabled=True, broker_client_available=False)
    assert out.execution_block_reason == EXEC_BROKER_CLIENT_UNAVAILABLE
    assert out.no_trade_reason_class is None


def test_approved_unsubmitted_with_no_mode_flag_is_paper_submission_not_attempted():
    out = _classify(parsed_proposal_count=2, routed_execution_eligible=2, orders_submitted=0)
    assert out.execution_block_reason == EXEC_PAPER_SUBMISSION_NOT_ATTEMPTED
    assert out.no_trade_reason_class is None


def test_genuine_model_zero_and_provider_failure_remain_no_trade_not_block():
    zero = _classify(parsed_proposal_count=0, routed_execution_eligible=0)
    assert zero.no_trade_reason_class == MODEL_ZERO_CANDIDATES
    assert zero.execution_block_reason is None
    failure = _classify(provider_failed=True, parsed_proposal_count=0, routed_execution_eligible=0)
    assert failure.no_trade_reason_class == PROVIDER_FAILURE
    assert failure.execution_block_reason is None


# --- benchmark integrity ----------------------------------------------------


def test_same_period_excess_is_correct_not_inflated():
    # The reported bug: team_return 0.0000, SPY -0.0012 -> excess must be +0.0012,
    # never +0.0113 (which came from mixing a live return with a stale SPY anchor).
    assert safe_excess(0.0, -0.0012) == pytest.approx(0.0012)
    anchors = build_benchmark_anchors(
        "team_alpha", timeframe=TIMEFRAME_WEEKLY,
        team_start_equity=1_000_000.0, team_end_equity=1_000_000.0,  # team return 0.0
        spy_start_price=500.0, spy_end_price=499.4,                  # ~ -0.0012
    )
    assert anchors.team_return == pytest.approx(0.0)
    assert anchors.spy_return == pytest.approx(-0.0012)
    assert anchors.excess_return == pytest.approx(0.0012)
    assert abs(anchors.excess_return - 0.0113) > 1e-4  # not the inflated value
    assert "beat SPY" in anchors.spy_relative_phrase()


def test_missing_anchors_produce_na_not_false_claim():
    assert safe_excess(0.0, None) is None
    assert safe_excess(None, -0.0012) is None
    anchors = build_benchmark_anchors(
        "team_alpha", team_start_equity=1_000_000.0, team_end_equity=1_000_000.0,
        spy_start_price=None, spy_end_price=None,
    )
    assert anchors.anchors_available is False
    assert anchors.excess_return is None
    phrase = anchors.spy_relative_phrase()
    assert "n/a" in phrase
    assert "beat" not in phrase and "trailed" not in phrase


def test_zero_or_invalid_start_anchor_is_na():
    anchors = BenchmarkAnchors(
        team_id="t", team_start_equity=0.0, team_end_equity=1.0,
        spy_start_price=500.0, spy_end_price=505.0,
    )
    assert anchors.team_return is None  # divide-by-zero guarded
    assert anchors.excess_return is None


# --- integration via run_week_cycle ----------------------------------------


def test_cycle_records_no_trade_reason_class_for_model_zero(tmp_path):
    def zero_source(_team):
        return ProposalBundle(proposals=[], provider_called=True, provider_failed=False,
                              provider_name="openai", model_name="gpt-test")

    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        proposal_source=zero_source, dry_run=True, **_dirs(tmp_path),
    )
    assert result.candidate_outcome.reached_candidate_generation is True
    assert result.no_trade_reason_class == MODEL_ZERO_CANDIDATES
    # Persisted + visible on the scorecard (never null after a completed cycle).
    assert result.scorecard.no_trade_reason_class == MODEL_ZERO_CANDIDATES
    assert result.scorecard.routed_provider == "openai"
    assert result.scorecard.candidate_generation_outcome["provider_outcome"] == PROVIDER_OUTCOME_ZERO_CANDIDATES


def test_cycle_records_provider_failure(tmp_path):
    def failed_source(_team):
        return ProposalBundle(proposals=[], provider_called=True, provider_failed=True,
                              raw_errors=["LLM call failed: boom"], provider_name="openai")

    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        proposal_source=failed_source, dry_run=True, **_dirs(tmp_path),
    )
    assert result.no_trade_reason_class == PROVIDER_FAILURE
    assert result.scorecard.no_trade_reason_class == PROVIDER_FAILURE


def test_cycle_unavailable_account_grounds_as_account_state_unavailable(tmp_path):
    snap = unavailable_snapshot("team_alpha", classification="unauthorized_401")
    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        snapshot=snap, dry_run=True, **_dirs(tmp_path),
    )
    assert result.no_trade_reason_class == ACCOUNT_STATE_UNAVAILABLE
    assert result.scorecard.account_read_ok is False
    assert result.scorecard.reconciliation_status == "account_state_unavailable"


def test_cycle_risk_rejection_recorded(tmp_path):
    # A stock long priced so high the deterministic engine rounds qty below 1 share.
    def reject_source(_team):
        return [stock_long(symbol="ZZZ", estimated_price=1e12)]

    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        proposal_source=reject_source, dry_run=True, **_dirs(tmp_path),
    )
    assert result.routing.rejected, "proposal should be rejected by deterministic risk"
    assert result.no_trade_reason_class == RISK_REJECTED


def test_genuine_low_buying_power_still_blocks_new_entries(tmp_path):
    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=low_bp_account(),
        dry_run=True, **_dirs(tmp_path),
    )
    # Deterministic gate unchanged: low BP blocks new-money buys -> no-trade.
    assert result.portfolio_decision.low_buying_power is True
    assert result.portfolio_decision.is_no_trade() is True
    assert result.no_trade_reason_class == LIVE_PORTFOLIO_HEALTH_BLOCK


def test_cycle_benchmark_anchors_persisted(tmp_path):
    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        dry_run=True, spy_start_price=500.0, spy_current_price=499.4,
        benchmark_timeframe=TIMEFRAME_WEEKLY, **_dirs(tmp_path),
    )
    assert result.scorecard.spy_start_price == 500.0
    assert result.scorecard.spy_end_price == 499.4
    assert result.scorecard.benchmark_timeframe == TIMEFRAME_WEEKLY


# --- PM evidence + history-alone guards ------------------------------------


def test_no_trade_decision_names_current_evidence_source():
    low = review_portfolio(
        team_id="team_alpha", config=PortfolioManagerConfig(), permissions=perms(),
        account=low_bp_account(), candidate_count=3,
    )
    assert low.is_no_trade() is True
    assert low.no_trade_evidence_source == "current_account_state"

    zero = review_portfolio(
        team_id="team_alpha", config=PortfolioManagerConfig(), permissions=perms(),
        account=healthy_account(), candidate_count=0,
    )
    assert zero.no_trade_evidence_source == "current_market_research_evidence"


def test_history_failure_streak_alone_does_not_zero_a_healthy_cap():
    feedback = {"outcome_feedback": {"worked_count": 0, "failed_count": 5, "pending_count": 0}}
    decision = review_portfolio(
        team_id="team_beta", config=PortfolioManagerConfig(max_new_proposals_beta=1),
        permissions=perms(), account=healthy_account(), candidate_count=3, spy_excess=0.0,
        attribution_feedback=feedback,
    )
    # Historical losses alone must not force max_new=0 on a healthy cycle.
    assert decision.max_new_proposals_this_cycle >= 1
    assert decision.allowed_to_generate_new_orders is True


def test_llm_history_only_hold_downgraded_when_no_current_support():
    decision = review_portfolio(
        team_id="team_alpha", config=PortfolioManagerConfig(), permissions=perms(),
        account=healthy_account(), candidate_count=3, spy_excess=0.05,  # ahead of SPY
        llm_intent={"decision_type": "no_trade", "rationale": "old playbook says avoid"},
    )
    # No current condition supports a hard block -> the model hold is advisory only.
    assert decision.allowed_to_generate_new_orders is True
    assert "advisory" in decision.risk_notes.lower()


# --- safety guarantees unchanged -------------------------------------------


def test_new_modules_never_submit_orders():
    import src.competition.broker_snapshot as bs
    import src.competition.candidate_generation as cg
    import src.competition.state_reconciliation as sr
    import src.competition.benchmark as bm

    forbidden = ("submit_paper_order", "submit_order", "submit_paper_short_order",
                 "submit_paper_option_order", "submit_paper_margin_order")
    for module in (bs, cg, sr, bm):
        source = inspect.getsource(module)
        assert not any(name in source for name in forbidden), module.__name__


def test_blank_key_cli_fails_fast(monkeypatch):
    # CLI invocation (fail_fast default True): a missing/blank key SystemExits
    # before any broker work.
    import src.main as main
    from src.agents.llm_provider import LLMProviderError

    def _boom(*a, **k):
        raise LLMProviderError("OPENAI_API_KEY is missing.")

    monkeypatch.setattr(main, "build_routed_provider", _boom)
    monkeypatch.setattr(main, "_broker_snapshot_for_source",
                        lambda *a, **k: pytest.fail("snapshot must not be built on fail-fast"))
    with pytest.raises(SystemExit):
        main.run_week_cycle_cli("team_alpha", proposal_source="llm")


def test_loop_blank_key_records_provider_failure_not_silent_zero(monkeypatch):
    # Loop invocation (fail_fast=False): a blank key must NOT SystemExit and must
    # run a GROUNDED provider_failure cycle — never a silent model-zero result.
    import src.main as main
    from src.agents.llm_provider import LLMProviderError

    snap = build_snapshot_from_parts(
        "team_alpha", account={"equity": 1e6, "cash": 1e6, "buying_power": 4e6},
        raw_positions=[], account_read_ok=True,
    )

    def _boom(*a, **k):
        raise LLMProviderError("OPENAI_API_KEY is missing.")

    monkeypatch.setattr(main, "build_routed_provider", _boom)
    monkeypatch.setattr(main, "_broker_snapshot_for_source", lambda *a, **k: snap)
    from src.competition.state_reconciliation import HistoricalSignals, reconcile_state
    monkeypatch.setattr(main, "_reconcile_team_state", lambda *a, **k: reconcile_state(snap, HistoricalSignals()))
    monkeypatch.setattr(main, "_market_data_price_fn", lambda settings: None)
    monkeypatch.setattr(main, "_safe_read_client", lambda team, settings: None)
    monkeypatch.setattr(main, "load_competition_state", lambda *a, **k: type(
        "S", (), {"starting_spy_price": None, "week_start": None, "week_end": None, "starting_equity": 1e6})())
    monkeypatch.setattr(main, "read_kill_switch", lambda *a, **k: type("K", (), {"engaged": False})())

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _capture(team, **kwargs):
        captured["source"] = kwargs.get("proposal_source")
        captured["snapshot"] = kwargs.get("snapshot")
        raise _Stop()

    monkeypatch.setattr(main, "run_week_cycle", _capture)

    with pytest.raises(_Stop):  # reached run_week_cycle -> NO SystemExit on the blank key
        main.run_week_cycle_cli("team_alpha", proposal_source="llm", fail_fast_on_provider_error=False)

    # The cycle is grounded on the fresh snapshot and fed a provider-FAILED bundle.
    assert captured["snapshot"] is snap
    bundle = captured["source"]("team_alpha")
    assert bundle.provider_called is True and bundle.provider_failed is True
    assert bundle.proposals == []
    # Run that bundle through the deterministic classifier the cycle uses.
    out = classify_candidate_outcome(
        team_id="team_alpha", account_available=True, execution_config_enabled=True,
        health_block=False, portfolio_manager_allows_new=True,
        portfolio_manager_is_genuine_hold=False, provider_called=True,
        provider_failed=True, invalid_model_output=False, parsed_proposal_count=0,
        routed_execution_eligible=0, routed_simulation_only=0, routed_rejected=0,
        orders_submitted=0,
    )
    assert out.no_trade_reason_class == PROVIDER_FAILURE


def test_kill_switch_engaged_yields_execution_block_not_no_trade(tmp_path):
    # Kill switch with execution-eligible candidates is a SUBMISSION block, not a
    # no-trade: no_trade_reason_class must be None, execution_block_reason set.
    ks_path = str(tmp_path / "ks.json")
    from src.safety.kill_switch import engage

    engage(path=ks_path)
    d = _dirs(tmp_path)
    d["kill_switch_path"] = ks_path
    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        proposal_source=lambda _t: [stock_long()], dry_run=False, **d,
    )
    assert result.kill_switch_engaged is True
    assert sum(1 for r in result.execution_records if r.submitted) == 0
    assert result.routing.execution_eligible, "stock long should be risk-approved"
    assert result.no_trade_reason_class is None
    assert result.candidate_outcome.execution_block_reason == EXEC_KILL_SWITCH_ENGAGED
    assert result.scorecard.execution_block_reason == EXEC_KILL_SWITCH_ENGAGED


def test_alpha_style_dry_run_approved_is_execution_block_not_autonomy_disabled(tmp_path):
    # The exact reported Alpha inconsistency: portfolio_decision=add, approved>0,
    # orders_submitted=0. Must report a submission/execution block, NOT a no-trade.
    result = run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        proposal_source=lambda _t: [stock_long()], dry_run=True, **_dirs(tmp_path),
    )
    assert result.routing.execution_eligible, "stock long should be execution-eligible"
    assert sum(1 for r in result.execution_records if r.submitted) == 0
    assert result.no_trade_reason_class is None              # NOT autonomy_disabled
    assert result.scorecard.no_trade_reason_class is None
    assert result.candidate_outcome.execution_block_reason == EXEC_DRY_RUN
    assert result.scorecard.execution_block_reason == EXEC_DRY_RUN
