# Loop constraints

- One NLP mission, one immutable validator policy, one mutable candidate.
- Alpaca historical data and Alpaca reference simulation semantics are primary.
- Polygon/Massive is secondary for futures and crypto; no silent fallback.
- Strict chronological access; no lookahead or point-in-time universe drift.
- Raw prices define option strikes; stale quotes are rejected.
- Costs, slippage, fills, and holdout data cannot be changed by candidates.
- All provider, broker, and LLM credentials are removed from child execution.
- Every attempt is appended; no deletion or best-result-only reporting.
- No orders, accounts, live signals, deployments, or production promotion.
