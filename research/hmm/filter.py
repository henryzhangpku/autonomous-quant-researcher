"""Forward filtering only (spec: no_lookahead).

P(state_t | x_1..x_t), computed candle by candle with the forward recursion
alpha_t = normalize((alpha_{t-1} @ A) * b_t). No Viterbi, no smoothing: the
filtered probability at t is a function of candles <= t and nothing else,
which tests/test_hmm_model_filter.py proves by truncation. The next-candle
state distribution is alpha_t @ A.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import multivariate_normal


def emission_logprob(means: np.ndarray, covars: np.ndarray, X: np.ndarray) -> np.ndarray:
    """(n, k) log N(x_t; mu_i, Sigma_i)."""
    k = means.shape[0]
    out = np.empty((len(X), k))
    for i in range(k):
        out[:, i] = multivariate_normal(mean=means[i], cov=covars[i], allow_singular=True).logpdf(X)
    return out


def forward_filter(startprob: np.ndarray, transmat: np.ndarray, means: np.ndarray, covars: np.ndarray, X: np.ndarray, *, prior: np.ndarray | None = None) -> np.ndarray:
    """Filtered state probabilities, one row per candle, each summing to 1.

    `prior` lets a live loop continue from yesterday's last filtered row
    instead of the model's start distribution; the result is identical to
    filtering the concatenated series (the recursion is Markov)."""
    logb = emission_logprob(means, covars, X)
    n, k = logb.shape
    out = np.empty((n, k))
    alpha = (startprob if prior is None else prior).astype(float).copy()
    alpha = alpha / alpha.sum()
    for t in range(n):
        pred = alpha @ transmat if (t > 0 or prior is not None) else alpha
        logw = np.log(np.clip(pred, 1e-300, None)) + logb[t]
        logw -= logw.max()
        w = np.exp(logw)
        alpha = w / w.sum()
        out[t] = alpha
    return out


def next_state_probs(filtered_row: np.ndarray, transmat: np.ndarray) -> np.ndarray:
    return filtered_row @ transmat


def entropy(p: np.ndarray) -> np.ndarray:
    """Normalized posterior entropy in [0, 1], 0 = certain, 1 = uniform."""
    p = np.clip(p, 1e-12, 1.0)
    h = -(p * np.log(p)).sum(axis=-1)
    return h / np.log(p.shape[-1])
