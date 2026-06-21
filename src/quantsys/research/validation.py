"""Part-D anti-overfit machinery not already in the engine harness.

The existing `backtest/metrics.py` has deflated Sharpe + Monte-Carlo bootstrap.
The two pieces the spec asks for that were missing:

  * pbo_cscv  — Probability of Backtest Overfitting via Combinatorially-Symmetric
                Cross-Validation (Bailey, Borwein, Lopez de Prado & Zhu, 2015).
                Answers: "across all symmetric IS/OOS block splits, how often does
                the config that looked best in-sample land in the worse OOS half?"
                PBO near 0 = robust selection; near 1 = the selection is overfit.

  * purged_kfold — purged & embargoed K-fold CV (Lopez de Prado, AFML ch.7):
                removes train observations whose label window overlaps the test
                fold (purge) plus an embargo after it, so serially-correlated
                leakage cannot flatter OOS performance.

Both are generic (operate on a (T x N) per-period return matrix or an index
length) and are unit-tested on synthetic data with no network or market deps.
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np


def _sharpe(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if x.size < 2:
        return float("-inf")
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd > 0 else float("-inf")


def pbo_cscv(returns: np.ndarray, n_splits: int = 12,
             max_combos: int = 5000, seed: int = 7) -> dict:
    """PBO over a (T observations x N configs) per-period return matrix.

    T is split into `n_splits` contiguous equal blocks (n_splits even). For each
    way to choose half the blocks as IS (complement = OOS): pick the IS-best
    config, find its OOS rank; logit of the relative rank gives lambda. PBO =
    fraction of splits whose IS-best config is below the OOS median (lambda < 0).
    """
    R = np.asarray(returns, dtype=float)
    if R.ndim != 2 or R.shape[1] < 2:
        raise ValueError("returns must be (T, N) with N >= 2 configs")
    if n_splits % 2 != 0:
        raise ValueError("n_splits must be even")
    T, N = R.shape
    blocks = np.array_split(np.arange(T), n_splits)
    combos = list(combinations(range(n_splits), n_splits // 2))
    rng = np.random.default_rng(seed)
    if len(combos) > max_combos:
        idx = rng.choice(len(combos), size=max_combos, replace=False)
        combos = [combos[i] for i in idx]

    logits: list[float] = []
    n_overfit = 0
    for is_blocks in combos:
        is_set = set(is_blocks)
        is_rows = np.concatenate([blocks[b] for b in range(n_splits) if b in is_set])
        oos_rows = np.concatenate([blocks[b] for b in range(n_splits) if b not in is_set])
        is_perf = np.array([_sharpe(R[is_rows, n]) for n in range(N)])
        oos_perf = np.array([_sharpe(R[oos_rows, n]) for n in range(N)])
        n_star = int(np.argmax(is_perf))
        # relative OOS rank of the IS-best config (1 = worst ... N = best)
        order = np.argsort(np.argsort(oos_perf))  # ranks 0..N-1
        rank = order[n_star] + 1
        omega = rank / (N + 1)
        omega = min(max(omega, 1e-6), 1 - 1e-6)
        lam = math.log(omega / (1 - omega))
        logits.append(lam)
        if lam <= 0:        # IS-best fell into the worse OOS half
            n_overfit += 1

    logits_a = np.array(logits)
    return {
        "pbo": n_overfit / len(combos),
        "n_combos": len(combos),
        "n_splits": n_splits,
        "n_configs": N,
        "logit_mean": float(logits_a.mean()),
        "logit_median": float(np.median(logits_a)),
    }


def purged_kfold(n: int, n_splits: int = 5, embargo_pct: float = 0.01,
                 label_span: int = 1) -> list[tuple[np.ndarray, np.ndarray]]:
    """Purged & embargoed K-fold splits over a length-`n` ordered index.

    Each contiguous test fold purges train observations within `label_span` of the
    fold (label overlap) and embargoes `embargo_pct * n` observations immediately
    after it. Returns (train_idx, test_idx) per fold.
    """
    idx = np.arange(n)
    folds = np.array_split(idx, n_splits)
    embargo = int(n * embargo_pct)
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for f in folds:
        t0, t1 = f[0], f[-1]
        lo = t0 - label_span
        hi = t1 + label_span + embargo
        train_mask = (idx < lo) | (idx > hi)
        out.append((idx[train_mask], f))
    return out


def cv_sharpe_stability(returns: np.ndarray, n_splits: int = 5,
                        embargo_pct: float = 0.02) -> dict:
    """Per-fold OOS Sharpe of a single return stream under purged K-fold —
    a quick read on how stable an edge is across disjoint time blocks."""
    r = np.asarray(returns, dtype=float)
    sh = [_sharpe(r[test]) for _, test in purged_kfold(len(r), n_splits, embargo_pct)]
    sh = [s for s in sh if math.isfinite(s)]
    if not sh:
        return {"folds": 0}
    return {
        "folds": len(sh),
        "sharpe_min": float(min(sh)),
        "sharpe_mean": float(np.mean(sh)),
        "sharpe_max": float(max(sh)),
        "frac_positive": float(np.mean([s > 0 for s in sh])),
    }
