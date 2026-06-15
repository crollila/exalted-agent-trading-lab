from src.competition.risk_engine import AccountContext
from src.competition.scorecard import (
    TeamScorecard,
    load_latest_scorecard,
    rank_scorecards,
    save_scorecard,
)
from src.competition.week_competition import (
    competition_status,
    load_competition_state,
    run_week_cycle,
    start_week_competition,
    stop_week_competition,
)
from src.config.permissions import TradingPermissions
from src.learning.team_memory import TeamLearningLedger


def acct():
    return AccountContext(equity=1_000_000.0, cash=1_000_000.0, buying_power=2_000_000.0)


def perms(**o):
    return TradingPermissions.from_env(env={k: str(v) for k, v in o.items()})


def test_start_and_stop_competition(tmp_path):
    comp = tmp_path / "comp"
    state = start_week_competition(starting_equity=1000.0, competition_dir=comp)
    assert state.active is True
    assert state.week_start is not None and state.week_end is not None
    stopped = stop_week_competition(competition_dir=comp)
    assert stopped.active is False
    assert stopped.stopped_at is not None
    assert load_competition_state(comp).active is False


def test_run_cycle_writes_scorecard_and_learning(tmp_path):
    comp, sc, learn, ks = (tmp_path / d for d in ("comp", "sc", "learn", "ks.json"))
    start_week_competition(starting_equity=1_000_000.0, competition_dir=comp)
    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=acct(),
        dry_run=True,
        competition_dir=comp,
        scorecard_dir=sc,
        learning_dir=learn,
        kill_switch_path=str(ks),
    )
    # Default proposals: 1 long executes, short + option simulation-only.
    assert result.routing.summary()["execution_eligible"] == 1
    assert result.routing.summary()["simulation_only"] == 2

    card = load_latest_scorecard("team_alpha", sc)
    assert card is not None
    assert card.proposals_count == 3
    assert card.approved_count == 1

    ledger = TeamLearningLedger.load("team_alpha", learn)
    assert len(ledger.reviews) == 1
    assert ledger.active_strategy == "week_competition_default"


def test_scorecard_includes_spy_comparison():
    card = TeamScorecard(
        team_id="team_alpha",
        week_start="x",
        week_end="y",
        starting_equity=1000.0,
        current_equity=1100.0,
        spy_benchmark_return=0.05,
    )
    card.compute_excess_return()
    assert card.team_return == 0.10
    assert abs(card.excess_return_vs_spy - 0.05) < 1e-9


def test_competition_status_calculates_and_ranks(tmp_path):
    comp, sc = tmp_path / "comp", tmp_path / "sc"
    start_week_competition(starting_equity=1000.0, competition_dir=comp)
    save_scorecard(
        TeamScorecard(team_id="team_alpha", week_start="x", week_end="y",
                      starting_equity=1000.0, current_equity=1100.0, spy_benchmark_return=0.02),
        scorecard_dir=sc,
    )
    save_scorecard(
        TeamScorecard(team_id="team_beta", week_start="x", week_end="y",
                      starting_equity=1000.0, current_equity=1050.0, spy_benchmark_return=0.02),
        scorecard_dir=sc,
    )
    status = competition_status(competition_dir=comp, scorecard_dir=sc)
    teams = status["teams"]
    assert teams[0]["team_id"] == "team_alpha"  # higher excess vs SPY ranks first
    assert teams[0]["current_rank"] == 1
    assert teams[1]["team_id"] == "team_beta"


def test_rank_uses_excess_return():
    a = TeamScorecard(team_id="a", week_start="x", week_end="y", starting_equity=1000, current_equity=1010, spy_benchmark_return=0.0)
    b = TeamScorecard(team_id="b", week_start="x", week_end="y", starting_equity=1000, current_equity=1030, spy_benchmark_return=0.0)
    for c in (a, b):
        c.compute_excess_return()
    ranked = rank_scorecards([a, b])
    assert ranked[0].team_id == "b"
