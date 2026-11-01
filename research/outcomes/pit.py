"""Point-in-time fundamentals: vintages, as-of lookup, quarters from YTD, growth.

Every value here carries the date it became public (``filed``). The central
rule: a value is invisible before its filed date. Nothing is forward-filled,
interpolated or backfilled across a gap; an unknown value is ``None``.

Reported facts are grouped by period ``(start, end)``. A period's value as of
day ``d`` is the most recently filed report of it on or before ``d`` — so a
restatement in a later filing creates a new vintage without rewriting what
was known earlier.

Quarterly values come from one of three methods, recorded per row:

* ``direct`` — a reported three-month fact;
* ``ytd_diff`` — a year-to-date fact minus the previous year-to-date fact of
  the same fiscal year (cash-flow items such as capex are reported only
  year-to-date in 10-Qs: Q2 = 6M − 3M, Q3 = 9M − 6M);
* ``fy_minus_9m`` — the fiscal-year total from the 10-K minus the nine-month
  year-to-date, which is how a fourth quarter is obtained for nearly every
  US filer.

Both legs of a difference are read as of the same day, so a derived quarter
is stamped with the later of the two filing dates and never mixes a vintage
that was not yet public.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Iterable, Sequence

from research.providers.sec_xbrl import Fact

QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)
YOY_GAP_DAYS = (350, 380)
# A direct quarter and its YTD-derived twin should agree; beyond this the row
# is flagged, never silently reconciled.
MISMATCH_TOLERANCE = 0.01


def _within(value: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= value <= bounds[1]


@dataclass(frozen=True, slots=True)
class Vintage:
    """One period's value as it stood from ``filed`` until superseded."""

    ticker: str
    concept: str
    period_type: str            # "Q" or "FY"
    period_start: date
    period_end: date
    filed: date
    value: float
    form: str
    accession: str
    method: str                 # direct | ytd_diff | fy_minus_9m | annual
    tag: str
    inputs: tuple[str, ...]     # accessions of every fact used
    fiscal_year: int | None
    fiscal_quarter: int | None
    vintage: int                # 0 = first report of the period
    qa_flags: tuple[str, ...] = field(default_factory=tuple)
    # Every filing that reported this same value while it was current,
    # starting with its own. Lets a growth row tell a like-for-like
    # comparative from a comparison across a restatement.
    confirmed_by: tuple[str, ...] = field(default_factory=tuple)

    @property
    def calendar_quarter(self) -> str:
        """The calendar quarter containing the period midpoint (fiscal-year neutral)."""
        mid = self.period_start + (self.period_end - self.period_start) / 2
        if self.period_type == "FY":
            return f"CY{mid.year}"
        return f"{mid.year}Q{(mid.month - 1) // 3 + 1}"


class FactStore:
    """Facts grouped by period, read strictly as of a day."""

    def __init__(self, facts: Iterable[Fact]) -> None:
        self._by_period: dict[tuple[date, date], list[Fact]] = defaultdict(list)
        for fact in facts:
            if fact.start is None:
                continue
            self._by_period[(fact.start, fact.end)].append(fact)
        for rows in self._by_period.values():
            rows.sort(key=lambda f: (f.filed, f.form.endswith("/A"), f.accession))

    @property
    def periods(self) -> list[tuple[date, date]]:
        return sorted(self._by_period)

    def reports(self, period: tuple[date, date]) -> list[Fact]:
        return list(self._by_period.get(period, ()))

    def as_of(self, period: tuple[date, date], day: date) -> Fact | None:
        """The latest report of ``period`` filed on or before ``day``; never a later one."""
        latest = None
        for fact in self._by_period.get(period, ()):
            if fact.filed <= day:
                latest = fact
            else:
                break
        return latest


@dataclass(frozen=True)
class _QuarterPlan:
    start: date
    end: date
    ytd_long: tuple[date, date] | None      # (fy_start, end)
    ytd_short: tuple[date, date] | None     # (fy_start, start - 1)
    fiscal_year: int | None
    fiscal_quarter: int | None


def _fiscal_chains(store: FactStore) -> dict[date, list[date]]:
    """For each fiscal-year start, the ends of every period reported from it."""
    chains: dict[date, set[date]] = defaultdict(set)
    for start, end in store.periods:
        chains[start].add(end)
    return {start: sorted(ends) for start, ends in chains.items()}


def _fiscal_label(fy_start: date) -> int:
    # Year of the fiscal year's end, the convention every filer here uses
    # (MSFT FY2026 ends June 2026; NVDA FY2026 ends January 2026).
    return (fy_start + timedelta(days=360)).year


ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F")


def _fiscal_year_starts(store: FactStore) -> list[date]:
    """Fiscal-year starts: of every annual-length period an annual report carried, and the day after.

    Only annual reports define a fiscal year. A twelve-month period inside a
    10-Q is a trailing-twelve-month disclosure (Amazon reports trailing cash
    flows every quarter) and must not be read as a fiscal year, or its start
    would split a real fiscal year in two. The day after each fiscal year
    end starts the next one, which covers the year still in progress.
    """
    starts: set[date] = set()
    for start, end in store.periods:
        if not _within((end - start).days + 1, YEAR_DAYS):
            continue
        if any(fact.form in ANNUAL_FORMS for fact in store.reports((start, end))):
            starts.add(start)
            starts.add(end + timedelta(days=1))
    return sorted(starts)


def _fiscal_position(fy_starts: Sequence[date], q_start: date, q_end: date
                     ) -> tuple[int | None, int | None]:
    owners = [s for s in fy_starts if s <= q_start and (q_end - s).days + 1 <= YEAR_DAYS[1]]
    if not owners:
        return None, None
    fy_start = max(owners)
    return _fiscal_label(fy_start), max(1, round(((q_end - fy_start).days + 1) / 91.3))


def _plan_quarters(store: FactStore) -> list[_QuarterPlan]:
    chains = _fiscal_chains(store)
    fy_starts = _fiscal_year_starts(store)
    plans: dict[tuple[date, date], _QuarterPlan] = {}
    for fy_start in fy_starts:
        # Walk the year-to-date ends of one fiscal year: 3M, 6M, 9M, 12M.
        previous_end = fy_start - timedelta(days=1)
        for end in chains.get(fy_start, ()):
            total = (end - fy_start).days + 1
            if total > YEAR_DAYS[1]:
                break
            if not _within((end - previous_end).days, QUARTER_DAYS):
                # A missing year-to-date step: the quarter it would close
                # cannot be derived. Leave the gap; do not bridge it.
                previous_end = end
                continue
            q_start = previous_end + timedelta(days=1)
            if q_start == fy_start:
                long_leg, short_leg = None, None
            else:
                long_leg, short_leg = (fy_start, end), (fy_start, previous_end)
            fiscal_year, fiscal_quarter = _fiscal_position(fy_starts, q_start, end)
            plans[(q_start, end)] = _QuarterPlan(q_start, end, long_leg, short_leg,
                                                 fiscal_year, fiscal_quarter)
            previous_end = end
    # Direct three-month facts whose year-to-date chain is not visible.
    for start, end in store.periods:
        if _within((end - start).days + 1, QUARTER_DAYS) and (start, end) not in plans:
            fiscal_year, fiscal_quarter = _fiscal_position(fy_starts, start, end)
            plans[(start, end)] = _QuarterPlan(start, end, None, None, fiscal_year, fiscal_quarter)
    return [plans[key] for key in sorted(plans)]


def _value_as_of(store: FactStore, plan: _QuarterPlan, day: date
                 ) -> tuple[float, str, Fact, tuple[str, ...], tuple[str, ...]] | None:
    direct = store.as_of((plan.start, plan.end), day)
    derived = None
    if plan.ytd_long is not None and plan.ytd_short is not None:
        long_fact = store.as_of(plan.ytd_long, day)
        short_fact = store.as_of(plan.ytd_short, day)
        if long_fact is not None and short_fact is not None:
            long_days = (plan.ytd_long[1] - plan.ytd_long[0]).days + 1
            method = "fy_minus_9m" if _within(long_days, YEAR_DAYS) else "ytd_diff"
            trigger = max((long_fact, short_fact), key=lambda f: (f.filed, f.accession))
            derived = (long_fact.value - short_fact.value, method, trigger,
                       (short_fact.accession, long_fact.accession))
    flags: tuple[str, ...] = ()
    if direct is not None:
        if derived is not None:
            base = abs(direct.value) or 1.0
            gap = abs(direct.value - derived[0]) / base
            if gap > MISMATCH_TOLERANCE:
                flags = (f"direct_vs_{derived[1]}_mismatch_{gap:.3f}",)
        return direct.value, "direct", direct, (direct.accession,), flags
    if derived is not None:
        return derived[0], derived[1], derived[2], derived[3], flags
    return None


def quarterly_vintages(ticker: str, concept: str, facts: Sequence[Fact]) -> list[Vintage]:
    """Every distinct value each fiscal quarter has had, stamped with when it became public."""
    store = FactStore(facts)
    out: list[Vintage] = []
    for plan in _plan_quarters(store):
        # A derived quarter is re-read when its direct fact or its LONGER
        # year-to-date leg is (re)reported. A restated shorter leg alone does
        # not trigger a new vintage: pairing an original fiscal-year total
        # with a later-restated nine-month figure would manufacture a fourth
        # quarter that no filing ever implied.
        triggers = {fact.filed for fact in store.reports((plan.start, plan.end))}
        if plan.ytd_long is not None:
            triggers |= {fact.filed for fact in store.reports(plan.ytd_long)}
        short_dates = ({fact.filed for fact in store.reports(plan.ytd_short)}
                       if plan.ytd_short is not None else set())
        last_value: float | None = None
        count = 0
        for day in sorted(triggers | short_dates):
            if day not in triggers and count:
                continue
            resolved = _value_as_of(store, plan, day)
            if resolved is None:
                continue
            value, method, trigger, inputs, flags = resolved
            if last_value is not None and value == last_value:
                _confirm(out, trigger.accession)
                continue
            out.append(Vintage(
                ticker=ticker, concept=concept, period_type="Q",
                period_start=plan.start, period_end=plan.end, filed=day, value=value,
                form=trigger.form, accession=trigger.accession, method=method, tag=trigger.tag,
                inputs=inputs, fiscal_year=plan.fiscal_year, fiscal_quarter=plan.fiscal_quarter,
                vintage=count, qa_flags=flags, confirmed_by=(trigger.accession,),
            ))
            last_value = value
            count += 1
    return out


def _confirm(out: list[Vintage], accession: str) -> None:
    if accession not in out[-1].confirmed_by:
        out[-1] = replace(out[-1], confirmed_by=out[-1].confirmed_by + (accession,))


def annual_vintages(ticker: str, concept: str, facts: Sequence[Fact]) -> list[Vintage]:
    store = FactStore(facts)
    out: list[Vintage] = []
    for start, end in store.periods:
        if not _within((end - start).days + 1, YEAR_DAYS):
            continue
        last_value: float | None = None
        count = 0
        for fact in store.reports((start, end)):
            current = store.as_of((start, end), fact.filed)
            if current is None:
                continue
            if last_value is not None and current.value == last_value:
                _confirm(out, current.accession)
                continue
            out.append(Vintage(
                ticker=ticker, concept=concept, period_type="FY", period_start=start,
                period_end=end, filed=current.filed, value=current.value, form=current.form,
                accession=current.accession, method="annual", tag=current.tag,
                inputs=(current.accession,), fiscal_year=_fiscal_label(start),
                fiscal_quarter=None, vintage=count, confirmed_by=(current.accession,),
            ))
            last_value = current.value
            count += 1
    return out


# -- as-of views over vintages -------------------------------------------


class VintageSeries:
    """One ticker/concept/period_type series, readable as of any day."""

    def __init__(self, vintages: Iterable[Vintage]) -> None:
        self._by_period: dict[tuple[date, date], list[Vintage]] = defaultdict(list)
        for row in vintages:
            self._by_period[(row.period_start, row.period_end)].append(row)
        for rows in self._by_period.values():
            rows.sort(key=lambda v: (v.filed, v.vintage))

    @property
    def periods(self) -> list[tuple[date, date]]:
        return sorted(self._by_period)

    def vintages(self, period: tuple[date, date]) -> list[Vintage]:
        return list(self._by_period.get(period, ()))

    def as_of(self, period: tuple[date, date], day: date) -> Vintage | None:
        latest = None
        for row in self._by_period.get(period, ()):
            if row.filed <= day:
                latest = row
            else:
                break
        return latest

    def known_periods(self, day: date) -> list[tuple[date, date]]:
        """Periods with at least one report public on ``day``."""
        return [p for p in self.periods if self.as_of(p, day) is not None]

    def find_prior(self, period: tuple[date, date], gap_days: tuple[int, int]
                   ) -> tuple[date, date] | None:
        """The period ending ``gap_days`` before this one ends, matched on length."""
        start, end = period
        length = (end - start).days
        candidates = [p for p in self.periods
                      if _within((end - p[1]).days, gap_days)
                      and abs((p[1] - p[0]).days - length) <= 10]
        return max(candidates, key=lambda p: p[1]) if candidates else None


@dataclass(frozen=True, slots=True)
class GrowthRow:
    ticker: str
    concept: str
    period_type: str
    period_start: date
    period_end: date
    calendar_quarter: str
    filed: date                 # the day these numbers were all public
    accession: str              # the filing that made this row change
    value: float
    prior_quarter_value: float | None
    prior_year_value: float | None
    qoq: float | None
    yoy: float | None
    yoy_prev: float | None      # the previous period's YoY, as known the same day
    yoy_accel: float | None     # yoy - yoy_prev
    inputs_max_filed: date
    # True when the filing behind the current value also reported the
    # prior-year value used (a like-for-like comparative); False flags a
    # comparison across filings, e.g. across a restatement of basis.
    yoy_same_filing: bool | None


def _growth(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return current / base - 1.0


def growth_rows(vintages: Sequence[Vintage]) -> list[GrowthRow]:
    """YoY, QoQ and YoY acceleration, recomputed every day any input changed.

    Each row's numbers use only vintages filed on or before its ``filed``
    date. A missing comparison period leaves that field ``None``.
    """
    if not vintages:
        return []
    by_key: dict[tuple[str, str, str], list[Vintage]] = defaultdict(list)
    for row in vintages:
        by_key[(row.ticker, row.concept, row.period_type)].append(row)
    out: list[GrowthRow] = []
    for (ticker, concept, period_type), rows in sorted(by_key.items()):
        series = VintageSeries(rows)
        is_quarter = period_type == "Q"
        for period in series.periods:
            prior_q = series.find_prior(period, QUARTER_DAYS) if is_quarter else None
            prior_y = series.find_prior(period, YOY_GAP_DAYS)
            prior_qy = series.find_prior(prior_q, YOY_GAP_DAYS) if prior_q else None
            if not is_quarter:
                prior_qy = series.find_prior(prior_y, YOY_GAP_DAYS) if prior_y else None
            involved = [p for p in (period, prior_q, prior_y, prior_qy) if p is not None]
            first = series.vintages(period)[0].filed
            days = sorted({v.filed for p in involved for v in series.vintages(p) if v.filed >= first})
            last_signature = None
            for day in days:
                cur = series.as_of(period, day)
                pq = series.as_of(prior_q, day) if prior_q else None
                py = series.as_of(prior_y, day) if prior_y else None
                pqy = series.as_of(prior_qy, day) if prior_qy else None
                yoy = _growth(cur.value, py.value if py else None)
                if is_quarter:
                    yoy_prev = _growth(pq.value if pq else None, pqy.value if pqy else None)
                else:
                    yoy_prev = _growth(py.value if py else None, pqy.value if pqy else None)
                accel = None if yoy is None or yoy_prev is None else yoy - yoy_prev
                qoq = _growth(cur.value, pq.value) if is_quarter and pq else None
                signature = (cur.value, pq.value if pq else None, py.value if py else None,
                             pqy.value if pqy else None)
                if signature == last_signature:
                    continue
                last_signature = signature
                used = [v for v in (cur, pq, py, pqy) if v is not None]
                trigger = max(used, key=lambda v: (v.filed, v.accession))
                out.append(GrowthRow(
                    ticker=ticker, concept=concept, period_type=period_type,
                    period_start=period[0], period_end=period[1],
                    calendar_quarter=cur.calendar_quarter, filed=day,
                    accession=trigger.accession, value=cur.value,
                    prior_quarter_value=pq.value if pq else None,
                    prior_year_value=py.value if py else None,
                    qoq=qoq, yoy=yoy, yoy_prev=yoy_prev, yoy_accel=accel,
                    inputs_max_filed=max(v.filed for v in used),
                    yoy_same_filing=None if py is None else cur.accession in py.confirmed_by,
                ))
    return out


def latest_as_of(rows: Iterable, day: date, *, key=lambda r: (r.ticker, r.concept, r.period_type,
                                                               r.period_start, r.period_end)):
    """For each key, the newest row with ``filed <= day`` (vintages or growth rows)."""
    best: dict = {}
    for row in rows:
        if row.filed > day:
            continue
        k = key(row)
        if k not in best or row.filed >= best[k].filed:
            best[k] = row
    return [best[k] for k in sorted(best)]
