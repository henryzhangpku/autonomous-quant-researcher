Discover a robust SPY same-day-expiry or short-weekly options hypothesis that
improves net full-win edge over the every-session control on the fixed
validation split. Explore entry timing, adjustment-invariant intraday signals,
and defined-risk structure selection; prefer simple hypotheses that remain
positive across years and do not merely reduce the sample.

Return one Python candidate implementing this exact interface:

- `ENTRY_HM`: an Eastern-time `(hour, minute)` tuple.
- `LABEL`: a short research label.
- `signal(session, entry_minute) -> bool`: entry decision using only information
  available by the entry minute.
- `structure(session, entry_price) -> Structure`: a structure built from the
  pure helpers in `research.backtest`.

Candidate code may import only `math`, `statistics`, or `research.backtest`.
It must not load data, inspect files, access the network, alter the evaluator,
or calculate its own score. The fixed validator owns chronology, pricing,
costs, controls, splits, metrics, and the trial ledger.

The `session` argument is a `research.backtest.Session` object. Its complete
safe public surface is:

- `day: str` -- an ISO `YYYY-MM-DD` date.
- `open: float`.
- `price_at(hm: int) -> float | None`.
- `ret_from_open(hm: int) -> float | None`.
- `first_touch(level: float, after_hm: int) -> int | None`.

Minutes are integers counted from midnight Eastern time. The validator passes
`entry_minute = ENTRY_HM[0] * 60 + ENTRY_HM[1]`. A price, return, or touch can
be `None`; check it before comparing it or using arithmetic. The keyword for
`first_touch` is `after_hm`, not `after_minute`. The session is a causal view:
future prices return `None`, `first_touch` never searches beyond the entry
minute, and raw bars or the closing price are not exposed.

Build and return exactly one `Structure` with one of these signatures imported
from `research.backtest`:

- `call_debit_spread(entry_px: float, width: float, otm_offset: float = 0.0) -> Structure`
- `put_credit_spread(entry_px: float, width: float, otm_offset: float = 0.0) -> Structure`
- `long_put(strike: float) -> Structure`

Do not unpack, iterate, add to, or otherwise treat a `Structure` as a number or
collection. The candidate must be self-contained; every name it uses must be
defined or explicitly imported. A minimal contract-correct shape is:

```python
from research.backtest import call_debit_spread

ENTRY_HM = (12, 30)
LABEL = "descriptive research label"

def signal(session, entry_minute):
    observed_return = session.ret_from_open(entry_minute)
    return observed_return is not None and observed_return <= -0.5

def structure(session, entry_price):
    return call_debit_spread(entry_price, width=2.0)
```

This is an interface example, not a strategy suggestion.
