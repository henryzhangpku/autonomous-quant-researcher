"""Reference HMM regime research and its illustrative risk wrapper.

Three layers that never overlap: the research agent designs and reviews
(off the critical path); the Gaussian HMM reads the market state from PAST
candles only and emits probabilities; deterministic code owns every
threshold, size, veto and order. Nothing here places an order: `backtest`
is the only caller of the trader, and it reads the same pure functions.
"""
