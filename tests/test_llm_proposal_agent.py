"""LLM proposal agent: prompts, parsing, adaptation. Provider always mocked."""

from __future__ import annotations

import inspect
import json

from src.agents.llm_proposal_agent import (
    build_system_prompt,
    build_user_prompt,
    generate_llm_proposals,
    llm_dict_to_proposal,
)
from src.competition.llm_cycle import build_llm_context
from src.competition.proposals import ProposalType


class FakeProvider:
    name = "fake"

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def complete_json(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._payload if isinstance(self._payload, str) else json.dumps(self._payload)


def _proposal(asset_type, **over):
    base = {
        "asset_type": asset_type,
        "symbol": "SPY",
        "action": "buy",
        "thesis": "demo thesis",
        "confidence": 0.6,
        "estimated_price": 500.0,
        "data_sources_used": ["alpaca_quote"],
        "data_freshness": "live",
    }
    base.update(over)
    return base


def test_alpha_and_beta_prompts_differ():
    alpha = build_system_prompt("team_alpha")
    beta = build_system_prompt("team_beta")
    assert alpha != beta
    assert "aggressive" in alpha.lower()
    assert "contrarian" in beta.lower()


def test_all_asset_types_parse_into_proposals():
    for asset_type, expected in [
        ("stock_long", ProposalType.STOCK_LONG),
        ("stock_short", ProposalType.STOCK_SHORT),
        ("margin_stock_long", ProposalType.MARGIN_STOCK_LONG),
        ("margin_stock_short", ProposalType.MARGIN_STOCK_SHORT),
        ("option_long_call", ProposalType.OPTION_LONG_CALL),
        ("option_long_put", ProposalType.OPTION_LONG_PUT),
        ("option_debit_spread", ProposalType.OPTION_DEBIT_SPREAD),
        ("option_defined_risk_spread", ProposalType.OPTION_DEFINED_RISK_SPREAD),
    ]:
        proposal, reason = llm_dict_to_proposal(
            _proposal(asset_type), team_id="team_alpha", strategy_id="s", agent_id="a"
        )
        assert proposal is not None, (asset_type, reason)
        assert proposal.proposal_type == expected


def test_unknown_asset_type_rejected_safely():
    proposal, reason = llm_dict_to_proposal(
        _proposal("crypto_perp"), team_id="team_alpha", strategy_id="s", agent_id="a"
    )
    assert proposal is None
    assert "unknown asset_type" in reason


def test_missing_required_fields_rejected_safely():
    proposal, reason = llm_dict_to_proposal(
        {"asset_type": "stock_long", "symbol": "SPY"}, team_id="t", strategy_id="s", agent_id="a"
    )
    assert proposal is None
    assert reason


def test_generate_collects_valid_and_rejects_bad():
    payload = {
        "team_id": "team_alpha",
        "strategy_id": "alpha_llm_v1",
        "market_summary": "calm",
        "proposals": [_proposal("stock_long"), _proposal("nonsense", symbol="X")],
        "learning_update": {"what_worked": "a", "what_failed": "b", "next_adjustment": "c"},
        "hypothesis": "trend up",
        "watchlist": ["spy", "nvda"],
    }
    bundle = generate_llm_proposals(
        "team_alpha", provider=FakeProvider(payload), context={"team_id": "team_alpha"}, strategy_id="alpha_llm_v1"
    )
    assert len(bundle.proposals) == 1
    assert bundle.raw_errors  # the nonsense one
    assert bundle.market_summary == "calm"
    assert bundle.hypothesis == "trend up"
    assert bundle.watchlist == ["SPY", "NVDA"]
    assert bundle.learning_update["next_adjustment"] == "c"


def test_bad_json_rejected_without_crashing():
    bundle = generate_llm_proposals(
        "team_alpha", provider=FakeProvider("this is not json"), context={}, strategy_id="s"
    )
    assert bundle.proposals == []
    assert bundle.raw_errors


def test_llm_output_is_only_proposals_no_broker_access():
    # The agent never imports or calls broker submission.
    for fn in (generate_llm_proposals, llm_dict_to_proposal, build_user_prompt):
        source = inspect.getsource(fn)
        assert "submit_order" not in source
        assert "submit_paper_order" not in source


def test_prompt_contains_no_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET-OPENAI")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-SECRET-token")
    context = build_llm_context("team_alpha", client=None, price_fn=None)
    prompt = build_user_prompt("team_alpha", context)
    system = build_system_prompt("team_alpha")
    assert "sk-SECRET-OPENAI" not in prompt
    assert "discord-SECRET-token" not in prompt
    assert "sk-SECRET-OPENAI" not in system


def test_deterministic_engine_sizes_not_llm():
    # The LLM proposal carries an intent weight but no approved quantity field.
    proposal, _ = llm_dict_to_proposal(
        _proposal("stock_long", target_weight=0.9), team_id="t", strategy_id="s", agent_id="a"
    )
    assert proposal is not None
    assert not hasattr(proposal, "approved_quantity")
