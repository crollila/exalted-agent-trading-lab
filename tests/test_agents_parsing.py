"""Agent output parsing: strict, tolerant of junk, never crashes on bad JSON shapes."""

from __future__ import annotations

import pytest

from src.agents import parse_proposals, run_risk_analyst, run_researcher, run_strategist, ResearchBrief
from src.llm import LLM, LLMError, parse_json_object
from src.memory import AgentMemory


class FakeChatClient:
    """Mimics the OpenAI client shape; returns queued replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                reply = outer.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply

                class _Message:
                    content = reply

                class _Choice:
                    message = _Message()

                class _Response:
                    choices = [_Choice()]

                return _Response()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def make_llm(settings, replies) -> LLM:
    return LLM(settings, client=FakeChatClient(replies))


# --- parse_json_object ------------------------------------------------------

def test_parse_json_strips_markdown_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_rejects_empty_and_non_object():
    with pytest.raises(LLMError):
        parse_json_object("")
    with pytest.raises(LLMError):
        parse_json_object("[1, 2]")
    with pytest.raises(LLMError):
        parse_json_object("not json at all")


# --- proposal parsing --------------------------------------------------------

def test_parse_proposals_accepts_valid_and_rejects_junk():
    raw = [
        {"symbol": "nvda", "action": "buy", "weight_pct": 0.08, "thesis": "leadership"},
        {"symbol": "BAD SYMBOL!", "action": "buy", "thesis": "x"},
        {"symbol": "SPY", "action": "teleport", "thesis": "x"},
        {"symbol": "AAPL", "action": "sell", "fraction": 0.5},  # missing thesis
        "not an object",
    ]
    proposals, errors = parse_proposals(raw, max_proposals=5)
    assert len(proposals) == 1
    assert proposals[0].symbol == "NVDA"
    assert proposals[0].action == "buy"
    assert len(errors) == 4


def test_parse_proposals_caps_count_and_clamps_weights():
    raw = [
        {"symbol": f"SY{i}", "action": "buy", "weight_pct": 9.0, "thesis": "t"}
        for i in range(6)
    ]
    proposals, _ = parse_proposals(raw, max_proposals=3)
    assert len(proposals) == 3
    assert all(p.weight_pct <= 1.0 for p in proposals)


def test_parse_proposals_handles_non_list():
    proposals, errors = parse_proposals("nope", max_proposals=3)
    assert proposals == [] and errors


# --- agent runners with fake LLM ---------------------------------------------

def memory_for(settings, role="researcher"):
    return AgentMemory.load("team_alpha", role, settings.data_dir)


def test_researcher_happy_path(settings, team_alpha):
    llm = make_llm(settings, [
        '{"market_view": "Tape is risk-on.", "key_events": ["CPI cool"], '
        '"ideas": [{"symbol": "NVDA", "direction": "long", "note": "semis lead", "source_ids": ["news_1"]}], '
        '"risks": ["concentration"]}'
    ])
    brief = run_researcher(llm, team_alpha, memory_for(settings), {"prices": {}, "news": []})
    assert brief.market_view == "Tape is risk-on."
    assert brief.ideas[0]["symbol"] == "NVDA"


def test_strategist_no_trade_path(settings, team_alpha):
    llm = make_llm(settings, [
        '{"portfolio_view": "Nothing beats current holdings.", "proposals": [], '
        '"no_trade_reason": "no edge today"}'
    ])
    brief = ResearchBrief(market_view="quiet")
    out = run_strategist(llm, team_alpha, memory_for(settings, "strategist"), brief, {}, 3)
    assert out.proposals == []
    assert out.no_trade_reason == "no edge today"


def test_risk_analyst_missing_verdicts_fail_closed(settings, team_alpha):
    from src.agents import Proposal

    proposals = [
        Proposal("NVDA", "buy", 0.05, 1.0, "t", "e", 0.5),
        Proposal("SPY", "buy", 0.05, 1.0, "t", "e", 0.5),
    ]
    # Model only returns a verdict for index 0.
    llm = make_llm(settings, [
        '{"verdicts": [{"index": 0, "verdict": "approve", "reason": "fine"}]}'
    ])
    verdicts = run_risk_analyst(llm, team_alpha, memory_for(settings, "risk"), proposals, {})
    assert verdicts[0].verdict == "approve"
    assert verdicts[1].verdict == "reject"
    assert "fail closed" in verdicts[1].reason


def test_llm_retries_once_then_raises(settings):
    llm = make_llm(settings, [RuntimeError("boom"), RuntimeError("boom again")])
    with pytest.raises(LLMError, match="boom again"):
        llm.complete_json("researcher", "sys", "user")


def test_llm_retry_recovers_on_second_attempt(settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    llm = make_llm(settings, [RuntimeError("transient"), '{"ok": true}'])
    assert llm.complete_json("researcher", "sys", "user") == {"ok": True}


def test_llm_requires_key_for_openai(settings):
    from dataclasses import replace

    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        LLM(replace(settings, openai_api_key=None))
