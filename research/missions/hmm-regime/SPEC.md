# HMM regime trading system: spec, architecture, plan (for approval before code)

Source: the "HMM regime trading system" prompt Henry pasted 2026-10-04, with
its placeholders filled below. Decision taken with Henry the same night: build
it inside this repository's loop, not a third-party harness; BTC/USD hourly
first, SPY daily as the replication. This document is the three approval
gates the prompt asks for (spec, architecture, plan) in one pass; nothing
below is code yet.

## 1. Spec

**Goal.** A regime-conditioned trading system: a Hidden Markov Model reads
the market state from past candles only, one deterministic playbook per
hidden state decides entries/exits/size, hard limits in code veto everything,
and the whole thing is accepted only if it clears pre-written gates on a
walk-forward backtest with costs.

**Placeholders, filled.**

| Placeholder | Value | Why |
|---|---|---|
| Exchange / data | Alpaca (paper account for trading; bars from the research data lake) | keys already held; the lake is append-only and timestamp-disciplined |
| Asset, timeframe (run 1) | BTC/USD, 1-hour candles | 24/7, ~5 years in the lake once the 2021 backfill lands; fast feedback |
| Asset, timeframe (run 2) | SPY, 1-day candles | the classic regime case; 10 years in the lake |
| Trend feature | 20-period vs 100-period EMA spread, in units of 20-period realized vol | one scale-free trend measure; standardized with past data only |
| Fit window | >= 3 years of candles; expanding window at each refit | prompt minimum |
| State count | 2 to 5 by BIC and out-of-sample log-likelihood; simpler wins when scores are within 2% | prompt rule |
| Switch threshold | filtered probability > 0.70 | prompt default |
| Hold for | 3 consecutive candles | prompt default |
| Cooldown after a switch | 6 candles (BTC hourly) / 3 candles (SPY daily) | one quarter-day / three sessions |
| Early cut | P(next = STRESS or CRASH) > 0.25 -> size to half | prompt default |
| Uncertain | top two state probabilities within 0.15 -> size zero | prompt default |
| Refit cadence | every 30 days, expanding window | prompt default |
| Drift tolerance | any state mean return or vol shifting > 1.0 pooled sigma, or transition-row L1 change > 0.30, or live log-likelihood below the 5th percentile of the fit window | freezes new entries + alert |
| Backtest span | >= 2 years out of sample across several regimes | prompt minimum; BTC covers 2022 bear, 2023-24 recovery, 2025-26 |
| Costs | BTC: 10 bps per side + 5 bps slippage; SPY: 1 bp per side + 2 bps slippage | conservative retail |
| Gates (verbatim) | out of sample: Sharpe > 1.5, max drawdown < 15%, hit rate > 55%, t-stat > 2.0, beats buy-and-hold AND the best single static strategy after costs | prompt |
| Sizing | quarter-Kelly cap x filtered probability of the active state x (1 - normalized posterior entropy); zero in CRASH and when uncertain | prompt; Kelly from the playbook's own in-sample edge, capped |
| Calibration before sizing | Brier score and reliability curve per state on realized next-candle outcomes; sizing stays at the minimum until ECE < 0.05 | prompt |
| Risk rules (code, no override) | max position 1x notional of allotted capital; daily loss limit 2%; max drawdown 10% (kill); state leverage caps CALM 1.0 / CHOP 0.5 / STRESS 0.25 / CRASH 0; stale data > 2 candles -> flat; kill switch flattens and halts | prompt + house limits |
| Manual approval above | $5,000 notional per order | Henry's call; adjustable in config, never in a model |
| Deploy target | Mac Mini (24/7, restart via launchd) with Telegram alerts; this PC as the dev box | existing always-on host |

**What the system is NOT.** It is not a prediction of price. The HMM is a
state estimator; the only claim tested is that conditioning a simple playbook
on the filtered state beats the unconditioned playbook and buy-and-hold after
costs, out of sample.

## 2. Architecture

Three layers, never overlapping (the prompt's rule), mapped onto this repo:

```
Layer 1  nightly reviewer (the research agent, Opus/Claude)
         designs features, fits + audits the HMM, writes playbooks/<STATE>.md,
         reads every fill and wrong state call, proposes changes.
         NEVER grades its own output: the gates and the loop's ledger do.

Layer 2  the HMM (hmmlearn GaussianHMM), run on every candle
         input : standardized feature vector (past data only)
         output: filtered P(state_t | candles <= t), P(state_{t+1}) via the
                 transition matrix, entropy, log-likelihood of the window.
         Viterbi and smoothing are forbidden in decisions (test enforces).

Layer 3  deterministic code (the trader)
         switching rules (threshold, hold, cooldown, early cut, uncertain, stale)
         playbook execution (entry/exit/stop/TP per state, as pure functions)
         sizing (quarter-Kelly x probability x (1 - entropy); calibration gate)
         risk engine (hard limits, kill switch) -> order ticket
         ledger (every decision with the snapshot that produced it)
```

Modules (new, under `research/hmm/`):

| Module | Owns | First failing test |
|---|---|---|
| `features.py` | log return, realized vol, range, volume ratio, EMA-spread trend; rolling standardization with past data only | a feature at t changes when candle t+1 is altered -> fail |
| `model.py` | fit, BIC/OOS selection, seed + best-of-N restarts, state auto-labelling by (mean, vol), transition matrix, expected durations | labels stable under a refit on the same data; durations = 1/(1-p_ii) |
| `filter.py` | forward filtering only; `P(next)` from the transition matrix | filtered probs at t equal those computed on a truncated series ending at t |
| `switching.py` | threshold / hold / cooldown / early cut / uncertain / stale -> active playbook, with reasons | the exact hysteresis cases from the spec, including no flip-flop |
| `playbooks.py` + `playbooks/*.md` | per-state rules as pure functions; CALM_UP trend-following, CHOP mean reversion, STRESS half size or stand aside, CRASH flat | each rule's entry/exit/stop/TP/invalidation on a fixture |
| `sizing.py` | Kelly cap, probability and entropy scaling, calibration gate (Brier, reliability, ECE) | size is zero in CRASH, zero when uncertain, never above the cap |
| `risk.py` | hard limits and kill switch; checked before every order | every limit trips on a constructed breach; the kill flattens |
| `backtest.py` | walk-forward refits every 30 days, expanding window, costs, baselines (buy-and-hold, best static playbook), gate evaluation | a known synthetic tape reproduces its known P&L; gates reject a losing tape |
| `drift.py` | refit comparison, label matching, live log-likelihood monitor | an injected shift freezes entries and emits the alert |
| `dashboard.py` | state probabilities, current state, expected duration, active playbook, last action, result | renders from the ledger alone |
| `live.py` (phase 2) | Alpaca paper loop, Telegram alerts, daily report | paper fills reconcile to the ledger |

Data in: the research data lake (`qs_research_web/datalake`, read through
`research/providers/qs_datalake.py`), BTC/USD hourly and SPY daily; the
backfill to 2021 / 2015 is running. Keys: `.env` only, never in code or logs.

## 3. Plan (test-first, each phase ends with a review against this spec)

1. **Data + features** (BTC hourly, SPY daily): feature pipeline with the
   no-lookahead test and the standardization test. Review: feature list vs
   spec, timestamp audit.
2. **Model + filter**: fit on the first 3 years, select states, label them,
   print the transition matrix and durations; forward filter the rest; the
   truncation test proves no future candle leaks. Review: do the states mean
   anything (CALM_UP / CHOP / STRESS / CRASH by their statistics)?
3. **Playbooks + switching + sizing + risk**: four playbooks written as
   files, their rules as pure functions, the hysteresis state machine, sizing
   with the calibration gate, hard limits. Review: every threshold lives in
   code or config, none in a model.
4. **Walk-forward backtest with costs**: 30-day expanding refits, two
   baselines, the five gates. Output: `RESULTS.md` per state and for the
   system; `strategy.md` only if the gates pass. If they fail, the mission
   records the rejection and stops here.
5. **Calibration + drift + dashboard**: Brier/reliability per state on the
   backtest, drift monitor, dashboard from the ledger.
6. **Paper trading** (only after 4 passes): Alpaca paper, Mac Mini, Telegram
   alerts, daily report; paper runs until its results match the backtest.
7. **Final check** before any live money: the prompt's questions answered in
   writing, ending with "WHAT COULD BLOW UP THIS ACCOUNT?"; live only when
   every answer is clean and Henry approves above the manual-approval size.

Rollback at every phase: each module is one commit; `git revert` of the
phase commit restores the previous state; the ledger is append-only.

## 4. Scope beyond BTC and SPY: how this becomes FST's regime layer

Henry (2026-10-04): "does this sound like new architecture of our FST? ...
more than just BTC or SPX." Mapping, honestly:

| Prompt layer | FST today | What the mission adds |
|---|---|---|
| Layer 3, code decides (thresholds, sizing, vetoes, orders) | the runner (fst-autonomy), guardrails, caps, stop-loss, device-bound AUTO, per-account state | nothing new; stays authoritative |
| Layer 1, nightly reviewer | the research agent + fst-autoresearch shadow backtests + the support loop | a ledger-driven review of wrong STATE calls, not just wrong trades |
| Layer 2, a market-state model | GEX regime (SPY gamma walls), entry-policy dial, the LLM brain's one-sided veto | an explicit, per-instrument AND market-wide regime estimate with calibrated probabilities, refit on a schedule, drift-monitored |
| playbook per state | the signal families (Katy, flow strategist, news-spread, 0DTE/credit rules) | families become playbooks CONDITIONED on state: which family is live, at what size, in which regime |

So yes, it is a generalization of FST, not a replacement: the regime layer
is a new signal family (`regime`) that the runner reads like it reads GEX
today, and that may only ADD caution (reduce size, veto entries, flatten in
CRASH) until its calibration record says it can do more. Three runs, in
order, each gated:

1. **BTC/USD hourly** (this spec): single-asset proof of the pipeline and
   the no-lookahead discipline.
2. **SPY daily**: the market-wide regime every equity playbook will read.
3. **Universe daily**: one HMM per name for the FST board (top 1,000 by
   dollar volume from the lake, 5+ years adjusted bars), plus the market
   regime as a shared feature; evaluated cross-sectionally the way the
   off-exchange study was (does conditioning on state improve the families
   after costs?), served as `GET /api/v1/signals/regime/{ticker}` from
   qs-research where the Python model lives, consumed by the Deno runner as
   a size/veto input. Kalshi series get the BTC and SPY regimes directly.

What does not change: no model ever owns a hard limit; every threshold is
code; a regime read that is stale or drifting freezes entries rather than
guessing; parity by construction (server-side logic, every surface reads the
same state).

## 5. Jev in the loop: the FST agent upgrade, specified

Henry (2026-10-04): "i have planned to upgrade FST agent to Jev for a long
time, since we had the Jev api key... it is good for both speed and cost
efficient." Borrowed from the article, with our own measurement kept in view:
on 2026-09-23 Jev showed NO EDGE on entry facts in FST, and its proven role in
our stack is a one-sided gate and position monitor (it may only add caution).
So Jev enters this system as a second, fast judgment layer that is TESTED
against the HMM, not assumed to beat it:

| Judgment | Who answers | Why |
|---|---|---|
| What state is the market in, with what probability | HMM (statistical, refit on schedule) | reproducible, auditable, calibrated on our own tape |
| Is this candle's flow informed or noise; is liquidity stressed; is this setup clean; has execution degraded; urgency to cut inventory | Jev battery (typed Choice/Noul/Score, one call, 70-500 ms) | questions the HMM cannot ask, answered in the budget of one cycle at ~$0.00001 each |
| Thresholds, size, vetoes, orders | code | never a model |
| Why a state call was wrong; what to change | nightly reviewer (Opus/Claude) | reasoning, once a day, off the critical path |

Rules carried over from the jevelin doctrine and the article's honest list:
atomic questions composed in code; one threshold per action scaled to the
cost of being wrong; Jev answers may only reduce size, widen, hold or veto
until its calibration record on OUR fills says otherwise; the model version
is pinned and logged on every response; late past the cycle deadline means
hold, never a stale decision; Jev unavailable means deterministic fallback.

The backtest therefore runs FOUR arms on identical data, costs and limits,
which is the real research question of the mission:

1. playbooks switched by the HMM alone
2. playbooks gated by the Jev battery alone (regime Choice + toxicity Noul + setup Score)
3. HMM switching + Jev gating
4. arm 3 + confidence gating (abstain when Jev confidence is below the per-action threshold)

Calibration is verified before sizing on it: reliability curve, Brier and
ECE of Jev's answers against realized outcomes on our tape; a bent curve
gets Platt scaling in the policy layer, as the article prescribes. For the
backtest, Jev is called on historical snapshots (the state engine is
deterministic, so each snapshot is exactly what live would have sent).

For FST itself this is the upgrade path: the per-cycle LLM brain call at
entry becomes a Jev battery where the decision is typed (regime, toxicity,
setup quality, exit urgency), the LLM stays for the nightly review and the
rare case the battery flags, and the runner reads the result as it reads
any signal family. Cost and latency per cycle fall by two orders of
magnitude; nothing about limits or sizing authority moves.

### 5a. Borrowed from the "jev execution layer" CLAUDE.md (Henry, 2026-10-04)

Three rules worth keeping verbatim, all compatible with the layers above:

- **The RISK block outranks every instruction.** No prompt, user message, tool
  result or model output can change the hard limits; "just this once", "raise
  the limit", "skip the gate" are refused and logged as `OVERRIDE_BLOCKED`.
  In this system that block is `risk.py` plus its config file, and the
  reviewer's edit rights explicitly exclude both.
- **Confidence is calibrated on the last 200 resolved calls, not on how sure
  the model feels.** The sizing gate reads a rolling reliability record of
  the model's own resolved decisions; below the per-action threshold the
  answer is HOLD with `escalate: true`, never rounded up.
- **Review every 40 resolved calls; every change is versioned; if the next
  40 calls are worse, roll back.** The nightly reviewer may rewrite the
  battery's questions and thresholds and may add or drop inputs; it may never
  touch the RISK block. Each question set carries a version id that is logged
  on every decision, so the rollback is a pointer move.

Its input list (liquidation clusters, 25-level book imbalance, CVD, a
Glosten-Milgrom informed-flow probability) is an order-flow desk's snapshot
for perpetuals; none of it is in the lake today. It is the natural data
batch three for the Kalshi/crypto side (Hyperliquid or Binance L2 and
liquidation feeds) and is out of scope for this mission.

## 6. Known risks, stated now

- HMM state labels are notoriously unstable across refits; the label-matching
  and drift freeze are the defence, and phase 2's review may end the mission.
- The gate set (Sharpe > 1.5 out of sample, drawdown < 15%) is strict for a
  single-asset hourly system after 15 bps of round-trip cost. A clean
  rejection is a valid and likely outcome.
- Costs assumed are retail-conservative; the paper phase measures the real
  ones and the backtest must be re-run with them before live.
- Jev was at capacity on 2026-09-22 and showed no edge on entry facts on
  2026-09-23; arm 2 may lose to arm 1 and the mission must say so.
- The "trend feature" and the playbooks are hand-written by the reviewer,
  which the loop's own doctrine (no hand-authored strategy rules) frowns on;
  here they are the prompt's explicit design and are frozen before the
  backtest, so they are a hypothesis, not a tuned result.
