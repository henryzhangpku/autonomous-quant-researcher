Discover a robust overnight SPX-complex vertical-spread hypothesis: a
defined-risk SPXW 0DTE/1DTE debit or credit vertical entered during the
overnight session (20:15 ET through 09:15 ET) and cash settled at that day's
16:00 ET close. Explore entry timing across the overnight window, causal
overnight/prior-day signals, direction, and structure choice; prefer simple
hypotheses that stay positive across both halves of the split and do not
merely shrink the sample. A mechanical every-hour rotation is the control
being replaced -- do not re-propose rotation-shaped logic.

Return one Python candidate implementing this exact interface:

- `ENTRY_MIN`: an int, minutes since the 18:00 ET session start, between 135
  (20:15 ET) and 915 (09:15 ET), on the five-minute grid. 360 = midnight ET,
  555 = 03:15 ET, 795 = 07:15 ET.
- `LABEL`: a short research label.
- `signal(session, entry_minute) -> bool`: entry decision using only
  information available by the entry minute.
- `structure(session, entry_price) -> Structure`: one two-leg vertical built
  from the SPX-grid factories in `research.backtest`.

Candidate code may import only `math`, `statistics`, or `research.backtest`.
It must not load data, inspect files, access the network, alter the evaluator,
or calculate its own score. The fixed validator owns chronology, pricing,
costs, splits, gates, and the trial ledger.

The `session` argument is a causal overnight view. Its complete safe public
surface is:

- `day: str` -- the ISO settlement/expiry date.
- `day_of_week: int` -- 0=Monday .. 4=Friday for the settlement day.
- `evening_open: float` -- the 18:00 ET session open.
- `prior_close: float | None` -- the prior day's 16:00 ET settlement level.
- `prior_rth_high: float | None`, `prior_rth_low: float | None` -- the prior
  regular day session's extremes.
- `price_at(minute: int) -> float | None`.
- `ret_from_evening_open(minute: int) -> float | None` -- percent.
- `ret_from_prior_close(minute: int) -> float | None` -- percent.
- `first_touch_from_evening_open(open_offset: float, after_minute: int) -> int | None`
  -- highs answer upside offsets, lows answer downside offsets.

Prices are ES front-month levels near 6000, so offsets and widths are in
index points. Any price, return, or touch can be `None`; check before
arithmetic. Futures return `None` past the entry minute by construction.

Build and return exactly one two-leg `Structure` with one of these factories
imported from `research.backtest` (strikes snap to the 5-point SPX grid;
width must be between 5 and 200 points):

- `spx_call_debit_spread(entry_px, width, otm_offset=0.0)` -- bullish debit
- `spx_put_debit_spread(entry_px, width, otm_offset=0.0)` -- bearish debit
- `spx_put_credit_spread(entry_px, width, otm_offset=0.0)` -- bullish credit
- `spx_call_credit_spread(entry_px, width, otm_offset=0.0)` -- bearish credit

Do not unpack, iterate, or treat a `Structure` as a number or collection. The
candidate must be self-contained; every name it uses must be defined or
explicitly imported. A minimal contract-correct shape is:

```python
from research.backtest import spx_put_credit_spread

ENTRY_MIN = 360
LABEL = "descriptive research label"

def signal(session, entry_minute):
    observed = session.ret_from_prior_close(entry_minute)
    return observed is not None and observed <= -0.4

def structure(session, entry_price):
    return spx_put_credit_spread(entry_price, width=25.0, otm_offset=20.0)
```

This is an interface example, not a strategy suggestion. Vary entry minute,
condition, direction, structure family, width, and offset materially between
candidates.
