"""The HMM recovers planted regimes, labels them by their statistics, and the
filter never sees a future candle."""

from __future__ import annotations

import numpy as np
import pytest

from research.hmm.filter import entropy, forward_filter, next_state_probs
from research.hmm.model import admissible, describe, fit_k, label_states, match_labels, select_and_fit


def _two_regime_tape(n: int = 3000, seed: int = 3):
    """Planted regimes: calm up-drift (low vol) and stress (high vol, negative drift), sticky."""
    rng = np.random.default_rng(seed)
    A = np.array([[0.98, 0.02], [0.05, 0.95]])
    state = 0
    states, ret, rvol = [], [], []
    for _ in range(n):
        state = rng.choice(2, p=A[state])
        states.append(state)
        r = rng.normal(0.0008, 0.006) if state == 0 else rng.normal(-0.002, 0.02)
        ret.append(r)
        rvol.append(abs(rng.normal(0.006 if state == 0 else 0.02, 0.001)))
    ret = np.array(ret)
    rvol = np.array(rvol)
    X = np.column_stack([ret / 0.012, (rvol - 0.012) / 0.007])
    return X, ret, np.array(states)


def test_fit_recovers_two_planted_states_and_labels_them():
    X, ret, truth = _two_regime_tape()
    m = fit_k(X, 2)
    fitted = describe(m, X, ret)
    assert set(fitted.labels) == {"CALM_UP", "STRESS"}
    probs = forward_filter(fitted.startprob, fitted.transmat, fitted.means, fitted.covars, X)
    hard = probs.argmax(axis=1)
    # agreement with the planted path up to label permutation
    agree = max((hard == truth).mean(), (hard != truth).mean())
    assert agree > 0.85
    stress = fitted.labels.index("STRESS")
    assert fitted.state_stats[stress]["vol"] > fitted.state_stats[1 - stress]["vol"]
    assert fitted.expected_durations.min() > 5


def test_selection_prefers_the_simpler_model_on_a_two_regime_tape():
    X, ret, _ = _two_regime_tape(n=4000)
    fitted = select_and_fit(X[:3000], X[3000:], ret[:3000], states=(2, 3, 4))
    assert fitted.n_states in (2, 3)
    assert fitted.selection["chosen_k"] == fitted.n_states and len(fitted.selection["candidates"]) == 3


def test_filtered_probabilities_never_depend_on_future_candles():
    X, ret, _ = _two_regime_tape(n=1500)
    m = fit_k(X, 2)
    full = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X)
    cut = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X[:900])
    np.testing.assert_allclose(full[:900], cut, rtol=0, atol=1e-12)
    altered = X.copy()
    altered[900:] = altered[900:] * 5 + 3  # garbage after the cut
    again = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, altered)
    np.testing.assert_allclose(full[:900], again[:900], rtol=0, atol=1e-12)


def _overlapping_tape(n: int = 2000, seed: int = 5):
    """Regimes whose emissions overlap, so the smoother's look-ahead matters."""
    rng = np.random.default_rng(seed)
    A = np.array([[0.97, 0.03], [0.06, 0.94]])
    state, states, ret = 0, [], []
    for _ in range(n):
        state = rng.choice(2, p=A[state])
        states.append(state)
        ret.append(rng.normal(0.0005, 0.010) if state == 0 else rng.normal(-0.001, 0.016))
    ret = np.array(ret)
    return np.column_stack([ret / 0.013, np.abs(ret) / 0.013]), ret, np.array(states)


def test_filtering_differs_from_smoothing_which_is_forbidden():
    X, ret, _ = _overlapping_tape()
    m = fit_k(X, 2)
    filtered = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X)
    smoothed = m.predict_proba(X)  # hmmlearn's posteriors use the whole series (forward-backward)
    assert np.abs(filtered - smoothed).max() > 0.05  # the smoother peeks ahead; the filter cannot
    assert np.allclose(filtered.sum(axis=1), 1.0)


def test_live_continuation_equals_filtering_the_whole_series():
    X, ret, _ = _two_regime_tape(n=1200)
    m = fit_k(X, 2)
    whole = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X)
    first = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X[:800])
    rest = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X[800:], prior=first[-1])
    np.testing.assert_allclose(np.vstack([first, rest]), whole, atol=1e-12)
    nxt = next_state_probs(whole[-1], m.transmat_)
    assert nxt.sum() == pytest.approx(1.0)
    assert 0.0 <= entropy(whole).max() <= 1.0


def test_labels_follow_statistics_and_survive_a_refit():
    labels = label_states(np.array([0.001, 0.0, -0.003, -0.0005]), np.array([0.005, 0.004, 0.03, 0.02]))
    assert labels == ("CALM_UP", "CHOP", "CRASH", "STRESS")
    X, ret, _ = _two_regime_tape(n=2500, seed=11)
    a = describe(fit_k(X[:2000], 2, seed=1), X[:2000], ret[:2000])
    b = describe(fit_k(X, 2, seed=99), X, ret)
    assert sorted(match_labels(a, b)) == sorted(a.labels)


def test_a_tail_state_is_not_a_regime():
    good = ({"state": 0, "share": 0.6, "expected_duration": 12.0}, {"state": 1, "share": 0.4, "expected_duration": 8.0})
    assert admissible(good) == (True, "ok")
    thin = good + ({"state": 2, "share": 0.0001, "expected_duration": 1.0},)
    ok, why = admissible(thin)
    assert ok is False and "0.01%" in why
    brief = good + ({"state": 2, "share": 0.1, "expected_duration": 2.0},)
    ok, why = admissible(brief)
    assert ok is False and "lasts 2.0" in why
    X, ret, _ = _two_regime_tape(n=4000)
    fitted = select_and_fit(X[:3000], X[3000:], ret[:3000], states=(2, 3, 4, 5))
    assert all("admissible" in r for r in fitted.selection["candidates"])
    assert all(s["share"] >= 0.02 and s["expected_duration"] >= 3 for s in fitted.state_stats)
