from competition_helpers import option_long_call, stock_long, stock_short
from src.competition.risk_engine import AccountContext
from src.competition.router import route_proposals
from src.config.permissions import TradingPermissions

EQUITY = 1_000_000.0


def acct(**overrides) -> AccountContext:
    values = dict(equity=EQUITY, cash=EQUITY, buying_power=EQUITY * 2)
    values.update(overrides)
    return AccountContext(**values)


def perms(**overrides) -> TradingPermissions:
    return TradingPermissions.from_env(env={k: str(v) for k, v in overrides.items()})


def test_disabled_levels_go_to_simulation_only():
    result = route_proposals([stock_long(), stock_short(), option_long_call()], perms(), acct())
    assert len(result.execution_eligible) == 1  # only the long
    assert len(result.simulation_only) == 2  # short + option disabled
    assert len(result.rejected) == 0


def test_enabled_levels_become_execution_eligible():
    result = route_proposals(
        [stock_long(), stock_short(), option_long_call()],
        perms(ENABLE_PAPER_SHORTING="true", ENABLE_PAPER_OPTIONS="true"),
        acct(),
    )
    assert len(result.execution_eligible) == 3


def test_daily_order_cap_demotes_extra_to_simulation():
    proposals = [stock_long(confidence=c / 10.0) for c in range(1, 6)]  # 5 longs
    result = route_proposals(proposals, perms(MAX_DAILY_ORDERS_PER_TEAM="2"), acct())
    assert len(result.execution_eligible) == 2
    # The two highest-confidence proposals are kept.
    kept_conf = sorted(r.proposal.confidence for r in result.execution_eligible)
    assert kept_conf == [0.4, 0.5]
    assert all(r.decision.route.value == "simulation_only" for r in result.simulation_only)


def test_orders_today_counts_against_cap():
    result = route_proposals(
        [stock_long(), stock_long()],
        perms(MAX_DAILY_ORDERS_PER_TEAM="3"),
        acct(orders_today=3),
    )
    assert len(result.execution_eligible) == 0
