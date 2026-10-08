"""SEC EDGAR XBRL adapter: company facts, filing lists and filing instances.

Free, keyless, read-only. SEC fair-access rules apply and are enforced here:
every request carries a descriptive User-Agent with a contact address, and
requests are spaced so this client stays well under the 10 requests/second
ceiling. Every raw response is cached on disk byte-for-byte, so a rebuild
from the cache is offline and reproducible, and the manifest can pin each
raw file by SHA-256.

The as-of date of every fact is its filing date (``filed``), never the
period end: a value is invisible before the day the filing that carried it
reached EDGAR. ``accepted_utc`` (the EDGAR acceptance timestamp) is attached
where the submissions index provides it, for callers that need intraday
precision.

Two sources are parsed:

* ``companyfacts`` (data.sec.gov) — every non-dimensional fact a company has
  tagged, with ``start``/``end``/``val``/``accn``/``form``/``filed``. It
  carries no segment or product dimensions.
* a filing's XBRL instance (``*_htm.xml`` in the EDGAR archive) — used only
  where a dimensional fact is needed (NVIDIA Data Center revenue is tagged
  on ``srt:ProductOrServiceAxis``), parsed with the standard library.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Iterable, Mapping

from .contracts import CapabilityProbeError

DEFAULT_USER_AGENT = "autonomous-quant-researcher research heng.henry.zhang@gmail.com"
DATA_HOST = "https://data.sec.gov"
ARCHIVE_HOST = "https://www.sec.gov"
# SEC allows 10 requests/second; stay at roughly half of that.
MIN_REQUEST_INTERVAL_SECONDS = 0.2

HttpGet = Callable[[str, Mapping[str, str], float], bytes]

_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"


@dataclass(frozen=True, slots=True)
class Fact:
    """One reported XBRL value as it appeared in one filing."""

    tag: str
    start: date | None          # None for instant facts
    end: date
    value: float
    unit: str
    accession: str
    form: str
    filed: date
    fy: int | None = None       # fiscal year/period of the FILING, not of the period
    fp: str | None = None
    dimension: str = ""         # "axis=member" for a dimensional fact, else ""

    @property
    def duration_days(self) -> int | None:
        return None if self.start is None else (self.end - self.start).days + 1


class SecXbrlClient:
    """Cache-first, rate-limited reader of SEC EDGAR JSON and XBRL files."""

    def __init__(
        self,
        *,
        cache_root: Path,
        user_agent: str = DEFAULT_USER_AGENT,
        http_get: HttpGet | None = None,
        timeout_seconds: float = 30.0,
        min_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        offline: bool = False,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC fair access requires a User-Agent with a contact e-mail")
        self.cache_root = Path(cache_root)
        self.user_agent = user_agent
        self.http_get = http_get or self._urlopen
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        self.offline = offline
        self.network_requests = 0
        self.read_log: dict[str, str] = {}   # url -> sha256 of the raw bytes used

    # -- transport -----------------------------------------------------

    def _urlopen(self, url: str, headers: Mapping[str, str], timeout: float) -> bytes:
        request = urllib.request.Request(url, headers=dict(headers))
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
            payload = response.read()
            if response.headers.get("Content-Encoding", "") == "gzip":
                payload = gzip.decompress(payload)
        return payload

    def cache_path(self, url: str) -> Path:
        match = re.fullmatch(r"https://([a-z.]+)/(.+)", url)
        if match is None:
            raise ValueError(f"not an https SEC url: {url}")
        relative = Path(match.group(1), *match.group(2).split("/"))
        if ".." in relative.parts:
            raise ValueError(f"refusing to cache outside the root: {url}")
        return self.cache_root / relative

    def fetch(self, url: str) -> bytes:
        path = self.cache_path(url)
        if path.exists():
            payload = path.read_bytes()
        else:
            if self.offline:
                raise CapabilityProbeError(f"offline and not cached: {url}")
            payload = self._network_get(url)
            _atomic_write(path, payload)
        self.read_log[url] = hashlib.sha256(payload).hexdigest()
        return payload

    def _network_get(self, url: str) -> bytes:
        if self._last_request is not None:
            wait = self.min_interval_seconds - (self._monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        try:
            payload = self.http_get(url, headers, self.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - urllib raises several types
            raise CapabilityProbeError(f"SEC request failed for {url}: {exc}") from exc
        finally:
            self._last_request = self._monotonic()
            self.network_requests += 1
        return payload

    def fetch_json(self, url: str) -> Any:
        return json.loads(self.fetch(url).decode("utf-8"))

    # -- endpoints -----------------------------------------------------

    @staticmethod
    def companyfacts_url(cik: int) -> str:
        return f"{DATA_HOST}/api/xbrl/companyfacts/CIK{cik:010d}.json"

    @staticmethod
    def submissions_url(cik: int, page: str | None = None) -> str:
        return f"{DATA_HOST}/submissions/{page or f'CIK{cik:010d}.json'}"

    @staticmethod
    def filing_index_url(cik: int, accession: str) -> str:
        return f"{ARCHIVE_HOST}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/index.json"

    def companyfacts(self, cik: int) -> Mapping[str, Any]:
        return self.fetch_json(self.companyfacts_url(cik))

    def filings(self, cik: int) -> list[dict[str, Any]]:
        """Every filing in the submissions index, including the paged history."""
        root = self.fetch_json(self.submissions_url(cik))
        tables = [root["filings"]["recent"]]
        for extra in root["filings"].get("files", ()):
            tables.append(self.fetch_json(self.submissions_url(cik, extra["name"])))
        rows: list[dict[str, Any]] = []
        for table in tables:
            for i in range(len(table["accessionNumber"])):
                rows.append({key: table[key][i] for key in
                             ("accessionNumber", "form", "filingDate", "acceptanceDateTime",
                              "primaryDocument", "isXBRL")})
        return rows

    def filing_instance(self, cik: int, accession: str) -> tuple[str, bytes]:
        """The XBRL instance of a filing: ``*_htm.xml`` (inline) or the plain instance."""
        index = self.fetch_json(self.filing_index_url(cik, accession))
        names = [item["name"] for item in index["directory"]["item"]]
        instance = [n for n in names if n.endswith("_htm.xml")]
        if not instance:
            instance = [n for n in names if n.endswith(".xml") and n != "FilingSummary.xml"
                        and not re.search(r"_(cal|def|lab|pre)\.xml$", n)]
        if len(instance) != 1:
            raise CapabilityProbeError(f"{accession}: cannot identify one XBRL instance in {names}")
        base = self.filing_index_url(cik, accession).rsplit("/", 1)[0]
        url = f"{base}/{instance[0]}"
        return url, self.fetch(url)


# -- parsing ------------------------------------------------------------


def _day(raw: str | None) -> date | None:
    return None if raw in (None, "") else date.fromisoformat(raw)


def companyfacts_facts(payload: Mapping[str, Any], tag: str, *, namespace: str = "us-gaap",
                       unit: str = "USD") -> list[Fact]:
    """Every value reported for ``namespace:tag`` in ``unit``, one per filing."""
    node = payload.get("facts", {}).get(namespace, {}).get(tag)
    if node is None:
        return []
    out: list[Fact] = []
    for row in node.get("units", {}).get(unit, ()):
        out.append(Fact(
            tag=tag, start=_day(row.get("start")), end=date.fromisoformat(row["end"]),
            value=float(row["val"]), unit=unit, accession=row["accn"], form=row["form"],
            filed=date.fromisoformat(row["filed"]), fy=row.get("fy"), fp=row.get("fp"),
        ))
    return out


def instance_dimensional_facts(xml_bytes: bytes, *, concepts: Iterable[str], axis: str,
                               member: str, accession: str, form: str, filed: date,
                               unit: str = "USD") -> list[Fact]:
    """Duration facts for ``concepts`` whose context has exactly one dimension, ``axis=member``.

    ``concepts`` are ``prefix:Local`` names (e.g. ``us-gaap:Revenues``); the
    prefix is ignored and the local name matched, which is unambiguous for
    us-gaap concepts. A context carrying any further dimension is a finer
    breakdown and is excluded rather than summed.
    """
    wanted = {concept.split(":", 1)[-1] for concept in concepts}
    root = ET.fromstring(xml_bytes)
    contexts: dict[str, tuple[date, date]] = {}
    for ctx in root.findall(f"{{{_XBRLI}}}context"):
        members = ctx.findall(f".//{{{_XBRLDI}}}explicitMember")
        typed = ctx.findall(f".//{{{_XBRLDI}}}typedMember")
        if typed or len(members) != 1:
            continue
        if members[0].get("dimension") != axis or (members[0].text or "").strip() != member:
            continue
        period = ctx.find(f"{{{_XBRLI}}}period")
        start = period.find(f"{{{_XBRLI}}}startDate") if period is not None else None
        end = period.find(f"{{{_XBRLI}}}endDate") if period is not None else None
        if start is None or end is None:
            continue
        contexts[ctx.get("id")] = (date.fromisoformat(start.text.strip()),
                                   date.fromisoformat(end.text.strip()))
    units: dict[str, str] = {}
    for node in root.findall(f"{{{_XBRLI}}}unit"):
        measure = node.find(f"{{{_XBRLI}}}measure")
        if measure is not None and measure.text:
            units[node.get("id")] = measure.text.strip().split(":")[-1]
    seen: dict[tuple[str, date, date], Fact] = {}
    for element in root:
        local = element.tag.split("}")[-1]
        ref = element.get("contextRef")
        if local not in wanted or ref not in contexts or units.get(element.get("unitRef")) != unit:
            continue
        if element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        start, end = contexts[ref]
        fact = Fact(tag=local, start=start, end=end, value=float(element.text.strip()), unit=unit,
                    accession=accession, form=form, filed=filed,
                    dimension=f"{axis}={member}")
        key = (local, start, end)
        if key in seen and seen[key].value != fact.value:
            raise CapabilityProbeError(f"{accession}: conflicting duplicate values for {key}")
        seen[key] = fact
    return sorted(seen.values(), key=lambda f: (f.tag, f.start, f.end))


def acceptance_utc(raw: str | None) -> datetime | None:
    """EDGAR ``acceptanceDateTime`` (e.g. ``2026-08-26T20:36:00.000Z``) as aware UTC."""
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.",
                            suffix=".tmp") as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
