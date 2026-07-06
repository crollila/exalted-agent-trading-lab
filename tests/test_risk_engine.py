"""The deterministic risk engine is the load-bearing safety wall — test it hard."""

from __future__ import annotations

from src.agents import Proposal, RiskVerdict
from src.config import RiskLimits
from src.risk import evaluate_proposals
from tests.conftest import StaticAssets, StaticPrices, make_position


def proposal(symbol="NVDA", action="buy", weight=0.10, fraction=1.0) -> Proposal:
    return Proposal(
        symbol=symbol, action=action, weight_pct=weight, fraction=fraction,
        thesis="test thesis", exit_plan="test exit", confidence=0.6,
    )


def approve(index=0, adjusted=None, verdict="approve") -> RiskVerdict:
    return RiskVerdict(index=index, verdict=verdict, adjusted_weight_pct=adjusted, reason="ok")


def run(proposals, verdicts, account, positions=None, limits=None, prices=None,
        orders_today=0, notional_today=0.0, assets=None):
    return evaluate_proposals(
        proposals, verdicts,
        account=account,
        positions=positions or [],
        limits=limits or RiskLimits(),
        orders_today=orders_today,
        notional_today=notional_today,
        price_of=prices or StaticPrices({"NVDA": 200.0, "SPY": 500.0, "AAPL": 250.0}),
        asset_of=assets or StaticAssets(),
    )


def test_approves_and_sizes_simple_buy(account):
    decisions = run([proposal(weight=0.10)], [approve()], account)
    d = decisions[0]
    assert d.approved
    # 10% of $1M = $100k at $200 = 500 shares
    assert d.qty == 500
    assert d.order_side == "buy"


def test_rejects_when_risk_analyst_rejects(account):
    decisions = run([proposal()], [approve(verdict="reject")], account)
    assert not decisions[0].approved
    assert "risk analyst rejected" in decisions[0].reason_text


def test_missing_verdict_fails_closed(account):
    decisions = run([proposal()], [], account)
    assert not decisions[0].approved
    assert "no risk-analyst verdict" in decisions[0].reason_text


def test_analyst_can_shrink_but_not_enlarge(account):
    # Analyst "reduce" to 0.02 -> smaller than requested 0.10.
    decisions = run([proposal(weight=0.10)], [approve(adjusted=0.02, verdict="reduce")], account)
    assert decisions[0].approved
    assert decisions[0].qty == 100  # 2% of 1M / 200

    # Analyst tries to ENLARGE via reduce verdict -> clamped to requested weight.
    decisions = run([proposal(weight=0.05)], [approve(adjusted=0.50, verdict="reduce")], account)
    assert decisions[0].qty == 250  # 5%, not 50%


def test_position_weight_cap_applies(account):
    decisions = run([proposal(weight=0.90)], [approve()], account)
    assert decisions[0].approved
    # capped at 15% of equity = $150k / $200 = 750 shares
    assert decisions[0].qty == 750


def test_existing_position_counts_toward_cap(account):
    positions = [make_position("NVDA", qty=700, price=200.0)]  # $140k held (14%)
    decisions = run([proposal(weight=0.10)], [approve()], account, positions=positions)
    d = decisions[0]
    assert d.approved
    # only $10k of room left -> 50 shares
    assert d.qty == 50


def test_position_at_cap_rejected(account):
    positions = [make_position("NVDA", qty=800, price=200.0)]  # $160k > 15%
    decisions = run([proposal(weight=0.05)], [approve()], account, positions=positions)
    assert not decisions[0].approved
    assert "max weight" in decisions[0].reason_text


def test_unknown_ticker_rejected(account):
    decisions = run(
        [proposal(symbol="FAKETK")], [approve()], account,
        prices=StaticPrices({"FAKETK": 10.0}), assets=StaticAssets(missing=["FAKETK"]),
    )
    assert not decisions[0].approved
    assert "not found or not tradable" in decisions[0].reason_text


def test_no_price_rejected(account):
    decisions = run([proposal(symbol="AAPL")], [approve()], account, prices=StaticPrices({}))
    assert not decisions[0].approved
    assert "no live price" in decisions[0].reason_text


def test_short_requires_allow_shorts(account):
    limits = RiskLimits(allow_shorts=False)
    decisions = run([proposal(action="short")], [approve()], account, limits=limits)
    assert not decisions[0].approved
    assert "shorting disabled" in decisions[0].reason_text


def test_short_requires_shortable_asset(account):
    decisions = run(
        [proposal(action="short")], [approve()], account,
        assets=StaticAssets(unshortable=["NVDA"]),
    )
    assert not decisions[0].approved
    assert "not shortable" in decisions[0].reason_text


def test_short_approved_uses_sell_side(account):
    decisions = run([proposal(action="short", weight=0.05)], [approve()], account)
    assert decisions[0].approved
    assert decisions[0].order_side == "sell"


def test_sell_requires_long_position(account):
    decisions = run([proposal(action="sell")], [approve()], account)
    assert not decisions[0].approved
    assert "no long position" in decisions[0].reason_text


def test_sell_full_position(account):
    positions = [make_position("NVDA", qty=300, price=200.0)]
    decisions = run([proposal(action="sell", fraction=1.0)], [approve()], account, positions=positions)
    assert decisions[0].approved
    assert decisions[0].qty == 300
    assert decisions[0].order_side == "sell"


def test_sell_half_position(account):
    positions = [make_position("NVDA", qty=300, price=200.0)]
    decisions = run([proposal(action="sell", fraction=0.5)], [approve()], account, positions=positions)
    assert decisions[0].approved
    assert decisions[0].qty == 150


def test_cover_requires_short_position(account):
    decisions = run([proposal(action="cover")], [approve()], account)
    assert not decisions[0].approved

    positions = [make_position("NVDA", qty=200, side="short", price=200.0)]
    decisions = run([proposal(action="cover")], [approve()], account, positions=positions)
    assert decisions[0].approved
    assert decisions[0].order_side == "buy"
    assert decisions[0].qty == 200


def test_buy_while_short_rejected(account):
    positions = [make_position("NVDA", qty=100, side="short", price=200.0)]
    decisions = run([proposal(action="buy")], [approve()], account, positions=positions)
    assert not decisions[0].approved
    assert "cover it instead" in decisions[0].reason_text


def test_daily_order_cap(account):
    limits = RiskLimits(max_orders_per_day=5)
    decisions = run([proposal()], [approve()], account, limits=limits, orders_today=5)
    assert not decisions[0].approved
    assert "daily order cap" in decisions[0].reason_text


def test_daily_notional_cap(account):
    limits = RiskLimits(max_daily_notional=100_000.0)
    decisions = run([proposal(weight=0.10)], [approve()], account, limits=limits,
                    notional_today=100_000.0)
    assert not decisions[0].approved
    assert "daily notional cap" in decisions[0].reason_text


def test_cash_floor_blocks_buys(account):
    from src.broker import AccountInfo

    poor = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=40_000.0, buying_power=2_000_000.0)
    decisions = run([proposal(weight=0.10)], [approve()], poor)  # floor = $50k > cash
    assert not decisions[0].approved
    assert "cash floor" in decisions[0].reason_text


def test_insufficient_buying_power_blocks(account):
    from src.broker import AccountInfo

    broke = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=1_000_000.0, buying_power=0.0)
    decisions = run([proposal(weight=0.10)], [approve()], broke)
    assert not decisions[0].approved


def test_gross_exposure_cap(account):
    limits = RiskLimits(max_gross_exposure=0.5)
    positions = [make_position("AAPL", qty=2000, price=250.0)]  # $500k = 50% gross
    decisions = run([proposal(weight=0.10)], [approve()], account, positions=positions, limits=limits)
    assert not decisions[0].approved
    assert "gross exposure cap" in decisions[0].reason_text


def test_duplicate_symbol_side_in_cycle_rejected(account):
    proposals = [proposal(weight=0.05), proposal(weight=0.05)]
    verdicts = [approve(0), approve(1)]
    decisions = run(proposals, verdicts, account)
    assert decisions[0].approved
    assert not decisions[1].approved
    assert "duplicate" in decisions[1].reason_text


def test_second_buy_sees_cash_used_by_first(account):
    from src.broker import AccountInfo

    # $260k cash, floor $50k -> $210k spendable. First buy takes $150k (cap),
    # second (AAPL) must shrink to the remaining $60k -> 240 shares at $250.
    acct = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=260_000.0, buying_power=2_000_000.0)
    proposals = [proposal("NVDA", weight=0.15), proposal("AAPL", weight=0.15)]
    verdicts = [approve(0), approve(1)]
    decisions = run(proposals, verdicts, acct)
    assert decisions[0].approved and decisions[0].qty == 750
    assert decisions[1].approved
    assert decisions[1].qty == 240
