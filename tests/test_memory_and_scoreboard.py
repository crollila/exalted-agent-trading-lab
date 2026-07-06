"""Persistent memory (learning) and the competition scoreboard."""

from __future__ import annotations

from src.memory import AgentMemory, MAX_LESSONS_KEPT, LESSONS_KEPT_AFTER_COMPACT
from src.scoreboard import load_scoreboard, record_day, render, spy_cumulative, totals


# --- memory -------------------------------------------------------------------

def test_memory_roundtrip(tmp_path):
    memory = AgentMemory.load("team_alpha", "researcher", tmp_path)
    memory.add_lessons("2026-07-06", ["NVDA gapped on earnings; check pre-market gaps first."])
    memory.set_playbook(["Prefer liquid megacaps."])
    memory.record_day(beat_spy=True)
    memory.save()

    loaded = AgentMemory.load("team_alpha", "researcher", tmp_path)
    assert loaded.lessons[0]["text"].startswith("NVDA gapped")
    assert loaded.playbook == ["Prefer liquid megacaps."]
    assert loaded.days_recorded == 1 and loaded.wins_vs_spy == 1


def test_memory_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "memory" / "team_alpha" / "risk.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")
    memory = AgentMemory.load("team_alpha", "risk", tmp_path)
    assert memory.lessons == [] and memory.playbook == []


def test_memory_compaction_flow(tmp_path):
    memory = AgentMemory.load("team_beta", "strategist", tmp_path)
    for i in range(MAX_LESSONS_KEPT + 5):
        memory.add_lessons(f"2026-06-{i % 28 + 1:02d}", [f"lesson {i}"])
    assert memory.needs_compaction
    memory.compact(["principle A", "principle B"])
    assert memory.playbook == ["principle A", "principle B"]
    assert len(memory.lessons) == LESSONS_KEPT_AFTER_COMPACT
    assert not memory.needs_compaction


def test_memory_render_is_bounded_and_informative(tmp_path):
    memory = AgentMemory.load("team_alpha", "strategist", tmp_path)
    text = memory.render()
    assert "first day" in text  # empty-memory message

    memory.add_lessons("2026-07-06", ["x" * 5000])
    assert len(memory.render(max_chars=1000)) <= 1000


# --- scoreboard ----------------------------------------------------------------

def test_record_day_and_totals(tmp_path):
    record_day(
        tmp_path,
        date="2026-07-06",
        team_returns={"team_alpha": 0.01, "team_beta": -0.002},
        team_equities={"team_alpha": 1_010_000.0, "team_beta": 998_000.0},
        spy_return=0.004,
    )
    record_day(
        tmp_path,
        date="2026-07-07",
        team_returns={"team_alpha": -0.01, "team_beta": 0.006},
        team_equities={"team_alpha": 999_900.0, "team_beta": 1_004_000.0},
        spy_return=0.005,
    )
    scoreboard = load_scoreboard(tmp_path)
    assert len(scoreboard["days"]) == 2

    stats = totals(scoreboard)
    assert stats["team_alpha"]["beat_spy"] == 1
    assert stats["team_alpha"]["lost_to_spy"] == 1
    assert stats["team_beta"]["beat_spy"] == 1
    assert stats["team_alpha"]["h2h_wins"] == 1
    assert stats["team_beta"]["h2h_wins"] == 1

    # cumulative compounding: (1.01 * 0.99) - 1
    assert abs(stats["team_alpha"]["cum_return"] - (1.01 * 0.99 - 1)) < 1e-9
    assert abs(spy_cumulative(scoreboard) - (1.004 * 1.005 - 1)) < 1e-9


def test_record_day_is_idempotent_per_date(tmp_path):
    for _ in range(3):
        record_day(
            tmp_path, date="2026-07-06",
            team_returns={"team_alpha": 0.01, "team_beta": 0.02},
            team_equities={"team_alpha": 1.0, "team_beta": 1.0},
            spy_return=0.0,
        )
    assert len(load_scoreboard(tmp_path)["days"]) == 1


def test_record_day_handles_unknown_returns(tmp_path):
    day = record_day(
        tmp_path, date="2026-07-06",
        team_returns={"team_alpha": None, "team_beta": 0.01},
        team_equities={"team_alpha": None, "team_beta": 1.0},
        spy_return=None,
    )
    assert day["teams"]["team_alpha"]["beat_spy"] is None
    assert day["head_to_head"] is None
    text = render(load_scoreboard(tmp_path))
    assert "n/a" in text


def test_render_empty_scoreboard(tmp_path):
    assert "No trading days recorded yet" in render(load_scoreboard(tmp_path))
def test_risk_stats_sharpe_vol_drawdown():
    from src.scoreboard import risk_stats

    # Deterministic series: +1%, -0.5%, +1%, -0.5%, +1% (n=5)
    returns = [0.01, -0.005, 0.01, -0.005, 0.01]
    stats = risk_stats(returns)
    assert stats["sharpe"] is not None and stats["sharpe"] > 0
    assert stats["volatility"] is not None and stats["volatility"] > 0
    # Max drawdown is the single worst dip: -0.5%
    assert abs(stats["max_drawdown"] - (-0.005)) < 1e-9


def test_risk_stats_too_few_days():
    from src.scoreboard import risk_stats

    stats = risk_stats([0.01, 0.02])
    assert stats["sharpe"] is None and stats["volatility"] is None
    assert stats["max_drawdown"] is not None
    assert risk_stats([]) == {"sharpe": None, "volatility": None, "max_drawdown": None}


def test_render_includes_sharpe_and_drawdown(tmp_path):
    from src.scoreboard import load_scoreboard, record_day, render

    for i in range(6):
        record_day(
            tmp_path, date=f"2026-07-{10 + i:02d}",
            team_returns={"team_alpha": 0.01 if i % 2 else -0.004, "team_beta": 0.001},
            team_equities={"team_alpha": 1.0, "team_beta": 1.0},
            spy_return=0.002,
        )
    text = render(load_scoreboard(tmp_path))
    assert "Sharpe" in text
    assert "max drawdown" in text
