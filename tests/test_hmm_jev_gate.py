"""The Jev gate: thresholds in code, answers cached, multipliers only ever reduce size."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from research.hmm.jev_gate import CONF_MIN, JevGate, multiplier, snapshot


def _ans(trending=0.9, chaotic=0.0, conf=0.95, clean=0.7, risk=1.0):
    return {"regime": {"choice": "trending", "confidence": conf, "probabilities": {"trending": trending, "mean_reverting": 1 - trending - chaotic, "chaotic": chaotic}},
            "clean_setup": {"noul": clean}, "risk_state": {"score": risk}}


def test_multiplier_thresholds_live_in_code_and_only_reduce():
    assert multiplier(_ans(), confidence_gate=False)[0] == 1.0
    assert multiplier(_ans(trending=0.5), confidence_gate=False)[0] == 0.5
    assert multiplier(_ans(chaotic=0.6, trending=0.3), confidence_gate=False)[0] == 0.0
    assert multiplier(_ans(risk=2.7), confidence_gate=False)[0] == 0.0
    assert multiplier(_ans(clean=0.2), confidence_gate=False)[0] == 0.0
    assert multiplier(_ans(conf=CONF_MIN - 0.01), confidence_gate=True)[0] == 0.0
    assert multiplier(_ans(conf=CONF_MIN - 0.01), confidence_gate=False)[0] == 1.0
    for kw in ({}, {"trending": 0.5}, {"chaotic": 0.9, "trending": 0.05}, {"clean": 0.1}, {"risk": 3.0}):
        assert 0.0 <= multiplier(_ans(**kw), confidence_gate=True)[0] <= 1.0


def test_gate_caches_answers_and_never_sends_future_data(tmp_path: Path):
    n = 40
    ts = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    feats = pd.DataFrame({"ts": ts, "ret": np.linspace(-1, 1, n), "rvol": 0.0, "range": 0.0, "vol_ratio": 0.0, "trend": np.linspace(-2, 2, n)})
    close = 100 * np.exp(np.cumsum(np.full(n, 0.001)))
    sent = []

    def fake(body):
        sent.append(body)
        return {"model": "jev-test", "answers": _ans(trending=0.9 if body["state"]["trend_z"] > 0 else 0.2)}

    gate = JevGate("X/Y", "hour", feats, close, cache_dir=tmp_path, confidence_gate=False, client=fake)
    assert gate(30) == 1.0 and gate(5) == 0.5
    assert sent[0]["state"] == snapshot(feats.iloc[30], float(np.log(close[30] / close[29])), float(np.log(close[30] / close[6])))
    assert set(sent[0]["state"]) == {"ret_z", "rvol_z", "range_z", "vol_ratio_z", "trend_z", "ret_1_bps", "ret_24_bps"}
    assert gate(30) == 1.0 and len(sent) == 2  # cached, not re-asked
    again = JevGate("X/Y", "hour", feats, close, cache_dir=tmp_path, confidence_gate=False, client=fake)
    assert again(30) == 1.0 and len(sent) == 2  # cache survives a new process
    assert again.model_versions == set() and gate.model_versions == {"jev-test"}


def test_unavailable_jev_means_hold_and_is_asked_again_later(tmp_path: Path):
    n = 30
    ts = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    feats = pd.DataFrame({"ts": ts, "ret": 0.0, "rvol": 0.0, "range": 0.0, "vol_ratio": 0.0, "trend": 1.0})
    close = np.full(n, 100.0)
    state = {"fail": True, "calls": 0}

    def flaky(body):
        state["calls"] += 1
        if state["fail"]:
            return {"model": None, "answers": None, "unavailable": "ReadTimeout"}
        return {"model": "jev-test", "answers": _ans()}

    gate = JevGate("X/Y", "hour", feats, close, cache_dir=tmp_path, confidence_gate=False, client=flaky)
    assert gate(10) == 0.0 and state["calls"] == 1  # hold, not a guess
    assert not gate.cache_path.exists() or "2026-01-01 10:00" not in gate.cache_path.read_text()
    state["fail"] = False
    assert gate(10) == 1.0 and state["calls"] == 2  # asked again once the API is back, then cached
    assert gate(10) == 1.0 and state["calls"] == 2
    conf = JevGate("X/Y", "hour", feats, close, cache_dir=tmp_path, confidence_gate=True, client=flaky).share_cache_with(gate)
    assert conf(10) == 1.0 and state["calls"] == 2  # the shared store answers the second gate
