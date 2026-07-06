"""Team charter (self-set parameters, clamped to platform) and dynamic watchlist."""

from __future__ import annotations

from src.charter import TeamCharter
from src.config import RiskLimits
from src.watchlist import MAX_WATCHLIST, TeamWatchlist
from tests.conftest import StaticAssets

LIMITS = RiskLimits()  # platform defaults: pos 0.30, gross 2.0, cycle 5-120


def test_charter_defaults_differ_by_team(tmp_path):
    alpha = TeamCharter.load("team_alpha", tmp_path, LIMITS)
    beta = TeamCharter.load("team_beta", tmp_path, LIMITS)
    assert alpha.cycle_minutes < beta.cycle_minutes          # alpha trades faster
    assert alpha.max_gross_exposure > beta.max_gross_exposure


def test_charter_updates_apply_and_persist(tmp_path):
    charter = TeamCharter.load("team_alpha", tmp_path, LIMITS)
    changed = charter.apply_updates(
        {"max_position_pct": 0.20, "cycle_minutes": 10, "instruments": ["stocks", "options"]},
        LIMITS,
        "pressing our edge",
    )
    assert changed["max_position_pct"] == (0.15, 0.20)
    assert changed["cycle_minutes"] == (20, 10)
    charter.save()

    reloaded = TeamCharter.load("team_alpha", tmp_path, LIMITS)
    assert reloaded.max_position_pct == 0.20
    assert reloaded.cycle_minutes == 10
    assert reloaded.instruments == ["stocks", "options"]
    assert reloaded.history[-1]["reason"] == "pressing our edge"


def test_charter_clamped_to_platform_caps(tmp_path):
    charter = TeamCharter.load("team_alpha", tmp_path, LIMITS)
    changed = charter.apply_updates(
        {"max_position_pct": 0.95, "max_gross_exposure": 9.0, "cycle_minutes": 1},
        LIMITS,
        "going wild",
    )
    assert charter.max_position_pct == LIMITS.max_position_pct    # 0.30, not 0.95
    assert charter.max_gross_exposure == LIMITS.max_gross_exposure  # 2.0, not 9.0
    assert charter.cycle_minutes == LIMITS.min_cycle_minutes      # 5, not 1
    assert changed  # it did change, just to the clamped values


def test_charter_cannot_enable_platform_disabled_instruments(tmp_path):
    limits = RiskLimits(allow_options=False, allow_margin=False)
    charter = TeamCharter.load("team_alpha", tmp_path, limits)
    charter.apply_updates({"instruments": ["stocks", "options", "margin", "shorts"]}, limits, "try")
    assert "options" not in charter.instruments
    assert "margin" not in charter.instruments
    assert "shorts" in charter.instruments
    assert "stocks" in charter.instruments


def test_charter_stocks_always_present(tmp_path):
    charter = TeamCharter.load("team_beta", tmp_path, LIMITS)
    charter.apply_updates({"instruments": ["options"]}, LIMITS, "options only!")
    assert "stocks" in charter.instruments


def test_charter_invalid_updates_ignored(tmp_path):
    charter = TeamCharter.load("team_beta", tmp_path, LIMITS)
    before = charter.max_position_pct
    assert charter.apply_updates("not a dict", LIMITS, "junk") == {}
    assert charter.apply_updates({"max_position_pct": "lots"}, LIMITS, "junk") == {}
    assert charter.max_position_pct == before


def test_charter_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "charter" / "team_alpha.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    charter = TeamCharter.load("team_alpha", tmp_path, LIMITS)
    assert charter.max_position_pct == 0.15  # alpha default


def test_charter_render_mentions_platform_caps(tmp_path):
    text = TeamCharter.load("team_alpha", tmp_path, LIMITS).render(LIMITS)
    assert "NEVER exceed" in text
    assert "selling/writing options is never allowed" in text


# --- watchlist -----------------------------------------------------------------

CORE = ("SPY", "QQQ")


def test_watchlist_add_validates_against_broker(tmp_path):
    wl = TeamWatchlist.load("team_alpha", tmp_path, CORE)
    result = wl.apply_changes(["smci", "FAKETK", "bad sym"], [], StaticAssets(missing=["FAKETK"]))
    assert result["added"] == ["SMCI"]
    assert any("FAKETK" in r for r in result["rejected"])
    assert wl.symbols == ["SPY", "QQQ", "SMCI"]

    # persists
    reloaded = TeamWatchlist.load("team_alpha", tmp_path, CORE)
    assert "SMCI" in reloaded.symbols


def test_watchlist_core_protected(tmp_path):
    wl = TeamWatchlist.load("team_alpha", tmp_path, CORE)
    result = wl.apply_changes([], ["SPY"], StaticAssets())
    assert result["removed"] == []
    assert any("core" in r for r in result["rejected"])
    assert "SPY" in wl.symbols


def test_watchlist_remove_extra(tmp_path):
    wl = TeamWatchlist.load("team_alpha", tmp_path, CORE)
    wl.apply_changes(["SMCI"], [], StaticAssets())
    result = wl.apply_changes([], ["SMCI"], StaticAssets())
    assert result["removed"] == ["SMCI"]
    assert "SMCI" not in wl.symbols


def test_watchlist_size_cap(tmp_path):
    wl = TeamWatchlist.load("team_alpha", tmp_path, CORE)
    additions = [f"SY{i}" for i in range(MAX_WATCHLIST + 5)]
    result = wl.apply_changes(additions, [], StaticAssets())
    assert len(wl.symbols) == MAX_WATCHLIST
    assert any("full" in r for r in result["rejected"])


def test_watchlist_duplicates_ignored(tmp_path):
    wl = TeamWatchlist.load("team_alpha", tmp_path, CORE)
    result = wl.apply_changes(["SPY", "NVDA", "NVDA"], [], StaticAssets())
    assert result["added"] == ["NVDA"]
