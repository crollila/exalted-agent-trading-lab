from datetime import datetime, timezone

from src.portfolio.portfolio_state import PortfolioState
from src.strategies.cash_only import CashOnlyStrategy


def test_cash_only_produces_zero_proposals():
    strategy = CashOnlyStrategy()

    proposals = strategy.generate_proposals(
        PortfolioState(
            equity=10000,
            cash=10000,
            positions={},
            timestamp=datetime.now(timezone.utc),
        )
    )

    assert proposals == []
