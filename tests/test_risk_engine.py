"""The deterministic risk engine is the load-bearing safety wall — test it hard."""

from __future__ import annotations

from src.agents import Proposal, RiskVerdict
from src.broker import AccountInfo, OptionContract, PositionInfo
from src.charter import TeamCharter
from src.config import RiskLimits
from src.risk import evaluate_proposals
from tests.conftest import StaticAssets, StaticPrices, make_position

# Pinned platform caps used by most tests (tighter than the real defaults so
# cap behavior is exercised without margin noise).
TIGHT = RiskLimits(
    max_position_pct=0.15, max_gross_exposure=1.0,
    allow_margin=False, allow_options=True, allow_shorts=True,
)


def proposal(symbol="NVDA", action="buy", weight=0.10, fraction=1.0, **kwargs) -> Proposal:
    return Proposal(
        symbol=symbol, action=action, weight_pct=weight, fraction=fraction,
        thesis="test thesis", exit_plan="test exit", confidence=0.6, **kwargs,
    )


def approve(index=0, adjusted=None, verdict="approve") -> RiskVerdict:
    return RiskVerdict(index=index, verdict=verdict, adjusted_weight_pct=adjusted, reason="ok")


def make_charter(tmp_path, limits, **overrides) -> TeamCharter:
    charter = TeamCharter.load("team_alpha", tmp_path, limits)
    for key, value in overrides.items():
        setattr(charter, key, value)
    charter._enforce(limits)
    return charter


def run(proposals, verdicts, account, positions=None, limits=None, prices=None,
        orders_today=0, notional_today=0.0, assets=None, charter=None, resolver=None):
    return evaluate_proposals(
        proposals, verdicts,
        account=account,
        positions=positions or [],
        limits=limits or TIGHT,
        charter=charter,
        orders_today=orders_today,
        notional_today=notional_today,
        price_of=prices or StaticPrices({"NVDA": 200.0, "SPY": 500.0, "AAPL": 250.0}),
        asset_of=assets or StaticAssets(),
        resolve_option=resolver,
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
    decisions = run([proposal(weight=0.10)], [approve(adjusted=0.02, verdict="reduce")], account)
    assert decisions[0].approved
    assert decisions[0].qty == 100  # 2% of 1M / 200

    decisions = run([proposal(weight=0.05)], [approve(adjusted=0.50, verdict="reduce")], account)
    assert decisions[0].qty == 250  # 5%, not 50%


def test_platform_position_cap_applies(account):
    decisions = run([proposal(weight=0.90)], [approve()], account)
    assert decisions[0].approved
    # capped at platform 15% of equity = $150k / $200 = 750 shares
    assert decisions[0].qty == 750


def test_charter_tightens_position_cap(account, tmp_path):
    charter = make_charter(tmp_path, TIGHT, max_position_pct=0.05)
    decisions = run([proposal(weight=0.90)], [approve()], account, charter=charter)
    assert decisions[0].approved
    assert decisions[0].qty == 250  # 5% of 1M / 200


def test_charter_cannot_loosen_platform_cap(account, tmp_path):
    charter = make_charter(tmp_path, TIGHT, max_position_pct=0.99)
    decisions = run([proposal(weight=0.90)], [approve()], account, charter=charter)
    # charter is clamped to platform 15% at enforce time
    assert decisions[0].qty == 750


def test_existing_position_counts_toward_cap(account):
    positions = [make_position("NVDA", qty=700, price=200.0)]  # $140k held (14%)
    decisions = run([proposal(weight=0.10)], [approve()], account, positions=positions)
    d = decisions[0]
    assert d.approved
    assert d.qty == 50  # only $10k of room left


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


def test_short_requires_platform_flag(account):
    limits = RiskLimits(allow_shorts=False, allow_margin=False)
    decisions = run([proposal(action="short")], [approve()], account, limits=limits)
    assert not decisions[0].approved
    assert "shorting not enabled" in decisions[0].reason_text


def test_short_requires_charter_instrument(account, tmp_path):
    charter = make_charter(tmp_path, TIGHT, instruments=["stocks"])
    decisions = run([proposal(action="short")], [approve()], account, charter=charter)
    assert not decisions[0].approved
    assert "shorting not enabled" in decisions[0].reason_text


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
    assert "no long stock position" in decisions[0].reason_text


def test_sell_full_and_partial_position(account):
    positions = [make_position("NVDA", qty=300, price=200.0)]
    decisions = run([proposal(action="sell", fraction=1.0)], [approve()], account, positions=positions)
    assert decisions[0].approved and decisions[0].qty == 300

    decisions = run([proposal(action="sell", fraction=0.5)], [approve()], account, positions=positions)
    assert decisions[0].approved and decisions[0].qty == 150


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
    limits = RiskLimits(max_orders_per_day=5, allow_margin=False)
    decisions = run([proposal()], [approve()], account, limits=limits, orders_today=5)
    assert not decisions[0].approved
    assert "daily order cap" in decisions[0].reason_text


def test_daily_notional_cap(account):
    limits = RiskLimits(max_daily_notional=100_000.0, allow_margin=False)
    decisions = run([proposal(weight=0.10)], [approve()], account, limits=limits,
                    notional_today=100_000.0)
    assert not decisions[0].approved
    assert "daily notional cap" in decisions[0].reason_text


def test_without_margin_buys_limited_to_cash():
    poor = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=40_000.0, buying_power=2_000_000.0)
    decisions = run([proposal(weight=0.10)], [approve()], poor)  # TIGHT has margin off
    assert decisions[0].approved
    assert decisions[0].qty == 200  # $40k cash / $200, not the $100k requested


def test_with_margin_buys_can_exceed_cash():
    poor = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=40_000.0, buying_power=2_000_000.0)
    limits = RiskLimits(max_position_pct=0.15, max_gross_exposure=1.0, allow_margin=True)
    decisions = run([proposal(weight=0.10)], [approve()], poor, limits=limits)
    assert decisions[0].approved
    assert decisions[0].qty == 500  # full $100k on buying power


def test_margin_needs_charter_instrument(tmp_path):
    poor = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=40_000.0, buying_power=2_000_000.0)
    limits = RiskLimits(max_position_pct=0.15, max_gross_exposure=1.0, allow_margin=True)
    charter = make_charter(tmp_path, limits, instruments=["stocks", "shorts"])  # no margin
    decisions = run([proposal(weight=0.10)], [approve()], poor, limits=limits, charter=charter)
    assert decisions[0].qty == 200  # cash-limited despite platform margin


def test_zero_buying_power_blocks(account):
    broke = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=1_000_000.0, buying_power=0.0)
    decisions = run([proposal(weight=0.10)], [approve()], broke)
    assert not decisions[0].approved


def test_gross_exposure_cap(account):
    limits = RiskLimits(max_gross_exposure=0.5, allow_margin=False)
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


def test_second_buy_sees_cash_used_by_first():
    # No margin: $260k cash. First buy takes $150k (15% cap), second (AAPL)
    # must shrink to the remaining $110k -> 440 shares at $250.
    acct = AccountInfo(equity=1_000_000.0, last_equity=1_000_000.0, cash=260_000.0, buying_power=2_000_000.0)
    proposals = [proposal("NVDA", weight=0.15), proposal("AAPL", weight=0.15)]
    verdicts = [approve(0), approve(1)]
    decisions = run(proposals, verdicts, acct)
    assert decisions[0].approved and decisions[0].qty == 750
    assert decisions[1].approved
    assert decisions[1].qty == 440


# --- Options -----------------------------------------------------------------

NVDA_CALL = OptionContract(
    occ_symbol="NVDA260807C00200000", underlying="NVDA", option_type="call",
    strike=200.0, expiration="2026-08-07", dte=32, bid=7.0, ask=8.0,
)


def option_proposal(weight=0.02, **kwargs) -> Proposal:
    return proposal(
        symbol="NVDA", action="buy", weight=weight,
        instrument="option", option_type="call", **kwargs,
    )


def make_option_position(occ=NVDA_CALL.occ_symbol, qty=3, premium=8.0) -> PositionInfo:
    return PositionInfo(
        symbol=occ, qty=qty, side="long", avg_entry_price=premium,
        current_price=premium, market_value=qty * premium * 100,
        unrealized_plpc=0.0, asset_class="us_option",
    )


def test_option_buy_sized_by_premium(account):
    decisions = run([option_proposal(weight=0.02)], [approve()], account,
                    resolver=lambda *a, **k: NVDA_CALL)
    d = decisions[0]
    assert d.approved
    assert d.symbol == NVDA_CALL.occ_symbol
    assert d.instrument == "option"
    # budget: min(2% of 1M, 3% cap) = $20k; $8 ask x100 = $800/contract -> 25
    assert d.qty == 25
    assert d.est_notional == 25 * 800.0


def test_option_per_trade_premium_cap(account):
    # weight 10% requested but per-trade cap is 3% of equity = $30k -> 37 contracts
    decisions = run([option_proposal(weight=0.10)], [approve()], account,
                    resolver=lambda *a, **k: NVDA_CALL)
    assert decisions[0].approved
    assert decisions[0].qty == 37


def test_option_total_premium_cap(account):
    limits = RiskLimits(max_total_option_premium_pct=0.01, allow_margin=False)
    positions = [make_option_position(qty=13, premium=8.0)]  # $10,400 open premium > 1%
    decisions = run([option_proposal()], [approve()], account, positions=positions,
                    limits=limits, resolver=lambda *a, **k: NVDA_CALL)
    assert not decisions[0].approved
    assert "total open option premium cap" in decisions[0].reason_text


def test_option_requires_enabled(account, tmp_path):
    limits = RiskLimits(allow_options=False)
    decisions = run([option_proposal()], [approve()], account, limits=limits,
                    resolver=lambda *a, **k: NVDA_CALL)
    assert not decisions[0].approved
    assert "options not enabled" in decisions[0].reason_text

    charter = make_charter(tmp_path, TIGHT, instruments=["stocks"])
    decisions = run([option_proposal()], [approve()], account, charter=charter,
                    resolver=lambda *a, **k: NVDA_CALL)
    assert not decisions[0].approved


def test_option_no_contract_rejected(account):
    decisions = run([option_proposal()], [approve()], account, resolver=lambda *a, **k: None)
    assert not decisions[0].approved
    assert "no suitable option contract" in decisions[0].reason_text


def test_option_sell_to_close(account):
    positions = [make_option_position(qty=4, premium=10.0)]
    sell = proposal(symbol=NVDA_CALL.occ_symbol, action="sell", fraction=0.5, instrument="option")
    decisions = run([sell], [approve()], account, positions=positions)
    d = decisions[0]
    assert d.approved
    assert d.order_side == "sell"
    assert d.qty == 2
    assert d.est_notional == 2 * 10.0 * 100


def test_option_sell_requires_held_contract(account):
    sell = proposal(symbol=NVDA_CALL.occ_symbol, action="sell", instrument="option")
    decisions = run([sell], [approve()], account)
    assert not decisions[0].approved
    assert "no long option position" in decisions[0].reason_text
