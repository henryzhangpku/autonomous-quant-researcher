"""Point-in-time fundamentals: as-of visibility, YTD-to-quarter derivation, growth."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from research.outcomes.pit import (
    FactStore,
    VintageSeries,
    annual_vintages,
    growth_rows,
    latest_as_of,
    quarterly_vintages,
)
from research.providers.sec_xbrl import Fact

D = date.fromisoformat


def fact(start, end, value, filed, *, form="10-Q", accn=None, tag="PaymentsToAcquirePropertyPlantAndEquipment"):
    return Fact(tag=tag, start=D(start), end=D(end), value=float(value), unit="USD",
                accession=accn or f"acc-{filed}", form=form, filed=D(filed))


def calendar_year_capex(year: int, q: tuple[float, float, float, float], filed: tuple[str, str, str, str],
                        *, prior=None):
    """Cash-flow style reporting: 3M, then 6M/9M year-to-date, then the FY total."""
    y = str(year)
    q1, q2, q3, q4 = q
    out = [
        fact(f"{y}-01-01", f"{y}-03-31", q1, filed[0]),
        fact(f"{y}-01-01", f"{y}-06-30", q1 + q2, filed[1]),
        fact(f"{y}-01-01", f"{y}-09-30", q1 + q2 + q3, filed[2]),
        fact(f"{y}-01-01", f"{y}-12-31", q1 + q2 + q3 + q4, filed[3], form="10-K"),
    ]
    return out


@pytest.fixture
def two_years():
    facts = calendar_year_capex(2023, (10, 12, 14, 20), ("2023-04-27", "2023-07-27", "2023-10-26", "2024-02-02"))
    facts += calendar_year_capex(2024, (15, 18, 21, 30), ("2024-04-25", "2024-07-25", "2024-10-31", "2025-01-30"))
    return facts


def by_period(vintages):
    return {(v.period_start, v.period_end): v for v in vintages}


# -- as-of visibility ----------------------------------------------------


def test_value_is_invisible_before_its_filed_date():
    store = FactStore([fact("2024-01-01", "2024-03-31", 100, "2024-05-01")])
    period = (D("2024-01-01"), D("2024-03-31"))
    assert store.as_of(period, D("2024-04-30")) is None
    assert store.as_of(period, D("2024-05-01")).value == 100


def test_restatement_does_not_rewrite_what_was_known_earlier():
    store = FactStore([
        fact("2024-01-01", "2024-03-31", 100, "2024-05-01", accn="orig"),
        fact("2024-01-01", "2024-03-31", 90, "2025-05-01", accn="restated"),
    ])
    period = (D("2024-01-01"), D("2024-03-31"))
    assert store.as_of(period, D("2025-04-30")).accession == "orig"
    assert store.as_of(period, D("2025-05-01")).accession == "restated"
    vintages = quarterly_vintages("X", "capex", store.reports(period))
    assert [(v.vintage, v.value, v.filed) for v in vintages] == [
        (0, 100.0, D("2024-05-01")), (1, 90.0, D("2025-05-01"))]
    series = VintageSeries(vintages)
    assert series.as_of(period, D("2024-04-30")) is None
    assert series.as_of(period, D("2024-12-31")).value == 100.0


def test_latest_as_of_ignores_rows_filed_later():
    vintages = quarterly_vintages("X", "capex", [
        fact("2024-01-01", "2024-03-31", 100, "2024-05-01"),
        fact("2024-01-01", "2024-03-31", 90, "2025-05-01"),
    ])
    assert latest_as_of(vintages, D("2024-04-30")) == []
    assert [v.value for v in latest_as_of(vintages, D("2025-01-01"))] == [100.0]
    assert [v.value for v in latest_as_of(vintages, D("2025-05-01"))] == [90.0]


# -- YTD-to-quarter derivation --------------------------------------------


def test_quarters_are_derived_from_year_to_date_and_fiscal_year_totals(two_years):
    rows = by_period(quarterly_vintages("X", "capex", two_years))
    q = {(s.isoformat(), e.isoformat()): (v.value, v.method, v.filed.isoformat(), v.fiscal_quarter)
         for (s, e), v in rows.items() if s.year == 2023}
    assert q == {
        ("2023-01-01", "2023-03-31"): (10.0, "direct", "2023-04-27", 1),
        ("2023-04-01", "2023-06-30"): (12.0, "ytd_diff", "2023-07-27", 2),
        ("2023-07-01", "2023-09-30"): (14.0, "ytd_diff", "2023-10-26", 3),
        ("2023-10-01", "2023-12-31"): (20.0, "fy_minus_9m", "2024-02-02", 4),
    }


def test_derived_quarter_is_stamped_with_its_later_leg_and_invisible_before(two_years):
    vintages = quarterly_vintages("X", "capex", two_years)
    q4 = by_period(vintages)[(D("2023-10-01"), D("2023-12-31"))]
    assert set(q4.inputs) == {"acc-2023-10-26", "acc-2024-02-02"}
    series = VintageSeries(vintages)
    assert series.as_of((q4.period_start, q4.period_end), D("2024-02-01")) is None
    assert series.as_of((q4.period_start, q4.period_end), D("2024-02-02")).value == 20.0


def test_restated_short_leg_alone_does_not_manufacture_a_quarter(two_years):
    # The 2024 Q3 10-Q restates the 2023 nine-month comparative (42 -> 40).
    # Pairing that with the ORIGINAL 2023 FY total would imply Q4 = 22, a
    # number no filing ever implied; the next 10-K re-reports FY 2023 too.
    facts = two_years + [
        fact("2023-01-01", "2023-09-30", 40, "2024-10-31", accn="q3-2024"),
        fact("2023-01-01", "2023-12-31", 61, "2025-01-30", form="10-K", accn="fy-2024"),
    ]
    q4 = [v for v in quarterly_vintages("X", "capex", facts)
          if v.period_start == D("2023-10-01")]
    assert [(v.value, v.filed.isoformat()) for v in q4] == [(20.0, "2024-02-02"), (21.0, "2025-01-30")]


def test_missing_year_to_date_step_leaves_a_gap_and_is_never_bridged():
    facts = [f for f in calendar_year_capex(2023, (10, 12, 14, 20),
                                            ("2023-04-27", "2023-07-27", "2023-10-26", "2024-02-02"))
             if f.end != D("2023-03-31")]
    rows = by_period(quarterly_vintages("X", "capex", facts))
    assert (D("2023-01-01"), D("2023-03-31")) not in rows
    assert (D("2023-04-01"), D("2023-06-30")) not in rows      # needs the missing 3M leg
    assert rows[(D("2023-07-01"), D("2023-09-30"))].value == 14.0
    assert rows[(D("2023-10-01"), D("2023-12-31"))].value == 20.0


def test_direct_quarter_wins_and_a_disagreeing_ytd_twin_is_flagged():
    facts = calendar_year_capex(2023, (10, 12, 14, 20), ("2023-04-27", "2023-07-27", "2023-10-26", "2024-02-02"))
    facts.append(fact("2023-04-01", "2023-06-30", 13, "2023-07-27"))
    q2 = by_period(quarterly_vintages("X", "capex", facts))[(D("2023-04-01"), D("2023-06-30"))]
    assert q2.method == "direct" and q2.value == 13.0
    assert q2.qa_flags and q2.qa_flags[0].startswith("direct_vs_ytd_diff_mismatch")


def test_trailing_twelve_months_in_a_10q_is_not_a_fiscal_year(two_years):
    # Amazon reports trailing-twelve-month cash flows in each 10-Q.
    facts = two_years + [fact("2023-07-01", "2024-06-30", 99, "2024-07-25", form="10-Q")]
    rows = by_period(quarterly_vintages("X", "capex", facts))
    q3 = rows[(D("2023-07-01"), D("2023-09-30"))]
    assert (q3.fiscal_year, q3.fiscal_quarter, q3.method) == (2023, 3, "ytd_diff")


def test_52_53_week_fiscal_year_is_labelled_by_its_end_year():
    # NVIDIA-style: FY2024 runs 2023-01-30 .. 2024-01-28.
    facts = [
        fact("2023-01-30", "2023-04-30", 7192, "2023-05-26", tag="Revenues"),
        fact("2023-01-30", "2023-07-30", 20699, "2023-08-28", tag="Revenues"),
        fact("2023-07-31", "2023-10-29", 18120, "2023-11-21", tag="Revenues"),
        fact("2023-01-30", "2023-10-29", 38819, "2023-11-21", tag="Revenues"),
        fact("2023-01-30", "2024-01-28", 60922, "2024-02-21", form="10-K", tag="Revenues"),
    ]
    rows = by_period(quarterly_vintages("NVDA", "revenue", facts))
    q4 = rows[(D("2023-10-30"), D("2024-01-28"))]
    assert (q4.value, q4.method, q4.fiscal_year, q4.fiscal_quarter, q4.calendar_quarter) == (
        22103.0, "fy_minus_9m", 2024, 4, "2023Q4")
    q2 = rows[(D("2023-05-01"), D("2023-07-30"))]
    assert (q2.value, q2.method) == (13507.0, "ytd_diff")


# -- growth ------------------------------------------------------------------


def test_growth_values_and_acceleration(two_years):
    rows = growth_rows(quarterly_vintages("X", "capex", two_years))
    latest = {r.period_end: r for r in latest_as_of(rows, D("2030-01-01"))}
    q4 = latest[D("2024-12-31")]
    assert q4.yoy == pytest.approx(30 / 20 - 1)
    assert q4.qoq == pytest.approx(30 / 21 - 1)
    assert q4.yoy_prev == pytest.approx(21 / 14 - 1)
    assert q4.yoy_accel == pytest.approx((30 / 20 - 1) - (21 / 14 - 1))
    assert q4.filed == D("2025-01-30")
    first = latest[D("2023-03-31")]
    assert first.yoy is None and first.qoq is None and first.yoy_accel is None


def test_growth_rows_never_use_a_vintage_filed_after_the_row(two_years):
    facts = two_years + [fact("2023-01-01", "2023-03-31", 9, "2024-04-25", accn="acc-2024-04-25")]
    vintages = quarterly_vintages("X", "capex", facts)
    for row in growth_rows(vintages):
        assert row.inputs_max_filed <= row.filed
        series = VintageSeries(vintages)
        assert series.as_of((row.period_start, row.period_end), row.filed).value == row.value


def test_growth_is_recomputed_when_the_comparison_period_is_restated(two_years):
    facts = two_years + [fact("2023-01-01", "2023-03-31", 12, "2024-06-01", accn="restate")]
    rows = [r for r in growth_rows(quarterly_vintages("X", "capex", facts))
            if r.period_end == D("2024-03-31")]
    assert [(r.filed.isoformat(), round(r.yoy, 6)) for r in rows] == [
        ("2024-04-25", round(15 / 10 - 1, 6)), ("2024-06-01", round(15 / 12 - 1, 6))]
    assert rows[1].yoy_same_filing is False


def test_annual_series_growth_flags_a_comparison_across_a_restatement():
    facts = [
        fact("2022-01-01", "2022-12-31", 7000, "2023-04-20", form="20-F", accn="a23", tag="Revenues"),
        fact("2023-01-01", "2023-12-31", 9000, "2024-04-26", form="20-F", accn="a24", tag="Revenues"),
        fact("2022-01-01", "2022-12-31", 7000, "2024-04-26", form="20-F", accn="a24", tag="Revenues"),
        fact("2023-01-01", "2023-12-31", 20, "2025-04-30", form="20-F", accn="a25", tag="Revenues"),
    ]
    rows = growth_rows(annual_vintages("NBIS", "revenue", facts))
    by_filed = {(r.period_end.year, r.filed.isoformat()): r for r in rows}
    assert by_filed[(2023, "2024-04-26")].yoy_same_filing is True
    assert by_filed[(2023, "2025-04-30")].yoy_same_filing is False


# -- the committed dataset ------------------------------------------------------

DATA = Path(__file__).resolve().parents[1] / "research" / "data" / "gpu_outcomes"


def _rows(name):
    with (DATA / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_committed_tables_match_their_manifest_hashes():
    manifest = json.loads((DATA / "fundamentals_manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["data_sha256"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest, name


def test_committed_pit_rows_are_never_dated_before_their_period_ends():
    rows = _rows("fundamentals_pit.csv")
    assert rows
    for row in rows:
        assert D(row["filed"]) > D(row["period_end"]), row
        assert float(row["value"]) > 0, row


def test_committed_vintages_are_strictly_ordered_by_filing_date():
    seen: dict[tuple, list[tuple[int, date]]] = {}
    for row in _rows("fundamentals_pit.csv"):
        key = (row["ticker"], row["concept"], row["period_type"], row["period_start"], row["period_end"])
        seen.setdefault(key, []).append((int(row["vintage"]), D(row["filed"])))
    for key, items in seen.items():
        items.sort()
        assert [i for i, _ in items] == list(range(len(items))), key
        assert all(a[1] < b[1] for a, b in zip(items, items[1:])), key


def test_committed_growth_rows_use_only_inputs_public_by_their_date():
    pit = _rows("fundamentals_pit.csv")
    filed_by_accession = {r["accession"]: D(r["filed"]) for r in pit}
    for row in _rows("fundamentals_growth.csv"):
        assert D(row["inputs_max_filed"]) <= D(row["filed"]), row
        assert filed_by_accession.get(row["accession"], D(row["filed"])) <= D(row["filed"])


def test_committed_quarters_reconcile_to_annual_totals_where_both_exist():
    facts = _rows("sec_facts.csv")
    pit = _rows("fundamentals_pit.csv")
    latest: dict[tuple, float] = {}
    for row in sorted(pit, key=lambda r: r["filed"]):
        if row["period_type"] == "Q":
            latest[(row["ticker"], row["concept"], row["period_start"], row["period_end"])] = float(row["value"])
    checked = 0
    for f in facts:
        if f["form"] != "10-K" or not 350 <= int(f["duration_days"] or 0) <= 380:
            continue
        start, end = D(f["period_start"]), D(f["period_end"])
        quarters = [v for (t, c, s, e), v in latest.items()
                    if t == f["ticker"] and c == f["concept"] and start <= D(s) and D(e) <= end]
        if len(quarters) == 4:
            checked += 1
            # Later comparatives can restate single quarters; stay within 2%.
            assert sum(quarters) == pytest.approx(float(f["value"]), rel=0.02), f
    assert checked >= 20
