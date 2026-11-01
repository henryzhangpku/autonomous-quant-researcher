"""Freeze the point-in-time outcome datasets for the gpu-leads-revisions test.

Outcome side only: SEC XBRL fundamentals (hyperscaler capex, supplier and
neocloud revenue, NVIDIA Data Center revenue) stamped with their filing
dates, and daily split/dividend-adjusted prices from Alpaca. The GPU price
signal and the campaign itself live elsewhere.

Outputs (``--out``, default ``research/data/gpu_outcomes``):

* ``sec_facts.csv`` — every XBRL fact used, as reported, one row per filing;
* ``fundamentals_pit.csv`` — quarterly (and NBIS annual) values, one row per
  vintage, with method (direct / ytd_diff / fy_minus_9m / annual);
* ``fundamentals_growth.csv`` — QoQ, YoY and YoY acceleration, recomputed on
  every filing date that changed an input;
* ``fundamentals_manifest.json`` and ``prices_manifest.json`` — hashes,
  provenance, coverage, gaps and limitations;
* ``prices_daily.csv`` — host-local (git-ignored); its hash is in the manifest.

Raw SEC responses and Alpaca snapshots are cached under ``--cache`` (default
``.research/outcomes-cache``). ``--offline`` rebuilds from the cache only;
the same cache always produces byte-identical tables.

Alpaca keys are read from ALPACA_API_KEY / ALPACA_API_SECRET and never
printed or written.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from research.outcomes import fundamentals as F
from research.outcomes import prices as P
from research.providers.sec_xbrl import DEFAULT_USER_AGENT, SecXbrlClient

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPOSITORY_ROOT / "research" / "data" / "gpu_outcomes"
DEFAULT_CACHE = REPOSITORY_ROOT / ".research" / "outcomes-cache"

FUNDAMENTALS_LIMITATIONS = [
    "as-of date is the 10-Q/10-K filing date; earnings press releases (8-K exhibit 99, not "
    "XBRL-tagged for these figures) usually reach the market minutes to days earlier, so the "
    "series lag the information they carry; this is conservative, never look-ahead",
    "capex is cash paid for property and equipment from the cash-flow statement; it excludes "
    "assets acquired under finance leases, which are material for MSFT, AMZN and META",
    "fiscal calendars differ: MSFT FY ends June, ORCL May, NVDA late January, AVGO around "
    "1 November, AMD late December (52/53-week); compare by period dates or calendar_quarter "
    "(the calendar quarter containing the period midpoint), not by fiscal labels",
    "fourth quarters are FY total minus nine-month YTD (method fy_minus_9m); Q2/Q3 cash-flow "
    "quarters are YTD differences (ytd_diff); a derived quarter is re-read only when its "
    "longer leg is re-reported",
    "comparatives restated in later filings create new vintages (META capex, CRWV rounding to "
    "millions in 2026 filings); vintage 0 is the first report",
    "NBIS: annual 20-F only; the FY2025 20-F restates prior years to continuing operations "
    "after the Yandex divestiture, so FY2023/FY2024 values change by an order of magnitude "
    "between vintages",
    "CRWV: XBRL begins with its first 10-Q (filed 2025-05-15); 2024 quarters exist only as "
    "comparatives filed in 2025-2026",
    "NVDA Data Center revenue is the market-platform disclosure on srt:ProductOrServiceAxis, "
    "not an operating segment (NVDA's segments are Compute & Networking and Graphics)",
]

UNAVAILABLE = [
    {"series": "NBIS quarterly revenue", "reason": "Nebius furnishes quarterly results on 6-K "
     "without XBRL; only the annual 20-F is XBRL-tagged. Not scraped from press releases."},
    {"series": "CRWV pre-IPO quarters", "reason": "no XBRL before the first 10-Q; 2024 quarters "
     "appear only as comparatives filed from 2025-05-15"},
    {"series": "point-in-time consensus estimate revisions", "reason": "no free, licensed "
     "historical point-in-time source; see docs/AS_OF_DISCIPLINE.md"},
]


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)  # bytes, so Windows newline translation cannot change the hash
    return F.sha256(data)


def build_fundamentals(*, out: Path, cache: Path, offline: bool) -> dict[str, Any]:
    client = SecXbrlClient(cache_root=cache / "sec", offline=offline)
    result = F.build(client)
    files = {
        "sec_facts.csv": F.facts_csv(result),
        "fundamentals_pit.csv": F.pit_csv(result),
        "fundamentals_growth.csv": F.growth_csv(result),
    }
    hashes = {name: _write(out / name, data) for name, data in files.items()}
    manifest = {
        "provider": "sec-edgar-xbrl",
        "endpoints": ["data.sec.gov/api/xbrl/companyfacts", "data.sec.gov/submissions",
                      "www.sec.gov/Archives/edgar/data (NVDA 10-Q/10-K XBRL instances)"],
        "user_agent": DEFAULT_USER_AGENT,
        "as_of_field": "filed",
        "earliest_period_end": F.EARLIEST_PERIOD_END.isoformat(),
        "series": [{"ticker": s.ticker, "concept": s.concept, "tags": list(s.tags),
                    "period_types": list(s.period_types), "note": s.note}
                   for s in (*F.SERIES, F.NVDA_DATACENTER)],
        "ciks": F.CIKS,
        "data_sha256": hashes,
        "row_counts": {"sec_facts.csv": len(result.facts), "fundamentals_pit.csv": len(result.vintages),
                       "fundamentals_growth.csv": len(result.growth)},
        "coverage": F.coverage(result),
        "raw_sources_sha256": dict(sorted(client.read_log.items())),
        "network_requests_this_build": client.network_requests,
        "nvda_instance_parse_failures": result.instance_failures,
        "unavailable": UNAVAILABLE,
        "limitations": FUNDAMENTALS_LIMITATIONS,
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    (out / "fundamentals_manifest.json").write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def _alpaca_provider_for(adjustment: str):
    from alpaca.data.historical import StockHistoricalDataClient  # noqa: PLC0415

    from research.providers.alpaca import AlpacaHistoricalProvider  # noqa: PLC0415
    from research.providers.contracts import CapabilityProbeError  # noqa: PLC0415

    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise CapabilityProbeError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    return AlpacaHistoricalProvider(stock_client=StockHistoricalDataClient(key, secret),
                                    request_factory=P.request_factory(adjustment))


def build_prices(*, out: Path, cache: Path, start: date, end: date, offline: bool) -> dict[str, Any]:
    symbols = (*P.SYMBOLS, *P.BENCHMARKS)
    rows, audit = P.build(symbols, start, end, P.PriceCache(cache / "alpaca"),
                          None if offline else _alpaca_provider_for)
    data = P.prices_csv(rows)
    digest = _write(out / "prices_daily.csv", data)
    manifest = {
        "provider": "alpaca",
        "endpoint": "StockHistoricalDataClient.get_stock_bars",
        "timeframe": "1Day", "feed": P.FEED,
        "adjustment": "all (splits and dividends); raw fetched alongside for audit only",
        "requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "symbols": list(P.SYMBOLS), "benchmarks": list(P.BENCHMARKS),
        "data_file": "prices_daily.csv (host-local, git-ignored; rebuild with this script)",
        "data_sha256": digest, "row_count": len(rows), "bytes": len(data),
        "per_symbol": audit,
        "limitations": [
            "all-adjusted levels are rescaled backwards at every new dividend; use returns, "
            "not levels, and never treat an adjusted level as what was quoted that day",
            "CRWV listed 2025-03-28 and NBIS resumed trading 2024-10-21; earlier sessions "
            "are absent, not filled",
            "consolidated SIP daily bars; session close is the official consolidated close as "
            "Alpaca aggregates it",
        ],
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    (out / "prices_manifest.json").write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--start", type=date.fromisoformat, default=P.DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat,
                        default=date.today() - timedelta(days=1))
    parser.add_argument("--offline", action="store_true", help="cache only; no network")
    parser.add_argument("--only", choices=("fundamentals", "prices"))
    args = parser.parse_args(argv)
    summary: dict[str, Any] = {}
    if args.only in (None, "fundamentals"):
        m = build_fundamentals(out=args.out, cache=args.cache, offline=args.offline)
        summary["fundamentals"] = {"data_sha256": m["data_sha256"], "row_counts": m["row_counts"]}
    if args.only in (None, "prices"):
        m = build_prices(out=args.out, cache=args.cache, start=args.start, end=args.end,
                         offline=args.offline)
        summary["prices"] = {"data_sha256": m["data_sha256"], "row_count": m["row_count"],
                             "errors": {s: a["error"] for s, a in m["per_symbol"].items() if "error" in a}}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
