"""Tests for the grounded conversational Agent Hub backend.

No real Discord/Ollama/Alpaca/internet/secrets. The model call is injected; status questions
and the no-runtime path return deterministic, evidence-only answers (no hallucination).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.agents.hermes_runtime import HermesRuntimeConfig
from src.ui.agent_hub import (
    agent_chat_reply,
    agent_role_from_id,
    build_agent_hub_evidence_context,
    build_team_chat_prompt,
    deterministic_status_answer,
    is_status_question,
    render_evidence_context,
    team_chat_reply,
)
from src.ui.dashboard_state import (
    AGENT_CHAT_MODE,
    ASK_AGENT_MODE,
    ASK_TEAM_MODE,
    TEAM_CHAT_MODE,
    TeamStatus,
    agent_hub_history_key,
)

_ENABLED = HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes")


def _status(tmp_path, **overrides) -> TeamStatus:
    proposal = tmp_path / "proposal.json"
    risk = tmp_path / "risk.md"
    review = tmp_path / "review.md"
    for path in (proposal, risk, review):
        path.write_text("x", encoding="utf-8")
    base = dict(
        team_id="team_alpha",
        autonomy_enabled=False,
        mode="paper_stocks_only",
        max_paper_orders_per_day=1,
        max_daily_notional=250000.0,
        natural_chat_channel_id=111,
        latest_proposal_path=proposal,
        latest_risk_note_path=risk,
        latest_review_note_path=review,
        execution_eligible_count=1,
        simulation_only_count=2,
        rejected_count=0,
        risk_approved=True,
        review_approved=False,
        stock_long_eligible=False,
        paper_order_status="none recorded",
    )
    base.update(overrides)
    return TeamStatus(**base)


def _capturing_chat(reply_text, captured):
    def chat_fn(config, request, output_file):
        captured["prompt"] = request.prompt_text
        return SimpleNamespace(response_text=reply_text, output_file=output_file)

    return chat_fn


def test_history_keys_differ_across_all_four_modes():
    keys = {
        agent_hub_history_key("team_alpha", TEAM_CHAT_MODE),
        agent_hub_history_key("team_alpha", AGENT_CHAT_MODE, "alpha_risk_01"),
        agent_hub_history_key("team_alpha", ASK_TEAM_MODE),
        agent_hub_history_key("team_alpha", ASK_AGENT_MODE, "alpha_risk_01"),
    }
    assert len(keys) == 4


def test_agent_role_from_id():
    assert agent_role_from_id("alpha_research_01") == "research_agent"
    assert agent_role_from_id("beta_risk_01") == "risk_agent"
    assert agent_role_from_id("alpha_review_01") == "review_agent"
    assert agent_role_from_id("mystery_agent") == "agent"


def test_evidence_context_includes_paths_and_approvals(tmp_path):
    status = _status(tmp_path)
    evidence = build_agent_hub_evidence_context("team_alpha", status=status)
    assert evidence.latest_proposal_path == status.latest_proposal_path
    assert evidence.latest_risk_note_path == status.latest_risk_note_path
    assert evidence.latest_review_note_path == status.latest_review_note_path
    assert evidence.risk_approved is True
    assert evidence.review_approved is False
    assert evidence.has_evidence is True

    text = render_evidence_context(evidence)
    assert str(status.latest_proposal_path) in text
    assert "parsed risk approval: yes" in text
    assert "parsed review approval: no" in text
    assert "paper-only, no live trading" in text


def test_evidence_context_with_no_status_has_no_evidence():
    evidence = build_agent_hub_evidence_context("team_alpha", status=None)
    assert evidence.has_evidence is False
    assert "nothing recorded" in render_evidence_context(evidence).lower() or "no saved" in render_evidence_context(evidence).lower()


def test_render_evidence_context_redacts_secrets(tmp_path):
    leaky = tmp_path / "DISCORD_BOT_TOKEN=should-not-render.json"
    evidence = build_agent_hub_evidence_context(
        "team_alpha", status=None, recent_proposal_paths=[leaky]
    )
    text = render_evidence_context(evidence)
    assert "should-not-render" not in text
    assert "********" in text


def test_deterministic_status_answer_uses_evidence_and_does_not_invent(tmp_path):
    status = _status(tmp_path)
    evidence = build_agent_hub_evidence_context("team_alpha", status=status)
    answer = deterministic_status_answer("hey guys, what are you working on right now?", evidence)
    assert answer is not None
    assert str(status.latest_proposal_path) in answer
    assert "1 execution-eligible" in answer
    assert "Nothing has been traded." in answer
    # Must not hallucinate topics that are not in the evidence.
    assert "VIX" not in answer
    assert "sector" not in answer.lower()


def test_deterministic_status_answer_when_no_evidence():
    evidence = build_agent_hub_evidence_context("team_alpha", status=None)
    answer = deterministic_status_answer("what are you working on?", evidence)
    assert answer is not None
    assert "nothing" in answer.lower()
    assert "traded" in answer.lower()


def test_non_status_question_has_no_deterministic_answer(tmp_path):
    evidence = build_agent_hub_evidence_context("team_alpha", status=_status(tmp_path))
    assert deterministic_status_answer("tell me a joke", evidence) is None
    assert is_status_question("tell me a joke") is False


def test_team_chat_prompt_instructs_use_only_runtime_evidence(tmp_path):
    captured = {}
    team_chat_reply(
        "team_alpha",
        "what are you working on?",
        config=_ENABLED,
        status=_status(tmp_path),
        chat_fn=_capturing_chat("grounded reply", captured),
        output_dir=tmp_path,
    )
    assert "Use ONLY the runtime evidence" in captured["prompt"]
    assert "no live trading" in captured["prompt"].lower()


def test_team_chat_prompt_includes_runtime_memory_and_data_rules():
    prompt = build_team_chat_prompt(
        "team_alpha",
        "what should we do?",
        "latest proposal: none",
        memory_context="goal=Beat SPY safely\nlesson=Keep Beta disabled",
        data_rules="No market/account/news data context was supplied. Do not invent current prices.",
    )

    assert "Runtime memory" in prompt
    assert "Beat SPY safely" in prompt
    assert "Data/tool rules" in prompt
    assert "Do not invent current prices" in prompt


def test_agent_chat_prompt_instructs_use_only_runtime_evidence(tmp_path):
    captured = {}
    agent_chat_reply(
        "team_alpha",
        "alpha_risk_01",
        "what are you working on?",
        config=_ENABLED,
        status=_status(tmp_path),
        chat_fn=_capturing_chat("grounded reply", captured),
        output_dir=tmp_path,
    )
    assert "Use ONLY the runtime evidence" in captured["prompt"]


def test_status_question_falls_back_to_deterministic_when_model_unavailable(tmp_path):
    status = _status(tmp_path)

    def failing_chat(config, request, output_file):
        raise RuntimeError("runtime disabled")

    reply = team_chat_reply(
        "team_alpha",
        "what are you working on?",
        config=_ENABLED,
        status=status,
        chat_fn=failing_chat,
        output_dir=tmp_path,
    )
    # Deterministic, evidence-grounded — not a vague offline message.
    assert str(status.latest_proposal_path) in reply
    assert "VIX" not in reply


def test_non_status_question_falls_back_to_generic_when_model_unavailable(tmp_path):
    def failing_chat(config, request, output_file):
        raise RuntimeError("runtime disabled")

    reply = agent_chat_reply(
        "team_alpha",
        "alpha_review_01",
        "tell me a joke",
        config=_ENABLED,
        status=_status(tmp_path),
        chat_fn=failing_chat,
        output_dir=tmp_path,
    )
    assert "can't chat live" in reply
    assert "no live trading" in reply.lower()


def test_conversational_hub_does_not_call_alpaca_orders_or_cycle(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("conversational Agent Hub must not trade or run a paper cycle")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)
    monkeypatch.setattr("src.discord_bot.bot.build_team_paper_cycle_summary", forbidden)

    captured = {}
    team = team_chat_reply(
        "team_alpha",
        "hey guys",
        config=_ENABLED,
        status=_status(tmp_path),
        chat_fn=_capturing_chat("hi!", captured),
        output_dir=tmp_path,
    )
    assert team == "hi!"
