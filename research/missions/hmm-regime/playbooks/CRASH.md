# Playbook: CRASH (frozen 2026-10-04)

Lowest mean return with above-median volatility.

- Flat. Size multiplier 0.00; any held position is closed on the candle the state is confirmed.
- Re-entry only after the state has switched away and the TREND rule fires again.
- Max size: RISK cap 0.0, overriding every other instruction.
