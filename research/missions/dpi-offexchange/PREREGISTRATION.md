# Pre-registration: the off-exchange-share monthly book (frozen 2026-10-04 03:00 UTC)

Found in-sample (FINDINGS.md): the decile of liquid US stocks with the
highest off-exchange share of volume underperforms the lowest decile by
about 2% over the following 20 trading days, 2021 to 2026. This freezes the
rule so the next twelve months are an honest test.

## Frozen rule

On the first trading day of each month (decision day t), using the
off-exchange row for t-1 (publication lag) and the lake's adjusted bars:

- universe: close >= $10 and 20-day median dollar volume >= $50M on t
- signal: `short_vol_share` = OTC short volume / consolidated volume on t-1
- deciles across the universe that day; LONG decile 1, SHORT decile 10,
  equal weight within each side, dollar-neutral
- hold to the close of t + 20 trading days; no intra-month changes
- also recorded: the long-only version (decile 1 vs the equal-weight
  universe) and decile 10 alone vs the universe

`book.py` produces each month's lists from the lake with no discretion;
the lists are committed before the month's outcome exists.

## Wall

Discovery: everything through the 2026-10 decision day (in-sample).
Forward: decision days from 2026-11-02.

## Acceptance (first checked after 12 forward months, then every month)

- long/short spread: mean > 0 and t >= 2.0 on the forward months
- decile 10 alone: mean excess < 0
- no single month explaining more than half of the cumulative spread

Failing retires the rule. Passing promotes it to a live-sized FST book, with
borrow cost and realized slippage measured on the paper book that runs in
parallel from the first forward month.
