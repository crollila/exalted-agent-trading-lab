from __future__ import annotations

from pathlib import Path

from src.brokers.order_models import OrderRequest, RiskDecision, TradeProposal
from src.db.database import insert_order, insert_risk_decision, insert_trade_proposal


class OrderExecutor:
    def __init__(self, database_path: Path | str, dry_run: bool = True):
        self.database_path = Path(database_path)
        self.dry_run = dry_run

    def handle_decision(self, proposal: TradeProposal, decision: RiskDecision) -> None:
        insert_trade_proposal(self.database_path, proposal)
        insert_risk_decision(self.database_path, decision)

        if not decision.approved:
            return

        order = self._proposal_to_order(proposal, decision)
        submitted = False

        if self.dry_run:
            submitted = False
        else:
            # TODO Phase 2: submit to Alpaca paper through AlpacaClientWrapper.
            # Live-money trading must remain disabled.
            submitted = False

        insert_order(self.database_path, order, submitted=submitted)

    def _proposal_to_order(self, proposal: TradeProposal, decision: RiskDecision) -> OrderRequest:
        if decision.approved_quantity is None or decision.approved_quantity <= 0:
            raise ValueError("Approved risk decision must include a positive approved quantity.")

        return OrderRequest(
            proposal_id=proposal.proposal_id,
            symbol=proposal.symbol,
            action=proposal.action,
            asset_class=proposal.asset_class,
            quantity=decision.approved_quantity,
            dry_run=self.dry_run,
            risk_approved=True,
        )
