"""Performance metrics for backtest results.

All formulas standard; the anti-self-deception pieces:
- deflated_sharpe (Bailey & Lopez de Prado 2014): probability the observed
  Sharpe exceeds the expected maximum Sharpe of `n_trials` zero-skill trials,
  accounting for non-normal returns. The sensitivity sweep feeds n_trials.
- monte_carlo_resample: stationary block bootstrap of daily returns ->
  distributions of Sharpe and max drawdown, so a single lucky path cannot
  pass the gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

TRADING_DAYS = 252
_PHI = NormalDist()


# ----------------------------------------------------------------- helpers
def _drawdown_stats(equity: np.ndarray) -> tuple[float, float]:
    """(max_drawdown, time_in_drawdown_fraction)"""
    peak = np.maximum.accumulate(equity)
    dd = np.where(peak > 0, (peak - equity) / peak, 0.0)
    return float(dd.max(initial=0.0)), float((dd > 1e-12).mean()) if len(dd) else 0.0


def _ann_sharpe(returns: np.ndarray) -> float | None:
    if len(returns) < 2:
        return None
    sd = returns.std(ddof=1)
    if sd <= 0:
        return None
    return float(returns.mean() / sd * math.sqrt(TRADING_DAYS))


# ------------------------------------------------------------------- main
def compute_metrics(daily_equity: list[tuple[object, float]],
                    trades: list,
                    total_fees: float,
                    traded_notional: float,
                    starting_equity: float,
                    gross_samples: list[float] | None = None,
                    equity_samples: list[float] | None = None) -> dict:
    eq = np.array([v for _, v in daily_equity], dtype=float)
    out: dict = {"n_days": int(len(eq))}
    if len(eq) < 3:
        out["insufficient_data"] = True
        return out

    rets = np.diff(eq) / eq[:-1]
    years = len(eq) / TRADING_DAYS
    max_dd, tid = _drawdown_stats(eq)

    out["total_return"] = float(eq[-1] / eq[0] - 1.0)
    out["cagr"] = float((eq[-1] / eq[0]) ** (1 / years) - 1.0) if years > 0 and eq[0] > 0 else None
    out["ann_vol"] = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(rets) > 1 else None
    out["sharpe"] = _ann_sharpe(rets)
    downside = np.minimum(rets, 0.0)
    dsd = math.sqrt(float((downside ** 2).mean()))
    out["sortino"] = (float(rets.mean()) / dsd * math.sqrt(TRADING_DAYS)) if dsd > 0 else None
    out["max_dd"] = max_dd
    out["time_in_dd"] = tid
    out["calmar"] = (out["cagr"] / max_dd) if out["cagr"] is not None and max_dd > 0 else None
    if len(rets) > 3:
        m = rets.mean()
        s = rets.std(ddof=1)
        if s > 0:
            z = (rets - m) / s
            out["skew"] = float((z ** 3).mean())
            out["kurtosis"] = float((z ** 4).mean())  # raw (normal = 3)

    # trade stats (completed round trips, net of their fees)
    if trades:
        net = [t.pnl - t.fees for t in trades]
        wins = [x for x in net if x > 0]
        losses = [x for x in net if x < 0]
        out["n_trades"] = len(trades)
        out["hit_rate"] = len(wins) / len(net)
        gp, gl = sum(wins), -sum(losses)
        out["profit_factor"] = (gp / gl) if gl > 0 else None
        out["avg_trade_net"] = float(np.mean(net))
    else:
        out["n_trades"] = 0

    out["total_fees"] = float(total_fees)
    out["traded_notional"] = float(traded_notional)
    out["cost_drag_bps"] = (total_fees / traded_notional * 1e4) if traded_notional > 0 else None
    avg_eq = float(eq.mean())
    out["turnover_x_per_year"] = (traded_notional / avg_eq / years) if avg_eq > 0 and years > 0 else None
    out["fee_drag_on_capital_ann"] = (total_fees / starting_equity / years) if years > 0 else None
    if gross_samples and equity_samples:
        ge = [g / e for g, e in zip(gross_samples, equity_samples) if e > 0]
        if ge:
            out["avg_gross_exposure_x"] = float(np.mean(ge))
            out["max_gross_exposure_x"] = float(np.max(ge))
    return out


# --------------------------------------------------- deflated Sharpe ratio
def expected_max_sharpe(n_trials: int, n_obs: int, sr_var: float | None = None) -> float:
    """E[max SR] of n_trials zero-skill strategies over n_obs periods
    (per-period units). Bailey/LdP using the Euler-Mascheroni approximation."""
    if n_trials <= 1:
        return 0.0
    if sr_var is None:
        sr_var = 1.0 / n_obs  # variance of a zero-mean SR estimator
    gamma = 0.5772156649015329
    e = (1 - gamma) * _PHI.inv_cdf(1 - 1.0 / n_trials) \
        + gamma * _PHI.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_var) * e


def deflated_sharpe(sr_ann: float | None, n_obs: int, skew: float = 0.0,
                    kurtosis: float = 3.0, n_trials: int = 1) -> float | None:
    """P[true SR > 0 | observed SR, after multiple-testing deflation].
    Values near 1 are good; below ~0.95 the edge is not distinguishable from
    selection bias. sr_ann is annualised; computation in per-day units."""
    if sr_ann is None or n_obs < 10:
        return None
    sr = sr_ann / math.sqrt(TRADING_DAYS)          # per-day SR
    sr0 = expected_max_sharpe(n_trials, n_obs)     # deflation benchmark
    denom = math.sqrt(
        max(1e-12, (1 - skew * sr + (kurtosis - 1) / 4.0 * sr * sr) / (n_obs - 1))
    )
    return float(_PHI.cdf((sr - sr0) / denom))


# ------------------------------------------------------------ Monte Carlo
def monte_carlo_resample(daily_equity: list[tuple[object, float]],
                         n_paths: int = 1000, block: int = 5,
                         seed: int = 11) -> dict:
    """Moving-block bootstrap on daily returns. Returns distribution stats of
    Sharpe and max drawdown across resampled paths."""
    eq = np.array([v for _, v in daily_equity], dtype=float)
    if len(eq) < 10:
        return {"insufficient_data": True}
    rets = np.diff(eq) / eq[:-1]
    n = len(rets)
    rng = np.random.default_rng(seed)
    sharpes, mdds = [], []
    n_blocks = max(1, math.ceil(n / block))
    for _ in range(n_paths):
        starts = rng.integers(0, max(1, n - block), size=n_blocks)
        path = np.concatenate([rets[s:s + block] for s in starts])[:n]
        sr = _ann_sharpe(path)
        if sr is not None:
            sharpes.append(sr)
        curve = np.cumprod(1 + path)
        mdd, _ = _drawdown_stats(curve)
        mdds.append(mdd)
    sharpes_a, mdds_a = np.array(sharpes), np.array(mdds)

    def _pct(a: np.ndarray, q: float) -> float | None:
        return float(np.percentile(a, q)) if a.size else None

    # No positive-variance paths (flat equity / ~no trades) => no Sharpe samples.
    # Don't crash on np.percentile([]); report it and treat P(SR<0) as worst.
    return {
        "n_paths": n_paths,
        "sharpe_p05": _pct(sharpes_a, 5),
        "sharpe_p50": _pct(sharpes_a, 50),
        "sharpe_p95": _pct(sharpes_a, 95),
        "p_sharpe_negative": float((sharpes_a < 0).mean()) if sharpes_a.size else 1.0,
        "max_dd_p50": _pct(mdds_a, 50),
        "max_dd_p95": _pct(mdds_a, 95),
        "insufficient_data": sharpes_a.size == 0,
    }
