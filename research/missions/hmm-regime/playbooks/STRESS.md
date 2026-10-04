# Playbook: STRESS (frozen 2026-10-04)

The highest-volatility non-crash state.

- Base strategy: TREND rule, size multiplier 0.25.
- Early cut: when P(next candle is STRESS or CRASH) exceeds 0.25 while in any other state, size halves before the switch happens.
- Max size: RISK cap 0.25.
- Stops as CALM_UP; in this state the 2 x rvol stop is wide in price and the size is small, which is the intended trade-off.
