"""news-gap-fade campaign (preregistered 2026-10-17): after an overnight gap that
came with company news, does fading the gap from the morning to 15:55 ET pay,
net of SPY and costs — and more than fading a gap that came without news?

Hypothesis source, stated honestly: Jev `equity_priced` shadow data,
2026-10-08 .. 2026-10-16 (835 news calls, 211 names). Fading the gap of
early (pre-market / before 11:00 ET) company-news calls earned +0.84% mean on
460 calls, +1.78% in high-vol names, more when the news model said the news
justified the gap (+1.09%) than when it did not (+0.48%). Seven sessions, one
risk-off regime, mid prices, no costs: an anecdote. `idio-gap` (2026-08-14)
found no NEXT-DAY edge after idiosyncratic gaps, so the claim is intraday and
news-conditioned. 2025-07-01 onward is never read (2026-07-01 onward is
CONTAMINATED/RESERVED for the Jev-filter study).

Declared BEFORE any event return is computed (full text, prompt and every
convention: research/missions/news-gap-fade/PREREGISTRATION.md; arithmetic:
research/prereg/news_gap_fade.py):

Universe   current S&P 500 ∪ Nasdaq-100 ∪ qs-research most-active options
           names (822, SPY excluded). PIT filters: raw prior close >= $5,
           median 20-session raw dollar volume >= $20M. Survivorship stated.
Events     |open_D / close_prev - 1| >= T. NEWS: a headline tagging S with
           <= 3 symbols, created_at in (prev 16:00 ET, D 09:25 ET].
           CONTROL: same thresholds, no such headline.
Trade      fade (short gap-ups, buy gap-downs); entry = close of the 1-min
           bar ending 09:35 or 10:00; exit = close of the bar ending 15:55.
           Statistic: side x (r_stock - r_spy) over the same minutes.
Grid       T {1%, 2%, 4%} x vol {all, top tercile of prior-20d vol within the
           calendar year} x entry {09:35, 10:00} = 12 cells, NEWS and CONTROL.
Unit       equal-weight portfolio per session; ISO-week block bootstrap,
           2000 resamples, seed 20261017.
Costs      10 bps RT base; 20 bps stress; 40 bps extra stress (top tercile).
Split      discovery 2016-01-01..2022-12-31; validation 2023-01-01..
           2025-06-30, one shot, passing cells only.
Gates      >= 100 event-sessions; both halves positive; positive at base and
           every stress cost; survives dropping the largest-|x| and the best
           session; bootstrap 5th pct > 0; NEWS - CONTROL bootstrap 5th pct > 0.
Validation positive at stress cost(s), both halves positive.
Part B     Qwen2.5-3B (local llama-server, <= 2 concurrent, paused weekdays
           13:00-16:30 PT) scores "does this news justify this gap?" on <= 15k
           discovery NEWS events of T=2%. One comparison: fade (09:35 entry)
           when justified >= 0.5 minus when < 0.5, daily means, bootstrap
           5th pct > 0 with >= 100 sessions per group. Validation one shot.

Commands (in order; each evaluation is one-use in the ledger):

    python -m research.experiments.sweep_news_gap_fade build
    python -m research.experiments.sweep_news_gap_fade dryrun
    python -m research.experiments.sweep_news_gap_fade freeze
    python -m research.experiments.sweep_news_gap_fade partA-discovery
    python -m research.experiments.sweep_news_gap_fade partA-validation
    python -m research.experiments.sweep_news_gap_fade partB-score discovery
    python -m research.experiments.sweep_news_gap_fade partB-discovery
    python -m research.experiments.sweep_news_gap_fade partB-score validation
    python -m research.experiments.sweep_news_gap_fade partB-validation
    python -m research.experiments.sweep_news_gap_fade report
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.experiments.prepare_news_gap_fade import CACHE, read_gz, spy_daily, status  # noqa: E402
from research.prereg import ledger  # noqa: E402
from research.prereg import news_gap_fade as ngf  # noqa: E402

MISSION_DIR = ROOT / "research" / "missions" / "news-gap-fade"
OUT = ROOT / ".research" / "sweeps" / "news-gap-fade"
EVENTS = CACHE / "derived" / "events.csv.gz"
UNIVERSE_DAYS = CACHE / "derived" / "universe_days.csv.gz"
BOUND_CODE = (
    "research/prereg/news_gap_fade.py",
    "research/experiments/prepare_news_gap_fade.py",
    "research/experiments/sweep_news_gap_fade.py",
)
LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLM_MODEL = "qwen2.5:3b"
LLM_CONCURRENCY = 2
PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc
PRICE_COLUMNS = ("px_0935", "px_1000", "px_exit", "spy_0935", "spy_1000", "spy_exit")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value):
    """numpy scalars -> python, for canonical JSON."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


# --------------------------------------------------------------------- build ---

def _news_index(universe: set[str], tickers: ngf.TickerMap, sessions: list[date]) -> dict:
    """(date, symbol) -> list of (created_at, headline, as-of tag) qualifying for that
    event. An item with an empty headline is not a headline and does not qualify;
    an article id is counted once."""
    out: dict[tuple[date, str], list[tuple[str, str, str]]] = {}
    prev_of = {sessions[i]: sessions[i - 1] for i in range(1, len(sessions))}
    for path in sorted((CACHE / "news").glob("*.json.gz")):
        day = date.fromisoformat(path.name[:10])
        if day > ngf.VALIDATION[1]:
            continue
        start, end = ngf.news_window_utc(prev_of[day], day)
        seen_ids: set = set()
        for item in read_gz(path):
            tags = item.get("symbols") or []
            headline = (item.get("headline") or "").strip()
            if not ngf.qualifies(tags) or not headline or item.get("id") in seen_ids:
                continue
            seen_ids.add(item.get("id"))
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            if not start < created <= end:
                continue
            for symbol in ngf.tagged_universe_symbols(tags, created, universe, tickers):
                asof = symbol if symbol in tags else next(
                    (a.old for t in tags for a in tickers.aliases.get(t, ()) if a.new == symbol), symbol)
                out.setdefault((day, symbol), []).append((item["created_at"], headline, asof))
    return out


def _prices(day: date) -> tuple[dict[str, dict[str, float]], bool]:
    """symbol -> {px_0935, px_1000, px_exit}; and whether SPY has an exit bar."""
    body = read_gz(CACHE / "minutes" / f"{day.isoformat()}.json.gz")
    out: dict[str, dict[str, float]] = {}
    for symbol, bars in body["bars"].items():
        parsed = sorted((datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ngf.ET).time(), float(c))
                        for t, c, _v in bars)
        row = {}
        for name, entry in (("px_0935", "09:35"), ("px_1000", "10:00")):
            hit = ngf.entry_price(parsed, entry)
            row[name] = hit[0] if hit else np.nan
        hit = ngf.exit_price(parsed)
        row["px_exit"] = hit[0] if hit else np.nan
        out[symbol] = row
    spy = out.get("SPY", {})
    ok = day not in ngf.EARLY_CLOSES and all(np.isfinite(spy.get(k, np.nan))
                                             for k in ("px_0935", "px_1000", "px_exit"))
    return out, bool(ok)


def build() -> pd.DataFrame:
    status("build", note="building event table (no returns)")
    sessions = spy_daily()
    universe = set(json.loads((CACHE / "universe" / "universe.json").read_text(encoding="utf-8"))["symbols"])
    changes = json.loads((CACHE / "universe" / "name_changes_full.json").read_text(encoding="utf-8"))
    tickers = ngf.ticker_map(changes, universe)
    duplicates = ngf.same_security_duplicates(tickers, universe, ngf.VALIDATION[1])
    universe -= duplicates
    print(f"same-security duplicates dropped: {sorted(duplicates)}; universe {len(universe)}")
    study = [d for d in sessions if ngf.DISCOVERY[0] <= d <= ngf.VALIDATION[1]]
    # Alpaca SIP history begins 2016-01-04: that session has no prior close, so no news window.
    for folder, needed in (("news", study[1:]), ("minutes", study)):
        missing = [d for d in needed if not (CACHE / folder / f"{d.isoformat()}.json.gz").exists()]
        if missing:
            raise SystemExit(f"{len(missing)} sessions lack a {folder} file, e.g. {missing[:5]}")
    days = pd.read_csv(UNIVERSE_DAYS, parse_dates=["date"], dtype={"symbol": str}, keep_default_na=False, na_values=[""])
    days["date"] = days["date"].dt.date
    days = days[(days["date"] >= ngf.DISCOVERY[0]) & (days["date"] <= ngf.VALIDATION[1])
                & days["symbol"].isin(universe)]
    days["year"] = [d.year for d in days["date"]]
    cut = days.groupby("year")["vol20"].quantile(2 / 3)
    days["top_tercile"] = days["vol20"] >= days["year"].map(cut)
    events = days[days["gap"].abs() >= min(ngf.THRESHOLDS)].copy()
    news = _news_index(universe, tickers, sessions)
    heads, counts, asof = [], [], []
    for day, symbol in zip(events["date"], events["symbol"]):
        items = sorted(news.get((day, symbol), []))
        counts.append(len(items))
        recent: list[str] = []
        for _created, text, _tag in reversed(items):
            if text not in recent:
                recent.append(text)
            if len(recent) == ngf.PART_B_MAX_HEADLINES:
                break
        heads.append(json.dumps(recent))
        asof.append(items[-1][2] if items else symbol)   # the ticker in force, for the Part B prompt
    events["n_headlines"] = counts
    events["asof_ticker"] = asof
    events["news"] = events["n_headlines"] > 0
    events["headlines"] = heads
    events["stage"] = [ngf.stage_of(d) for d in events["date"]]
    rows = []
    for day in sorted(set(events["date"])):
        prices, spy_ok = _prices(day)
        spy = prices.get("SPY", {})
        for symbol in events.loc[events["date"] == day, "symbol"]:
            px = prices.get(symbol, {})
            rows.append({"date": day, "symbol": symbol, "spy_session_ok": spy_ok,
                         **{k: px.get(k, np.nan) for k in ("px_0935", "px_1000", "px_exit")},
                         **{f"spy_{k[3:]}": spy.get(k, np.nan) for k in ("px_0935", "px_1000", "px_exit")}})
    events = events.merge(pd.DataFrame(rows), on=["date", "symbol"], how="left")
    events["year_cut"] = events["year"].map(cut)
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(EVENTS, index=False, compression="gzip")
    status("build_done", events=len(events), note="event table built")
    return events


def load_events() -> pd.DataFrame:
    frame = pd.read_csv(EVENTS, parse_dates=["date"], dtype={"symbol": str, "asof_ticker": str}, keep_default_na=False, na_values=[""])
    frame["date"] = frame["date"].dt.date
    return frame


# ------------------------------------------------------------------- selection ---

def select(events: pd.DataFrame, *, stage: str, series: str, threshold: float, bucket: str) -> pd.DataFrame:
    mask = (events["stage"] == stage) & (events["gap"].abs() >= threshold)
    mask &= events["news"] if series == "NEWS" else ~events["news"]
    if bucket == "top_tercile":
        mask &= events["top_tercile"]
    return events[mask]


def priced(frame: pd.DataFrame, entry: str) -> pd.DataFrame:
    key = entry.replace(":", "")
    cols = [f"px_{key}", "px_exit", f"spy_{key}", "spy_exit"]
    ok = frame["spy_session_ok"].astype(bool)
    for col in cols:
        ok &= frame[col].notna() & (frame[col] > 0)
    return frame[ok]


def cell_name(series: str, threshold: float, bucket: str, entry: str) -> str:
    return f"{series}:T{round(threshold * 100)}:{bucket}:{entry}"


def returns(frame: pd.DataFrame, entry: str) -> pd.DataFrame:
    """Gross raw and market-adjusted fade returns. Called only inside an opened evaluation."""
    key = entry.replace(":", "")
    out = frame[["date", "symbol", "gap", "stage"]].copy()
    raw, adj = [], []
    for g, e, x, se, sx in zip(frame["gap"], frame[f"px_{key}"], frame["px_exit"],
                               frame[f"spy_{key}"], frame["spy_exit"]):
        r, a = ngf.fade_returns(g, e, x, se, sx)
        raw.append(r)
        adj.append(a)
    out["raw"], out["adj"] = raw, adj
    return out


def _mean(series: dict) -> float:
    return float(np.mean(list(series.values()))) if series else float("nan")


# ------------------------------------------------------------------ dry run ---

def dryrun() -> None:
    events = load_events()
    print(f"events (|gap|>=1%, disc+val): {len(events)}; news share {events['news'].mean():.1%}; "
          f"SPY early-close sessions {int((~events.drop_duplicates('date')['spy_session_ok'].astype(bool)).sum())}")
    for stage in ("discovery", "validation"):
        for series in ngf.SERIES:
            for t in ngf.THRESHOLDS:
                for bucket in ngf.VOL_BUCKETS:
                    sel = select(events, stage=stage, series=series, threshold=t, bucket=bucket)
                    for entry in ngf.ENTRIES:
                        p = priced(sel, entry)
                        print(f"{stage:10s} {cell_name(series, t, bucket, entry):32s} events {len(sel):6d} "
                              f"priced {len(p):6d} sessions {p['date'].nunique():5d}")


# --------------------------------------------------------------------- ledger ---

def freeze() -> None:
    frozen = ledger.freeze(MISSION_DIR, BOUND_CODE)
    print(f"frozen {frozen.mission} {frozen.event_hash} at {frozen.created_at}")


def inputs() -> dict[str, str]:
    return {"events.csv.gz": file_sha256(EVENTS), "universe_days.csv.gz": file_sha256(UNIVERSE_DAYS)}


def save(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(_clean(payload), indent=1, default=str), encoding="utf-8")


# --------------------------------------------------------------- Part A eval ---

def _cell(events: pd.DataFrame, *, stage: str, series: str, threshold: float, bucket: str, entry: str) -> dict:
    sel = select(events, stage=stage, series=series, threshold=threshold, bucket=bucket)
    p = priced(sel, entry)
    r = returns(p, entry)
    adj = ngf.daily_portfolio(zip(r["date"], r["adj"]))
    raw = ngf.daily_portfolio(zip(r["date"], r["raw"]))
    side = {}
    for name, part in (("gap_down_long", r[r["gap"] < 0]), ("gap_up_short", r[r["gap"] > 0])):
        d = ngf.daily_portfolio(zip(part["date"], part["adj"]))
        side[name] = {"events": len(part), "sessions": len(d), "adj_net_base": _mean(d) - ngf.COST_BASE}
    by_year: dict[int, dict] = {}
    for day, value in adj.items():
        by_year.setdefault(day.year, []).append(value)
    return {
        "cell": cell_name(series, threshold, bucket, entry), "stage": stage, "series": series,
        "threshold": threshold, "bucket": bucket, "entry": entry,
        "events_selected": len(sel), "events_priced": len(p), "events_dropped_no_price": len(sel) - len(p),
        "sessions": len(adj),
        "adj_gross": _mean(adj), "adj_net_base": _mean(adj) - ngf.COST_BASE,
        "adj_net_20bps": _mean(adj) - ngf.COST_STRESS, "adj_net_40bps": _mean(adj) - ngf.COST_STRESS_TOPVOL,
        "raw_net_base": _mean(raw) - ngf.COST_BASE,
        "event_level_adj_mean": float(r["adj"].mean()) if len(r) else float("nan"),
        "event_level_adj_median": float(r["adj"].median()) if len(r) else float("nan"),
        "sides": side,
        "by_year_adj_net_base": {y: {"sessions": len(v), "mean": float(np.mean(v)) - ngf.COST_BASE}
                                 for y, v in sorted(by_year.items())},
        "_daily": adj,
    }


def _report_dict(report: ngf.CellReport) -> dict:
    return {"passed": report.passed, "failed": report.failed,
            "gates": [{"name": g.name, "passed": g.passed, "value": g.value, "detail": g.detail}
                      for g in report.gates]}


def part_a_discovery() -> None:
    ledger.open_evaluation(MISSION_DIR, "partA-discovery", inputs=inputs())
    status("partA_discovery", note="evaluation opened")
    events = load_events()
    cells = []
    for t in ngf.THRESHOLDS:
        for bucket in ngf.VOL_BUCKETS:
            for entry in ngf.ENTRIES:
                news = _cell(events, stage="discovery", series="NEWS", threshold=t, bucket=bucket, entry=entry)
                ctrl = _cell(events, stage="discovery", series="CONTROL", threshold=t, bucket=bucket, entry=entry)
                rep = ngf.discovery_gates(news["cell"], bucket, news["_daily"], ctrl["_daily"], news["events_priced"])
                news.update(_report_dict(rep))
                news["news_minus_control_adj"] = news["adj_gross"] - ctrl["adj_gross"]
                crep = ngf.discovery_gates(ctrl["cell"], bucket, ctrl["_daily"], None, ctrl["events_priced"])
                ctrl.update(_report_dict(crep))
                ctrl["note"] = "descriptive: CONTROL is not gated; it is run through gates 1-5 only"
                news["plain_gap_fade_only"] = (not rep.passed and rep.failed == ["news_beats_control_lb_positive"])
                for item in (news, ctrl):
                    item.pop("_daily")
                cells += [news, ctrl]
                print(f"{news['cell']:30s} n={news['sessions']:5d} adj {news['adj_net_base'] * 1e4:+7.1f}bp "
                      f"ctrl {ctrl['adj_net_base'] * 1e4:+7.1f}bp  {'PASS' if news['passed'] else news['failed']}")
    passing = [c["cell"] for c in cells if c["series"] == "NEWS" and c["passed"]]
    verdict = "PASS" if passing else "FAIL"
    result = {"verdict": verdict, "passing_cells": passing, "cells": cells}
    ledger.record_verdict(MISSION_DIR, "partA-discovery", _clean(result))
    save("partA_discovery", result)
    status("partA_discovery_done", note=f"verdict {verdict}; passing {passing}")
    print(f"Part A discovery: {verdict} {passing}")


def part_a_validation() -> None:
    disc = json.loads((OUT / "partA_discovery.json").read_text(encoding="utf-8"))
    passing = disc["passing_cells"]
    ledger.open_evaluation(MISSION_DIR, "partA-validation", requires_pass="partA-discovery",
                           inputs={**inputs(), "cells": ",".join(passing)})
    events = load_events()
    cells = []
    for name in passing:
        _series, tag, bucket, entry = name.split(":", 3)
        t = int(tag[1:]) / 100
        news = _cell(events, stage="validation", series="NEWS", threshold=t, bucket=bucket, entry=entry)
        ctrl = _cell(events, stage="validation", series="CONTROL", threshold=t, bucket=bucket, entry=entry)
        rep = ngf.validation_gates(news["cell"], bucket, news["_daily"], news["events_priced"])
        news.update(_report_dict(rep))
        news["validation_control_adj_net_base"] = ctrl["adj_net_base"]
        news.pop("_daily")
        cells.append(news)
    result = {"verdict": "PASS" if any(c["passed"] for c in cells) else "FAIL",
              "validated_cells": [c["cell"] for c in cells if c["passed"]], "cells": cells}
    ledger.record_verdict(MISSION_DIR, "partA-validation", _clean(result))
    save("partA_validation", result)
    print(json.dumps({"verdict": result["verdict"], "validated": result["validated_cells"]}))


# -------------------------------------------------------------------- Part B ---

def _paused_now() -> float:
    """Seconds to wait if inside the weekday 13:00-16:30 PT production window."""
    now = datetime.now(PT)
    if now.weekday() < 5 and time(13, 0) <= now.time() < time(16, 30):
        resume = datetime.combine(now.date(), time(16, 30), PT)
        return (resume - now).total_seconds() + 5
    return 0.0


def _ask(messages: list[dict]) -> tuple[float | None, str]:
    import httpx
    body = {"model": LLM_MODEL, "messages": messages, "temperature": 0, "max_tokens": 24,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "verdict", "strict": True, "schema": ngf.RESPONSE_SCHEMA}}}
    content = ""
    parse_attempts = 0
    delay = 5.0
    started = _time.monotonic()
    while parse_attempts < 2:
        wait = _paused_now()
        while wait > 0:
            status("partB_paused", note="weekday 13:00-16:30 PT production window")
            _time.sleep(min(wait, 300))
            wait = _paused_now()
        try:
            response = httpx.post(LLM_URL, json=body, timeout=180)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - server/transport trouble is not a parse failure
            if _time.monotonic() - started > 6 * 3600:
                raise SystemExit(f"llama-server unavailable for 6h: {type(exc).__name__}") from exc
            status("partB_waiting_server", note=f"{type(exc).__name__}; retrying in {delay:.0f}s")
            _time.sleep(delay)
            delay = min(delay * 2, 300)
            continue
        parse_attempts += 1     # a 200 response: parse it, retry once if it does not parse
        try:
            value = float(json.loads(content)["justified"])
            if np.isfinite(value):
                return min(max(value, 0.0), 1.0), content
        except (ValueError, KeyError, TypeError):
            pass
    return None, content


def part_b_sample(events: pd.DataFrame, stage: str) -> pd.DataFrame:
    pool = events[(events["stage"] == stage) & events["news"] & (events["gap"].abs() >= ngf.PART_B_THRESHOLD)
                  & (events["headlines"] != "[]")]
    keys = {f"{d.isoformat()}|{s}": (d, s) for d, s in zip(pool["date"], pool["symbol"])}
    by_year: dict[int, list[str]] = {}
    for key, (d, _s) in keys.items():
        by_year.setdefault(d.year, []).append(key)
    chosen = set(ngf.part_b_sample(by_year, label=stage))
    pool = pool.assign(key=[f"{d.isoformat()}|{s}" for d, s in zip(pool["date"], pool["symbol"])])
    return pool[pool["key"].isin(chosen)]


def part_b_score(stage: str) -> None:
    if stage == "validation":
        verdict = ledger.verdicts(MISSION_DIR).get("partB-discovery", {}).get("verdict")
        if verdict != "PASS":
            raise SystemExit(f"Part B validation is sealed: discovery verdict is {verdict}")
    events = load_events()
    sample = part_b_sample(events, stage)
    target = OUT / f"partB_scores_{stage}.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    done = set()
    if target.exists():
        done = {json.loads(line)["key"] for line in target.read_text(encoding="utf-8").splitlines() if line}
    todo = [row for row in sample.itertuples() if row.key not in done]
    lock = threading.Lock()
    counter = {"n": len(done)}

    def one(row) -> None:
        messages = ngf.part_b_messages(row.asof_ticker, json.loads(row.headlines), row.gap)
        value, content = _ask(messages)
        with lock:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"key": row.key, "justified": value, "content": content}) + "\n")
            counter["n"] += 1
            if counter["n"] % 100 == 0:
                status("partB_score", note=f"{stage} scored {counter['n']}/{len(sample)}", events=len(sample))

    with ThreadPoolExecutor(LLM_CONCURRENCY) as pool:
        list(pool.map(one, todo))
    status("partB_score_done", note=f"{stage} scored {counter['n']}/{len(sample)}", events=len(sample))


def _scores(stage: str) -> dict[str, float | None]:
    lines = (OUT / f"partB_scores_{stage}.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["key"]: r["justified"] for r in map(json.loads, (line for line in lines if line.strip()))}


def _part_b_preflight(stage: str) -> None:
    """Fail BEFORE the one-use receipt: every sampled event must be scored, every line must parse."""
    target = OUT / f"partB_scores_{stage}.jsonl"
    if not target.exists():
        raise SystemExit(f"{target.name} missing; run partB-score {stage} first")
    scores = _scores(stage)
    sample = part_b_sample(load_events(), stage)
    missing = [k for k in sample["key"] if k not in scores]
    if missing:
        raise SystemExit(f"{len(missing)} sampled events are unscored; finish partB-score {stage} first")
    if all(scores[k] is None for k in sample["key"]):
        raise SystemExit("no sampled event has a usable score")


def _part_b_groups(stage: str) -> tuple[dict, dict, dict]:
    events = load_events()
    sample = part_b_sample(events, stage)
    scores = _scores(stage)
    sample = sample.assign(justified=pd.to_numeric(pd.Series([scores[k] for k in sample["key"]],
                                                             index=sample.index, dtype="object"),
                                                   errors="coerce").astype(float))
    unparsed = int(sample["justified"].isna().sum())
    scored = sample[sample["justified"].notna()]
    p = priced(scored, ngf.PART_B_ENTRY)
    r = returns(p, ngf.PART_B_ENTRY).assign(justified=p["justified"].to_numpy())
    j = r[r["justified"] >= ngf.PART_B_CUT]
    u = r[r["justified"] < ngf.PART_B_CUT]
    jd = ngf.shift(ngf.daily_portfolio(zip(j["date"], j["adj"])), ngf.COST_BASE)
    ud = ngf.shift(ngf.daily_portfolio(zip(u["date"], u["adj"])), ngf.COST_BASE)
    info = {"sampled": len(sample), "unparsed_excluded": unparsed, "priced": len(p),
            "dropped_no_price": len(scored) - len(p), "j_events": len(j), "u_events": len(u),
            "j_sessions": len(jd), "u_sessions": len(ud),
            "j_adj_net_base": _mean(jd), "u_adj_net_base": _mean(ud), "difference": _mean(jd) - _mean(ud),
            "difference_halves": ngf.difference_halves(jd, ud),
            "justified_share": float((scored["justified"] >= ngf.PART_B_CUT).mean()),
            "justified_quartiles": [float(q) for q in scored["justified"].quantile([0.25, 0.5, 0.75])],
            "justified_share_by_direction": {
                "gap_up": float((scored.loc[scored["gap"] > 0, "justified"] >= ngf.PART_B_CUT).mean()),
                "gap_down": float((scored.loc[scored["gap"] < 0, "justified"] >= ngf.PART_B_CUT).mean())}}
    return jd, ud, info


def part_b_discovery() -> None:
    _part_b_preflight("discovery")
    ledger.open_evaluation(MISSION_DIR, "partB-discovery",
                           inputs={**inputs(), "partB_scores_discovery.jsonl":
                                   file_sha256(OUT / "partB_scores_discovery.jsonl")})
    jd, ud, info = _part_b_groups("discovery")
    rep = ngf.part_b_discovery(jd, ud)
    result = {"verdict": "PASS" if rep.passed else "FAIL", **info, **_report_dict(rep)}
    ledger.record_verdict(MISSION_DIR, "partB-discovery", _clean(result))
    save("partB_discovery", result)
    print(json.dumps(_clean(result), indent=1))


def part_b_validation() -> None:
    _part_b_preflight("validation")
    ledger.open_evaluation(MISSION_DIR, "partB-validation", requires_pass="partB-discovery",
                           inputs={**inputs(), "partB_scores_validation.jsonl":
                                   file_sha256(OUT / "partB_scores_validation.jsonl")})
    jd, ud, info = _part_b_groups("validation")
    rep = ngf.part_b_validation(jd, ud)
    result = {"verdict": "PASS" if rep.passed else "FAIL", **info, **_report_dict(rep)}
    ledger.record_verdict(MISSION_DIR, "partB-validation", _clean(result))
    save("partB_validation", result)
    print(json.dumps(_clean(result), indent=1))


def report() -> None:
    out = {"mission": "news-gap-fade", "authority": "research_only",
           "frozen": ledger.verify(MISSION_DIR).event_hash, "verdicts": ledger.verdicts(MISSION_DIR)}
    for name in ("partA_discovery", "partA_validation", "partB_discovery", "partB_validation"):
        path = OUT / f"{name}.json"
        out[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    (MISSION_DIR / "results.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"wrote {MISSION_DIR / 'results.json'}")


COMMANDS = {
    "build": build, "dryrun": dryrun, "freeze": freeze,
    "partA-discovery": part_a_discovery, "partA-validation": part_a_validation,
    "partB-discovery": part_b_discovery, "partB-validation": part_b_validation, "report": report,
}


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "partB-score":
        part_b_score(sys.argv[2])
    else:
        COMMANDS[command]()
