# news-gap-fade (2026-10-17): the anecdote is refuted. On news days the
# gap CONTINUES intraday; it does not revert

Trigger: seven sessions of Jev `equity_priced` shadow data (2026-10-08 ..
10-16) in which fading the overnight gap on early company-news calls earned
+0.84% mean SPY-adjusted, more in high-vol names and more when the news
"justified" the gap. Preregistered and frozen before any return was read
(`PREREGISTRATION.md`; ledger `ledger.jsonl`, freeze event
`70e80fd4…f9d23`, commit `7de485c`, pushed before evaluation). Ten years of
Alpaca SIP history: 820 names (current S&P 500 ∪ Nasdaq-100 ∪ most-active
options names), 317,998 symbol-sessions with |gap| >= 1% after point-in-time
filters, 21% of them with a qualifying overnight headline. Discovery
2016-02 .. 2022-12; validation 2023-01 .. 2025-06 (sealed, never read: no
cell passed discovery). Nothing on or after 2025-07-01 was downloaded.
Authority `research_only`.

## Verdict A: the news-gap fade is REFUSED (0 of 12 cells), and the sign is the opposite

Every NEWS cell failed every return gate: both halves, base cost, stress
cost, drop-one, bootstrap and NEWS-vs-CONTROL. None of them failed narrowly.
The fade **loses** on news days in every cell, and in every discovery year
in 11 of the 12 cells (the one positive cell-year is T4 top 09:35 in 2016:
+1.5 bp on 140 sessions).
Market-adjusted, equal-weight daily portfolio, basis points:

| cell (NEWS) | sessions | events | gross | net 10 bp | halves (net) | boot 5% | NEWS−CTRL 5% |
|---|---:|---:|---:|---:|---:|---:|---:|
| T1 all 09:35 | 1,724 | 43,516 | −4.5 | −14.5 | −18.4 / −10.5 | −18.0 | −10.9 |
| T1 all 10:00 | 1,724 | 43,590 | −4.7 | −14.7 | −17.5 / −11.9 | −17.3 | −7.3 |
| T1 top 09:35 | 1,708 | 21,811 | −3.1 | −13.1 | −17.6 / −8.5 | −19.1 | −13.1 |
| T1 top 10:00 | 1,708 | 21,841 | −7.2 | −17.2 | −22.0 / −12.3 | −22.2 | −11.0 |
| T2 all 09:35 | 1,709 | 21,791 | −12.1 | −22.1 | −30.2 / −14.0 | −28.1 | −28.3 |
| T2 all 10:00 | 1,709 | 21,855 | −10.6 | −20.6 | −24.7 / −16.4 | −25.3 | −20.8 |
| T2 top 09:35 | 1,633 | 12,373 | −10.6 | −20.6 | −29.4 / −11.8 | −29.4 | −32.5 |
| T2 top 10:00 | 1,633 | 12,401 | −13.9 | −23.9 | −28.8 / −18.9 | −31.6 | −28.8 |
| T4 all 09:35 | 1,557 | 8,433 | −26.9 | −36.9 | −50.1 / −23.7 | −49.3 | −62.9 |
| T4 all 10:00 | 1,557 | 8,481 | −20.1 | −30.1 | −36.7 / −23.6 | −39.8 | −45.4 |
| T4 top 09:35 | 1,338 | 5,276 | −19.5 | −29.5 | −38.5 / −20.4 | −45.0 | −63.1 |
| T4 top 10:00 | 1,338 | 5,301 | −20.2 | −30.2 | −38.9 / −21.4 | −43.4 | −50.9 |

("top" = top tercile of prior-20-session realized vol within the year. The
40 bp stress for top-tercile cells is moot: they are negative before cost.)

By year (NEWS, T2 all 09:35, net 10 bp): 2016 −24.8, 2017 −47.7,
2018 −24.7, 2019 −16.3, 2020 −8.5, 2021 −9.2, 2022 −24.7. T4 all 09:35:
−43.8, −90.4, −29.6, −39.4, −9.7, −26.0, −30.4. Of 84 cell-years, one is
positive (+1.5 bp, above).

Both sides lose (net 10 bp, T2 all 09:35): buying news gap-downs −31.7
(10,202 events), shorting news gap-ups −14.9 (11,589). At T4: −44.0 and −21.9.

The shape matters. At T1/T2 with the 09:35 entry, the per-event median fade
is slightly positive (T2 all 09:35: mean −3.0 bp, median +2.4 bp gross; T2
top 09:35: −1.2 / +8.3), while the day-weighted mean is clearly negative. The
typical event is a small fade gain and the heavy news days are large losses.
At T4 even the median is negative (all 09:35: −2.8 bp). A left-skewed payoff
with a positive median is exactly what makes a seven-day sample look good.

**The bigger the news gap, the stronger the continuation.** Gross, SPY-adjusted
drift in the gap's direction from 09:35 to 15:55 rises from ~5 bp (T1) to
~12 bp (T2) to ~27 bp (T4). This is the post-news drift the literature
describes (news-driven moves continue, no-news moves revert), measured
intraday. It is **not** a validated finding of this campaign. The campaign
claimed the opposite sign, and a continuation rule was never preregistered.
See "What would be needed".

## CONTROL (gaps without news): descriptive only, no edge either

Run through the same gates 1-5 as a description, never as a claim. No
CONTROL cell passes. Net of 10 bp the no-news fade sits between −11 and
+9 bp. The only positive pocket is shorting no-news gap-ups (T4 all 09:35:
+28.4 bp net on 6,419 events; T4 top 09:35: +35.8 bp), while buying no-news
gap-downs loses everywhere (−8 to −14 bp). That pocket is a post-hoc slice of
a descriptive series, and shorts carry unmodeled borrow on exactly the names
that gap up hardest. It is recorded so nobody rediscovers it as new. It has
no standing.

| cell (CONTROL) | sessions | gross | net 10 bp | gap-down long | gap-up short |
|---|---:|---:|---:|---:|---:|
| T1 all 09:35 | 1,728 | +3.0 | −7.0 | −10.6 | +0.5 |
| T2 all 09:35 | 1,687 | +8.0 | −2.0 | −11.0 | +9.5 |
| T2 top 09:35 | 1,643 | +11.2 | +1.2 | −8.5 | +13.7 |
| T4 all 09:35 | 1,122 | +15.9 | +5.9 | −12.3 | +28.4 |
| T4 top 09:35 | 988 | +19.2 | +9.2 | −11.7 | +35.8 |
| T4 all 10:00 | 1,124 | +7.8 | −2.2 | −12.2 | +13.5 |

(All 24 cells with every gate value are in `results.json` and the ledger's
`partA-discovery` verdict.)

## Verdict B: "the news justifies the gap" is UNTESTABLE with the stand-in model (recorded FAIL)

Qwen2.5-3B (local llama-server, temperature 0, strict JSON schema) scored
15,000 discovery NEWS events of the T = 2% threshold, stratified by year
(2016 1,135 … 2022 3,675; coverage of the news feed grows sharply after
2019). Every response parsed. The model said the news justified the gap in
**99.3%** of events (quartiles 0.7 / 0.7 / 0.8; 99.0% of gap-ups, 99.6% of
gap-downs). It does not discriminate. It agrees with whatever the gap says.

| group | events | sessions | fade, net 10 bp |
|---|---:|---:|---:|
| justified >= 0.5 | 14,777 | 1,683 | −22.1 bp |
| justified < 0.5 | 102 | 86 | −20.5 bp |
| difference J − U | | | −1.7 bp (halves −58.4 / +10.9; boot 5% −58.5) |

Both preregistered gates failed: the unjustified group has 86 sessions
against a floor of 100, and the bootstrap bound is far below zero. Part B
validation is sealed. The honest reading is not "news quality doesn't
matter". It is that **this model cannot measure news quality**, so the
question is still open. The Jev anecdote's J-vs-U split (+1.09% vs +0.48%) is
neither confirmed nor refuted, and the direction it assumed (that the fade
pays at all) is refuted by Part A.

## Failures and what was not run

- Part A: 12 of 12 NEWS cells FAIL discovery. Part A validation never
  opened; the ledger refuses it (`partA-validation is sealed until
  partA-discovery passes`). The 2023-01 .. 2025-06 window has not been read
  by any return computation in this campaign.
- Part B: discovery FAIL (min sessions per group, bootstrap bound). Part B
  validation scoring and evaluation were not run, as preregistered.
- Ledger (`ledger.jsonl`): freeze 2026-10-17T20:19:46Z; partA-discovery
  opened 20:20:48Z, verdict FAIL; partB-discovery opened 2026-10-18T01:24:33Z,
  verdict FAIL. Every cell (24 in Part A, NEWS and CONTROL) and every gate
  value is in the verdict payloads and in `results.json`.
- Found and fixed BEFORE the freeze by an independent code review, recorded
  so they are not repeated: SIP minute bars include extended hours, so
  13:00 early closes still had "15:55" bars (now dropped by date); the
  ticker-alias walk inherited recycled tickers (A-Mark news would have
  counted as Barrick's); SATS/ECHO was one security listed twice; Part B's
  completeness check ran after the one-shot receipt.

## Caveats

- **Survivorship.** The universe is today's index members and active
  options names, so delisted, acquired and failed companies are missing. The
  bias works on which names are present, not on the sign of an intraday
  move. 83 of 84 cell-years are negative, which a bias concentrated in a
  few years could not produce.
- **Costs.** 10/20/40 bp round trip at bar-close prices, with no spread model
  at 09:35, where spreads are widest. It doesn't matter for the verdict: the
  NEWS fade is negative before any cost. It would matter a great deal for
  any continuation follow-up.
- **Borrow.** Not modeled. The fade shorts gap-ups; a continuation
  expression would short gap-downs, often hard-to-borrow names on bad news.
- **Clustering.** Earnings seasons put hundreds of events on one session. The
  unit is the equal-weight daily portfolio and the bootstrap resamples whole
  ISO weeks. Event-level means are smaller (T2 all 09:35: −3.0 bp mean,
  +2.4 bp median, gross) than day-weighted ones (−12.1 bp gross). The
  losses concentrate on the heavy news days.
- **Regime.** Discovery spans 2016-2022: the 2018 drawdown, the 2020 crash,
  the 2021 retail boom, the 2022 bear market. The effect is weakest in
  2020-2021 (−8.5 / −9.2 bp at T2 all 09:35) and positive in only one
  cell-year of 84.
- **News data.** Benzinga via Alpaca; `created_at` as stamped, not audited
  against wire timestamps, and the feed's coverage roughly triples from
  2016 to 2022 (so the NEWS/CONTROL split is not constant over time).
  Renames before 2017-04-10 are
  unmapped. Share classes (GOOG/GOOGL, …) double-count one headline. GSAH
  maps to both VRT and MIR.
- **Multiplicity.** 12 cells were tested. The result is uniform in sign, so
  multiplicity cannot explain it away. It would only weaken a lone pass.
- **The stand-in model** may have read about some 2016-2024 events in
  pre-training. Its failure is a lack of discrimination, not leakage.

## Why the anecdote looked the other way

The Jev sample is seven sessions in one risk-off week of October 2026 (the
reserved window), measured from the call time (up to 11:00 ET) at mid
prices, on 211 names chosen by the lane, with no costs. In ten years of
history the per-event median fade at T1/T2 is slightly positive while the
mean is negative. A short sample in a mean-reverting week can easily produce
+0.8% mean and median, and then lose it on the few news days where the
information is real. Nothing here says the October 2026 observations were
mismeasured. They are a small sample from a distribution whose mean is
below zero.

## What would be needed before any policy

1. **For the fade: nothing. It is refused.** No Jev-lane rule should fade
   news gaps on the strength of the shadow data.
2. **For continuation (the opposite sign):** a NEW preregistered campaign,
   stated before reading 2023-01 .. 2025-06, with the claim "follow the gap
   on news days" (T4, both entries). It would use that window as its one-shot
   holdout, model the 09:35 spread from quotes rather than bar closes, model
   borrow on the short side, and test capacity on the T4 names. A
   continuation rule taken from this campaign's discovery numbers without
   that step would be the post-hoc reversal the program forbids.
3. **For "justified" news:** a scorer that actually discriminates, measured
   first on labeled headlines with no returns attached. The cut would be set
   before any return is read, then tested on the reserved Jev window as the
   design already plans.
4. **For the CONTROL gap-up pocket:** nothing. It is a post-hoc slice of a
   descriptive series.

## Product consequences

- The `equity_priced` lane's early-news "fade the gap" observation has no
  historical support. Treat the live result as noise until the reserved-window
  study says otherwise.
- `idio-gap` found no next-day edge after idiosyncratic gaps. This campaign
  finds that the same-day intraday move after a news gap is, if anything,
  continuation. The two are consistent: no reversion to harvest, at either
  horizon.
- No policy artifact, no live signal, no change to any production service.

Reproduce: `python -m research.experiments.prepare_news_gap_fade all`, then
the `sweep_news_gap_fade` commands in its docstring. The ledger makes every
evaluation one-shot, so a rerun reproduces numbers only through a copy of
the mission directory with a fresh ledger.

Backtests are hypothetical, research-only evidence and are not investment
advice.
