"""End-of-day integration: scoring, reflection into memory, team debrief, report."""

from __future__ import annotations

import json

from src.eod import run_eod
from src.ledger import record_trade
from src.memory import AgentMemory
from src.scoreboard import load_scoreboard
from tests.test_agents_parsing import make_llm
from tests.test_cycle import FakeBroker


class EodBroker(FakeBroker):
    """Fake broker with configurable day return."""

    def __init__(self, equity: float, last_equity: float, spy_change: float = 0.004):
        super().__init__(market_open=False)
        self._equity = equity
        self._last = last_equity
        self._spy = spy_change

    def account(self):
        from src.broker import AccountInfo

        return AccountInfo(
            equity=self._equity, last_equity=self._last,
            cash=self._equity, buying_power=self._equity * 2,
        )

    def snapshots(self, symbols):
        from src.broker import SnapshotInfo

        return {
            s.upper(): SnapshotInfo(
                symbol=s.upper(), price=500.0, prev_close=500.0 / (1 + self._spy),
                day_change_pct=self._spy,
            )
            for s in symbols
        }


REFLECTION = json.dumps({"lessons": ["Watch pre-market gaps."], "noted": True})
DEBRIEF = json.dumps({
    "what_we_did": "We bought 259 AAPL and shorted 203 TSLA.",
    "why_we_did_it": "AAPL showed relative strength; TSLA broke down.",
    "what_we_expected": "AAPL to outperform SPY this week.",
    "what_we_observed": "AAPL is up 1.2% since entry; TSLA flat.",
    "what_we_learned": "Selective momentum beat broad beta today.",
    "plan_going_forward": "Hold winners, cut TSLA if it reclaims its open.",
})
REBUTTAL = json.dumps({
    "rebuttal": "Nice day, but chasing AAPL after a 5% pop is late money.",
    "lessons_from_rival": ["Rival's earnings-aware exits avoided a gap loss; copy that."],
})


def test_run_eod_scores_learns_and_reports(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)

    record_trade(
        settings.data_dir, "team_alpha",
        symbol="AAPL", action="buy", order_side="buy", qty=259,
        est_price=308.0, est_notional=79772.0,
        thesis="relative strength", exit_plan="stop -5%", confidence=0.7,
        submitted=True, order_id="o1", status="accepted", error=None,
    )

    brokers = {
        "team_alpha": EodBroker(equity=1_010_000.0, last_equity=1_000_000.0),  # +1.0%
        "team_beta": EodBroker(equity=998_000.0, last_equity=1_000_000.0),     # -0.2%
    }
    # Per team: 3 reflections + 1 debrief (alpha then beta), then 2 rebuttals.
    llm = make_llm(
        settings,
        [REFLECTION] * 3 + [DEBRIEF] + [REFLECTION] * 3 + [DEBRIEF] + [REBUTTAL, REBUTTAL],
    )

    report_path = run_eod(settings, llm=llm, brokers=brokers)
    report = open(report_path, encoding="utf-8").read()

    # Scoreboard recorded the day: alpha beat SPY (+1.0% vs +0.4%), beta lost.
    scoreboard = load_scoreboard(settings.data_dir)
    assert len(scoreboard["days"]) == 1
    day = scoreboard["days"][0]
    assert day["teams"]["team_alpha"]["beat_spy"] is True
    assert day["teams"]["team_beta"]["beat_spy"] is False
    assert day["head_to_head"] == "team_alpha"

    # Memories were updated with today's lesson and the day result.
    memory = AgentMemory.load("team_alpha", "researcher", settings.data_dir)
    assert memory.lessons[-1]["text"] == "Watch pre-market gaps."
    assert memory.days_recorded == 1 and memory.wins_vs_spy == 1

    # Cross-team learning: rival lessons landed in the strategist memory.
    strategist = AgentMemory.load("team_alpha", "strategist", settings.data_dir)
    assert any("[from rival]" in l["text"] for l in strategist.lessons)

    # The report carries the debrief sections, the rebuttal, and the trade.
    assert "What we did today" in report
    assert "We bought 259 AAPL" in report
    assert "How we intend to go forward" in report
    assert "Rebuttal to" in report
    assert "chasing AAPL" in report
    assert "BUY 259 AAPL" in report
    assert "COMPETITION SCOREBOARD" in report


def test_run_eod_survives_llm_failure(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    brokers = {
        "team_alpha": EodBroker(equity=1_000_000.0, last_equity=1_000_000.0),
        "team_beta": EodBroker(equity=1_000_000.0, last_equity=1_000_000.0),
    }
    # Every LLM call fails (each complete_json retries once -> 2 raises per call).
    llm = make_llm(settings, [RuntimeError("api down")] * 32)

    report_path = run_eod(settings, llm=llm, brokers=brokers)
    report = open(report_path, encoding="utf-8").read()

    # The day is still scored, the report still written, and errors were logged.
    assert len(load_scoreboard(settings.data_dir)["days"]) == 1
    assert "debrief unavailable" in report
    from src.notify import recent_errors

    errors = recent_errors(settings, count=10)
    assert any("reflection" in line for line in errors)
    assert any("debrief" in line for line in errors)

    # Memory untouched on failure.
    memory = AgentMemory.load("team_alpha", "researcher", settings.data_dir)
    assert memory.days_recorded == 0
