# Playbook: CALM_UP (frozen 2026-10-04, before the backtest)

State statistics that earn the name: the highest mean return among the low-volatility states.

- Base strategy: TREND. Long when the standardized trend feature (EMA20 minus EMA100, in units of realized vol, z-scored on the past) is above 0; flat otherwise.
- Size multiplier: 1.00 of the sizing layer's cap (quarter-Kelly x P(state) x (1 - entropy)).
- Entry: trend > 0 and no stop-out pending.
- Exit: trend <= 0, or the state leaves CALM_UP for CRASH (flat), or a stop.
- Stop: 2 x realized vol below entry, checked on the close.
- Take profit: none (trend).
- Max size: the RISK block's CALM_UP cap, 1.0 x allotted capital.
- Invalidation: filtered P(CALM_UP) under 0.70 for 3 candles switches the state; the playbook of the new state takes over at the next candle.
