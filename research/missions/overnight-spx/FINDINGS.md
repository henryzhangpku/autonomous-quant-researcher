# overnight-spx campaign 1 — findings (2026-08-13)

Campaign: `overnight-spx-autoresearch`, evaluator `overnight-spx-causal-v1`,
snapshot `29167dd5…` (482 stitched ES overnight sessions, 2024-09-03 →
2026-08-12). 40 scientific trials, 9 admission failures, local Qwen2.5-Coder-7B
proposer. Ledger: `.research/runs/overnight-spx-autoresearch/state/`.

## Verdict

**No candidate passed the discovery gate set.** Budget exhaustion is a pause,
not a success; validation and holdout remain sealed. Authority of everything
below is `research_only`, and nothing here authorizes an entry policy.

## What the 40 trials actually mapped

**1. The overnight put-credit premium is real, thin, and time-localized.**
Unconditional (control) net mean P&L for OTM put credit verticals, base cost
5% of width per round trip:

| Entry (ET) | Control net mean (fraction of width) |
|---|---|
| 00:00 | +0.004 … +0.014 (consistently positive, 287 sessions) |
| 01:30 | +0.013 |
| 03:15 | +0.002 … +0.010 (positive at offsets 5–25) |
| 06:00 | ≈ 0 to −0.001 |
| 07:15 | −0.002 … −0.006 (consistently negative) |

Geometry matters: width 20–25 with the short strike 10–20 points OTM carries
the premium; width 50 or offset 30 destroy it (the credit does not scale with
width, the modeled cost does). Every other structure family — call credit,
call debit, put debit — is unconditionally negative at every minute tried.

The premium clears base cost but **dies at the doubled-cost survival gate**
(needs ≥ +0.05 width; best observed ≈ +0.014). At modeled overnight SPX
spread costs this is inside the noise floor of execution quality. The binding
constraint is cost, not signal.

**2. The post-drop "edge" is a pricing-model artifact until proven otherwise.**
Every high-net conditioned trial (+0.02 … +0.10 width/trade) used the same
condition — overnight return ≤ −0.4% from prior close — and profited in BOTH
directions (call debit +0.099, put debit +0.049 on overlapping subsets). Both
wings winning means the trailing-20-session variance estimate underprices
options on post-drop nights (vol clusters faster than the window adapts), so
the modeled fills are too cheap exactly there. This is evaluator evidence, not
tradable alpha. It also means conditioned PUT-CREDIT results (selling cheap
modeled premium into drops) are biased AGAINST that structure symmetrically.

**3. The hourly rotation is confirmed dead.** Unconditional debit verticals —
the shape the retired Kalshi-mirror rotation traded — lose approximately their
cost at every entry minute. The demotion shipped in the 0DTE app is the
correct standing state.

## Limitations (carried from the manifest, plus campaign-level)

- ES front-month proxies SPX; basis not modeled.
- Modeled Black-Scholes marks with `vrp_mult = 1.10`; no overnight OPRA/CBOE
  quote tape exists in this snapshot. Credit-structure results lean on the
  VRP assumption; a real-quote capture must confirm before any promotion.
- History begins 2024-09 (provider boundary): one regime, no 2022-style bear.
- Conditions firing on ~25 of 287 sessions cannot reach the 60-trade
  discovery floor in 14 months of data; the floors deliberately force broad
  hypotheses at this sample size.

## Preregistered entry-minute sweep (2026-08-13, same frozen evaluator)

`research/experiments/sweep_overnight_entry.py` — 114 grid points (19 entry
minutes × widths {20,25} × offsets {10,15,20}), unconditional put credit,
evaluated by the untouched validator on the pinned snapshot. Unconditional net
mean per trade (fraction of width, base 5% cost), discovery split:

| Entry ET | Net mean per trade |
|---|---|
| 20:15 (curb open) | **+0.022 … +0.029 — the peak** |
| 21:00 | +0.017 … +0.025 |
| 22:30–01:30 | +0.010 … +0.019 |
| 02:15–03:00 | +0.007 … +0.014 |
| 03:45 | +0.002 … +0.008 |
| 04:30–08:30 | ≈ 0, oscillating slightly negative |
| 09:00–09:15 | −0.005 … −0.012 (worst of the whole window) |

The premium decays monotonically from the evening open of the curb to a
negative morning. The campaign's LLM trials only sampled midnight and later;
the designed grid found the maximum at the window's very start. Best cell:
20:15 ET, width 20, offset 15 → +0.0287 width/trade at 5% modeled cost.

## First real-cost measurement (2026-08-13 22:30 ET, live curb)

The forward-capture harness (`~/.qs-tools/spxw-curb-capture/`, scheduled
nightly at 20:16 / 00:00 / 03:15 ET) measured executable SPXW put-credit
economics for the exact campaign structures (expiry 2026-08-14, SPX forward
7805 by parity pivot, ES basis +21):

| width | offset | exec credit | mid credit | half-spread cost (frac of width) |
|---|---|---|---|---|
| 20 | 10 | 5.00 | 5.20 | **0.0100** |
| 20 | 15 | 4.20 | 4.35 | **0.0075** |
| 20 | 20 | 3.60 | 3.75 | **0.0075** |
| 25 | 10 | 5.90 | 6.05 | **0.0060** |
| 25 | 15 | 4.90 | 5.05 | **0.0060** |
| 25 | 20 | 4.20 | 4.40 | **0.0080** |

**One night, one minute — but the measured cost is 0.6–1.0% of width, not
the modeled 5%.** If nightly captures confirm this level at 20:15/00:00/03:15
ET, the sweep's +2.9%-at-5%-cost peak becomes roughly +6.9% of width per
session at real cost, clearing every gate including doubled-cost. Campaign 2
(evaluator v2: measured costs, EWMA vol) is justified the moment a week of
captures agrees.

## Campaign 2 — PROMOTED (2026-08-13, evaluator v2)

Preregistered grid promotion (`sweep_overnight_entry_v2.py`): 228 cells,
economics declared before any result — cost 0.015 of width (2x stress 0.03),
vrp 1.0, EWMA vol 0.85, same pinned snapshot. 9 discovery gate-passers; the
declared top-3 shortlist went to validation (2025-12-01..2026-06-30) and
**all three passed every gate, stronger out of sample**:

| Cell (all: entry 20:15 ET, settle 16:00 ET) | Disc net/w | Val net/w | Val wk Sharpe | Val bootstrap LB |
|---|---|---|---|---|
| put credit w20 o15 (leader) | +0.0441 | +0.0674 | 2.798 | +0.0206 |
| put credit w20 o20 | +0.0398 | +0.0657 | 2.796 | +0.0213 |
| put credit w25 o10 | +0.0421 | +0.0653 | 2.686 | +0.0183 |

Morning-exit variants underperformed settlement everywhere; the premium is
earned across the whole hold, not just overnight. Finding authority remains
`research_only`. Deployment preconditions, in order: (1) nightly captures
confirm the 20:15 ET half-spread <= 0.015 of width and real credit levels at
or above the vrp-1.0 model; (2) the sealed 2026-07+ holdout is spent ONCE on
the leader cell; (3) paper deployment in the 0DTE app with nightly
research-vs-paper reconciliation. Regime caveat stands: 2024-09+ only, no
bear-market year in evidence.

## The structure matrix, completed (2026-08-14, evaluator v2 economics)

Owner directive: test call and put, credit/debit/single leg. Under the
measured costs (1.5% of width / 6% of premium, vrp 1.0), the full space:

| | Credit vertical | Debit vertical | Long single |
|---|---|---|---|
| **Put side** | **PROMOTED — the only survivor** | 0 passers (morning 07:15-08:15 near-miss: +2.0-2.7%/w, fails significance) | deep-OTM "+200%" flagged as flat-vol SKEW ARTIFACT, do not trade |
| **Call side** | 0 of 228 — selling upside fights the drift everywhere | 0 passers | dead at every entry and strike |

Naked short singles are excluded permanently by mandate (undefined risk).
One survivor in ~800 gated evaluations across the whole space: the
promoted put credit is not a lucky cell, it is the only structure the
overnight session pays. The last remaining axis, the hold-through 1DTE
horizon, was swept and closed the same night — see the section below.

## The 1DTE hold-through horizon — REJECTED as drift (2026-08-14)

`sweep_overnight_1dte.py`, 228 preregistered cells on the `next_session`
horizon (enter tonight, settle at TOMORROW's 16:00 ET), same frozen v2
evaluator and economics. 26 discovery gate-passers; the declared top-3 went
to validation and one passed every gate:

| Cell (entry 20:15 ET, settle next 16:00 ET) | Disc net/w | Val net/w | Val wk Sharpe | Val bootstrap LB |
|---|---|---|---|---|
| **call debit w20 o0 (ATM)** | +0.0737 | +0.0762 | 2.268 | +0.0058 |
| put credit w20 o0 (ATM) | +0.0752 | +0.0612 | 1.972 | −0.0056 (rejected) |
| put credit w25 o0 (ATM) | +0.0716 | +0.0581 | 1.884 | −0.0087 (rejected) |

**It was not promoted.** Two features of the shape are the tell: both
surviving families are LONG delta, and both peaked at offset 0 — the
maximum-delta cell — while the payoff grew when the hold was extended by a
session. On a snapshot that starts 2024-09 and holds no bear market, that is
what being invested looks like.

`attribute_overnight_1dte_drift.py` put it to a preregistered control: regress
each trade's net on the underlying's move over the identical hold, then apply
the frozen weekly-bootstrap gate to the drift-neutral residual. The script
first reproduces the validator's own per-trade economics and aborts unless it
matches to 1e-9 (it matched exactly, 0.00e+00, on every cell below).

| Cell | Split | Mean net/w | = drift | + alpha | Drift share | Alpha bootstrap LB |
|---|---|---|---|---|---|---|
| 1DTE ATM call debit | discovery | +0.0737 | +0.0234 | +0.0503 | 32% | +0.0081 ✓ |
| 1DTE ATM call debit | validation | +0.0762 | +0.0429 | +0.0333 | **56%** | **−0.00016 ✗** |
| **promoted put credit w20 o15** | discovery | +0.0441 | +0.0085 | +0.0356 | 19% | +0.0054 ✓ |
| **promoted put credit w20 o15** | validation | +0.0674 | +0.0243 | +0.0431 | 36% | +0.0118 ✓ |

The 1DTE cell fails its own control out of sample: past the halfway point the
majority of its payoff is direction, and what is left is not distinguishable
from zero. **The 1DTE horizon is closed. Do not promote it.**

The same control, run on the ALREADY-PROMOTED overnight put credit, passes on
both splits — the cell keeps its approval, now having survived a test it was
never originally asked to pass. Its drift load is roughly half the ATM cells'
at every split, which is what the 15-point OTM offset is buying.

Two honest caveats on the method:

- The mirror cells (same strikes, each delta sign) are a **consistency check,
  not evidence**: a vertical and its credit counterpart are exact complements,
  so their nets sum to precisely −2 × cost_frac by construction (measured:
  −0.03 on every pair). It confirms the pricing is coherent and nothing more.
- The residual was checked against the cheaper explanation before being called
  alpha. `check_1dte_vol_model.py` compared the v2 two-day variance extension,
  `sqrt(sigma_entry² + sigma_135²)`, against realized dispersion over the same
  holds: 0.877× on discovery, 1.022× on validation. The extension does not
  systematically overstate two-day variance, so the residual is not a
  volatility-model artifact. It is simply too small and too noisy to promote.
- The regression uses a full-sample beta, which is lookahead. That is
  deliberate: lookahead in the control biases the test TOWARD finding alpha,
  so the 1DTE rejection is conservative, and the promoted cell's pass would
  still need a causal implementation before it were used to size anything.

## THE HOLDOUT IS SPENT — the promotion is WITHDRAWN (2026-08-14)

Owner asked for a yes/no on trading this. The remaining research question was
precondition (2), the sealed 2026-07+ holdout, so it was spent once on the
leader cell — `put credit w20 o15`, entry 20:15 ET, settle 16:00 ET.

| Split | Trades | Weeks | Net/w | Wk Sharpe | Bootstrap LB | Status |
|---|---|---|---|---|---|---|
| discovery | 288 | 61 | +0.0441 | 1.76 | +0.0075 | passed |
| validation | 144 | 31 | +0.0674 | 2.80 | +0.0206 | passed |
| **holdout** | **30** | **7** | **+0.0015** | 1.40 | **−0.0739** | **REJECTED** |

Gates failed on the holdout: `minimum_weeks`, `survives_without_best_week`,
`weekly_bootstrap_positive`. The drift control run on the same split:
payoff +0.0015 = drift +0.0022 + alpha **−0.0007**, so the entire (tiny)
result is direction and the drift-neutral residual is negative.

Read honestly, in both directions:

- The effect size did not shrink, it **vanished** — +4.4% and +6.7% of width
  per session became +0.15%. Removing the single best week turns it negative.
  That is not a marginal miss.
- But the window is **7 weeks and 30 trades**, short of the 8-week floor. This
  is an UNDERPOWERED rejection, not proof the premium is absent. The honest
  statement is "the holdout did not confirm it", not "the edge is disproven".
- Either way it is **not a green light**, and the holdout does not reset. Any
  future promotion of this structure needs a NEW sealed window — which means
  waiting for calendar time — plus the live-cost captures it still lacks.

The registry artifact `overnight-spx-put-credit-v1` in the downstream signal registry now carries
this receipt, keeps `authority: research_only`, and is marked `withdrawn_at`.

A gate hole was found and closed while recording it: the registry required a
holdout receipt to exist before authority could exceed `research_only`, but
never checked that it PASSED — so writing down this failure would have been
the act that unlocked paper and live. It now requires `status: passed`.

**Standing state: the lab has no promoted policy and nothing to trade.**

## Recommended follow-ups (each is a new frozen contract, not a tweak)

1. **Real-cost capture (highest value).** Forward-capture actual SPXW vertical
   quotes during the CBOE overnight curb (tasty DXLink) at 00:00/03:15 ET for
   widths 20–25, offsets 10–20. Replace the 5%-of-width assumption with
   measured half-spreads. If real cost ≈ 2–3% of width, the put-credit
   premium clears base cost with margin; if ≈ 5%+, the answer is "no trade"
   and the demotion stands permanently.
2. **Evaluator v2 with a faster vol estimator** (EWMA on 5-minute returns).
   If the post-drop both-wings anomaly evaporates, it was the artifact; if it
   survives honest pricing, it becomes the most interesting hypothesis in the
   map.
3. **Designed entry-minute sweep** as a preregistered grid (not LLM-proposed):
   put credit w20–25, offsets 10–20, every 45 minutes from 20:15 to 09:15,
   with the weekly-bootstrap gate — to pin the edge's time boundary precisely.

Backtests are hypothetical, research-only evidence and are not investment
advice.
