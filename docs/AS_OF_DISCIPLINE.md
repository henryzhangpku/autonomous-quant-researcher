# As-of discipline for outcome data

The outcome side of a pre-registered campaign (fundamentals and prices) is
built by `python -m research.experiments.prepare_gpu_outcomes` into
`research/data/gpu_outcomes/`. These are the rules it follows.

## One rule

**A value is invisible before the day it became public.** For an SEC fact,
that day is the filing date (`filed`) of the filing that carried it, never the
period end. A test that reads a fundamental on day `d` must use only rows with
`filed <= d`. The EDGAR acceptance timestamp is attached for intraday work; a
filing accepted after 17:30 ET carries the next day's `filed`, so `filed` is
the conservative choice.

Earnings press releases usually reach the market earlier than the periodic
report, by minutes to days. Those releases are not XBRL-tagged for the figures
used here, so the tables lag the information. The bias is towards lateness,
never look-ahead.

## Vintages, not values

A period can be reported many times: originally, then as a comparative in later
filings, sometimes restated. `fundamentals_pit.csv` keeps one row per distinct
value (`vintage` 0 is the first report) with `confirmed_by` listing every
filing that re-reported the same number. Reading "as of `d`" means the latest
vintage with `filed <= d`. Nothing is overwritten.

## Quarters from year-to-date

Cash-flow items (capex) are reported year-to-date in interim filings, and
almost no filer reports a fourth quarter directly. `method` says how each
quarter was obtained:

| method | how |
|---|---|
| `direct` | a reported three-month fact |
| `ytd_diff` | 6M − 3M or 9M − 6M of the same fiscal year |
| `fy_minus_9m` | the annual total − the nine-month YTD |
| `annual` | a fiscal-year fact |

Both legs of a difference are read as of the same day, so a derived quarter is
stamped with its later leg's filing date. A derived quarter is re-read only
when its longer leg is re-reported; a restated shorter leg alone would pair an
original annual total with a restated nine-month figure and imply a fourth
quarter that no filing ever showed. Fiscal years are identified only from
annual reports, so a trailing-twelve-month figure inside an interim filing
cannot split a fiscal year. A missing YTD step is a gap, never bridged.

## Growth

`fundamentals_growth.csv` holds QoQ, YoY, and YoY acceleration (`yoy - yoy_prev`,
the previous quarter's YoY as known that same day). A row is written on every
filing date that changed any input, and every number in it was public on its
`filed` date (`inputs_max_filed <= filed`, checked by the tests).
`yoy_same_filing = False` marks a comparison whose prior-year value came from a
different filing than the current one, which is how a comparison across a
restatement of basis shows up. Fiscal calendars differ by company; align series
by `period_end` or `calendar_quarter` (the calendar quarter containing the
period midpoint), not by fiscal labels.

## Prices

Daily bars are split- and dividend-adjusted (`adjustment=all`, SIP feed).
Adjusted *levels* are rescaled backwards at every new dividend, so they are not
point-in-time; adjusted *returns* are. A raw pass is fetched alongside only to
audit the adjustment: `adj_factor = raw_close / close` may step only on a
corporate-action day, and the manifest lists every step and every adjusted
one-day move beyond ±40% log return for review. Untraded placeholder bars
(zero volume and zero trades) are dropped, not kept as flat returns.

## Consensus estimates: no free point-in-time source

The primary campaign needs consensus estimate history as it was known on each
date. Checked on 2026-10-08:

- **SEC EDGAR** carries no analyst estimates.
- **Free web pages and free-tier APIs** expose the current consensus, at most
  with a short revision trail. None publishes an archive of what the consensus
  was on past dates, and the web pages' terms do not permit automated
  collection. A free-tier API key could record a forward-only tape from today,
  which cannot cover the historical window.
- **Point-in-time revision history** is licensed vendor data.

So nothing was collected. `research/providers/licensed.py` remains the
boundary: it declares the record schema and refuses until a licensed adapter is
injected. The free/public example in `research/providers/public_csv.py` shows
the other end of the same interface.
