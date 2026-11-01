"""Build the point-in-time fundamentals tables from SEC XBRL.

What is built (frozen here, documented in ``docs/AS_OF_DISCIPLINE.md``):

* hyperscaler capex, quarterly — MSFT, AMZN, GOOGL, META, ORCL;
* total revenue, quarterly — NVDA, AMD, AVGO, CRWV;
* NVIDIA Data Center revenue, quarterly — a dimensional fact read from each
  10-Q/10-K instance (``srt:ProductOrServiceAxis = nvda:DataCenterMember``);
* Nebius (NBIS) revenue, annual only — it files 20-F annually and furnishes
  quarterly results on 6-K without XBRL, so no quarterly XBRL exists.

Each concept is pinned to the XBRL tag the company actually uses for it; the
tag is recorded on every row. No value is estimated, scraped or filled.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from research.providers.sec_xbrl import (
    Fact,
    SecXbrlClient,
    acceptance_utc,
    companyfacts_facts,
    instance_dimensional_facts,
)

from .pit import GrowthRow, Vintage, annual_vintages, growth_rows, quarterly_vintages

CIKS = {
    "MSFT": 789019, "AMZN": 1018724, "GOOGL": 1652044, "META": 1326801, "ORCL": 1341439,
    "NVDA": 1045810, "AMD": 2488, "AVGO": 1730168, "CRWV": 1769628, "NBIS": 1513845,
}
# Periods ending before this are not needed: 2023 YoY acceleration needs 2021
# comparatives at most.
EARLIEST_PERIOD_END = date(2020, 10, 1)


@dataclass(frozen=True)
class SeriesSpec:
    ticker: str
    concept: str
    tags: tuple[str, ...]          # first tag with any data for a period wins
    period_types: tuple[str, ...]  # "Q", "FY"
    note: str


SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("MSFT", "capex", ("PaymentsToAcquirePropertyPlantAndEquipment",), ("Q",),
               "Additions to property and equipment (cash); fiscal year ends June; excludes finance leases"),
    SeriesSpec("AMZN", "capex", ("PaymentsToAcquireProductiveAssets",), ("Q",),
               "Purchases of property and equipment (cash, gross of proceeds/incentives); excludes finance leases"),
    SeriesSpec("GOOGL", "capex", ("PaymentsToAcquirePropertyPlantAndEquipment",), ("Q",),
               "Purchases of property and equipment (cash)"),
    SeriesSpec("META", "capex", ("PaymentsToAcquirePropertyPlantAndEquipment",), ("Q",),
               "Purchases of property and equipment (cash); excludes finance lease principal"),
    SeriesSpec("ORCL", "capex", ("PaymentsToAcquirePropertyPlantAndEquipment",), ("Q",),
               "Capital expenditures (cash); fiscal year ends May"),
    SeriesSpec("NVDA", "revenue", ("Revenues",), ("Q",),
               "Total revenue; 52/53-week fiscal year ending late January"),
    SeriesSpec("AMD", "revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax",), ("Q",),
               "Net revenue; 52/53-week fiscal year ending late December"),
    SeriesSpec("AVGO", "revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax",), ("Q",),
               "Net revenue; 52/53-week fiscal year ending around 1 November"),
    SeriesSpec("CRWV", "revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax",), ("Q",),
               "Revenue; public since March 2025, so XBRL starts with the first 10-Q (May 2025)"),
    SeriesSpec("NBIS", "revenue", ("Revenues",), ("FY",),
               "Annual 20-F only; quarterly results are furnished on 6-K without XBRL. The FY2025 20-F "
               "restates prior years to continuing operations after the Yandex divestiture"),
)

NVDA_DATACENTER = SeriesSpec(
    "NVDA", "datacenter_revenue", ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"), ("Q",),
    "Revenue on srt:ProductOrServiceAxis = nvda:DataCenterMember (market-platform disclosure), read "
    "from each 10-Q/10-K XBRL instance; single-dimension contexts only",
)
NVDA_DC_AXIS = "srt:ProductOrServiceAxis"
NVDA_DC_MEMBER = "nvda:DataCenterMember"
NVDA_DC_FIRST_FILED = date(2021, 1, 1)
PERIODIC_FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A")


@dataclass
class FundamentalsBuild:
    facts: list[tuple[str, str, Fact]]          # (ticker, concept, fact)
    vintages: list[Vintage]
    growth: list[GrowthRow]
    accepted: dict[str, str]                    # accession -> acceptance UTC ISO
    instance_failures: list[str]


def _select_tagged(facts_by_tag: Sequence[list[Fact]]) -> list[Fact]:
    """Within each filing, per period, the facts of the first listed tag that reports it.

    The choice is per filing, not per period: a filer that moved from one
    equivalent tag to another (NVIDIA: RevenueFromContractWithCustomer... to
    Revenues) keeps its earlier filings visible under the older tag.
    """
    chosen: dict[tuple, str] = {}
    out: list[Fact] = []
    for facts in facts_by_tag:
        for fact in facts:
            key = (fact.start, fact.end, fact.accession)
            owner = chosen.setdefault(key, fact.tag)
            if owner == fact.tag:
                out.append(fact)
    return out


def _keep(fact: Fact) -> bool:
    return fact.start is not None and fact.end >= EARLIEST_PERIOD_END


def nvda_datacenter_facts(client: SecXbrlClient, filings: Iterable[dict[str, Any]]
                          ) -> tuple[list[Fact], list[str]]:
    cik = CIKS["NVDA"]
    facts: list[Fact] = []
    failures: list[str] = []
    for filing in sorted(filings, key=lambda f: f["filingDate"]):
        if filing["form"] not in PERIODIC_FORMS:
            continue
        filed = date.fromisoformat(filing["filingDate"])
        if filed < NVDA_DC_FIRST_FILED:
            continue
        accession = filing["accessionNumber"]
        _, xml_bytes = client.filing_instance(cik, accession)
        found = instance_dimensional_facts(
            xml_bytes, concepts=("us-gaap:" + t for t in NVDA_DATACENTER.tags),
            axis=NVDA_DC_AXIS, member=NVDA_DC_MEMBER, accession=accession,
            form=filing["form"], filed=filed,
        )
        if not found:
            failures.append(f"{accession} ({filing['form']} filed {filed}): no {NVDA_DC_MEMBER} revenue")
        facts.extend(found)
    by_tag = [[f for f in facts if f.tag == tag] for tag in NVDA_DATACENTER.tags]
    return [f for f in _select_tagged(by_tag) if _keep(f)], failures


def build(client: SecXbrlClient, *, series: Sequence[SeriesSpec] = SERIES,
          nvda_datacenter: bool = True) -> FundamentalsBuild:
    all_facts: list[tuple[str, str, Fact]] = []
    vintages: list[Vintage] = []
    accepted: dict[str, str] = {}
    filings_by_ticker: dict[str, list[dict[str, Any]]] = {}
    tickers = {spec.ticker for spec in series} | ({"NVDA"} if nvda_datacenter else set())
    for ticker in sorted(tickers):
        filings_by_ticker[ticker] = client.filings(CIKS[ticker])
        for row in filings_by_ticker[ticker]:
            stamp = acceptance_utc(row.get("acceptanceDateTime"))
            if stamp is not None:
                accepted[row["accessionNumber"]] = stamp.isoformat().replace("+00:00", "Z")
    for spec in series:
        payload = client.companyfacts(CIKS[spec.ticker])
        selected = [f for f in _select_tagged([companyfacts_facts(payload, t) for t in spec.tags])
                    if _keep(f)]
        all_facts.extend((spec.ticker, spec.concept, f) for f in selected)
        if "Q" in spec.period_types:
            vintages.extend(quarterly_vintages(spec.ticker, spec.concept, selected))
        if "FY" in spec.period_types:
            vintages.extend(annual_vintages(spec.ticker, spec.concept, selected))
    failures: list[str] = []
    if nvda_datacenter:
        dc_facts, failures = nvda_datacenter_facts(client, filings_by_ticker["NVDA"])
        all_facts.extend(("NVDA", NVDA_DATACENTER.concept, f) for f in dc_facts)
        vintages.extend(quarterly_vintages("NVDA", NVDA_DATACENTER.concept, dc_facts))
    vintages.sort(key=lambda v: (v.ticker, v.concept, v.period_type, v.period_end, v.filed))
    return FundamentalsBuild(all_facts, vintages, growth_rows(vintages), accepted, failures)


# -- serialisation -------------------------------------------------------

FACT_COLUMNS = ("ticker", "concept", "tag", "dimension", "period_start", "period_end",
                "duration_days", "filed", "accepted_utc", "value", "unit", "form", "accession",
                "filing_fy", "filing_fp")
PIT_COLUMNS = ("ticker", "concept", "period_type", "period_start", "period_end", "calendar_quarter",
               "fiscal_year", "fiscal_quarter", "filed", "accepted_utc", "value", "form",
               "accession", "method", "tag", "inputs", "vintage", "confirmed_by", "qa_flags")
GROWTH_COLUMNS = ("ticker", "concept", "period_type", "period_start", "period_end",
                  "calendar_quarter", "filed", "accepted_utc", "accession", "value",
                  "prior_quarter_value", "prior_year_value", "qoq", "yoy", "yoy_prev",
                  "yoy_accel", "yoy_same_filing", "inputs_max_filed")


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(round(value, 10))
    if isinstance(value, (tuple, list)):
        return ";".join(str(v) for v in value)
    return str(value)


def _csv_bytes(columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_fmt(row.get(c)) for c in columns])
    return buffer.getvalue().encode("utf-8")


def facts_csv(result: FundamentalsBuild) -> bytes:
    rows = []
    for ticker, concept, f in sorted(result.facts, key=lambda x: (x[0], x[1], x[2].end, x[2].start,
                                                                     x[2].filed, x[2].accession)):
        rows.append({"ticker": ticker, "concept": concept, "tag": f.tag, "dimension": f.dimension,
                     "period_start": f.start, "period_end": f.end, "duration_days": f.duration_days,
                     "filed": f.filed, "accepted_utc": result.accepted.get(f.accession),
                     "value": f.value, "unit": f.unit, "form": f.form, "accession": f.accession,
                     "filing_fy": f.fy, "filing_fp": f.fp})
    return _csv_bytes(FACT_COLUMNS, rows)


def pit_csv(result: FundamentalsBuild) -> bytes:
    rows = [{**{c: getattr(v, c) for c in PIT_COLUMNS if hasattr(v, c)},
             "calendar_quarter": v.calendar_quarter,
             "accepted_utc": result.accepted.get(v.accession)} for v in result.vintages]
    return _csv_bytes(PIT_COLUMNS, rows)


def growth_csv(result: FundamentalsBuild) -> bytes:
    rows = [{**{c: getattr(g, c) for c in GROWTH_COLUMNS if hasattr(g, c)},
             "accepted_utc": result.accepted.get(g.accession)} for g in result.growth]
    return _csv_bytes(GROWTH_COLUMNS, rows)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def coverage(result: FundamentalsBuild) -> list[dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for v in result.vintages:
        key = (v.ticker, v.concept, v.period_type)
        entry = out.setdefault(key, {"ticker": v.ticker, "concept": v.concept,
                                     "period_type": v.period_type, "periods": set(),
                                     "first_period_end": v.period_end, "last_period_end": v.period_end,
                                     "first_filed": v.filed, "last_filed": v.filed,
                                     "vintages": 0, "qa_flagged": 0, "methods": set()})
        entry["periods"].add(v.period_end)
        entry["first_period_end"] = min(entry["first_period_end"], v.period_end)
        entry["last_period_end"] = max(entry["last_period_end"], v.period_end)
        entry["first_filed"] = min(entry["first_filed"], v.filed)
        entry["last_filed"] = max(entry["last_filed"], v.filed)
        entry["vintages"] += 1
        entry["qa_flagged"] += bool(v.qa_flags)
        entry["methods"].add(v.method)
    rows = []
    for key in sorted(out):
        e = out[key]
        rows.append({**e, "periods": len(e["periods"]), "methods": sorted(e["methods"]),
                     **{k: e[k].isoformat() for k in ("first_period_end", "last_period_end",
                                                       "first_filed", "last_filed")}})
    return rows
