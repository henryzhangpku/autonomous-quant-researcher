# btc-sessions campaign 1 + replication — findings (2026-08-13)

BTC/USD spot, Alpaca hourly tape (44,085 bars, 2021-08 → 2026-08), cut into
the windows a trader can actually take. Snapshots and sweeps:
`research/experiments/prepare_crypto_sessions.py`,
`sweep_btc_weekend.py`, `replicate_btc_drop_filter.py`.
Ledgers: `.research/sweeps/btc-weekend-v1/`, `.research/sweeps/btc-drop-filter-replication/`.

Windows (ET): weekend Fri 16:00 → Mon 09:30 (242 of them), overnight
16:00 → 09:30 Mon–Thu (1,029). Decision at the window's open; every feature
computed from bars at or before that instant; payoff is the window's own
return. Costs 10 bps round trip, doubled-cost stress 20 bps.

## Verdict

**No promotion.** The weekend campaign produced one cell that passed
discovery and stayed positive out of sample; a preregistered replication on
weeknight windows **refuted its mechanism outright**. The holdout of neither
snapshot was opened.

## 1. There is no unconditional weekend drift

Run before any conditional cell, precisely so no conditional result could be
mistaken for the drift underneath it:

| Unconditional long, weekend | discovery | validation |
|---|---:|---:|
| bps per window | +10.24 | **−5.75** |
| Sharpe | 0.42 | −0.31 |

Positive in discovery, negative out of sample. All four discovery
gate-passers in the weekend grid were long, i.e. that drift wearing a
condition — the same trap bars campaign 1 fell into, caught immediately here
because the baseline ran first.

## 2. The weekend drop-filter, and why it looked real

`ret_24h > -0.01 → long` (take the weekend UNLESS BTC fell more than 1% in
the prior 24 hours):

| | discovery | validation |
|---|---:|---:|
| windows | 93 | 34 |
| bps per window | **+46.68** | **+20.47** |
| Sharpe | **2.24** | 1.03 |
| status | **passed every gate** | rejected |

Positive in both splits against a baseline that lost money out of sample, so
the condition — not the drift — carried it. Rejected on
`positive_both_halves` and `survives_without_best_day`: with 34 validation
weekends a single window carries the mean. The mirror cell agreed with the
mechanism, returning **−54.93 bps**: buying into a weekend after a sharp drop
was the worst thing in the grid.

The correct response to a thin sample is more data, not a softer gate.

## 3. The replication refutes it

Same rule, stated in advance and unchanged, on 1,029 weeknight overnight
windows — four times the sample, data the rule had never seen:

| Cell | Overnight discovery | Overnight validation |
|---|---:|---:|
| `ret_24h > -0.01 → long` (the rule) | +0.95 bps, Sharpe 0.06 | +2.33 bps, Sharpe 0.20 |
| `ret_24h < -0.01 → long` (the mirror) | +8.52 bps, Sharpe 0.60 | +52.59 bps, Sharpe 3.96 |
| unconditional long | +3.31 bps, Sharpe 0.22 | +17.45 bps, Sharpe 1.45 |

**The sign flips.** On weekends, buying after a sharp 24h decline was the
worst cell in the grid; on weeknights it is the best. The proposed mechanism —
that a sharp decline predicts continued weakness across a non-equity-hours
window — does not survive contact with the larger sample. It is refuted, and
the weekend result should be treated as a 34-window coincidence until some
other evidence revives it.

## 4. An observation that is NOT a finding

The overnight mirror passed every gate in validation (62 windows, +52.59 bps,
Sharpe 3.96) while **failing discovery** on `survives_double_cost`. Passing
validation after failing discovery is backwards: the shortlist is chosen on
discovery, so this cell was never eligible for promotion and is not promoted.
The discovery/validation gap (+8.5 → +52.6) is itself a warning — the
validation year (2024-08 → 2025-08) contained large drawdowns and rebounds,
and a buy-the-dip rule is exactly what a rebound year flatters.

It is recorded because it is worth ONE preregistered campaign of its own, not
because it is evidence now.

## Limitations

- Costs are assumed 10 bps round trip, not measured. BTC/USD spot at this
  venue quotes inside that, but no execution capture backs the number.
- Hourly bars price the 09:30 boundary from the most recent bar at or before
  it. That is causal, but it is not a 09:30 print.
- One crypto regime: 2021-12 onward, with no pre-2021 history from this
  provider.
- 242 weekend windows is small and always will be — the window occurs once a
  week. Weekend-specific claims cannot be rescued by waiting.

## Follow-ups

1. **Preregister the overnight buy-the-dip cell properly**, with its own
   frozen contract, a threshold declared in advance, and a discovery split
   that it must pass first. If it cannot pass discovery it does not exist.
2. **Cross-asset weekend test.** BTC prices continuously while IBIT does not,
   so the Monday IBIT open is a scheduled repricing of a known weekend move.
   That is a mechanically-grounded question this campaign never asked, and it
   sits directly on an instrument already traded.
3. **Do not re-run the weekend grid.** 242 windows is the entire population;
   more cells on it buy multiplicity, not information.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# btc-monday-reprice campaign 1 — findings (2026-08-13)

Does the session following a weekend do anything with a move that is already
public? BTC/USD Monday 09:30–16:00 ET, 243 Mondays 2021-12-20 → 2026-08-10,
conditioned on `weekend_ret` (Fri 16:00 → Mon 09:30), complete and observable
at the decision instant. 10 bps round trip.
Sweep: `research/experiments/sweep_btc_monday_reprice.py`.

## Verdict

**46 cells, 3 discovery gate-passers, 0 promotions.** Holdout untouched.

| Unconditional long, Monday RTH | discovery | validation |
|---|---:|---:|
| bps per session | +10.32 | **−5.72** |
| Sharpe | 0.76 | −0.47 |

The same shape as the weekend window: a discovery-period drift that reverses
out of sample, and all three discovery passers were long — that drift wearing
a threshold. Validation killed them:

| Cell | Val n | Val bps | Val Sharpe |
|---|---:|---:|---:|
| `weekend_ret > +0.01 → long` | 20 | −17.48 | −1.20 |
| `weekend_abs > 0.03 → long` | 14 | +27.14 | 1.46 (below trade floor) |
| `weekend_abs > 0.02 → long` | 24 | +7.34 | 0.46 |

**The weekend move carries no information for the Monday session**, in either
direction, at any threshold tested. Continuation and fade were tested
symmetrically and neither survives.

---

# Standing verdict across my campaigns (2026-08-13)

Six campaigns, zero promotions: three on daily-bar equity rules, three on BTC
session windows. Every one asked the same kind of question — **predict the
direction of a liquid spot instrument** — and every one produced the same
answer.

The single promotion in this repo, the peer's 20:15 ET SPX put credit, is not
directional. It **captures a structural premium**, and it only cleared its
gates once cost was measured rather than assumed. Two of my campaigns died at
`survives_double_cost` against a number nobody had measured.

The conclusion is about strategy, not execution: directional prediction on
liquid instruments does not clear an honest gate set, and no further grid over
spot features is worth running. Premium capture, with measured costs, is where
the evidence points.

**Next line: RTH 0DTE/1DTE option structures on the names actually traded
(SPY, QQQ, IWM, Mag 7, AVGO, IBIT).** Alpaca serves historical option bars —
verified with real prints — so unlike the overnight-spx program this can be
evaluated on ACTUAL traded option prices rather than modeled marks, removing
the assumption that program's promotion still rests on.

Backtests are hypothetical, research-only evidence and are not investment
advice.
