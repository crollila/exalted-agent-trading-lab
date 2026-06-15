"""Research feeds the prompt + proposals carry source IDs; research CLI wiring."""

from __future__ import annotations

import json

import src.main as main
from src.agents.llm_proposal_agent import build_user_prompt, generate_llm_proposals
from src.competition.llm_cycle import build_llm_context
from src.research.research import plan_research, run_research
from src.research.research_config import ResearchConfig


def _research_run():
    config = ResearchConfig.from_env(env={"ENABLE_LIVE_NEWS_RESEARCH": "true", "NEWS_PROVIDER": "alpaca"})

    def fake_news(tickers, start, limit):
        return [{"headline": "NVDA AI demand surges", "summary": "s", "url": "http://x", "symbols": tickers}]

    return run_research("team_alpha", plan_research("team_alpha", config), config=config, alpaca_news_fn=fake_news)


def test_prompt_includes_research_but_not_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-PROMPT-SECRET")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-PROMPT-SECRET")
    context = build_llm_context("team_alpha", client=None, price_fn=None, research_run=_research_run())
    prompt = build_user_prompt("team_alpha", context)
    assert "NVDA AI demand surges" in prompt  # research present
    assert "research" in prompt
    assert "sk-PROMPT-SECRET" not in prompt
    assert "discord-PROMPT-SECRET" not in prompt


class _Provider:
    name = "fake"

    def complete_json(self, system, user):
        return json.dumps(
            {
                "team_id": "team_alpha",
                "strategy_id": "alpha_llm_v1",
                "proposals": [
                    {
                        "asset_type": "stock_long", "symbol": "NVDA", "action": "buy",
                        "thesis": "AI catalyst", "confidence": 0.7, "estimated_price": 120.0,
                        "target_weight": 0.05, "data_sources_used": ["alpaca_news"],
                        "data_freshness": "live", "research_source_ids": ["r1", "r2"],
                        "research_changed_proposal": True,
                    }
                ],
            }
        )


def test_llm_proposals_carry_source_ids():
    bundle = generate_llm_proposals("team_alpha", provider=_Provider(), context={}, strategy_id="alpha_llm_v1")
    assert bundle.proposals
    pid = bundle.proposals[0].proposal_id
    assert bundle.proposal_source_ids[pid] == ["r1", "r2"]
    assert bundle.research_source_ids == ["r1", "r2"]


# --- CLI registration ---


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    main.main()


def test_research_status_registered(monkeypatch):
    calls = {}
    monkeypatch.setattr(main, "run_research_status", lambda: calls.setdefault("ran", True))
    _run_cli(monkeypatch, ["research-status"])
    assert calls.get("ran") is True


def test_proposal_attribution_registered(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "run_proposal_attribution", lambda team: captured.setdefault("team", team))
    _run_cli(monkeypatch, ["proposal-attribution", "--team", "team_beta"])
    assert captured["team"] == "team_beta"
