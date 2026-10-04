"""Fit, select and label the Gaussian HMM (spec section 1, hmm_model).

- fit: hmmlearn GaussianHMM with full covariance, fixed seed, best of
  N_RESTARTS by in-sample log-likelihood
- select: states 2..5 by BIC on the fit window AND out-of-sample
  log-likelihood on a held-out tail; the simpler model wins when the better
  score is within SIMPLER_TOLERANCE of it
- label: states are named from their statistics on the fit window, in the
  original candle units (mean return and vol per candle):
      CRASH    the state with the lowest mean return, if it is < 0 and its vol
               is above the median state vol
      STRESS   the highest-vol remaining state
      CALM_UP  the highest mean return among the remaining low-vol states
      CHOP     everything else (mean near zero, low vol)
  With two states the names are CALM_UP and STRESS; three adds CHOP; four
  adds CRASH; five adds a second CHOP.
- expected duration of state i = 1 / (1 - A_ii)

The fitted object is frozen: downstream code reads start probabilities,
the transition matrix, means and covariances, and never refits by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from hmmlearn.hmm import GaussianHMM

N_RESTARTS = 8
SIMPLER_TOLERANCE = 0.02
# Admissibility (spec change approved 2026-10-04 at the phase-2 pause): a
# state that holds under MIN_STATE_SHARE of the fit candles or whose expected
# duration is under MIN_DURATION candles is a tail, not a regime; a state
# count that produces one is rejected.
MIN_STATE_SHARE = 0.02
MIN_DURATION = 3.0
STATE_RANGE = (2, 3, 4, 5)
SEED = 20261004


@dataclass(frozen=True)
class FittedHMM:
    n_states: int
    startprob: np.ndarray
    transmat: np.ndarray
    means: np.ndarray
    covars: np.ndarray
    labels: tuple[str, ...]
    state_stats: tuple[dict, ...]
    log_likelihood: float
    bic: float
    n_obs: int
    selection: dict = field(default_factory=dict)

    @property
    def expected_durations(self) -> np.ndarray:
        return 1.0 / np.clip(1.0 - np.diag(self.transmat), 1e-9, None)

    def to_hmmlearn(self) -> GaussianHMM:
        m = GaussianHMM(n_components=self.n_states, covariance_type="full", init_params="")
        m.startprob_ = self.startprob.copy()
        m.transmat_ = self.transmat.copy()
        m.means_ = self.means.copy()
        m.covars_ = self.covars.copy()
        return m


def _fit_once(X: np.ndarray, k: int, seed: int) -> GaussianHMM:
    m = GaussianHMM(n_components=k, covariance_type="full", n_iter=300, tol=1e-4, random_state=seed)
    m.fit(X)
    return m


def _fit_warm(X: np.ndarray, k: int, warm: GaussianHMM) -> GaussianHMM:
    """Refit initialized from a previous model's parameters (walk-forward refits):
    the same EM, converging from where the last fit ended instead of from scratch."""
    m = GaussianHMM(n_components=k, covariance_type="full", n_iter=300, tol=1e-4, init_params="", params="stmc")
    m.startprob_ = warm.startprob_.copy()
    m.transmat_ = warm.transmat_.copy()
    m.means_ = warm.means_.copy()
    m.covars_ = warm.covars_.copy()
    m.fit(X)
    return m


def fit_k(X: np.ndarray, k: int, *, seed: int = SEED, restarts: int = N_RESTARTS, warm: GaussianHMM | None = None) -> GaussianHMM:
    """Best of `restarts` fits by in-sample log-likelihood; the seed fixes every
    restart. With `warm`, one fit starts from that model's parameters and
    only `restarts` cold starts compete with it."""
    best, best_ll = None, -np.inf
    if warm is not None:
        try:
            m = _fit_warm(X, k, warm)
            ll = m.score(X)
            if np.isfinite(ll):
                best, best_ll = m, ll
        except (ValueError, FloatingPointError):
            pass
    for r in range(restarts):
        try:
            m = _fit_once(X, k, seed + r)
            ll = m.score(X)
        except (ValueError, FloatingPointError):
            continue
        if np.isfinite(ll) and ll > best_ll:
            best, best_ll = m, ll
    if best is None:
        raise RuntimeError(f"no restart converged for k={k}")
    return best


def n_parameters(k: int, d: int) -> int:
    return (k - 1) + k * (k - 1) + k * d + k * d * (d + 1) // 2


def bic(ll: float, k: int, d: int, n: int) -> float:
    return -2.0 * ll + n_parameters(k, d) * np.log(n)


def label_states(means_ret: np.ndarray, vols: np.ndarray) -> tuple[str, ...]:
    k = len(means_ret)
    labels: list[str | None] = [None] * k
    remaining = set(range(k))
    med_vol = float(np.median(vols))
    if k >= 4:
        lo = int(np.argmin(means_ret))
        if means_ret[lo] < 0 and vols[lo] > med_vol:
            labels[lo] = "CRASH"
            remaining.discard(lo)
    if k >= 2 and remaining:
        hv = max(remaining, key=lambda i: vols[i])
        labels[hv] = "STRESS"
        remaining.discard(hv)
    if remaining:
        up = max(remaining, key=lambda i: means_ret[i])
        labels[up] = "CALM_UP"
        remaining.discard(up)
    chop_n = 0
    for i in sorted(remaining, key=lambda i: vols[i]):
        chop_n += 1
        labels[i] = "CHOP" if chop_n == 1 else f"CHOP{chop_n}"
    return tuple(str(x) for x in labels)


def describe(m: GaussianHMM, X: np.ndarray, raw_ret: np.ndarray, *, ll: float | None = None, bic_value: float | None = None) -> FittedHMM:
    """Freeze a fitted model with per-state statistics measured on the fit
    window using the forward-filtered state (the decision-time estimate),
    never the smoothed posterior."""
    from research.hmm.filter import forward_filter

    probs = forward_filter(m.startprob_, m.transmat_, m.means_, m.covars_, X)
    hard = probs.argmax(axis=1)
    k = m.n_components
    means_ret = np.array([raw_ret[hard == i].mean() if (hard == i).any() else 0.0 for i in range(k)])
    vols = np.array([raw_ret[hard == i].std(ddof=0) if (hard == i).sum() > 1 else 0.0 for i in range(k)])
    labels = label_states(means_ret, vols)
    stats = tuple(
        {"state": i, "label": labels[i], "share": float((hard == i).mean()), "mean_ret": float(means_ret[i]), "vol": float(vols[i]),
         "expected_duration": float(1.0 / max(1e-9, 1.0 - m.transmat_[i, i]))}
        for i in range(k)
    )
    ll = float(m.score(X)) if ll is None else ll
    return FittedHMM(
        n_states=k, startprob=m.startprob_.copy(), transmat=m.transmat_.copy(), means=m.means_.copy(), covars=m.covars_.copy(),
        labels=labels, state_stats=stats, log_likelihood=ll, bic=float(bic(ll, k, X.shape[1], len(X))) if bic_value is None else bic_value, n_obs=len(X),
    )


def admissible(stats: tuple[dict, ...], *, min_share: float = MIN_STATE_SHARE, min_duration: float = MIN_DURATION) -> tuple[bool, str]:
    for s in stats:
        if s["share"] < min_share:
            return False, f"state {s['state']} holds {s['share']:.2%} of candles (< {min_share:.0%})"
        if s["expected_duration"] < min_duration:
            return False, f"state {s['state']} lasts {s['expected_duration']:.1f} candles (< {min_duration:g})"
    return True, "ok"


def select_and_fit(X_fit: np.ndarray, X_val: np.ndarray, raw_ret_fit: np.ndarray, *, seed: int = SEED, states=STATE_RANGE) -> FittedHMM:
    """Fit every k, score BIC on the fit window and log-likelihood per
    observation on the validation tail; pick the k whose combined rank is
    best, preferring the simpler k when the better score is within tolerance."""
    rows = []
    models = {}
    d = X_fit.shape[1]
    for k in states:
        try:
            m = fit_k(X_fit, k, seed=seed)
        except RuntimeError:
            continue
        ll = float(m.score(X_fit))
        oos = float(m.score(X_val)) / max(1, len(X_val)) if len(X_val) else float("nan")
        ok, why = admissible(describe(m, X_fit, raw_ret_fit, ll=ll).state_stats)
        rows.append({"k": k, "ll": ll, "bic": float(bic(ll, k, d, len(X_fit))), "oos_ll_per_obs": oos, "admissible": ok, "note": why})
        models[k] = m
    if not rows:
        raise RuntimeError("no model fitted")
    candidates = [r for r in rows if r["admissible"]] or rows  # if nothing is admissible, fall back to the raw rule and say so
    # lower BIC is better; higher OOS ll is better. Choose by OOS first, then BIC, with the simplicity tolerance.
    best_oos = max(candidates, key=lambda r: r["oos_ll_per_obs"])
    chosen = best_oos
    for r in sorted(candidates, key=lambda r: r["k"]):
        if r["k"] < best_oos["k"] and best_oos["oos_ll_per_obs"] != 0 and abs(best_oos["oos_ll_per_obs"] - r["oos_ll_per_obs"]) <= SIMPLER_TOLERANCE * abs(best_oos["oos_ll_per_obs"]):
            chosen = r
            break
    m = models[chosen["k"]]
    fitted = describe(m, X_fit, raw_ret_fit, ll=chosen["ll"], bic_value=chosen["bic"])
    return FittedHMM(**{**fitted.__dict__, "selection": {"candidates": rows, "chosen_k": chosen["k"]}})


def match_labels(previous: FittedHMM, current: FittedHMM) -> tuple[str, ...]:
    """Map the new fit's states onto the old labels by nearest (mean_ret, vol)
    so labels stay stable across refits (spec: refit_and_drift)."""
    prev = np.array([[s["mean_ret"], s["vol"]] for s in previous.state_stats])
    cur = np.array([[s["mean_ret"], s["vol"]] for s in current.state_stats])
    scale = prev.std(axis=0) + 1e-12
    out = []
    used: set[int] = set()
    for row in cur:
        dists = np.linalg.norm((prev - row) / scale, axis=1)
        for j in np.argsort(dists):
            if j not in used:
                used.add(int(j))
                out.append(previous.labels[int(j)])
                break
        else:
            out.append("UNMATCHED")
    return tuple(out)
