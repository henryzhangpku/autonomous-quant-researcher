"""SEC XBRL adapter: fair-access transport, cache reproducibility, parsing."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from research.outcomes import fundamentals as F
from research.providers.contracts import CapabilityProbeError
from research.providers.sec_xbrl import (
    SecXbrlClient,
    acceptance_utc,
    companyfacts_facts,
    instance_dimensional_facts,
)


class FakeHttp:
    def __init__(self, routes: dict[str, bytes]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        if url not in self.routes:
            raise OSError(f"404 {url}")
        return self.routes[url]


class Clock:
    def __init__(self):
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_user_agent_must_carry_a_contact_address(tmp_path):
    with pytest.raises(ValueError):
        SecXbrlClient(cache_root=tmp_path, user_agent="anonymous-bot")


def test_requests_identify_themselves_and_are_spaced(tmp_path):
    url_a = "https://data.sec.gov/a.json"
    url_b = "https://data.sec.gov/b.json"
    http = FakeHttp({url_a: b"{}", url_b: b"[]"})
    clock = Clock()
    client = SecXbrlClient(cache_root=tmp_path, http_get=http, sleep=clock.sleep,
                           monotonic=clock.monotonic)
    client.fetch(url_a)
    client.fetch(url_b)
    assert all("@" in headers["User-Agent"] for _, headers in http.calls)
    assert clock.slept == [pytest.approx(0.2)]   # 5 req/s, under SEC's 10 req/s
    assert 1 / client.min_interval_seconds < 10


def test_cache_is_read_back_without_network_and_offline_refuses_misses(tmp_path):
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"
    http = FakeHttp({url: b'{"facts": {}}'})
    first = SecXbrlClient(cache_root=tmp_path, http_get=http)
    assert first.fetch(url) == b'{"facts": {}}'
    offline = SecXbrlClient(cache_root=tmp_path, http_get=FakeHttp({}), offline=True)
    assert offline.fetch(url) == b'{"facts": {}}'
    assert offline.network_requests == 0
    assert offline.read_log[url] == first.read_log[url]
    with pytest.raises(CapabilityProbeError):
        offline.fetch("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000002.json")


def test_cache_path_cannot_escape_its_root(tmp_path):
    client = SecXbrlClient(cache_root=tmp_path)
    with pytest.raises(ValueError):
        client.cache_path("https://data.sec.gov/../../etc/passwd")
    with pytest.raises(ValueError):
        client.cache_path("http://data.sec.gov/x.json")


def test_companyfacts_rows_keep_filed_form_and_accession():
    payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"start": "2024-01-01", "end": "2024-03-31", "val": 5, "accn": "0001-24-1",
         "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-05-01"}]}}}}}
    (f,) = companyfacts_facts(payload, "Revenues")
    assert (f.start, f.end, f.value, f.filed, f.form, f.accession, f.duration_days) == (
        date(2024, 1, 1), date(2024, 3, 31), 5.0, date(2024, 5, 1), "10-Q", "0001-24-1", 91)
    assert companyfacts_facts(payload, "Missing") == []


INSTANCE = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:context id="dc"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">nvda:DataCenterMember</xbrldi:explicitMember></xbrli:segment>
    </xbrli:entity><xbrli:period><xbrli:startDate>2026-04-27</xbrli:startDate><xbrli:endDate>2026-07-26</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="dc_geo"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">nvda:DataCenterMember</xbrldi:explicitMember>
    <xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:US</xbrldi:explicitMember></xbrli:segment>
    </xbrli:entity><xbrli:period><xbrli:startDate>2026-04-27</xbrli:startDate><xbrli:endDate>2026-07-26</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="gaming"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">nvda:GamingMember</xbrldi:explicitMember></xbrli:segment>
    </xbrli:entity><xbrli:period><xbrli:startDate>2026-04-27</xbrli:startDate><xbrli:endDate>2026-07-26</xbrli:endDate></xbrli:period></xbrli:context>
  <us-gaap:Revenues contextRef="dc" unitRef="usd" decimals="-6">89023000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="dc" unitRef="usd" decimals="-6">89023000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="dc_geo" unitRef="usd" decimals="-6">40000000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="gaming" unitRef="usd" decimals="-6">4000000000</us-gaap:Revenues>
</xbrli:xbrl>"""


def test_dimensional_parser_takes_single_dimension_member_only():
    facts = instance_dimensional_facts(
        INSTANCE, concepts=("us-gaap:Revenues",), axis="srt:ProductOrServiceAxis",
        member="nvda:DataCenterMember", accession="acc", form="10-Q", filed=date(2026, 8, 26))
    assert [(f.start, f.end, f.value, f.dimension) for f in facts] == [
        (date(2026, 4, 27), date(2026, 7, 26), 89023000000.0,
         "srt:ProductOrServiceAxis=nvda:DataCenterMember")]


def test_acceptance_timestamp_is_parsed_as_utc():
    assert acceptance_utc("2026-08-26T20:36:00.000Z") == datetime(2026, 8, 26, 20, 36, tzinfo=timezone.utc)
    assert acceptance_utc("") is None


def _companyfacts(rows):
    return json.dumps({"facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}}).encode()


def _row(start, end, val, filed, form="10-Q", accn=None):
    return {"start": start, "end": end, "val": val, "accn": accn or f"acc-{filed}",
            "fy": 2024, "fp": "Q", "form": form, "filed": filed}


def test_rebuild_from_cache_is_byte_identical_and_offline(tmp_path):
    spec = F.SeriesSpec("NVDA", "revenue", ("Revenues",), ("Q",), "test")
    rows = [
        _row("2024-01-29", "2024-04-28", 26044, "2024-05-29"),
        _row("2024-01-29", "2024-07-28", 56084, "2024-08-28"),
        _row("2024-01-29", "2024-10-27", 91166, "2024-11-20"),
        _row("2024-01-29", "2025-01-26", 130497, "2025-02-26", form="10-K"),
    ]
    submissions = json.dumps({"filings": {"recent": {
        "accessionNumber": [r["accn"] for r in rows], "form": [r["form"] for r in rows],
        "filingDate": [r["filed"] for r in rows],
        "acceptanceDateTime": [f"{r['filed']}T21:00:00.000Z" for r in rows],
        "primaryDocument": ["x.htm"] * 4, "isXBRL": [1] * 4}, "files": []}}).encode()
    http = FakeHttp({
        SecXbrlClient.companyfacts_url(F.CIKS["NVDA"]): _companyfacts(rows),
        SecXbrlClient.submissions_url(F.CIKS["NVDA"]): submissions,
    })
    online = SecXbrlClient(cache_root=tmp_path, http_get=http, sleep=lambda s: None)
    first = F.build(online, series=(spec,), nvda_datacenter=False)
    offline = SecXbrlClient(cache_root=tmp_path, http_get=FakeHttp({}), offline=True)
    second = F.build(offline, series=(spec,), nvda_datacenter=False)
    assert offline.network_requests == 0
    for render in (F.facts_csv, F.pit_csv, F.growth_csv):
        assert render(first) == render(second)
    q4 = [v for v in second.vintages if v.method == "fy_minus_9m"]
    assert [(v.value, v.filed) for v in q4] == [(130497 - 91166, date(2025, 2, 26))]
    assert b"2025-02-26T21:00:00Z" in F.pit_csv(second)
