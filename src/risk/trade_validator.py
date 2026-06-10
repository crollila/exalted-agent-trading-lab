from __future__ import annotations

from dataclasses import dataclass, field

from src.brokers.order_models import AssetClass, RiskDecision, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.risk.position_sizing import dollars_for_target_weight, shares_for_dollars
from src.risk.risk_rules import RiskRules


@dataclass
class TradeValidator:
    rules: RiskRules
    new_positions_today: int = 0
    daily_turnover_value: float = 0.0
    cash_delta_from_approved_trades: float = 0.0
    position_value_delta_by_symbol: dict[str, float] = field(default_factory=dict)
    position_quantity_delta_by_symbol: dict[str, float] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "TradeValidator":
        return cls(rules=RiskRules())

    def validate(self, proposal: TradeProposal, portfolio: PortfolioState) -> RiskDecision:
        reasons: list[str] = []

        if self.rules.stocks_only and proposal.asset_class != AssetClass.STOCK:
            reasons.append("Rejected: only stock trades are allowed.")

        if proposal.asset_class == AssetClass.OPTION and not self.rules.allow_options:
            reasons.append("Rejected: options are disabled.")

        if proposal.estimated_price <= 0:
            reasons.append("Rejected: estimated price must be greater than zero.")

        trade_quantity = self._estimate_quantity(proposal, portfolio) if proposal.estimated_price > 0 else 0.0
        trade_value = trade_quantity * proposal.estimated_price if trade_quantity > 0 else 0.0

        if trade_quantity <= 0:
            reasons.append("Rejected: estimated trade quantity must be greater than zero.")

        if proposal.action == TradeAction.SELL:
            current_qty = self._projected_quantity_before_trade(proposal.symbol, portfolio)
            if trade_quantity > current_qty and not self.rules.allow_shorting:
                reasons.append("Rejected: sell quantity exceeds current position and shorting is disabled.")

        if proposal.action == TradeAction.BUY:
            current_value = self._projected_position_value_before_trade(proposal.symbol, portfolio)
            projected_value = current_value + trade_value
            projected_weight = projected_value / portfolio.equity if portfolio.equity > 0 else 1.0
            if projected_weight > self.rules.max_position_pct:
                reasons.append("Rejected: projected position exceeds max position weight.")

            projected_cash = portfolio.cash + self.cash_delta_from_approved_trades - trade_value
            min_cash = portfolio.equity * self.rules.min_cash_pct
            if projected_cash < min_cash:
                reasons.append("Rejected: projected cash falls below minimum cash requirement.")

            if self._opens_new_position(proposal.symbol, portfolio):
                if self.new_positions_today + 1 > self.rules.max_new_positions_per_day:
                    reasons.append("Rejected: max new positions per day exceeded.")

        projected_turnover = self.daily_turnover_value + trade_value
        max_turnover = portfolio.equity * self.rules.max_daily_turnover_pct
        if projected_turnover > max_turnover:
            reasons.append("Rejected: daily turnover limit exceeded.")

        approved = len(reasons) == 0
        if approved:
            self._record_approved_trade(proposal, portfolio, trade_quantity, trade_value)

        return RiskDecision(
            proposal_id=proposal.proposal_id,
            approved=approved,
            reasons=["Approved."] if not reasons else reasons,
            approved_quantity=trade_quantity if approved else None,
            approved_trade_value=trade_value if approved else None,
        )

    def _estimate_quantity(self, proposal: TradeProposal, portfolio: PortfolioState) -> float:
        if proposal.quantity is not None:
            return proposal.quantity

        if proposal.target_weight is not None:
            target_dollars = dollars_for_target_weight(portfolio.equity, proposal.target_weight)
            current_value = portfolio.position_value(proposal.symbol)

            if proposal.action == TradeAction.BUY:
                trade_dollars = max(target_dollars - current_value, 0.0)
            else:
                trade_dollars = max(current_value - target_dollars, 0.0)

            return shares_for_dollars(trade_dollars, proposal.estimated_price)

        return 0.0

    def _projected_quantity_before_trade(self, symbol: str, portfolio: PortfolioState) -> float:
        position = portfolio.positions.get(symbol)
        current_quantity = 0.0 if position is None else position.quantity
        return current_quantity + self.position_quantity_delta_by_symbol.get(symbol, 0.0)

    def _projected_position_value_before_trade(self, symbol: str, portfolio: PortfolioState) -> float:
        return portfolio.position_value(symbol) + self.position_value_delta_by_symbol.get(symbol, 0.0)

    def _opens_new_position(self, symbol: str, portfolio: PortfolioState) -> bool:
        return self._projected_quantity_before_trade(symbol, portfolio) <= 0

    def _record_approved_trade(
        self,
        proposal: TradeProposal,
        portfolio: PortfolioState,
        trade_quantity: float,
        trade_value: float,
    ) -> None:
        self.daily_turnover_value += trade_value

        quantity_delta = trade_quantity if proposal.action == TradeAction.BUY else -trade_quantity
        value_delta = trade_value if proposal.action == TradeAction.BUY else -trade_value

        if proposal.action == TradeAction.BUY and self._opens_new_position(proposal.symbol, portfolio):
            self.new_positions_today += 1

        self.cash_delta_from_approved_trades -= value_delta
        self.position_quantity_delta_by_symbol[proposal.symbol] = (
            self.position_quantity_delta_by_symbol.get(proposal.symbol, 0.0) + quantity_delta
        )
        self.position_value_delta_by_symbol[proposal.symbol] = (
            self.position_value_delta_by_symbol.get(proposal.symbol, 0.0) + value_delta
        )
