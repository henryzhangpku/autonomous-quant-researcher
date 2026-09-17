from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research.experiments.pine_export import PineExportError, export

FEEDBACK = (
    "accepted; robust_portfolio_score=1.2345; evidence: gate_penalty=0; "
    "net_trade_mean_bps=42.5; trades=17"
)

GOLDEN = '''\
LABEL = "golden test candidate"

def signal(symbol, features):
    vol = features['rv_20d']
    if vol > 0.02 and features['rsv_20'] < 0.3:
        return 1
    elif features.get('dist_high_20') > -0.01 or features['ret_1d'] < -0.03:
        return -1
    else:
        return 0
'''


def _make_run(tmp_path: Path, source: str, *, feedback: str = FEEDBACK,
              accepted: bool = True) -> tuple[Path, str]:
    """A minimal .research/runs/<slug> layout in tmp_path."""
    run_dir = tmp_path / "test-mission"
    digest = hashlib.sha256(source.encode()).hexdigest()
    candidate = run_dir / "workspace" / "candidates" / digest / "candidate.py"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(source)
    state = run_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    record = {"accepted": accepted, "candidate_hash": digest, "feedback": feedback}
    (state / "runs.jsonl").write_text(json.dumps(record) + "\n")
    return run_dir, digest


def test_golden_translation(tmp_path: Path):
    run_dir, digest = _make_run(tmp_path, GOLDEN)
    pine = export(run_dir)

    assert pine.startswith("//@version=5")
    assert 'strategy("golden test candidate [autonomous-quant-researcher]"' in pine
    assert f"// Candidate hash: {digest}" in pine
    assert "// Mission: test-mission" in pine

    # Feature computations with the exact ta.* forms and thresholds.
    assert "float f_rv_20d = ta.stdev(_ret1, 20, false)" in pine
    assert "float _span20 = ta.highest(high, 20) - ta.lowest(low, 20)" in pine
    assert "float f_rsv_20 = _span20 > 0 ? (close - ta.lowest(low, 20)) / _span20 : 0.5" in pine
    assert "float f_dist_high_20 = close / ta.highest(close, 20) - 1" in pine
    assert "float f_ret_1d = _ret1" in pine
    # Unused features are not emitted.
    assert "f_trend_slope_60" not in pine

    # Alias, decision chain, negated constant.
    assert "float vol = f_rv_20d" in pine
    assert "int sig = if vol > 0.02 and f_rsv_20 < 0.3" in pine
    assert "else if f_dist_high_20 > -0.01 or f_ret_1d < -0.03" in pine
    assert "\n    1\n" in pine
    assert "\n    -1\n" in pine

    # Entries, exits, markers.
    assert 'strategy.entry("L", strategy.long)' in pine
    assert 'strategy.entry("S", strategy.short)' in pine
    assert 'strategy.close_all(comment="sig flat")' in pine
    assert "plotshape(open_long" in pine
    assert "plotshape(open_short" in pine
    # Horizon note required by the lab's payoff definition.
    assert "open" in pine and "horizon" in pine.lower()

    # Explicit-hash export resolves the same candidate; --out writes the file.
    out = tmp_path / "out" / "golden.pine"
    again = export(run_dir, candidate_hash=digest, out=out)
    assert again == pine
    assert out.read_text(encoding="utf-8") == pine


def test_unsupported_constructs_raise(tmp_path: Path):
    bad_candidates = [
        # for-loop inside signal()
        'LABEL = "x"\n\ndef signal(symbol, features):\n    for i in range(3):\n        pass\n    return 0\n',
        # module-level import
        'import math\n\nLABEL = "x"\n\ndef signal(symbol, features):\n    return 1\n',
        # function call on a feature
        'LABEL = "x"\n\ndef signal(symbol, features):\n    if abs(features["rv_20d"]) > 0.02:\n        return 1\n    return 0\n',
        # return value outside -1/0/1
        'LABEL = "x"\n\ndef signal(symbol, features):\n    return 2\n',
        # feature not in the daily family and no dataset-native mapping
        'LABEL = "x"\n\ndef signal(symbol, features):\n    if features["mystery"] > 1:\n        return 1\n    return 0\n',
        # feature-vs-feature comparison
        'LABEL = "x"\n\ndef signal(symbol, features):\n    if features["rv_20d"] > features["rv_5d"]:\n        return 1\n    return 0\n',
    ]
    for source in bad_candidates:
        run_dir, _ = _make_run(tmp_path, source)
        with pytest.raises(PineExportError):
            export(run_dir)


def test_evidence_header_carries_scores(tmp_path: Path):
    run_dir, _ = _make_run(tmp_path, GOLDEN)
    pine = export(run_dir)
    assert "robust_portfolio_score=1.2345" in pine
    assert "net_trade_mean_bps=42.5" in pine
    assert "trades=17" in pine
    assert "gate_penalty=0" in pine


def test_chart_undefinable_features_become_constants(tmp_path: Path):
    source = (
        'LABEL = "xs"\n\ndef signal(symbol, features):\n'
        "    if features['xs_rank_ret_1d'] > 0.6 and features['event_next_session'] < 0.5:\n"
        "        return 1\n    return 0\n"
    )
    run_dir, _ = _make_run(tmp_path, source)
    pine = export(run_dir)
    assert "float f_xs_rank_ret_1d = 0.5" in pine
    assert "float f_event_next_session = 0.0" in pine
    assert "// Cross-sectional rank has no single-chart definition" in pine
    assert "staged FOMC/CPI/NFP calendar" in pine or "staged-calendar" in pine


def test_dataset_native_credit_frac(tmp_path: Path):
    source = (
        'LABEL = "credit"\n\ndef signal(symbol, features):\n'
        "    if features['credit_frac'] > 0.1 and features['day_of_week'] < 2.5:\n"
        "        return 1\n    return 0\n"
    )
    run_dir, _ = _make_run(tmp_path, source)
    pine = export(run_dir)
    assert 'input.float(0.0, "credit_frac placeholder")' in pine
    assert "// TODO: credit_frac is dataset-native" in pine
    assert "float f_day_of_week = dayofweek - 2" in pine
    assert "int sig = if f_credit_frac > 0.1 and f_day_of_week < 2.5" in pine


def test_no_accepted_candidate_raises(tmp_path: Path):
    run_dir, _ = _make_run(tmp_path, GOLDEN, accepted=False)
    with pytest.raises(PineExportError, match="accepted"):
        export(run_dir)
