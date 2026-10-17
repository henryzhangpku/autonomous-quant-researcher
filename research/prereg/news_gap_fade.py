"""news-gap-fade: the arithmetic that judges the campaign, frozen before any outcome.

Pure functions over already-downloaded data. Bound by content hash into
``research/missions/news-gap-fade/ledger.jsonl``; editing this file after the
freeze fails ``ledger.verify``. The design is in
``research/missions/news-gap-fade/PREREGISTRATION.md`` and
``research/experiments/sweep_news_gap_fade.py``.

Conventions, all declared before any event return was computed:

- ``D`` is a session; ``prev(D)`` is the session before it. Sessions are the
  dates on which SPY has an adjusted SIP daily bar (cross-checked against
  ``research.prereg.xnys`` wherever that calendar is defined, 2022 onward).
- gap(S, D) = open_adj(D) / close_adj(prev(D)) - 1; no gap if S has no bar on
  ``prev(D)``.
- Point-in-time filters, prior data only: raw close on ``prev(D)`` >= $5 and
  the median of raw close x raw volume over the 20 sessions ending ``prev(D)``
  >= $20M (all 20 present).
- vol20 = sample std of adjusted log close-to-close returns over the 20
  sessions ending ``prev(D)`` (21 closes, all present).
- top tercile: vol20 >= the 2/3 quantile of vol20 over every filter-passing
  symbol-session of the same calendar year (the cut uses the whole year's
  cross-section; a boundary-only look-ahead, never a return).
- qualifying headline for (S, D): ``created_at`` in (prev(D) 16:00 ET,
  D 09:25 ET], at most 3 tagged symbols, S among them (old tickers mapped to
  the current one only while the old ticker was in force).
- early closes (``EARLY_CLOSES``) are dropped whole.
- prices: the close of the 1-minute bar ENDING at the time, i.e. Alpaca's bar
  stamped one minute earlier; if that bar is missing, the latest earlier bar
  inside the window (entry: bars stamped 09:30 .. entry-1; exit: 15:30 ..
  15:54). No bar in the window: the event has no price and is dropped, and
  the drop is counted. A session whose SPY has no entry or exit bar is
  dropped whole.
- fade: s = -sign(gap). raw = s * r_stock; adjusted = s * (r_stock - r_spy)
  over the same entry/exit minutes (beta 1). Net = gross - round-trip cost.
- unit: equal-weight portfolio per session per cell; a cell's statistic is the
  mean over its event-sessions. The drop-one-session gates pick the session
  on base-cost net values.
- bootstrap: block = one ISO calendar week; weeks are drawn with replacement,
  as many as the series has; the statistic is the mean over every
  event-session in the drawn weeks. 2000 resamples. Each bootstrap call seeds
  its own generator from SEED and its own label, so results do not depend on
  evaluation order.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")

SEED = 20261017
RESAMPLES = 2000
ALPHA = 0.05

THRESHOLDS = (0.01, 0.02, 0.04)
VOL_BUCKETS = ("all", "top_tercile")
ENTRIES = ("09:35", "10:00")
EXIT = "15:55"
SERIES = ("NEWS", "CONTROL")

COST_BASE = 0.0010
COST_STRESS = 0.0020
COST_STRESS_TOPVOL = 0.0040

MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20e6
LOOKBACK = 20
MAX_TAGS = 3
MIN_EVENT_DAYS = 100

DISCOVERY = (date(2016, 1, 1), date(2022, 12, 31))
VALIDATION = (date(2023, 1, 1), date(2025, 6, 30))
RESERVED_FROM = date(2025, 7, 1)   # untouched; nothing in this campaign reads it

# NYSE 13:00 ET early closes inside the study. Alpaca SIP minute bars include
# extended-hours trades, so a 15:30-15:54 bar exists on these days too; they
# are dropped by date, not by bar presence.
EARLY_CLOSES = frozenset({
    date(2016, 11, 25), date(2017, 7, 3), date(2017, 11, 24), date(2018, 7, 3), date(2018, 11, 23),
    date(2018, 12, 24), date(2019, 7, 3), date(2019, 11, 29), date(2019, 12, 24), date(2020, 11, 27),
    date(2020, 12, 24), date(2021, 11, 26), date(2022, 11, 25), date(2023, 7, 3), date(2023, 11, 24),
    date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24),
})

NEWS_OPEN = time(16, 0)    # exclusive, on prev(D)
NEWS_CLOSE = time(9, 25)   # inclusive, on D
SESSION_OPEN = time(9, 30)
EXIT_WINDOW_START = time(15, 30)

PART_B_THRESHOLD = 0.02
PART_B_ENTRY = "09:35"
PART_B_CAP = 15000
PART_B_MAX_HEADLINES = 5
PART_B_CUT = 0.5


# ---------------------------------------------------------------- calendar ---

def stage_of(day: date) -> str | None:
    if DISCOVERY[0] <= day <= DISCOVERY[1]:
        return "discovery"
    if VALIDATION[0] <= day <= VALIDATION[1]:
        return "validation"
    return None


def news_window_utc(prev_session: date, session: date) -> tuple[datetime, datetime]:
    """(start exclusive, end inclusive) of the qualifying-news window, in UTC."""
    start = datetime.combine(prev_session, NEWS_OPEN, ET)
    end = datetime.combine(session, NEWS_CLOSE, ET)
    return start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC"))


# ------------------------------------------------------------------- news ---

def _ticker(value: str) -> bool:
    return bool(value) and value.replace(".", "").isalpha() and value.upper() == value


@dataclass(frozen=True)
class Alias:
    old: str
    new: str
    until: date        # the old ticker means ``new`` for news created before this date


@dataclass(frozen=True)
class TickerMap:
    aliases: Mapping[str, tuple[Alias, ...]]   # old ticker -> the universe symbols it meant
    since: Mapping[str, date]                  # universe symbol -> first day the ticker meant it


def ticker_map(name_changes: Iterable[Mapping[str, str]], universe: Iterable[str]) -> TickerMap:
    """Map historical tickers to current universe symbols through the
    name-change chain. A current ticker counts only from the day it was
    assigned; an old one only before it was retired."""
    earliest: dict[tuple[str, str], date] = {}
    for item in name_changes:
        old, new = item.get("old_symbol", ""), item.get("new_symbol", "")
        if not _ticker(old) or not _ticker(new) or old == new:
            continue
        processed = date.fromisoformat(item["process_date"])
        earliest[(old, new)] = min(processed, earliest.get((old, new), date.max))   # duplicate records
    by_new: dict[str, list[tuple[str, date]]] = {}
    for (old, new), processed in sorted(earliest.items()):
        by_new.setdefault(new, []).append((old, processed))
    aliases: dict[str, list[Alias]] = {}
    since: dict[str, date] = {}
    for symbol in universe:
        if symbol in by_new:
            since[symbol] = max(processed for _, processed in by_new[symbol])
        frontier = [(symbol, date.max)]
        seen = {symbol}
        while frontier:
            current, bound = frontier.pop()
            for old, processed in by_new.get(current, []):
                # Only renames strictly before ``current`` was retired: a recycled
                # ticker's later history belongs to a different company.
                if old in seen or processed >= bound:
                    continue
                seen.add(old)
                until = min(processed, bound)
                aliases.setdefault(old, []).append(Alias(old, symbol, until))
                frontier.append((old, until))
    return TickerMap({k: tuple(v) for k, v in aliases.items()}, since)


def same_security_duplicates(tickers: TickerMap, universe: Iterable[str], after: date) -> set[str]:
    """Universe tickers that are an older name of another universe ticker and were
    retired only after ``after``: the same security listed twice (SATS/ECHO).
    The older name is dropped."""
    members = set(universe)
    return {alias.old for aliases in tickers.aliases.values() for alias in aliases
            if alias.old in members and alias.new in members and alias.until > after}


def tagged_universe_symbols(tags: Sequence[str], created: datetime, universe: set[str],
                            tickers: TickerMap) -> set[str]:
    """Universe symbols an article tags, each ticker read as of the article's ET date."""
    hits: set[str] = set()
    day = created.astimezone(ET).date()
    for tag in tags:
        if tag in universe and day >= tickers.since.get(tag, date.min):
            hits.add(tag)
        for alias in tickers.aliases.get(tag, ()):
            if day < alias.until:
                hits.add(alias.new)
    return hits


def qualifies(tags: Sequence[str]) -> bool:
    return 0 < len(tags) <= MAX_TAGS


# ----------------------------------------------------------------- prices ---

def _minute(hhmm: str) -> time:
    hour, minute = hhmm.split(":")
    return time(int(hour), int(minute))


def price_at(bars: Sequence[tuple[time, float]], ending: str, window_start: time) -> tuple[float, time] | None:
    """Close of the bar ending at ``ending`` (stamped one minute earlier), else the
    latest earlier bar stamped at or after ``window_start``. ``bars`` are
    (ET start-of-minute, close) for one symbol and session."""
    end = _minute(ending)
    last_stamp = (datetime.combine(date(2000, 1, 3), end) - timedelta(minutes=1)).time()
    best: tuple[float, time] | None = None
    for stamp, close in bars:
        if window_start <= stamp <= last_stamp and (best is None or stamp > best[1]):
            if math.isfinite(close) and close > 0:
                best = (close, stamp)
    return best


def entry_price(bars: Sequence[tuple[time, float]], entry: str) -> tuple[float, time] | None:
    return price_at(bars, entry, SESSION_OPEN)


def exit_price(bars: Sequence[tuple[time, float]]) -> tuple[float, time] | None:
    return price_at(bars, EXIT, EXIT_WINDOW_START)


def fade_returns(gap: float, entry: float, exit_: float, spy_entry: float, spy_exit: float) -> tuple[float, float]:
    """(raw, market-adjusted) gross fade return of one event."""
    side = -1.0 if gap > 0 else 1.0
    r_stock = exit_ / entry - 1.0
    r_spy = spy_exit / spy_entry - 1.0
    return side * r_stock, side * (r_stock - r_spy)


# --------------------------------------------------------- daily portfolio ---

def daily_portfolio(events: Iterable[tuple[date, float]]) -> dict[date, float]:
    """Equal-weight mean per session: many events on one day count once."""
    sums: dict[date, list[float]] = {}
    for day, value in events:
        sums.setdefault(day, []).append(value)
    return {day: float(np.mean(values)) for day, values in sorted(sums.items())}


def _seed(label: str) -> int:
    digest = hashlib.sha256(f"{SEED}\x1f{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _week(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso.year, iso.week


def _week_groups(series: Mapping[date, float]) -> dict[tuple[int, int], tuple[float, int]]:
    groups: dict[tuple[int, int], list[float]] = {}
    for day, value in series.items():
        groups.setdefault(_week(day), []).append(value)
    return {week: (float(sum(values)), len(values)) for week, values in groups.items()}


def week_bootstrap_lower(series: Mapping[date, float], label: str) -> float:
    """5th percentile of the mean under a week-block bootstrap."""
    groups = _week_groups(series)
    if not groups:
        return float("nan")
    sums = np.array([value[0] for value in groups.values()])
    counts = np.array([value[1] for value in groups.values()])
    rng = np.random.default_rng(_seed(label))
    draws = rng.integers(0, len(groups), size=(RESAMPLES, len(groups)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return float(np.quantile(means, ALPHA))


def week_bootstrap_difference_lower(first: Mapping[date, float], second: Mapping[date, float],
                                    label: str) -> tuple[float, int]:
    """5th percentile of mean(first) - mean(second), weeks drawn jointly.

    Returns the bound and the number of resamples discarded because a drawn set
    of weeks held no session of one series."""
    weeks = sorted(set(_week_groups(first)) | set(_week_groups(second)))
    if not weeks:
        return float("nan"), RESAMPLES
    a, b = _week_groups(first), _week_groups(second)
    a_sum = np.array([a.get(w, (0.0, 0))[0] for w in weeks])
    a_cnt = np.array([a.get(w, (0.0, 0))[1] for w in weeks])
    b_sum = np.array([b.get(w, (0.0, 0))[0] for w in weeks])
    b_cnt = np.array([b.get(w, (0.0, 0))[1] for w in weeks])
    rng = np.random.default_rng(_seed(label))
    draws = rng.integers(0, len(weeks), size=(RESAMPLES, len(weeks)))
    ac, bc = a_cnt[draws].sum(axis=1), b_cnt[draws].sum(axis=1)
    ok = (ac > 0) & (bc > 0)
    diffs = a_sum[draws].sum(axis=1)[ok] / ac[ok] - b_sum[draws].sum(axis=1)[ok] / bc[ok]
    if diffs.size == 0:
        return float("nan"), RESAMPLES
    return float(np.quantile(diffs, ALPHA)), int((~ok).sum())


def halves(series: Mapping[date, float]) -> tuple[float, float]:
    """Means of the chronological first and second halves of the event-sessions."""
    days = sorted(series)
    cut = len(days) // 2
    first = [series[d] for d in days[:cut]]
    second = [series[d] for d in days[cut:]]
    return (float(np.mean(first)) if first else float("nan"),
            float(np.mean(second)) if second else float("nan"))


def difference_halves(first: Mapping[date, float], second: Mapping[date, float]) -> tuple[float, float]:
    """mean(first) - mean(second) in each chronological half of the union of sessions."""
    days = sorted(set(first) | set(second))
    if not days:
        return float("nan"), float("nan")
    boundary = days[len(days) // 2]
    out = []
    for keep in (lambda d: d < boundary, lambda d: d >= boundary):
        a = [v for d, v in first.items() if keep(d)]
        b = [v for d, v in second.items() if keep(d)]
        out.append(float(np.mean(a) - np.mean(b)) if a and b else float("nan"))
    return out[0], out[1]


def shift(series: Mapping[date, float], amount: float) -> dict[date, float]:
    return {day: value - amount for day, value in series.items()}


# ------------------------------------------------------------------ gates ---

@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    value: float | None
    detail: str


@dataclass
class CellReport:
    cell: str
    event_days: int
    events: int
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def failed(self) -> list[str]:
        return [g.name for g in self.gates if not g.passed]


def _positive(name: str, value: float, detail: str) -> Gate:
    return Gate(name, bool(math.isfinite(value) and value > 0), value, detail)


def costs_for(vol_bucket: str) -> tuple[float, ...]:
    """Stress costs that are gates for a cell (base is applied separately)."""
    return (COST_STRESS, COST_STRESS_TOPVOL) if vol_bucket == "top_tercile" else (COST_STRESS,)


def discovery_gates(cell: str, vol_bucket: str, news_gross: Mapping[date, float],
                    control_gross: Mapping[date, float] | None, events: int) -> CellReport:
    """Every discovery gate for one NEWS cell; ``*_gross`` are market-adjusted
    gross daily portfolios. All gates must pass."""
    report = CellReport(cell, len(news_gross), events)
    report.gates.append(Gate("min_event_days", len(news_gross) >= MIN_EVENT_DAYS,
                             float(len(news_gross)), f">= {MIN_EVENT_DAYS} event-sessions"))
    if len(news_gross) < 2:
        return report
    base = shift(news_gross, COST_BASE)
    first, second = halves(base)
    report.gates.append(_positive("first_half_positive", first, "first half of discovery, base cost"))
    report.gates.append(_positive("second_half_positive", second, "second half of discovery, base cost"))
    report.gates.append(_positive("base_cost_positive", float(np.mean(list(base.values()))), "10 bps"))
    for cost in costs_for(vol_bucket):
        stressed = float(np.mean(list(shift(news_gross, cost).values())))
        report.gates.append(_positive(f"stress_{round(cost * 1e4)}bps_positive", stressed,
                                      f"{round(cost * 1e4)} bps round trip"))
    values = list(base.values())
    by_abs = int(np.argmax(np.abs(values)))
    report.gates.append(_positive("drop_largest_abs_day_positive",
                                  float(np.mean(values[:by_abs] + values[by_abs + 1:])),
                                  "base-cost mean without the largest-|contribution| session"))
    by_max = int(np.argmax(values))
    report.gates.append(_positive("drop_largest_positive_day_positive",
                                  float(np.mean(values[:by_max] + values[by_max + 1:])),
                                  "base-cost mean without the single best session"))
    report.gates.append(_positive("week_bootstrap_lb_positive", week_bootstrap_lower(base, f"{cell}:lb"),
                                  f"5th pct, ISO-week blocks, {RESAMPLES} resamples, base cost"))
    if control_gross is None:
        return report        # a CONTROL cell, described with gates 1-5 only
    if control_gross:
        diff_lb, discarded = week_bootstrap_difference_lower(news_gross, control_gross, f"{cell}:news-control")
        report.gates.append(_positive("news_beats_control_lb_positive", diff_lb,
                                      f"5th pct of NEWS - CONTROL daily means ({discarded} draws discarded)"))
    else:
        report.gates.append(Gate("news_beats_control_lb_positive", False, None, "no CONTROL sessions"))
    return report


def validation_gates(cell: str, vol_bucket: str, news_gross: Mapping[date, float], events: int) -> CellReport:
    """One shot: net mean at every stress cost positive and both halves positive at stress."""
    report = CellReport(cell, len(news_gross), events)
    if not news_gross:
        report.gates.append(Gate("has_sessions", False, 0.0, "no validation sessions"))
        return report
    for cost in costs_for(vol_bucket):
        stressed = shift(news_gross, cost)
        tag = f"{round(cost * 1e4)}bps"
        report.gates.append(_positive(f"stress_{tag}_positive", float(np.mean(list(stressed.values()))),
                                      f"validation net mean at {tag}"))
        first, second = halves(stressed)
        report.gates.append(_positive(f"stress_{tag}_first_half_positive", first, "validation first half"))
        report.gates.append(_positive(f"stress_{tag}_second_half_positive", second, "validation second half"))
    return report


def part_b_discovery(justified: Mapping[date, float], unjustified: Mapping[date, float]) -> CellReport:
    """The single pre-registered Part B comparison, base-cost market-adjusted net
    (costs cancel in the difference)."""
    report = CellReport("partB:justified-minus-unjustified", len(set(justified) | set(unjustified)), 0)
    report.gates.append(Gate("min_event_days_each", min(len(justified), len(unjustified)) >= MIN_EVENT_DAYS,
                             float(min(len(justified), len(unjustified))),
                             f">= {MIN_EVENT_DAYS} event-sessions in each group"))
    lb, discarded = week_bootstrap_difference_lower(justified, unjustified, "partB:discovery")
    report.gates.append(_positive("difference_lb_positive", lb,
                                  f"5th pct of J - U daily means ({discarded} draws discarded)"))
    return report


def part_b_validation(justified: Mapping[date, float], unjustified: Mapping[date, float]) -> CellReport:
    report = CellReport("partB:validation", len(set(justified) | set(unjustified)), 0)
    if not justified or not unjustified:
        report.gates.append(Gate("has_both_groups", False, None, "a group is empty"))
        return report
    diff = float(np.mean(list(justified.values())) - np.mean(list(unjustified.values())))
    report.gates.append(_positive("difference_positive", diff, "J - U daily means, base cost"))
    first, second = difference_halves(justified, unjustified)
    report.gates.append(_positive("first_half_positive", first, "validation first half"))
    report.gates.append(_positive("second_half_positive", second, "validation second half"))
    return report


# ----------------------------------------------------------------- Part B ---

SYSTEM_PROMPT = "You are an equity news analyst. You answer only with JSON."

USER_TEMPLATE = (
    "Stock ticker: {symbol}\n"
    "These headlines about the company were published after the previous close "
    "and before today's open:\n"
    "{headlines}\n\n"
    "At today's open the stock gapped {direction} {size:.1f}% versus the previous close.\n\n"
    "Question: Does this news justify a move of this size in this direction?\n"
    'Reply with JSON {{"justified": p}}, where p is your probability from 0 to 1 '
    "that the news justifies the move."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"justified": {"type": "number", "minimum": 0, "maximum": 1}},
    "required": ["justified"],
    "additionalProperties": False,
}


def part_b_messages(symbol: str, headlines: Sequence[str], gap: float) -> list[dict[str, str]]:
    """Exact request messages. ``headlines`` are already the at-most-5 most recent."""
    body = "\n".join(f"- {h.strip()}" for h in headlines)
    user = USER_TEMPLATE.format(symbol=symbol, headlines=body,
                                direction="UP" if gap > 0 else "DOWN", size=abs(gap) * 100)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def part_b_sample(keys_by_year: Mapping[int, Sequence[str]], cap: int = PART_B_CAP, label: str = "") -> list[str]:
    """Stratified by year: each year keeps round(cap * share) keys (largest
    remainders settle the total), drawn without replacement with a fixed seed.
    Everything is kept when the population is within the cap."""
    total = sum(len(v) for v in keys_by_year.values())
    years = sorted(keys_by_year)
    if total <= cap:
        return sorted(k for y in years for k in keys_by_year[y])
    exact = {y: cap * len(keys_by_year[y]) / total for y in years}
    quota = {y: int(math.floor(exact[y])) for y in years}
    leftover = cap - sum(quota.values())
    for y in sorted(years, key=lambda y: (-(exact[y] - quota[y]), y))[:leftover]:
        quota[y] += 1
    out: list[str] = []
    for y in years:
        pool = sorted(keys_by_year[y])
        rng = np.random.default_rng(_seed(f"partB:sample:{label}:{y}"))
        idx = rng.choice(len(pool), size=quota[y], replace=False)
        out.extend(pool[i] for i in sorted(idx))
    return sorted(out)
