"""Stage the news-gap-fade data: universe, daily bars, overnight news, minute bars.

Data download only. Nothing here computes an event return: candidate events
are identified from the gap and the point-in-time filters (``|gap| >= 1%``,
the smallest declared threshold), which need only the open of D and data
before D. Minute bars are then fetched for those candidates (NEWS and CONTROL
alike) and SPY, inside two windows per session: 09:30-09:59 and 15:30-15:54 ET
(Alpaca stamps a bar by its start minute).

Alpaca historical data API only (``data.alpaca.markets``); no trading client.
Everything is cached under ``.research/cache/news-gap-fade/`` and a step is
skipped when its cache file exists, so an interrupted run resumes. Nothing on
or after 2025-07-01 is requested except the name-change metadata used to map
historical tickers, which carries no prices or news.

    python -m research.experiments.prepare_news_gap_fade [universe|daily|candidates|news|minutes|all]
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.prereg import news_gap_fade as ngf  # noqa: E402
from research.prereg import xnys  # noqa: E402

CACHE = ROOT / ".research" / "cache" / "news-gap-fade"
STATUS = Path.home() / ".qs-tools" / "research" / "news-gap-fade.status"
DATA = "https://data.alpaca.markets"
MOST_ACTIVE = Path.home() / "repo" / "qs-research" / "univ"
DAILY_START = date(2015, 9, 1)
LAST_DAY = ngf.VALIDATION[1]
WORKERS = 16
UTC = timezone.utc


# ------------------------------------------------------------------ plumbing ---

def status(phase: str, **fields: object) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"phase={phase}", f"updated={datetime.now(UTC).isoformat(timespec='seconds')}"]
    lines += [f"{k}={v}" for k, v in fields.items()]
    STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _headers() -> dict[str, str]:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "") or os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_API_SECRET missing")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


_local = threading.local()


def _client() -> httpx.Client:
    if not hasattr(_local, "client"):
        _local.client = httpx.Client(headers=_headers(), timeout=90)
    return _local.client


def get(path: str, params: dict) -> dict:
    """GET with backoff on 429 / 5xx / transport errors. Never logs headers."""
    delay = 1.0
    for attempt in range(12):
        try:
            response = _client().get(DATA + path, params=params)
        except httpx.TransportError:
            _time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429 or response.status_code >= 500:
            _time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        raise RuntimeError(f"{path} -> HTTP {response.status_code}: {response.text[:300]}")
    raise RuntimeError(f"{path}: gave up after repeated 429/5xx/transport errors")


def paged(path: str, params: dict, key: str) -> list:
    out: list = []
    token = None
    while True:
        query = dict(params)
        if token:
            query["page_token"] = token
        body = get(path, query)
        chunk = body.get(key) or []
        if isinstance(chunk, dict):
            out.append(chunk)
        else:
            out.extend(chunk)
        token = body.get("next_page_token")
        if not token:
            return out


def write_gz(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(obj, handle)
    tmp.replace(path)


def read_gz(path: Path) -> object:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


# ------------------------------------------------------------------ universe ---

def build_universe() -> dict:
    target = CACHE / "universe" / "universe.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    udir = CACHE / "universe"
    sp = re.findall(r"\{\{(?:Nyse|Nasdaq|Cboe|Bats)Symbol\|([A-Z.]+)", (udir / "sp500.wikitext").read_text(encoding="utf-8"))
    ndx = [row["symbol"] for row in json.loads((udir / "ndx_nasdaq_api.json").read_text(encoding="utf-8"))["data"]["data"]["rows"]]
    actives = sorted(MOST_ACTIVE.glob("most-active-stock-options-*.csv"),
                     key=lambda p: datetime.strptime(p.stem[-10:], "%m-%d-%Y"))
    active_file = actives[-1] if actives else None
    active: list[str] = []
    if active_file:
        with active_file.open(encoding="utf-8") as handle:
            active = [r["Symbol"].strip() for r in csv.DictReader(handle)
                      if r.get("Name") and re.fullmatch(r"[A-Z.]{1,6}", r["Symbol"].strip())]
    symbols = sorted((set(sp) | set(ndx) | set(active)) - {"SPY"})
    out = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": {
            "sp500": {"source": "en.wikipedia.org List_of_S&P_500_companies (wikitext, fetched 2026-10-17)", "count": len(set(sp))},
            "ndx": {"source": "api.nasdaq.com list-type nasdaq100 (as of Oct 15, 2026)", "count": len(set(ndx))},
            "most_active": {"source": str(active_file) if active_file else None, "count": len(set(active))},
        },
        "symbols": symbols,
    }
    target.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def name_changes() -> list[dict]:
    target = CACHE / "universe" / "name_changes_full.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    items: list[dict] = []
    for start, end in (("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-10-16")):
        for page in paged("/v1/corporate-actions", {"types": "name_change", "start": start, "end": end, "limit": 1000},
                          "corporate_actions"):
            items.extend(page.get("name_changes", []))
    target.write_text(json.dumps(items), encoding="utf-8")
    return items


# --------------------------------------------------------------------- daily ---

def fetch_daily(symbols: list[str]) -> None:
    out_dir = CACHE / "daily"
    batches = [symbols[i:i + 50] for i in range(0, len(symbols), 50)]

    def one(index: int, batch: list[str], adjustment: str) -> None:
        target = out_dir / f"{adjustment}_{index:03d}.json.gz"
        if target.exists():
            return
        pages = paged("/v2/stocks/bars", {
            "symbols": ",".join(batch), "timeframe": "1Day", "adjustment": adjustment, "feed": "sip",
            "start": DAILY_START.isoformat(), "end": f"{LAST_DAY.isoformat()}T23:59:00Z", "limit": 10000,
        }, "bars")
        merged: dict[str, list] = {}
        for page in pages:
            for sym, bars in page.items():
                merged.setdefault(sym, []).extend(bars)
        write_gz(target, merged)

    jobs = [(i, b, adj) for adj in ("all", "raw") for i, b in enumerate(batches)]
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(one, *job) for job in jobs]
        for n, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if n % 10 == 0:
                status("download_daily", note=f"daily batches {n}/{len(jobs)}")


def load_daily(adjustment: str) -> pd.DataFrame:
    rows = []
    for path in sorted((CACHE / "daily").glob(f"{adjustment}_*.json.gz")):
        for sym, bars in read_gz(path).items():
            for bar in bars:
                rows.append((sym, bar["t"][:10], bar["o"], bar["c"], bar["v"]))
    frame = pd.DataFrame(rows, columns=["symbol", "date", "open", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame.drop_duplicates(["symbol", "date"], keep="last")


def spy_daily() -> list[date]:
    target = CACHE / "daily" / "spy_all.json.gz"
    if not target.exists():
        pages = paged("/v2/stocks/bars", {"symbols": "SPY", "timeframe": "1Day", "adjustment": "all", "feed": "sip",
                                          "start": DAILY_START.isoformat(), "end": f"{LAST_DAY.isoformat()}T23:59:00Z",
                                          "limit": 10000}, "bars")
        bars = [b for page in pages for b in page.get("SPY", [])]
        write_gz(target, bars)
    days = sorted({date.fromisoformat(b["t"][:10]) for b in read_gz(target)})
    check = [d for d in days if d >= date(2022, 1, 1)]
    expected = xnys.sessions(date(2022, 1, 1), LAST_DAY)
    if check != expected:
        missing = sorted(set(expected) - set(check))[:10]
        extra = sorted(set(check) - set(expected))[:10]
        raise SystemExit(f"SPY sessions disagree with xnys: missing {missing} extra {extra}")
    return days


# ---------------------------------------------------------------- candidates ---

def features(sessions: list[date]) -> pd.DataFrame:
    """Per (session, symbol): gap, filters, vol20 — from data before D plus open(D)."""
    adj, raw = load_daily("all"), load_daily("raw")
    index = pd.Index(sessions, name="date")

    def pivot(frame: pd.DataFrame, column: str) -> pd.DataFrame:
        return frame.pivot(index="date", columns="symbol", values=column).reindex(index)

    open_adj, close_adj = pivot(adj, "open"), pivot(adj, "close")
    close_raw, vol_raw = pivot(raw, "close"), pivot(raw, "volume")
    close_raw, vol_raw = close_raw.reindex(columns=close_adj.columns), vol_raw.reindex(columns=close_adj.columns)
    gap = open_adj / close_adj.shift(1) - 1.0
    prev_close_raw = close_raw.shift(1)
    adv = (close_raw * vol_raw).shift(1).rolling(ngf.LOOKBACK, min_periods=ngf.LOOKBACK).median()
    vol20 = np.log(close_adj).diff().shift(1).rolling(ngf.LOOKBACK, min_periods=ngf.LOOKBACK).std()
    frame = pd.DataFrame({
        "gap": gap.stack(future_stack=True), "prev_close_raw": prev_close_raw.stack(future_stack=True),
        "adv20": adv.stack(future_stack=True), "vol20": vol20.stack(future_stack=True),
    }).reset_index()
    frame = frame[frame["date"] >= ngf.DISCOVERY[0]]
    frame["passes"] = ((frame["prev_close_raw"] >= ngf.MIN_PRICE) & (frame["adv20"] >= ngf.MIN_DOLLAR_VOLUME)
                       & frame["gap"].notna() & frame["vol20"].notna())
    return frame


def build_candidates(sessions: list[date]) -> pd.DataFrame:
    target = CACHE / "derived" / "universe_days.csv.gz"
    if target.exists():
        return pd.read_csv(target, parse_dates=["date"], dtype={"symbol": str}, keep_default_na=False, na_values=[""]).assign(date=lambda f: f["date"].dt.date)
    frame = features(sessions)
    frame = frame[frame["passes"]].drop(columns="passes")
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False, compression="gzip")
    return frame


# ---------------------------------------------------------------------- news ---

def fetch_news(sessions: list[date]) -> None:
    out_dir = CACHE / "news"
    pairs = [(sessions[i - 1], sessions[i]) for i in range(1, len(sessions))
             if ngf.DISCOVERY[0] <= sessions[i] <= LAST_DAY]

    def one(prev: date, day: date) -> int:
        target = out_dir / f"{day.isoformat()}.json.gz"
        if target.exists():
            return 0
        start, end = ngf.news_window_utc(prev, day)
        # A minute of slack each side; the exact (start, end] filter is applied downstream.
        items = paged("/v1beta1/news", {
            "start": (start - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (end + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 50, "sort": "asc",
        }, "news")
        kept = [{k: n.get(k) for k in ("id", "created_at", "updated_at", "symbols", "headline", "source")}
                for n in items]
        write_gz(target, kept)
        return len(kept)

    total = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(one, p, d) for p, d in pairs]
        for n, fut in enumerate(as_completed(futures), 1):
            total += fut.result()
            if n % 50 == 0:
                status("download_news", note=f"news windows {n}/{len(pairs)}", articles_this_run=total)


# ------------------------------------------------------------------- minutes ---

def fetch_minutes(candidates: pd.DataFrame, sessions: list[date]) -> None:
    out_dir = CACHE / "minutes"
    big = candidates[candidates["gap"].abs() >= min(ngf.THRESHOLDS)]
    by_day = big.groupby("date")["symbol"].apply(sorted).to_dict()
    days = [d for d in sessions if ngf.DISCOVERY[0] <= d <= LAST_DAY]

    def one(day: date) -> None:
        target = out_dir / f"{day.isoformat()}.json.gz"
        if target.exists():
            return
        symbols = ["SPY"] + [s for s in by_day.get(day, []) if s != "SPY"]
        merged: dict[str, list] = {}
        for start, end in ((time(9, 30), time(9, 59)), (time(15, 30), time(15, 54))):
            s_utc = datetime.combine(day, start, ngf.ET).astimezone(UTC)
            e_utc = datetime.combine(day, end, ngf.ET).astimezone(UTC)
            for i in range(0, len(symbols), 200):
                pages = paged("/v2/stocks/bars", {
                    "symbols": ",".join(symbols[i:i + 200]), "timeframe": "1Min", "adjustment": "all",
                    "feed": "sip", "start": s_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": e_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 10000,
                }, "bars")
                for page in pages:
                    for sym, bars in page.items():
                        merged.setdefault(sym, []).extend([[b["t"], b["c"], b["v"]] for b in bars])
        write_gz(target, {"symbols": symbols, "bars": merged})

    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(one, d) for d in days]
        for n, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if n % 50 == 0:
                status("download_minutes", note=f"minute sessions {n}/{len(days)}")


def main(argv: list[str]) -> None:
    step = argv[1] if len(argv) > 1 else "all"
    status("download", note=f"step {step}")
    universe = build_universe()
    symbols = universe["symbols"]
    name_changes()
    if step in ("daily", "all", "candidates", "news", "minutes"):
        fetch_daily(symbols)
        sessions = spy_daily()
    if step in ("candidates", "all", "minutes"):
        candidates = build_candidates(sessions)
        print(f"filter-passing symbol-sessions: {len(candidates)}; |gap|>=1%: "
              f"{int((candidates['gap'].abs() >= 0.01).sum())}")
    if step in ("news", "all"):
        fetch_news(sessions)
    if step in ("minutes", "all"):
        fetch_minutes(candidates, sessions)
    status("download_done", note=f"step {step} complete", symbols=len(symbols))


if __name__ == "__main__":
    main(sys.argv)
