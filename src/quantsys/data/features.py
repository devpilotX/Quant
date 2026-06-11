"""Quantitative feature kernels. Pure numpy/scipy, no state, no pandas in the
hot path. Every formula here is unit-tested against a reference implementation.

Conventions
-----------
- EWMA half-life parameterisation: lam = exp(ln(1/2) / halflife).
- Volatility uses the RiskMetrics zero-mean convention (EWMA of r^2);
  covariance demeans with EWMA means (matters for strategy-return inputs).
- ATR uses Wilder smoothing (alpha = 1/n).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import lfilter


def ewma_lambda(halflife: float) -> float:
    if halflife <= 0:
        raise ValueError("halflife must be positive")
    return math.exp(math.log(0.5) / halflife)


def log_returns(closes: np.ndarray) -> np.ndarray:
    c = np.asarray(closes, dtype=float)
    return np.diff(np.log(c))


def ema(x: np.ndarray, span: float) -> np.ndarray:
    """Recursive EMA (pandas ewm(span=..., adjust=False) semantics), ema[0]=x[0]."""
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return x
    alpha = 2.0 / (span + 1.0)
    # y[t] = alpha*x[t] + (1-alpha)*y[t-1]  ==  IIR filter b=[alpha], a=[1, alpha-1]
    zi = [(1.0 - alpha) * x[0]]
    y, _ = lfilter([alpha], [1.0, alpha - 1.0], x, zi=zi)
    return y


def ewma_vol(returns: np.ndarray, halflife: float) -> float:
    """Per-bar EWMA volatility (zero-mean convention), last value."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return float("nan")
    lam = ewma_lambda(halflife)
    w = lam ** np.arange(r.size - 1, -1, -1)
    w /= w.sum()
    return float(np.sqrt(np.sum(w * r * r)))


def ewma_vol_series(returns: np.ndarray, halflife: float) -> np.ndarray:
    """Full recursive EWMA vol series, var[0] seeded with overall variance."""
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return r
    lam = ewma_lambda(halflife)
    out = np.empty(r.size)
    v = float(np.nanvar(r)) or 1e-12
    for i, x in enumerate(r):
        v = lam * v + (1.0 - lam) * x * x
        out[i] = math.sqrt(v)
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    h, l, c = (np.asarray(a, dtype=float) for a in (high, low, close))
    prev_c = np.concatenate([[c[0]], c[:-1]])
    return np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Wilder ATR series (alpha = 1/n), seeded with the mean of the first n TRs."""
    tr = true_range(high, low, close)
    if tr.size < n + 1:
        return np.full(tr.size, np.nan)
    out = np.full(tr.size, np.nan)
    out[n - 1] = tr[:n].mean()
    alpha = 1.0 / n
    for i in range(n, tr.size):
        out[i] = out[i - 1] + alpha * (tr[i] - out[i - 1])
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> float:
    s = atr_series(high, low, close, n)
    return float(s[-1]) if s.size else float("nan")


def donchian(high: np.ndarray, low: np.ndarray, n: int) -> tuple[float, float]:
    """(highest high, lowest low) over the n bars PRECEDING the current bar."""
    h, l = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    if h.size < n + 1:
        return float("nan"), float("nan")
    return float(h[-(n + 1) : -1].max()), float(l[-(n + 1) : -1].min())


def ewma_cov(R: np.ndarray, halflife: float, shrink: float = 0.15) -> np.ndarray:
    """EWMA covariance of a (T, K) return matrix, shrunk toward its diagonal.

    Shrinkage targets the diagonal (variances kept, correlations damped):
    Sigma* = shrink * diag(Sigma) + (1 - shrink) * Sigma. This is the cheap,
    robust end of Ledoit-Wolf and is plenty for K <= ~40 instruments.
    """
    R = np.asarray(R, dtype=float)
    if R.ndim != 2 or R.shape[0] < 3:
        raise ValueError("need a (T>=3, K) return matrix")
    T, _ = R.shape
    lam = ewma_lambda(halflife)
    w = lam ** np.arange(T - 1, -1, -1)
    w /= w.sum()
    mu = w @ R
    X = R - mu
    S = (X * w[:, None]).T @ X
    if shrink > 0:
        S = shrink * np.diag(np.diag(S)) + (1.0 - shrink) * S
    return S


def corr_from_cov(S: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.clip(np.diag(S), 1e-18, None))
    C = S / np.outer(d, d)
    np.fill_diagonal(C, 1.0)
    return np.clip(C, -1.0, 1.0)


def aligned_close_matrix(histories: dict[str, "object"], symbols: list[str], window: int) -> np.ndarray | None:
    """Stack last `window` closes for symbols into (window, K); None if any lacks data."""
    cols = []
    for s in symbols:
        h = histories.get(s)
        if h is None or len(h) < window:
            return None
        cols.append(np.asarray(h.close[-window:], dtype=float))
    return np.column_stack(cols) if cols else None
