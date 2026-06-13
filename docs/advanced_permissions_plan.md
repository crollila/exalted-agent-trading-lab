# Advanced Permissions Plan

Phase 6S is a documentation and architecture design phase only. It does not enable shorting, margin, options, live trading, Hermes runtime execution, or any new broker behavior.

Current behavior remains:

- Dry-run first.
- Stock-only.
- Long-only.
- Cash-only.
- No live trading.
- No options.
- No margin.
- No shorting.
- No LLM direct execution.
- Alpaca paper broker calls remain limited to the existing stock-only wrapper.

## Staged Future Permission Phases

Advanced permissions must be staged so the project can model, simulate, test, and review each new risk surface before any broker-paper behavior exists.

Recommended future phases:

1. Paper shorting design.
2. Paper shorting dry-run simulation.
3. Paper margin design.
4. Paper margin dry-run simulation.
5. Paper options design.
6. Paper options dry-run simulation.
7. Broker-paper implementation only after simulator and risk tests pass.
8. Live trading remains out of scope until long-term validation.

Each phase should be reviewed independently. Approval for one advanced feature must not imply approval for another.

## Required Safety Gates

Before any future behavior changes, the following must be true:

- `docs/risk_policy.md` must be updated.
- `STATUS.md` must be updated.
- `BUILD_PLAN.md` must be updated.
- Tests must be added before behavior changes are accepted.
- Default behavior must remain stock-only, long-only, and dry-run first.
- Broker calls must remain disabled until an explicit later broker-paper phase.
- All advanced proposals must still pass deterministic risk validation.
- LLM/Hermes may only create proposals and must never create, approve, resize, or submit orders.
- Missing config must fail closed.
- Unknown permission levels must fail closed.
- Advanced permissions must be explicit, not inferred from account buying power or broker capabilities.

## Paper Shorting Design

Paper shorting should begin as a design-only phase, followed by dry-run simulation. It must not reach paper broker execution until simulator and risk tests prove the intended behavior.

Future required controls:

- Explicit strategy permission flag for short-capable strategies.
- Explicit CLI or user permission flag for short-enabled research runs.
- Max short exposure.
- Max gross exposure.
- Max net exposure.
- Max loss per short position.
- Forced-cover rule when short loss limits or exposure limits are breached.
- Borrow availability assumption logging.
- Hard ban on shorting without the specific permission level.
- No live shorting.

Future proposal or risk metadata may need:

- Short thesis.
- Borrow availability assumption.
- Borrow fee assumption.
- Expected cover condition.
- Stop or invalidation condition.
- Max loss estimate.

Shorting must never be enabled by interpreting a sell order as permission to open a short. Opening, increasing, reducing, and covering short exposure must be modeled explicitly before implementation.

## Paper Shorting Dry-Run Simulation

The first implementation after design should be simulator-only.

Expected simulation requirements:

- Synthetic borrow availability assumptions.
- Simulated short entry and forced-cover events.
- Mark-to-market short P/L.
- Gross exposure and net exposure reporting.
- Rejection tests proving short proposals fail when permission is absent.
- No broker calls.
- No live market data requirement.

## Paper Margin Design

Margin should be designed separately from shorting and options. Margin must never be silently implied by buying power.

Future required controls:

- Explicit permission level.
- Max gross exposure.
- Max net exposure.
- Max daily loss.
- Margin call simulation.
- Forced deleveraging rules.
- No live margin.
- Margin must never be silently implied by buying power.

Future portfolio or risk metadata may need:

- Borrowed amount.
- Equity buffer.
- Maintenance requirement assumption.
- Margin call threshold.
- Deleveraging priority.
- Gross and net exposure by asset class.

Margin should start as cash-account simulation plus explicit borrowed-capital accounting. Broker-paper margin behavior should not exist until the simulator, risk limits, and reports are proven.

## Paper Margin Dry-Run Simulation

The first implementation after design should be simulator-only.

Expected simulation requirements:

- Gross exposure calculations.
- Net exposure calculations.
- Borrowed amount calculations.
- Daily loss tracking.
- Margin call simulation.
- Forced deleveraging event logs.
- Rejection tests proving margin use fails when permission is absent.
- No broker calls.
- No live market data requirement.

## Paper Options Design

Options should begin as design-only, then dry-run simulation. Paper options broker behavior should only be considered after contract models, risk rules, simulations, and reports are complete.

Future required controls:

- Explicit option contract model.
- Underlying symbol.
- Call or put.
- Expiration.
- Strike.
- Quantity/contracts.
- Premium.
- Max premium at risk.
- Max contracts.
- Greeks fields if available.
- Liquidity/open-interest assumptions.
- Assignment/exercise risk notes.
- No 0DTE at first.
- No naked short options at first.
- No live options.

Future option proposal fields may need:

- Position effect, such as buy to open or sell to close.
- Contract multiplier.
- Estimated bid/ask spread.
- Implied volatility assumption.
- Delta, gamma, theta, vega, and rho when available.
- Expiration risk explanation.
- Assignment or exercise risk explanation.

Options must not be represented as stock proposals. The contract model must make the underlying, strike, expiration, premium, and contract count explicit.

## Paper Options Dry-Run Simulation

The first implementation after design should be simulator-only.

Expected simulation requirements:

- Option contract fixture data.
- Premium-at-risk accounting.
- Contract count limits.
- Expiration handling.
- Assignment/exercise risk logging.
- Liquidity and open-interest assumption logging.
- Rejection tests proving option proposals fail when permission is absent.
- No broker calls.
- No live market data requirement.

## Broker-Paper Implementation Gate

Broker-paper implementation for shorting, margin, or options should only happen after:

- The design phase is complete.
- Dry-run simulation exists.
- Deterministic risk tests exist.
- Rejection tests prove the feature is disabled by default.
- Reporting identifies the permission profile used.
- `docs/risk_policy.md`, `STATUS.md`, `BUILD_PLAN.md`, and `README.md` are updated.
- The user explicitly approves a broker-paper implementation phase.

Broker-paper implementation must remain paper-only. It must not create live-trading behavior.

## Live Trading Boundary

Live trading remains out of scope until long-term validation. Future live consideration would require:

- Long paper-trading validation.
- Walk-forward testing.
- Slippage and liquidity modeling.
- Complete audit logs.
- Risk review.
- Explicit user approval.
- Tiny live stock-only mode before any advanced live tools.

No live shorting, live margin, or live options should be considered until a much later explicit phase.

## LLM and Hermes Boundary

LLM/Hermes may only create structured proposals in future phases. They must never:

- Place orders.
- Approve proposals.
- Override the risk engine.
- Resize trades.
- Submit broker requests.
- Change permission levels.

All advanced proposals must still pass deterministic risk validation before any simulated or paper action.

## Non-Goals For Phase 6S

Phase 6S does not:

- Implement shorting.
- Implement margin.
- Implement options.
- Change `TradeProposal`.
- Change risk validation behavior.
- Change execution behavior.
- Change broker/order submission behavior.
- Add Alpaca shorting, margin, or options calls.
- Wire Hermes runtime.
- Create any path where a bot can place options, short, margin, or live trades.
