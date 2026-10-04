"""HMM regime trading system (mission research/missions/hmm-regime/SPEC.md).

Three layers that never overlap: the research agent designs and reviews
(nightly, off the critical path); the Gaussian HMM reads the market state
from PAST candles only and emits probabilities; deterministic code owns every
threshold, size, veto and order. Nothing here places an order: `backtest`
and, later, `live` are the only callers of the trader, and both read the
same pure functions.
"""
