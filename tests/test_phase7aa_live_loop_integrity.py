"""Phase 7AA — Live-loop execution integrity + error-truthful Discord.

Pins the production failures observed in the Alpha/Beta paper loop:

* Every normal LLM cycle crashed with
  ``UnboundLocalError: ... 'routing_status' ...`` because a Phase 7Z commit
  re-imported ``routing_status`` inside a branch of ``run_week_cycle_cli``, making
  it a function-local that shadowed the module-level import. The loop caught it,
  reported ``cycle_action=error``, and then the audit + Discord brief reused a
  STALE scorecard (showing a ``dry_run`` execution block, 3 approved proposals,
  ``account_read_ok=True``, "beat SPY", stale holdings) even though the live loop
  runs ``DRY_RUN=false``.
* A normal ``loop-watchdog`` child must be the paper-capable cheap loop (never
  ``--dry-run-loop``), launched with the repo root as its working directory so it
  reads the intended local ``.env`` (where ``DRY_RUN=false``).

Every test here is fully mocked: no real broker, provider, Discord, or network
call, and nothing is ever submitted.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import src.discord_bot.competition_updates as cu
import src.main as main_mod
from src.competition.candidate_generation import EXEC_DRY_RUN, classify_candidate_outcome
from src.competition.iteration_audit import load_latest_status
from src.competition.loop_watchdog import assess_loop_health
from src.config.settings import Settings
from src.ui import operator_controls as ops
from src.ui.process_control import BotActionResult


# --- helpers ----------------------------------------------------------------


def _settings(*, dry_run: bool) -> Settings:
    return Settings(
        alpaca_api_key=None,
        alpaca_secret_key=None,
        alpaca_paper=True,
        alpaca_base_url="",
        database_path=Path("data/trading_lab.sqlite3"),
        dry_run=dry_run,
        starting_equity=1_000_000.0,
        min_cash_pct=0.10,
        max_position_pct=0.20,
        max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )


def _dead_health():
    return assess_loop_health(
        pid=None, process_alive=False, heartbeat=None, heartbeat_age_seconds=None,
    )


def _wire_watchdog(monkeypatch, *, starter):
    monkeypatch.setattr(main_mod, "_gather_loop_health", lambda *_a, **_k: _dead_health())
    monkeypatch.setattr(main_mod, "read_kill_switch", lambda: SimpleNamespace(engaged=False))
    monkeypatch.setattr(main_mod, "_watchdog_log", lambda *_a, **_k: None)
    monkeypatch.setattr(ops, "detect_cheap_loop_processes", lambda: [])
    monkeypatch.setattr(ops, "start_cheap_loop", starter)


def _stale_scorecard() -> SimpleNamespace:
    """A prior dry-run scorecard whose fields MUST NOT leak into an errored cycle."""

    return SimpleNamespace(
        proposals_count=3,
        approved_count=3,
        simulation_only_count=0,
        rejected_count=0,
        orders_submitted=0,
        broker_rejected_count=0,
        portfolio_decision_type="add",
        portfolio_no_trade=False,
        max_new_proposals=3,
        candidate_generation_outcome={"detail": "Execution-eligible proposals not submitted: dry-run mode."},
        no_trade_reason_class=None,
        execution_block_reason="dry_run",
        reconciliation_status="clean",
        account_read_ok=True,
        account_snapshot_source="live_team_paper_account",
        account_snapshot_time="2026-06-30T00:56:24+00:00",
        reconciliation_conflicts=[],
        spy_start_price=None,
        spy_end_price=None,
        spy_benchmark_return=0.0087,
        excess_return_vs_spy=0.0113,
        team_return=0.0200,
        benchmark_timeframe="weekly",
        current_equity=1_000_000.0,
        cash=1_000_000.0,
        buying_power=2_000_000.0,
        gross_exposure=500_000.0,
        net_exposure=500_000.0,
        short_exposure=0.0,
        routed_provider=None,
        routed_model=None,
        provider_outcome=None,
    )


def _stub_gather_artifacts(monkeypatch, *, scorecard, attribution=None):
    """Make ``gather_team_iteration_context`` hermetic (no real artifact reads)."""

    monkeypatch.setattr("src.competition.scorecard.load_latest_scorecard", lambda _t: scorecard)
    monkeypatch.setattr("src.competition.attribution.performance_feedback", lambda _t: {})
    monkeypatch.setattr("src.competition.daily_review.load_daily_spy_attribution", lambda _t: attribution)
    monkeypatch.setattr("src.competition.daily_review.load_latest_daily_team_review", lambda _t: None)
    from src.learning.strategy_memory import StrategyMemory
    from src.learning.team_memory import TeamLearningLedger

    monkeypatch.setattr(StrategyMemory, "load", lambda _t: None)
    monkeypatch.setattr(TeamLearningLedger, "load", lambda _t: None)


# === 1. Root-cause regressions: routing_status + cwd-independent .env =========


def test_routing_status_is_not_function_local_in_week_cycle_cli():
    """The exact production crash: a function-local ``routing_status`` import made
    the module-level name unbound on every normal LLM cycle (UnboundLocalError)."""

    assert "routing_status" not in main_mod.run_week_cycle_cli.__code__.co_varnames


def test_repo_root_dotenv_is_cwd_independent(monkeypatch, tmp_path):
    """The diagnostic helper resolves the intended repo .env regardless of CWD.

    (The cheap-loop child is launched with repo_root as its CWD, so its CWD-based
    .env discovery finds this same file — see the watchdog/start_cheap_loop tests.)
    """

    monkeypatch.chdir(tmp_path)  # a CWD with no .env of its own
    resolved = main_mod.repo_root_dotenv()
    assert resolved is not None
    assert resolved == main_mod.repo_root() / ".env"


# === 2. Watchdog spawn: paper-capable, never --dry-run-loop, repo-root cwd =====


def test_build_cheap_loop_command_never_injects_dry_run_loop():
    cmd = ops.build_cheap_loop_command(sleep_seconds=900, team="both")
    assert "--dry-run-loop" not in cmd
    assert "run-cheap-competition-loop" in cmd


def test_watchdog_normal_spawn_is_paper_capable_with_repo_cwd(monkeypatch):
    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return BotActionResult(True, "Started cheap loop (PID 7).", 7)

    _wire_watchdog(monkeypatch, starter=fake_start)
    main_mod.run_loop_watchdog(team="both", sleep_seconds=900, once=True, dry_run=False)

    # The watchdog launches the normal child with the repo root as CWD and tags it
    # as watchdog-spawned — and never passes a dry-run flag.
    assert captured.get("spawned_by") == "watchdog"
    assert captured.get("cwd") == str(ops.repo_root())
    assert "dry_run_loop" not in captured  # the spawner has no dry-run knob at all


def test_start_cheap_loop_passes_repo_cwd_and_spawn_marker_no_dry_run(tmp_path):
    seen: dict = {}

    class _FakePopen:
        pid = 4321

    def fake_popen(command, **kwargs):
        seen["command"] = list(command)
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return _FakePopen()

    result = ops.start_cheap_loop(
        runtime_dir=tmp_path, popen=fake_popen,
        process_checker=lambda pid: False, detector=lambda: [],
        spawned_by="watchdog",
    )

    assert result.ok is True
    assert "--dry-run-loop" not in seen["command"]
    assert seen["cwd"] == str(ops.repo_root())
    assert seen["env"].get("LOOP_SPAWNED_BY") == "watchdog"


def test_explicit_dry_run_loop_command_keeps_flag():
    cmd = ops.build_cheap_loop_dry_run_command()
    assert "--dry-run-loop" in cmd
    cfg = main_mod._effective_execution_config(_settings(dry_run=False), dry_run_loop=True)
    assert cfg["execution_mode"] == "dry_run"
    assert cfg["loop_dry_run_flag"] is True


def test_dry_run_false_normal_loop_is_paper_execution(monkeypatch):
    monkeypatch.delenv("LOOP_SPAWNED_BY", raising=False)
    cfg = main_mod._effective_execution_config(_settings(dry_run=False), dry_run_loop=False)
    assert cfg["settings_dry_run"] is False
    assert cfg["loop_dry_run_flag"] is False
    assert cfg["execution_mode"] == "paper_execution_enabled"


# === 3. Phase 7Z classifier semantics preserved ===============================


def test_alpha_style_approved_not_submitted_is_dry_run_block_not_no_trade():
    """Alpha case: approved proposals, none submitted, explicit dry-run -> the
    cycle gets execution_block=dry_run and NO no_trade_reason_class."""

    outcome = classify_candidate_outcome(
        team_id="team_alpha", account_available=True, execution_config_enabled=True,
        health_block=False, portfolio_manager_allows_new=True,
        portfolio_manager_is_genuine_hold=False, provider_called=True,
        parsed_proposal_count=3, routed_execution_eligible=3, routed_simulation_only=0,
        routed_rejected=0, orders_submitted=0, risk_approved_count=3, dry_run=True,
        kill_switch_engaged=False, review_only=False, team_autonomy_enabled=True,
        broker_client_available=True,
    )
    assert outcome.execution_block_reason == EXEC_DRY_RUN
    assert outcome.no_trade_reason_class is None


def test_dry_run_false_paper_loop_is_not_classified_dry_run():
    """A DRY_RUN=false paper loop that submits must NOT show a dry-run block."""

    outcome = classify_candidate_outcome(
        team_id="team_alpha", account_available=True, execution_config_enabled=True,
        health_block=False, portfolio_manager_allows_new=True,
        portfolio_manager_is_genuine_hold=False, provider_called=True,
        parsed_proposal_count=2, routed_execution_eligible=2, routed_simulation_only=0,
        routed_rejected=0, orders_submitted=2, risk_approved_count=2, dry_run=False,
        kill_switch_engaged=False, review_only=False, team_autonomy_enabled=True,
        broker_client_available=True,
    )
    assert outcome.execution_block_reason is None
    assert outcome.execution_block_reason != EXEC_DRY_RUN
    assert outcome.no_trade_reason_class is None


# === 4. Audit: errored iteration is truthful, never reuses a stale scorecard ===


def test_errored_iteration_audit_does_not_reuse_stale_scorecard(monkeypatch, tmp_path):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("LOOP_AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv("LOOP_SPAWNED_BY", "watchdog")

    def _must_not_read(_team):  # the error path must never touch the latest scorecard
        raise AssertionError("errored iteration must not read a prior scorecard")

    monkeypatch.setattr(main_mod, "load_latest_scorecard", _must_not_read)

    main_mod._write_iteration_audit(
        iteration=73, team_id="team_alpha",
        started_at="2026-06-30T19:12:43+00:00", market_state="open",
        cycle_action="error",
        gate_decision=SimpleNamespace(
            should_run_full_cycle=True, recommend_review_only=False,
            reason="Minimum interval elapsed (1096m >= 15m); full cycle recommended.",
        ),
        kill_switch_engaged=False,
        exception_text="UnboundLocalError: cannot access local variable 'routing_status'...",
        settings=_settings(dry_run=False), portfolio_result=None, dry_run_loop=False,
        error_stage="full_cycle", error_type="UnboundLocalError",
        error_message="cannot access local variable 'routing_status' where it is not associated with a value",
    )

    rec = load_latest_status("team_alpha", audit_dir=audit_dir)
    assert rec is not None
    assert rec["cycle_action"] == "error"
    assert rec["source_freshness"] == "unavailable"
    # No stale scorecard narrative substituted into the errored iteration.
    assert rec["execution_block_reason"] is None
    assert rec["proposals_count"] is None
    assert rec["approved_count"] is None
    assert rec["account_read_ok"] is None
    assert rec["benchmark_anchors_available"] is None
    # Truthful CURRENT execution config + structured error metadata.
    assert rec["settings_dry_run"] is False
    assert rec["execution_mode"] == "paper_execution_enabled"
    assert rec["watchdog_spawned"] is True
    assert rec["error_type"] == "UnboundLocalError"
    assert rec["error_stage"] == "full_cycle"


def test_completed_cycle_audit_records_current_paper_execution(monkeypatch, tmp_path):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("LOOP_AUDIT_DIR", str(audit_dir))
    monkeypatch.delenv("LOOP_SPAWNED_BY", raising=False)
    monkeypatch.setattr(
        "src.competition.prompt_memory.load_prompt_memory_metadata", lambda *_a, **_k: {}
    )
    card = SimpleNamespace(
        proposals_count=2, approved_count=1, simulation_only_count=0, rejected_count=1,
        orders_submitted=1, broker_rejected_count=0, portfolio_decision_type="add",
        portfolio_no_trade=False, candidate_generation_outcome={}, no_trade_reason_class=None,
        execution_block_reason=None, account_read_ok=True,
        account_snapshot_source="live_team_paper_account", account_snapshot_time="t",
        reconciliation_status="clean", reconciliation_conflicts=[], provider_outcome="success",
        routed_provider="ollama", routed_model="m", spy_start_price=100.0, spy_end_price=101.0,
        benchmark_timeframe="weekly", current_equity=1_000_000.0, cash=1_000_000.0,
        buying_power=2_000_000.0,
    )
    monkeypatch.setattr(main_mod, "load_latest_scorecard", lambda _t: card)

    main_mod._write_iteration_audit(
        iteration=10, team_id="team_alpha", started_at="2026-06-30T15:00:00+00:00",
        market_state="open", cycle_action="full_cycle",
        gate_decision=SimpleNamespace(should_run_full_cycle=True, recommend_review_only=False, reason="go"),
        kill_switch_engaged=False, exception_text=None,
        settings=_settings(dry_run=False), portfolio_result=None, dry_run_loop=False,
    )

    rec = load_latest_status("team_alpha", audit_dir=audit_dir)
    assert rec["cycle_action"] == "full_cycle"
    assert rec["source_freshness"] == "current"
    assert rec["execution_mode"] == "paper_execution_enabled"
    assert rec["settings_dry_run"] is False
    assert rec["orders_submitted"] == 1


# === 5. Discord: errored cycle -> compact failure brief, no stale leakage ======


def test_errored_cycle_discord_brief_is_compact_failure_only(monkeypatch):
    # If the error path (wrongly) read the latest scorecard, this would explode.
    monkeypatch.setattr(
        "src.competition.scorecard.load_latest_scorecard",
        lambda _t: (_ for _ in ()).throw(AssertionError("error brief must not read a scorecard")),
    )

    ctx = cu.gather_team_iteration_context(
        "team_alpha", iteration=73, cycle_action="error",
        gate_decision=SimpleNamespace(reason="Minimum interval elapsed (1096m >= 15m)."),
        market_state="open", kill_switch_engaged=False,
        error_stage="full_cycle", error_type="UnboundLocalError",
        error_message="cannot access local variable 'routing_status'",
    )
    msg = cu.build_team_iteration_update("team_alpha", ctx, style="brief")

    # Truthful, current-fact-only failure brief.
    assert "Cycle error" in msg
    assert "full_cycle" in msg
    assert "UnboundLocalError" in msg
    # NONE of the stale Alpha leakage from the production posts.
    for stale in ("dry_run", "Proposals: 3", "approved 3", "beat SPY",
                  "Submission/execution block", "account_read_ok=True"):
        assert stale not in msg


def test_beta_style_failure_before_scorecard_has_error_metadata():
    """Beta posted no Phase 7Z fields and a stale narrative; the compact error
    brief instead carries the error stage/type and omits all stale grounding."""

    ctx = cu.gather_team_iteration_context(
        "team_beta", iteration=73, cycle_action="error",
        gate_decision=SimpleNamespace(reason="Minimum interval elapsed (1400m >= 15m)."),
        market_state="open", kill_switch_engaged=False,
        error_stage="full_cycle", error_type="UnboundLocalError",
        error_message="cannot access local variable 'routing_status'",
    )
    assert ctx["grounding_unavailable"] is True
    assert ctx["benchmark_anchors_available"] is False
    msg = cu.build_team_iteration_update("team_beta", ctx, style="brief")
    assert "Error stage: full_cycle" in msg
    assert "Error type: UnboundLocalError" in msg
    assert "low buying power" not in msg.lower()
    assert "vs SPY" not in msg  # no SPY-relative line at all on a failed cycle


# === 6. Discord: missing benchmark anchors suppress beat/loss/excess ===========


def test_missing_benchmark_anchors_suppress_all_spy_language():
    ctx = {
        "team_id": "team_alpha", "cycle_action": cu.ACTION_FULL_CYCLE,
        "team_return": 0.0200, "spy_return": 0.0087, "excess_return": 0.0113,
        "benchmark_anchors_available": False, "why_vs_spy": "short exposure lagged",
    }
    msg = cu.build_team_iteration_update("team_alpha", ctx, style="brief")
    assert "- vs SPY: n/a" in msg
    assert "beat SPY" not in msg
    assert "trailed SPY" not in msg
    assert "0.0113" not in msg          # stale excess never shown
    assert "Why vs SPY" not in msg      # omitted when anchors missing


def test_present_benchmark_anchors_still_show_spy_relative():
    ctx = {
        "team_id": "team_alpha", "cycle_action": cu.ACTION_FULL_CYCLE,
        "team_return": 0.0200, "spy_return": 0.0087,
        "benchmark_anchors_available": True, "why_vs_spy": "held index leadership",
    }
    msg = cu.build_team_iteration_update("team_alpha", ctx, style="brief")
    assert "vs SPY: beat SPY" in msg
    assert "Why vs SPY" in msg


def test_gather_sets_anchors_false_when_scorecard_anchors_missing(monkeypatch):
    card = _stale_scorecard()  # spy_start_price/spy_end_price are None
    _stub_gather_artifacts(monkeypatch, scorecard=card)
    ctx = cu.gather_team_iteration_context("team_alpha", cycle_action=cu.ACTION_FULL_CYCLE)
    assert ctx["benchmark_anchors_available"] is False
    assert ctx.get("spy_return") is None
    assert ctx.get("excess_return") is None


# === 7. Discord: zero live positions suppress stale active-holding language =====


def test_build_suppresses_weakest_holding_on_zero_positions():
    ctx = {
        "team_id": "team_alpha", "cycle_action": cu.ACTION_FULL_CYCLE,
        "weakest_symbol": "XYZ", "strongest_symbol": "ZZZ",
        "zero_positions": True, "benchmark_anchors_available": True,
        "team_return": 0.0, "spy_return": 0.0,
    }
    msg = cu.build_team_iteration_update("team_alpha", ctx, style="brief")
    assert "- Weakest holding: n/a" in msg
    assert "XYZ" not in msg
    assert "top winner ZZZ" not in msg


def test_gather_flags_zero_positions_and_suppresses_holdings(monkeypatch):
    card = SimpleNamespace(
        portfolio_decision_type="no_trade", portfolio_no_trade=True, max_new_proposals=0,
        team_return=0.0, spy_benchmark_return=None, excess_return_vs_spy=None,
        current_equity=1_000_000.0, proposals_count=0, approved_count=0, rejected_count=0,
        simulation_only_count=0, orders_submitted=0, broker_rejected_count=0,
        no_trade_reason_class=None, execution_block_reason=None, reconciliation_status="clean",
        account_read_ok=True, reconciliation_conflicts=[],
        spy_start_price=None, spy_end_price=None,
        gross_exposure=0.0, net_exposure=0.0, short_exposure=0.0,
    )
    attribution = SimpleNamespace(top_winners=[], top_losers=[{"symbol": "XYZ"}], submitted_orders=0)
    _stub_gather_artifacts(monkeypatch, scorecard=card, attribution=attribution)

    ctx = cu.gather_team_iteration_context("team_alpha", cycle_action=cu.ACTION_FULL_CYCLE)
    assert ctx["zero_positions"] is True
    msg = cu.build_team_iteration_update("team_alpha", ctx, style="brief")
    assert "- Weakest holding: n/a" in msg
    assert "XYZ" not in msg


def test_ungrounded_read_suppresses_holdings(monkeypatch):
    card = SimpleNamespace(
        portfolio_decision_type="no_trade", portfolio_no_trade=True, max_new_proposals=0,
        team_return=0.0, spy_benchmark_return=None, excess_return_vs_spy=None,
        current_equity=1_000_000.0, proposals_count=0, approved_count=0, rejected_count=0,
        simulation_only_count=0, orders_submitted=0, broker_rejected_count=0,
        no_trade_reason_class=None, execution_block_reason=None, reconciliation_status="account_state_unavailable",
        account_read_ok=None, reconciliation_conflicts=[],
        spy_start_price=None, spy_end_price=None,
        gross_exposure=0.0, net_exposure=0.0, short_exposure=0.0,
    )
    attribution = SimpleNamespace(top_winners=[{"symbol": "MSFT"}], top_losers=[{"symbol": "XYZ"}], submitted_orders=0)
    _stub_gather_artifacts(monkeypatch, scorecard=card, attribution=attribution)

    ctx = cu.gather_team_iteration_context("team_beta", cycle_action=cu.ACTION_FULL_CYCLE)
    assert ctx["grounding_unavailable"] is True
    msg = cu.build_team_iteration_update("team_beta", ctx, style="brief")
    assert "XYZ" not in msg and "MSFT" not in msg


# === 8. loop-runtime-config: safe booleans/paths only, proves not-dry-run ======


def test_loop_runtime_config_reports_paper_execution_no_secrets(capsys):
    main_mod.run_loop_runtime_config()
    out = capsys.readouterr().out
    assert "execution_mode=paper_execution_enabled" in out
    assert "settings_dry_run=False" in out
    assert "never includes --dry-run-loop" in out
    # No secret-looking material in the safe config dump.
    for needle in ("API_KEY", "SECRET", "TOKEN", "Bot "):
        assert needle not in out
