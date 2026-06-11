"""Diagonal-covariance Gaussian HMM with log-domain EM and forward filtering.

Written in-repo (~150 lines) rather than importing hmmlearn so the regime
layer has zero exotic dependencies, deterministic seeding, and explicit
degeneracy reporting. Sized for T ~ 1000 observations, K <= 4 states — EM
runs in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.special import logsumexp

_LOG2PI = float(np.log(2.0 * np.pi))


@dataclass
class HMMParams:
    startprob: np.ndarray   # (K,)
    transmat: np.ndarray    # (K, K)
    means: np.ndarray       # (K, D)
    variances: np.ndarray   # (K, D)
    loglik: float = -np.inf
    converged: bool = False
    degenerate: bool = True
    occupancy: np.ndarray = field(default_factory=lambda: np.array([]))

    def to_dict(self) -> dict:
        return {
            "startprob": self.startprob.tolist(),
            "transmat": self.transmat.tolist(),
            "means": self.means.tolist(),
            "variances": self.variances.tolist(),
            "loglik": float(self.loglik),
            "converged": self.converged,
            "degenerate": self.degenerate,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HMMParams":
        return cls(
            startprob=np.array(d["startprob"]),
            transmat=np.array(d["transmat"]),
            means=np.array(d["means"]),
            variances=np.array(d["variances"]),
            loglik=d.get("loglik", -np.inf),
            converged=d.get("converged", False),
            degenerate=d.get("degenerate", False),
        )


def _log_emissions(X: np.ndarray, means: np.ndarray, variances: np.ndarray) -> np.ndarray:
    """(T, K) log N(x_t | mu_k, diag(var_k))."""
    diff = X[:, None, :] - means[None, :, :]            # (T, K, D)
    return -0.5 * np.sum(_LOG2PI + np.log(variances)[None] + diff * diff / variances[None], axis=2)


def _forward(log_b: np.ndarray, log_pi: np.ndarray, log_A: np.ndarray) -> tuple[np.ndarray, float]:
    T, K = log_b.shape
    log_alpha = np.empty((T, K))
    log_alpha[0] = log_pi + log_b[0]
    for t in range(1, T):
        log_alpha[t] = log_b[t] + logsumexp(log_alpha[t - 1][:, None] + log_A, axis=0)
    return log_alpha, float(logsumexp(log_alpha[-1]))


def _backward(log_b: np.ndarray, log_A: np.ndarray) -> np.ndarray:
    T, K = log_b.shape
    log_beta = np.zeros((T, K))
    for t in range(T - 2, -1, -1):
        log_beta[t] = logsumexp(log_A + (log_b[t + 1] + log_beta[t + 1])[None, :], axis=1)
    return log_beta


def fit_hmm(
    X: np.ndarray,
    n_states: int,
    n_iter: int = 150,
    tol: float = 1e-7,
    seed: int = 0,
    n_restarts: int = 4,
    var_floor: float = 1e-6,
    min_occupancy: float = 0.02,
) -> HMMParams:
    """EM fit with seeded restarts; returns the best-likelihood solution."""
    X = np.asarray(X, dtype=float)
    T, D = X.shape
    best: HMMParams | None = None

    for r in range(n_restarts):
        rng = np.random.default_rng(seed + 1000 * r)
        # init: jittered quantile means on dim 0, global variance, sticky A
        order = np.argsort(X[:, 0])
        chunks = np.array_split(order, n_states)
        means = np.array([X[idx].mean(axis=0) for idx in chunks])
        means += rng.normal(0, 0.15, means.shape) * (X.std(axis=0) + 1e-9)
        variances = np.tile(np.maximum(X.var(axis=0), var_floor), (n_states, 1))
        pi = np.full(n_states, 1.0 / n_states)
        A = np.full((n_states, n_states), 0.10 / max(n_states - 1, 1))
        np.fill_diagonal(A, 0.90)

        ll_prev = -np.inf
        converged = False
        gamma = np.full((T, n_states), 1.0 / n_states)
        ll = -np.inf
        for _ in range(n_iter):
            log_b = _log_emissions(X, means, variances)
            log_alpha, ll = _forward(log_b, np.log(pi), np.log(A))
            log_beta = _backward(log_b, np.log(A))
            gamma = np.exp(log_alpha + log_beta - ll)
            # xi summed over t: (T-1, K, K) tensor, fine at this scale
            log_xi = (
                log_alpha[:-1, :, None]
                + np.log(A)[None]
                + (log_b[1:] + log_beta[1:])[:, None, :]
                - ll
            )
            xi_sum = np.exp(log_xi).sum(axis=0)

            pi = np.clip(gamma[0], 1e-12, None)
            pi /= pi.sum()
            A = xi_sum / np.clip(xi_sum.sum(axis=1, keepdims=True), 1e-12, None)
            A = np.clip(A, 1e-8, None)
            A /= A.sum(axis=1, keepdims=True)
            g_sum = np.clip(gamma.sum(axis=0), 1e-12, None)
            means = (gamma.T @ X) / g_sum[:, None]
            diff = X[:, None, :] - means[None]
            variances = np.einsum("tk,tkd->kd", gamma, diff * diff) / g_sum[:, None]
            variances = np.maximum(variances, var_floor)

            if abs(ll - ll_prev) < tol * max(abs(ll_prev), 1.0):
                converged = True
                break
            ll_prev = ll

        occupancy = gamma.sum(axis=0) / T
        degenerate = bool(occupancy.min() < min_occupancy)
        cand = HMMParams(pi, A, means, variances, ll, converged, degenerate, occupancy)
        if best is None or cand.loglik > best.loglik:
            best = cand
    assert best is not None
    return best


def filtered_probs(params: HMMParams, X: np.ndarray) -> np.ndarray:
    """P(z_T | x_1..T): online state probabilities at the last observation."""
    log_b = _log_emissions(np.asarray(X, dtype=float), params.means, params.variances)
    log_alpha, _ = _forward(log_b, np.log(params.startprob), np.log(params.transmat))
    last = log_alpha[-1]
    return np.exp(last - logsumexp(last))
