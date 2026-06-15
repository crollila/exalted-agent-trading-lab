"""Phase 7M Portfolio Manager / Capital Allocator. Deterministic; no network.

No real credentials, no OpenAI calls, no broker network, no market hours.
"""

from __future__ import annotations

import json

import pytest

from competition_helpers import stock_long, stock_short
from src.competition.attribution import load_team_attribution
from src.competition.execution import classify_broker_error
from src.competition.portfolio_manager import (
    PortfolioDecisionType,
    PortfolioManagerConfig,
    review_portfolio,
)
from src.competition.risk_engine import AccountContext
from src.competition.router import route_proposals
from src.competition.week_competition import apply_portfolio_gate, run_week_cycle
from src.config.permissions import TradingPermissions


def perms(**o):
    base = {"MAX_DAILY_ORDERS_PER_TEAM": 10, "ENABLE_PAPER_SHORTING": "true"}
    base.update(o)
    return TradingPermissions.from_env(env={k: str(v) for k, v in base.items()})


def cfg(**o):
    return PortfolioManagerConfig(**o)


def healthy_account(**o):
    base = dict(equity=1_000_000.0, cash=1_000_000.0, buying_power=2_000_000.0)
    base.update(o)
    return AccountContext(**base)


def low_bp_account():
    # buying power well under the default 15% of equity threshold.
    return AccountContext(equity=1_000_000.0, cash=50_000.0, buying_power=50_000.0)


# --- dynamic caps + personality -------------------------------------------


def test_alpha_gets_higher_cap_than_beta_same_conditions():
    alpha = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=5, spy_excess=0.0,
    )
    beta = review_portfolio(
        team_id="team_beta", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=5, spy_excess=0.0,
    )
    assert alpha.max_new_proposals_this_cycle > beta.max_new_proposals_this_cycle
    assert alpha.allowed_to_generate_new_orders is True
    assert beta.allowed_to_generate_new_orders is True


def test_beta_defaults_to_conservative_cap():
    beta = review_portfolio(
        team_id="team_beta", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=5, spy_excess=0.01,
    )
    assert beta.max_new_proposals_this_cycle <= 2
    assert beta.mode == "conservation"


def test_alpha_rotates_when_behind_spy_beta_holds():
    alpha = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=5, spy_excess=-0.05,
        positions=[{"symbol": "AAPL"}],
    )
    beta = review_portfolio(
        team_id="team_beta", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=5, spy_excess=-0.05,
        positions=[{"symbol": "AAPL"}],
    )
    assert alpha.decision_type == PortfolioDecisionType.ROTATE.value
    assert alpha.allowed_to_generate_new_orders is True
    assert beta.decision_type == PortfolioDecisionType.HOLD.value
    assert beta.is_no_trade() is True  # beta preserves capital when behind


def test_hard_cap_is_respected_over_team_cap():
    # Platform hard cap (1) wins even though alpha's tactical cap is 3.
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(max_new_proposals_alpha=3),
        permissions=perms(MAX_DAILY_ORDERS_PER_TEAM=1),
        account=healthy_account(), candidate_count=5, spy_excess=0.0,
    )
    assert decision.max_new_proposals_this_cycle == 1


# --- no-trade ---------------------------------------------------------------


def test_no_candidates_is_no_trade():
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=0,
    )
    assert decision.decision_type == PortfolioDecisionType.NO_TRADE.value
    assert decision.is_no_trade() is True


# --- low buying power -------------------------------------------------------


def test_low_buying_power_triggers_review_not_hard_stop():
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=low_bp_account(), candidate_count=3, positions=[{"symbol": "AAPL"}],
    )
    assert decision.low_buying_power is True
    # Review happened (a decision exists) and new-money buys are blocked.
    assert decision.allowed_to_generate_new_orders is False
    assert decision.rejected_new_ideas_reason is not None
    assert decision.decision_type == PortfolioDecisionType.REDUCE_GROSS_EXPOSURE.value


def test_low_buying_power_blocks_new_buys_without_freeing_action():
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=low_bp_account(), candidate_count=3,
    )
    assert decision.is_no_trade() is True


def test_low_buying_power_allows_rotate_to_free_room():
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=low_bp_account(), candidate_count=3, positions=[{"symbol": "AAPL"}],
        llm_intent={"decision_type": "rotate", "proposed_closes_or_trims": ["AAPL"]},
    )
    assert decision.decision_type == PortfolioDecisionType.ROTATE.value
    assert decision.allowed_to_generate_new_orders is True
    assert decision.max_new_proposals_this_cycle >= 1


def test_low_buying_power_allows_margin_request():
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=low_bp_account(), candidate_count=2,
        llm_intent={"decision_type": "increase_margin_exposure_request"},
    )
    assert decision.decision_type == PortfolioDecisionType.INCREASE_MARGIN_EXPOSURE_REQUEST.value
    assert decision.allowed_to_generate_new_orders is True


def test_llm_cannot_widen_cap():
    decision = review_portfolio(
        team_id="team_beta", config=cfg(max_new_proposals_beta=2), permissions=perms(),
        account=healthy_account(), candidate_count=5, spy_excess=0.0,
        llm_intent={"decision_type": "add", "max_new_proposals_this_cycle": 99},
    )
    assert decision.max_new_proposals_this_cycle <= 2  # LLM request clamped


# --- feedback content -------------------------------------------------------


def test_decision_includes_spy_and_attribution_context():
    feedback = {"outcome_feedback": {"worked_count": 2, "failed_count": 1, "pending_count": 0}}
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=healthy_account(), candidate_count=3, spy_excess=0.03,
        attribution_feedback=feedback,
    )
    assert "SPY" in decision.relation_to_spy_performance
    assert "worked=2" in decision.relation_to_recent_attribution
    assert "failed=1" in decision.relation_to_recent_attribution
    assert decision.review_questions  # self-review questions present


# --- apply_portfolio_gate ---------------------------------------------------


def test_gate_demotes_extra_eligible_to_simulation():
    routing = route_proposals([stock_long(), stock_long(symbol="QQQ"), stock_long(symbol="DIA")], perms(), healthy_account())
    assert len(routing.execution_eligible) == 3
    decision = review_portfolio(
        team_id="team_beta", config=cfg(max_new_proposals_beta=1), permissions=perms(),
        account=healthy_account(), candidate_count=3, spy_excess=0.0,
    )
    gated = apply_portfolio_gate(routing, decision)
    assert len(gated.execution_eligible) == 1
    assert len(gated.simulation_only) == 2
    assert "Portfolio manager" in gated.simulation_only[0].decision.reasons[-1]


def test_gate_blocks_all_when_no_trade():
    routing = route_proposals([stock_long(), stock_long(symbol="QQQ")], perms(), healthy_account())
    decision = review_portfolio(
        team_id="team_alpha", config=cfg(), permissions=perms(),
        account=low_bp_account(), candidate_count=2,
    )
    gated = apply_portfolio_gate(routing, decision)
    assert gated.execution_eligible == []
    assert len(gated.simulation_only) == 2


# --- broker rejection classification ---------------------------------------


def test_classify_insufficient_buying_power():
    cat, reason, code = classify_broker_error(Exception("insufficient buying power for this order"))
    assert cat == "insufficient_buying_power"
    assert "buying power" in reason


def test_classify_wash_trade():
    cat, _, _ = classify_broker_error(Exception("potential wash trade detected"))
    assert cat == "wash_trade"


def test_classify_unknown_broker_error():
    cat, _, _ = classify_broker_error(Exception("connection reset"))
    assert cat == "broker_error"


# --- run_week_cycle integration --------------------------------------------


def _dirs(tmp_path):
    return {
        "competition_dir": tmp_path / "comp",
        "scorecard_dir": tmp_path / "sc",
        "learning_dir": tmp_path / "learn",
        "kill_switch_path": str(tmp_path / "ks.json"),
        "attribution_dir": tmp_path / "attr",
    }


def test_run_cycle_no_trade_skips_execution_and_records(tmp_path):
    d = _dirs(tmp_path)
    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=low_bp_account(),  # low BP -> no-trade
        dry_run=True,
        portfolio_config=cfg(),
        **d,
    )
    assert result.no_trade is True
    assert sum(1 for r in result.execution_records if r.submitted) == 0
    # Still records a scorecard + a valid (non-crashing) result.
    assert result.scorecard is not None
    assert result.scorecard.portfolio_no_trade is True
    assert result.portfolio_decision is not None


def test_run_cycle_normal_allows_capped_orders(tmp_path):
    d = _dirs(tmp_path)
    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=healthy_account(),
        dry_run=True,
        portfolio_config=cfg(),
        **d,
    )
    assert result.portfolio_decision.allowed_to_generate_new_orders is True
    # Default proposals: SPY long is execution-eligible (within cap).
    assert len(result.routing.execution_eligible) >= 1


class _BrokerRejectClient:
    """Fake broker client that always rejects with insufficient buying power."""

    def submit_paper_order(self, order):
        raise RuntimeError("insufficient buying power for this order")

    def submit_paper_short_order(self, order):
        raise RuntimeError("insufficient buying power for this order")

    def submit_paper_margin_order(self, order):
        raise RuntimeError("insufficient buying power for this order")

    def submit_paper_option_order(self, order):
        raise RuntimeError("insufficient buying power for this order")


def test_broker_rejection_recorded_and_flows_to_attribution(tmp_path):
    d = _dirs(tmp_path)
    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=healthy_account(),
        client=_BrokerRejectClient(),
        dry_run=False,  # real submission path -> hits the fake reject
        portfolio_config=cfg(),
        **d,
    )
    rejected = [r for r in result.execution_records if r.broker_rejected]
    assert rejected, "expected at least one broker-rejected record"
    assert rejected[0].failure_category == "insufficient_buying_power"
    assert rejected[0].submitted is False

    # Flows into attribution for future Portfolio Manager context.
    entries = load_team_attribution("team_alpha", attribution_dir=d["attribution_dir"])
    rejected_attr = [e for e in entries if e.broker_rejected]
    assert rejected_attr
    assert rejected_attr[0].failure_category == "insufficient_buying_power"


def test_successful_submission_still_works(tmp_path):
    from types import SimpleNamespace

    d = _dirs(tmp_path)

    class _OkClient:
        def __init__(self):
            self.count = 0

        def _ok(self, order):
            self.count += 1
            return SimpleNamespace(id=f"paper-{self.count}")

        submit_paper_order = _ok
        submit_paper_short_order = _ok
        submit_paper_margin_order = _ok
        submit_paper_option_order = _ok

    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=healthy_account(),
        client=_OkClient(),
        dry_run=False,
        portfolio_config=cfg(),
        **d,
    )
    assert sum(1 for r in result.execution_records if r.submitted) >= 1
    assert not any(r.broker_rejected for r in result.execution_records)


def test_pm_disabled_passthrough(tmp_path):
    d = _dirs(tmp_path)
    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=healthy_account(),
        dry_run=True,
        portfolio_config=cfg(enabled=False),
        **d,
    )
    assert result.portfolio_decision.decision_type == PortfolioDecisionType.ADD.value
    assert result.portfolio_decision.allowed_to_generate_new_orders is True


# --- config from env --------------------------------------------------------


def test_config_from_env_defaults(monkeypatch):
    for key in (
        "PORTFOLIO_MANAGER_ENABLED", "LOW_BUYING_POWER_REVIEW_THRESHOLD_PCT",
        "ALLOW_NO_TRADE_DECISIONS", "MAX_NEW_PROPOSALS_ALPHA", "MAX_NEW_PROPOSALS_BETA",
        "CHEAP_CYCLE_GATE_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    c = PortfolioManagerConfig.from_env(env={})
    assert c.enabled is True
    assert c.max_new_proposals_alpha == 3
    assert c.max_new_proposals_beta == 2
    assert c.allow_no_trade_decisions is True
    assert c.cheap_cycle_gate_enabled is False


def test_config_from_env_overrides():
    c = PortfolioManagerConfig.from_env(
        env={
            "PORTFOLIO_MANAGER_ENABLED": "false",
            "MAX_NEW_PROPOSALS_ALPHA": "5",
            "MAX_NEW_PROPOSALS_BETA": "1",
            "LOW_BUYING_POWER_REVIEW_THRESHOLD_PCT": "0.25",
        }
    )
    assert c.enabled is False
    assert c.max_new_proposals_alpha == 5
    assert c.max_new_proposals_beta == 1
    assert c.low_buying_power_review_threshold_pct == 0.25


# --- llm portfolio_decision parsing + prompt language ----------------------


def test_llm_bundle_parses_portfolio_decision():
    import json as _json

    from src.agents.llm_proposal_agent import build_system_prompt, generate_llm_proposals

    class _Provider:
        name = "fake"

        def complete_json(self, system, user):
            return _json.dumps(
                {
                    "team_id": "team_alpha",
                    "strategy_id": "alpha_v1",
                    "proposals": [],
                    "portfolio_decision": {
                        "decision_type": "no_trade",
                        "rationale": "Holding; nothing beats current book.",
                        "allowed_to_generate_new_orders": False,
                        "max_new_proposals_this_cycle": 0,
                    },
                }
            )

    bundle = generate_llm_proposals(
        "team_alpha", provider=_Provider(), context={}, strategy_id="alpha_v1"
    )
    assert bundle.portfolio_decision["decision_type"] == "no_trade"

    # Prompt requires portfolio review + self-review and is compact.
    prompt = build_system_prompt("team_alpha")
    assert "portfolio_decision" in prompt
    assert "hold and observe" in prompt.lower()
    assert "EXPLORATION" in prompt  # alpha personality


def test_beta_prompt_is_conservation():
    from src.agents.llm_proposal_agent import build_system_prompt

    assert "CONSERVATION" in build_system_prompt("team_beta")


# --- spreads still refuse safely -------------------------------------------


def test_spreads_still_refuse_safely():
    from src.brokers.options_adapter import OptionsExecutionAdapter

    adapter = OptionsExecutionAdapter(enabled=True, enable_spreads=False)
    assert adapter.single_leg_enabled is True
    assert adapter.spreads_enabled is False
