"""Research engine: config, planning, providers, caps, logging. No real network."""

from __future__ import annotations

import json

from src.research.research import plan_research, run_research
from src.research.research_config import ResearchConfig
from src.research.research_log import log_research, read_latest_research, research_log_count


def cfg(**over):
    env = {"ENABLE_LIVE_NEWS_RESEARCH": "true", "NEWS_PROVIDER": "alpaca"}
    env.update({k: str(v) for k, v in over.items()})
    return ResearchConfig.from_env(env=env)


def fake_news(items=1):
    def fetch(tickers, start, limit):
        return [
            {"headline": f"news {i}", "summary": "s", "url": f"http://x/{i}", "symbols": tickers, "created_at": "2026-06-15"}
            for i in range(items)
        ]
    return fetch


def fake_web(items=1):
    def fetch(query, model, max_results):
        return [
            {"title": f"web {i}", "summary": "w", "url": f"http://w/{i}", "tickers": ["SPY"], "published_at": "2026-06-15"}
            for i in range(items)
        ]
    return fetch


# --- config / planning ---


def test_provider_none_is_unavailable():
    config = ResearchConfig.from_env(env={"NEWS_PROVIDER": "none"})
    assert config.available is False
    run = run_research("team_alpha", plan_research("team_alpha", config), config=config)
    assert run.available is False
    assert run.results == []


def test_alpha_and_beta_queries_differ():
    config = cfg()
    a = plan_research("team_alpha", config)
    b = plan_research("team_beta", config)
    assert a[0].query != b[0].query


# --- providers ---


def test_alpaca_provider_parses_results():
    config = cfg()
    run = run_research("team_alpha", plan_research("team_alpha", config), config=config, alpaca_news_fn=fake_news(1))
    assert run.results
    assert run.results[0].provider == "alpaca"
    assert run.results[0].title == "news 0"
    assert run.results[0].source_id == "r1"


def test_openai_web_provider_parses_results():
    config = cfg(NEWS_PROVIDER="openai_web", ENABLE_OPENAI_WEB_RESEARCH="true")
    run = run_research("team_beta", plan_research("team_beta", config), config=config, openai_web_fn=fake_web(1))
    assert run.results
    assert run.results[0].provider == "openai_web"


def test_hybrid_combines_and_dedupes():
    config = cfg(NEWS_PROVIDER="hybrid", ENABLE_OPENAI_WEB_RESEARCH="true", MAX_RESEARCH_QUERIES_PER_TEAM_PER_CYCLE=1)

    def same_news(tickers, start, limit):
        return [{"headline": "dup", "summary": "s", "url": "http://same"}]

    def same_web(query, model, max_results):
        return [{"title": "dup", "summary": "w", "url": "http://same"}]

    run = run_research("team_alpha", plan_research("team_alpha", config), config=config,
                       alpaca_news_fn=same_news, openai_web_fn=same_web)
    assert len(run.results) == 1  # deduped by (title, url)


# --- caps ---


def test_query_cap_enforced():
    config = cfg(MAX_RESEARCH_QUERIES_PER_TEAM_PER_CYCLE=2)
    queries = plan_research("team_alpha", config)
    assert len(queries) == 2
    run = run_research("team_alpha", queries, config=config, alpaca_news_fn=fake_news(1))
    assert len(run.queries) == 2


def test_result_cap_enforced():
    config = cfg(MAX_RESEARCH_QUERIES_PER_TEAM_PER_CYCLE=1, MAX_RESEARCH_RESULTS_PER_QUERY=3)
    run = run_research("team_alpha", plan_research("team_alpha", config), config=config, alpaca_news_fn=fake_news(10))
    assert len(run.results) == 3


def test_provider_failure_is_logged_not_fatal():
    config = cfg()

    def boom(tickers, start, limit):
        raise RuntimeError("alpaca down")

    run = run_research("team_alpha", plan_research("team_alpha", config), config=config, alpaca_news_fn=boom)
    assert run.errors
    assert run.results == []


# --- logging ---


def test_research_log_writes_and_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET")
    config = cfg()
    run = run_research("team_alpha", plan_research("team_alpha", config), config=config, alpaca_news_fn=fake_news(1))
    log_research(run, cycle_id="c1", proposal_source="llm", proposal_ids=["p1"], research_dir=tmp_path)

    latest = read_latest_research("team_alpha", research_dir=tmp_path)
    assert latest["team_id"] == "team_alpha"
    assert latest["proposal_ids"] == ["p1"]
    assert research_log_count(research_dir=tmp_path) == 1

    blob = (tmp_path / "research_log.jsonl").read_text(encoding="utf-8")
    assert "sk-SECRET" not in blob  # no secrets in logs
    parsed = json.loads(blob.splitlines()[0])
    assert parsed["results"][0]["source_id"] == "r1"
