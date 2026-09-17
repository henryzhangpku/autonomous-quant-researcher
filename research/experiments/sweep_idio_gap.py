"""Idiosyncratic-gap campaign: after a large single-name down day that the
market did NOT share, does the name continue lower or revert, net of costs?

Hypothesis source, stated honestly: AVGO fell -5.94% on 2026-08-14 while SPY
fell -0.20%, and a same-day diagnostic on AVGO ALONE (n=6 idiosyncratic
precedents, 5-day median -5.8%) suggested continuation rather than reversion.
That diagnostic was a peek at AVGO's history. Consequently:

    AVGO is flagged as CONTAMINATED. Its rows are reported for completeness,
    but every gate and the promotion decision are computed on the ex-AVGO
    panel. The 2026-08-14 event itself is excluded (no forward data).

Declared BEFORE the panel is evaluated:

Universe
  Tradeable set  : AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO IBIT
                   (the Mon/Wed/Fri single-name expiry universe)
  Power panel    : JPM V WMT PG XOM UNH HD MA COST NFLX AMD CRM ORCL KO PEP
                   BAC DIS CSCO JNJ NKE  (liquid large caps, never examined
                   today; replication surface for the mechanism)
  Daily bars 2016-01-01 .. 2026-08-13, Alpaca SIP.

Events
  ret_1d(name) <= T for T in {-4%, -6%}, with same-day SPY return > -1%
  (idiosyncratic). MIRROR series: same T with SPY <= -1% (market-wide), where
  the mechanism claims the OPPOSITE sign (crash days revert). A finding that
  does not reproduce the sign flip on the mirror is weaker evidence.

Positions and horizons
  Entry at the event-day close. Exits at the +1 close and the +5 close.
  Long-side net mean is the statistic; continuation edge = significantly
  NEGATIVE long mean (i.e. the tradeable expression would be short/avoid,
  and for our products: do not sell puts into it).

Costs
  10 bps round trip on the stock leg; doubled-cost stress at 20 bps.
  Borrow cost for any short expression is not modeled and is flagged.

Split
  discovery  2016-01-01 .. 2023-12-31
  validation 2024-01-01 .. 2026-08-13 (one shot, read only for cells that
  pass every discovery gate)

Gates, all required in discovery (ex-AVGO panel):
  n_events >= 30
  |net mean| direction consistent in both halves of discovery
  survives double cost
  survives dropping the single largest |contribution| event
  bootstrap 5th/95th percentile bound (2000 resamples, seed 20260814)
  excludes zero on the claimed side
Validation (one shot): same-sign net mean, same-sign both halves.

Promotion rule: a passing cell yields a FINDINGS entry and, at most, a
fail-closed advisory policy ("do not sell single-name puts within N days of
an idiosyncratic gap"). No standalone directional policy from daily bars
alone; authority research_only regardless.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "research"))

from providers.alpaca import AlpacaHistoricalProvider  # noqa: E402

OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "idio-gap"
TRADEABLE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "IBIT")
POWER = ("JPM", "V", "WMT", "PG", "XOM", "UNH", "HD", "MA", "COST", "NFLX",
         "AMD", "CRM", "ORCL", "KO", "PEP", "BAC", "DIS", "CSCO", "JNJ", "NKE")
CONTAMINATED = {"AVGO"}
THRESHOLDS = (-0.04, -0.06)
HORIZONS = (1, 5)
COST_RT = 0.0010
SEED = 20260814
DISC_END = "2023-12-31"
VAL_END = "2026-08-13"


def daily_closes(provider: AlpacaHistoricalProvider, symbol: str):
    snap = provider.bars(
        asset_class="equity", symbols=(symbol,),
        start_utc=datetime(2016, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 14, tzinfo=timezone.utc),
        timeframe="1Day",
    )
    rows = sorted((b.timestamp_utc.date().isoformat(), float(b.close)) for b in snap.bars)
    return [d for d, _ in rows], np.array([c for _, c in rows])


def build_events(provider: AlpacaHistoricalProvider):
    spy_d, spy_c = daily_closes(provider, "SPY")
    spy_ret = {spy_d[i]: spy_c[i] / spy_c[i - 1] - 1.0 for i in range(1, len(spy_d))}
    events = []
    for sym in TRADEABLE + POWER:
        d, c = daily_closes(provider, sym)
        for i in range(1, len(d) - 1):
            r = c[i] / c[i - 1] - 1.0
            if r > THRESHOLDS[0] or d[i] not in spy_ret or d[i] > VAL_END:
                continue
            fwd = {}
            for h in HORIZONS:
                if i + h < len(c):
                    fwd[h] = c[i + h] / c[i] - 1.0
            if not fwd:
                continue
            events.append({
                "symbol": sym, "date": d[i], "ret": float(r),
                "spy_ret": float(spy_ret[d[i]]),
                "idio": bool(spy_ret[d[i]] > -0.01),
                "fwd": {k: float(v) for k, v in fwd.items()},
                "tradeable": bool(sym in TRADEABLE),
                "contaminated": bool(sym in CONTAMINATED),
            })
    return events


def cell_stats(evts, horizon, cost):
    net = np.array([e["fwd"][horizon] for e in evts if horizon in e["fwd"]]) - cost
    return net


def gates(net, rng):
    if len(net) < 30:
        return False, "n<30", {}
    mean = net.mean()
    sign = np.sign(mean)
    if sign == 0:
        return False, "zero-mean", {}
    h1, h2 = net[: len(net) // 2], net[len(net) // 2:]
    if np.sign(h1.mean()) != sign or np.sign(h2.mean()) != sign:
        return False, "halves-disagree", {}
    # doubled-cost stress: one extra round-trip cost charged against the claim
    stressed_mean = mean - np.sign(mean) * COST_RT
    if np.sign(stressed_mean) != sign:
        return False, "dies-at-double-cost", {}
    drop_best = np.delete(net, int(np.argmax(np.abs(net))))
    if np.sign(drop_best.mean()) != sign:
        return False, "one-event-carries-it", {}
    boots = np.array([rng.choice(net, size=len(net), replace=True).mean() for _ in range(2000)])
    lo, hi = np.percentile(boots, [5, 95])
    if sign > 0 and lo <= 0:
        return False, "bootstrap-crosses-zero", {}
    if sign < 0 and hi >= 0:
        return False, "bootstrap-crosses-zero", {}
    return True, "pass", {"mean": mean, "lb5": lo, "ub95": hi}


def main():
    provider = AlpacaHistoricalProvider.from_env()
    events = build_events(provider)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "events.json").write_text(json.dumps(events, indent=1))
    rng = np.random.default_rng(SEED)

    print(f"events total: {len(events)}  "
          f"(idio {sum(e['idio'] for e in events)}, "
          f"mkt {sum(not e['idio'] for e in events)})")
    results = []
    for regime_name, regime in (("IDIO", True), ("MKT", False)):
        for thr in THRESHOLDS:
            for h in HORIZONS:
                for panel_name, panel in (
                    ("ex-AVGO", [e for e in events if not e["contaminated"]]),
                    ("tradeable", [e for e in events if e["tradeable"]]),
                ):
                    sel = [e for e in panel if e["idio"] == regime and e["ret"] <= thr]
                    disc = [e for e in sel if e["date"] <= DISC_END]
                    val = [e for e in sel if e["date"] > DISC_END]
                    dn = cell_stats(disc, h, COST_RT)
                    vn = cell_stats(val, h, COST_RT)
                    if len(dn) == 0:
                        continue
                    ok, why, info = gates(dn, rng)
                    row = {
                        "regime": regime_name, "thr": thr, "h": h, "panel": panel_name,
                        "n_disc": int(len(dn)), "disc_mean": float(dn.mean()),
                        "n_val": int(len(vn)),
                        "val_mean": float(vn.mean()) if len(vn) else None,
                        "gates": why,
                    }
                    if ok and len(vn):
                        same = np.sign(vn.mean()) == np.sign(dn.mean())
                        v1, v2 = vn[: len(vn) // 2], vn[len(vn) // 2:]
                        halves = np.sign(v1.mean()) == np.sign(vn.mean()) == np.sign(v2.mean())
                        row["validation"] = "PASS" if (same and halves) else "FAIL"
                        row.update(info)
                    results.append(row)
                    flag = row.get("validation", "")
                    print(f"{regime_name:4s} thr {thr:+.0%} h{h} {panel_name:9s} "
                          f"n={row['n_disc']:4d} disc {row['disc_mean']*100:+.2f}% "
                          f"val n={row['n_val']:3d} "
                          f"{(row['val_mean'] or 0)*100:+.2f}% "
                          f"[{why}] {flag}")
    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=1))
    print(f"\nartifacts: {OUT_DIR}")


if __name__ == "__main__":
    main()
