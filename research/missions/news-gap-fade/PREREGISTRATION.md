# Preregistration: news-gap-fade

Designed 2026-10-17 by Henry (owner); executed by the research agent. Frozen
in `ledger.jsonl` (event `preregistration_frozen`) before any event return of
this campaign was computed. The design below is Henry's and is fixed; the
section "Implementation conventions" records every choice the design left
open, each made before any outcome was read. Authority: `research_only`.

## Hypothesis source (stated honestly)

Live shadow data from the Jev `equity_priced` lane, 2026-10-08 .. 2026-10-16
(835 news calls, 211 names): for company-news headlines arriving pre-market or
before 11:00 ET, the stock's move from the call to the close, net of SPY over
the same window, went AGAINST the overnight gap. Fading the gap since the
prior close earned +0.84% mean / +0.77% median on 460 early calls; +1.78% /
+2.19% in high-volatility names (ATM straddle >= 6%). When the news model
agreed the news justified the gap, the fade was larger (+1.09%) than when it
disagreed (+0.48%). Seven trading days in one risk-off regime, mid prices, no
costs: **an anecdote**. Prior evidence in this repository: `idio-gap`
(2026-08-14) found NO next-day edge after idiosyncratic daily gaps, so the
effect, if real, is intraday and news-conditioned.

**Reserved window.** 2026-07-01 onward is CONTAMINATED/RESERVED (the anecdote
and a future Jev-filter study). In addition, 2025-07-01 onward is untouched by
this campaign. No gate or statistic reads anything dated on or after
2025-07-01; no news, daily or minute bar on or after that date is downloaded.
(The only post-2025-06-30 request is Alpaca name-change metadata, used to map
historical tickers to current ones; it carries no prices or news.)

## Part A — the gap fade on ten years of history

**Data.** Alpaca historical data API only (`data.alpaca.markets`; no trading
client, no account endpoint). News: `GET /v1beta1/news`, `created_at`,
headline, symbols, source. Daily bars: `/v2/stocks/bars` `1Day`,
`adjustment=all` (gaps, returns, vol) and `adjustment=raw` (price and dollar
volume filters), `feed=sip`. Minute bars: `1Min`, `adjustment=all`,
`feed=sip`, for candidate event sessions and SPY on every session used.
Cached under `.research/cache/news-gap-fade/` (gitignored).

**Universe.** A fixed list: current S&P 500 (Wikipedia constituents table,
fetched 2026-10-17; 502 tickers) ∪ Nasdaq-100 (nasdaq.com list, as of
2026-10-15; 101) ∪ the latest local `qs-research/univ/most-active-stock-options-*.csv`
(06-20-2026; 498 stocks, no ETFs) = 822 symbols, SPY excluded (it is the
hedge). SATS is dropped as the same security as ECHO (renamed 2026-06-24;
Alpaca serves the identical history under both) and PSKY likewise as SKYD
(renamed 2026-10-06; no bars under either), leaving 820. Point-in-time filters at each event date from prior data only: raw
close on the prior session >= $5 and median 20-session raw dollar volume
(raw close x raw volume, sessions ending the prior session, all 20 present)
>= $20M. **Survivorship bias** (current constituents) is a known limitation:
it is stated in the findings and results are reported by year.

**Events.** Session D, symbol S. Qualifying news = a headline tagging S with
at most 3 symbols, `created_at` in (prev(D) 16:00 ET, D 09:25 ET].
gap = open_D / close_prev(D) - 1 (adjusted daily bars). Event if |gap| >= T.
CONTROL series: same thresholds, no qualifying headline in that window.

**Trade (fade).** Short if gap > 0, long if gap < 0. Entry at the close of the
1-minute bar ending at the entry time; exit at the close of the bar ending
15:55 ET. Statistic: market-adjusted return = side x (stock return - SPY
return over the same minutes), beta 1. Raw (side x stock return) also
reported.

**Grid (declared now; no cell is added after results).**
T ∈ {1%, 2%, 4%} × vol bucket ∈ {all, top tercile of prior-20-session
realized daily vol, cross-sectionally within the calendar year} × entry ∈
{09:35, 10:00} = 12 cells, each for NEWS and CONTROL. Gap-down (long) and
gap-up (short) halves are reported separately (descriptive; shorts: borrow
not modeled).

**Unit of observation.** Equal-weight portfolio return per session per cell
(average of that session's events), so many events on one day count once.
Bootstrap: block = one ISO calendar week, 2000 resamples, seed 20261017.

**Costs.** 10 bps round trip base; stress 20 bps; top-tercile cells also
40 bps.

**Splits.** Discovery 2016-01-01 .. 2022-12-31. Validation 2023-01-01 ..
2025-06-30 (one shot, read only for cells that pass every discovery gate).
2025-07-01 onward untouched.

**Discovery gates (all required, NEWS series, market-adjusted net):**

1. >= 100 event-sessions.
2. Positive mean in both chronological halves of the discovery event-sessions
   (base cost). The claimed sign is positive (the fade earns).
3. Positive mean at base (10 bps) AND stress (20 bps); top-tercile cells also
   at 40 bps.
4. Survives dropping the single largest-|contribution| session, and survives
   dropping the single best session (base cost).
5. Week-block bootstrap 5th percentile of the mean > 0 (base cost).
6. NEWS beats CONTROL: week-block bootstrap (weeks drawn jointly) 5th
   percentile of mean(NEWS daily) - mean(CONTROL daily) > 0. A cell that
   passes 1-5 but fails 6 is a plain gap fade, not news-conditioned, is
   reported as such, and is not validated.

**Validation (one shot, passing cells only):** positive net mean at 20 bps
(and 40 bps for top-tercile cells), and positive in both chronological halves
of the validation event-sessions at each of those costs.

## Part B — does "the news justifies the gap" sharpen the fade?

Local model only: Qwen2.5-3B on this machine's llama-server
(`http://127.0.0.1:8080/v1/chat/completions`, model `qwen2.5:3b`). The
server is shared with production: at most 2 concurrent requests; all
requests pause on weekdays 13:00-16:30 America/Los_Angeles; the server and
every QS-* task are never restarted or reconfigured. Jev is deliberately NOT
used (unknown knowledge cutoff; a historical backtest of it may be
contaminated). The local model is a leakage-light stand-in, not leakage-free:
it may have read about some 2016-2024 events in pre-training.

**Sample.** DISCOVERY NEWS events of the T = 2% threshold (any vol bucket),
capped at 15,000, stratified by calendar year (each year keeps its
proportional share, largest remainders settle the total; drawn without
replacement with a seed derived from 20261017). Sampling is done before and
independent of price availability.

**Request.** Up to the 5 most recent qualifying headlines (identical texts
kept once), the ticker as tagged at the time (FB, not META, in 2016), the gap
direction and size. No date. `temperature 0`,
`max_tokens 24`, `response_format` = `json_schema` (strict) with schema
`{"type":"object","properties":{"justified":{"type":"number","minimum":0,"maximum":1}},"required":["justified"],"additionalProperties":false}`.
A 200 response that does not parse is retried once, then excluded and
counted. A server or transport error is not a parse failure: it is retried
with backoff (the shared server may be busy); after six hours without a
response the scorer stops without recording the event. Evaluation refuses to
open until every sampled event has a recorded line, and refuses if no sampled
event has a usable score.

Exact messages (from `research/prereg/news_gap_fade.py`):

System:

```
You are an equity news analyst. You answer only with JSON.
```

User (`{headlines}` is one `- <headline>` line per headline; `{direction}` is
`UP` or `DOWN`; `{size}` is |gap| in percent with one decimal):

```
Stock ticker: {symbol}
These headlines about the company were published after the previous close and before today's open:
{headlines}

At today's open the stock gapped {direction} {size}% versus the previous close.

Question: Does this news justify a move of this size in this direction?
Reply with JSON {"justified": p}, where p is your probability from 0 to 1 that the news justifies the move.
```

**The one pre-registered comparison.** Fade return (market-adjusted, entry
09:35, exit 15:55, base cost — costs cancel in the difference) when
justified >= 0.5 (J) vs < 0.5 (U): difference of daily means, J and U daily
portfolios built separately. Discovery PASS iff each group has >= 100
event-sessions and the week-block bootstrap (weeks drawn jointly) 5th
percentile of mean(J) - mean(U) > 0. Validation, one shot, only if discovery
passes: a validation sample of the same construction and cap; PASS iff
mean(J) - mean(U) > 0 and positive in both chronological halves of the
validation sessions.

## Implementation conventions (choices the design left open, fixed now)

1. **Calendar.** `research/prereg/xnys.py` is defined from 2022 only. Sessions
   are the dates of SPY's SIP daily bars; for 2022-01-01 .. 2025-06-30 they
   must equal `xnys.sessions` exactly (the prep script aborts otherwise).
   Alpaca's SIP history begins 2016-01-04, so with the 20-session filters the
   first evaluable session is in early February 2016. The daily bar `o` is
   the regular-session open (checked against the 09:30 minute bar and the
   pre-market prints on six sample sessions), so the gap is not measured
   against a pre-market trade.
2. **Tickers in news.** Alpaca serves bars under the current ticker for the
   whole history but tags news with the ticker in force at the time (FB, not
   META, in 2016). Old tickers are mapped to current universe symbols through
   Alpaca's name-change corporate actions, only for news created before the
   change; a current ticker counts only from the day it was assigned.
   Alpaca's name-change records begin 2017-04-10; earlier renames are not
   mapped (a stated limitation: such a news day falls into CONTROL). The
   chain is followed only through renames strictly earlier than the date the
   later ticker was retired, so a recycled ticker's other company is never
   inherited (A-Mark's AMRK is not Barrick's B; Gardner Denver's GDI is not
   Trane's TT). Duplicate rename records keep the earliest date. A universe
   ticker that is an older name of another universe ticker retired after
   2025-06-30 is the same security listed twice and is dropped (SATS, PSKY).
   A qualifying headline must have non-empty text; an article id counts
   once. Share classes of one issuer (GOOG/GOOGL, FOX/FOXA, NWS/NWSA) are
   separate symbols, so one headline can create two events on one session (a
   stated limitation). A ticker reused by two SPACs (GSAH: VRT in 2020, MIR
   in 2021) maps its pre-2020 news to both; the SPAC-era bars rarely pass the
   $20M dollar-volume filter, so this is noted, not corrected.
3. **Prices.** "The bar ending at t" is Alpaca's bar stamped t - 1 minute. If
   it is missing, the latest earlier bar in the window (entry: stamped
   09:30 .. t - 1; exit: 15:30 .. 15:54). No bar: the event is dropped and the
   drop is counted. A session whose SPY lacks an entry or exit bar is dropped
   whole. Alpaca SIP minute bars include extended-hours trades, so 13:00 ET
   early closes still show 15:30-15:54 bars; they are dropped by date
   (`EARLY_CLOSES`: the 18 NYSE early closes 2016-11-25 .. 2024-12-24).
4. **vol20** = sample std of adjusted log close-to-close returns over the 20
   sessions ending prev(D), all present. Top tercile: vol20 >= the 2/3
   quantile of vol20 over all filter-passing symbol-sessions of the same
   calendar year. The cut uses the whole year's cross-section (a
   boundary-only look-ahead; no return enters it).
5. **Costs** are charged once per event on the stock leg. The SPY hedge leg's
   cost is not charged separately (SPY spread ~1 bp; stated in findings).
6. **Halves** are chronological halves of the event-sessions
   (first floor(n/2) sessions vs the rest). The drop-one-session gates pick
   the session on base-cost net values.
7. **Verdicts.** Part A discovery verdict is PASS iff at least one cell passes
   every gate; Part A validation opens only then, reads only the passing
   cells, and records a verdict per cell. Part B likewise. Every cell,
   passing or failing, NEWS and CONTROL, is written to the ledger.
8. **Descriptive (never gates):** raw returns; CONTROL cells run through
   gates 1-5; gap-up / gap-down halves; by-year means; event drops; for
   validated cells, the validation CONTROL mean.
9. **Multiplicity.** 12 NEWS cells are tested; no correction beyond the
   gates. A single passing cell among 12 is weak evidence and is reported
   as such.
10. **What was seen before the freeze.** Data was downloaded before the
    freeze, as the design permits. Seen: universe size (822; 820 after the
    duplicates), filter-passing symbol-sessions (1,413,954 before dropping
    the duplicates), symbol-sessions with |gap| >= 1% (318,140; 317,998 in
    the event table), the NEWS share of those (21.0%),
    news-window article counts, event counts per cell from a dry run, and
    the model's answer to one synthetic test prompt. An independent
    pre-freeze code review read metadata (bar timestamps and counts on
    early-close days, universe and name-change files) and the first daily
    close of B, ECHO and SATS to identify which security each ticker was (a
    price level, not a return). The prep script's cache-read path was made
    NA-safe for tickers after the download ran; its download and feature
    code are unchanged. No event return, no
    exit price relative to entry, and no score on a real event.

## Code bound by hash in the freeze

- `research/prereg/news_gap_fade.py` — conventions, prompt, gates.
- `research/experiments/prepare_news_gap_fade.py` — download, filters, gaps, vol20.
- `research/experiments/sweep_news_gap_fade.py` — events, evaluation, ledger.

Outputs are research evidence only. No orders, no broker or account access,
no live signal, no policy artifact.
