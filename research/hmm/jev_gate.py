"""The Jev battery as a size gate (spec section 5, arms 2-4).

Per candle, one call with the deterministic snapshot the state engine already
computed (the five standardized features plus two raw returns), three typed
questions, answers cached on disk by (symbol, timeframe, candle, question
set version) so a backtest never pays twice and a live loop can replay.
Thresholds live HERE, in code, one per action, and the model version is
logged on every answer.

    regime        Choice: trending / mean_reverting / chaotic
    clean_setup   Noul: is this a clean trend-following setup for the next few candles
    risk_state    Score 0-3: how hostile is this state to holding a trend position

Gate (code):
    chaotic with p >= 0.5 or risk_state >= 2.5          -> 0.0
    clean_setup < 0.40                                   -> 0.0
    trending with p >= 0.60                              -> 1.0
    otherwise                                            -> 0.5
Confidence gating (arm 4): if the regime confidence is below CONF_MIN the
answer is treated as abstain -> 0.0 (the article's "abstain when uncertain").

Jev may only reduce size here (multipliers are in [0, 1]); it never raises
a cap, never places an order, and the RISK block runs after it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests

QUESTION_SET_VERSION = "hmm-regime/jev-gate/1"
MODEL = "jev-latest"
CONF_MIN = 0.80
API = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "regime": {"type": "choice", "instructions": "What market regime does this hourly/daily state describe for the next few candles?",
               "criteria": {"trending": None, "mean_reverting": None, "chaotic": None}},
    "clean_setup": {"type": "noul", "instructions": "Is this a clean trend-following setup for the next few candles (trend aligned, volatility normal, volume supportive)?"},
    # the raw API names a Score's rubric "criteria" (the SDK calls it legend)
    "risk_state": {"type": "score", "instructions": "How hostile is this state to holding a trend position? 0 benign, 1 normal, 2 elevated, 3 hostile (reduce now)",
                   "criteria": ["benign", "normal", "elevated", "hostile, reduce now"]},
}


def api_key() -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    p = Path.home() / ".qs-tools" / "typesafe-api-key"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def snapshot(features_row: dict[str, float], ret_1: float, ret_24: float) -> dict[str, Any]:
    """Dense, numeric, strictly from candles <= t (the features are already so)."""
    def f(v: float, nd: int = 3) -> float:
        v = float(v)
        return round(v, nd) if np.isfinite(v) else 0.0  # a non-finite feature is sent as 0, never as NaN

    return {
        "ret_z": f(features_row["ret"]), "rvol_z": f(features_row["rvol"]), "range_z": f(features_row["range"]),
        "vol_ratio_z": f(features_row["vol_ratio"]), "trend_z": f(features_row["trend"]),
        "ret_1_bps": f(float(ret_1) * 1e4, 1), "ret_24_bps": f(float(ret_24) * 1e4, 1),
    }


def multiplier(answers: dict[str, Any], *, confidence_gate: bool) -> tuple[float, str]:
    regime = answers["regime"]
    probs = regime.get("probabilities", {})
    conf = float(regime.get("confidence", 0.0))
    chaotic = float(probs.get("chaotic", 0.0))
    trending = float(probs.get("trending", 0.0))
    clean = float(answers["clean_setup"].get("noul", 0.0))
    risk = float(answers["risk_state"].get("score", 0.0))
    if confidence_gate and conf < CONF_MIN:
        return 0.0, f"abstain: regime confidence {conf:.2f} < {CONF_MIN}"
    if chaotic >= 0.5 or risk >= 2.5:
        return 0.0, f"hostile: chaotic {chaotic:.2f} risk {risk:.1f}"
    if clean < 0.40:
        return 0.0, f"no setup: clean {clean:.2f}"
    if trending >= 0.60:
        return 1.0, f"trending {trending:.2f} clean {clean:.2f}"
    return 0.5, f"unclear regime: trending {trending:.2f}"


class JevGate:
    """gate_fn for the backtest: multiplier for candle index t, cached."""

    def __init__(self, symbol: str, timeframe: str, feats: pd.DataFrame, close: np.ndarray, *, cache_dir: Path, confidence_gate: bool,
                 client: Callable[[dict[str, Any]], dict[str, Any]] | None = None, min_interval: float = 0.0) -> None:
        self.feats = feats.reset_index(drop=True)
        self.close = close
        self.confidence_gate = confidence_gate
        self.cache_path = cache_dir / f"jev_{symbol.replace('/', '-')}_{timeframe}_{QUESTION_SET_VERSION.replace('/', '_')}.jsonl"
        self.cache: dict[str, dict[str, Any]] = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                self.cache[rec["ts"]] = rec
        self.client = client or self._http
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0
        self.model_versions: set[str] = set()

    def _http(self, body: dict[str, Any]) -> dict[str, Any]:
        key = api_key()
        if not key:
            raise RuntimeError("no TypeSafe key")
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        for attempt in range(4):
            r = requests.post(API, json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"jev: HTTP {r.status_code} {r.text[:300]} for state {body['state']}")
            return r.json()
        raise RuntimeError(f"jev: HTTP {r.status_code} {r.text[:120]}")

    def answers_for(self, t: int) -> dict[str, Any]:
        ts = str(self.feats["ts"].iloc[t])
        if ts in self.cache:
            return self.cache[ts]
        row = self.feats.iloc[t]
        ret_1 = float(np.log(self.close[t] / self.close[t - 1])) if t >= 1 else 0.0
        ret_24 = float(np.log(self.close[t] / self.close[t - 24])) if t >= 24 else 0.0
        body = {"model": MODEL, "state": snapshot(row, ret_1, ret_24), "questions": QUESTIONS}
        res = self.client(body)
        rec = {"ts": ts, "model": res.get("model"), "answers": res["answers"], "version": QUESTION_SET_VERSION}
        self.cache[ts] = rec
        with self.cache_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.calls += 1
        if rec["model"]:
            self.model_versions.add(str(rec["model"]))
        return rec

    def __call__(self, t: int) -> float:
        rec = self.answers_for(t)
        m, _reason = multiplier(rec["answers"], confidence_gate=self.confidence_gate)
        return m
