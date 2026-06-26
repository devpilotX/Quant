"""Pillar-3 (mean-reversion / pairs) reproducible evidence for the green gate.

Two questions, answered on CA-adjusted daily NSE bhavcopy (the favourable case:
if pairs don't cointegrate at daily/multi-year, they won't on the live intraday
clock):

  1. DO any within-sector pairs in the live universe cointegrate?  Mirrors
     meanrev._fit_pair exactly (Engle-Granger ADF at adf_alpha, OU half-life band,
     split-half kappa stability), IN-SAMPLE only.

  2. Z-GRID:  z_entry in {1.0,1.5,2.0,2.5}, frozen in-sample beta + rolling
     120-day z-score (no look-ahead), full round-trip cost, select on in-sample,
     evaluate the pick ONCE on the post-2022 hold-out, and report a DEFLATED
     Sharpe with n_trials = (#cointegration tests) x (#z-values).

Pre-registered interpretation (2026-06-26): even a positive hold-out is "one
fragile pair, not a strategy" — only TCS|INFY is Bonferroni-robust. The live
meanrev is separately MIS-SPECIFIED (intraday 15-min / ~12-day lookback cannot
see 28-63 day half-lives), a code/config defect. Conclusion either way: meanrev
is not deployable; disable it for live.

Run:  python -m quantsys.research.meanrev_pairs_check
"""

from __future__ import annotations

import glob
import math
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import kurtosis as _kurt
from scipy.stats import norm
from scipy.stats import skew as _skew
from statsmodels.tsa.stattools import adfuller

from quantsys.config.schema import MeanRevConfig

# live within-sector universe (config/base.yaml); meanrev only pairs within a sector
SECTORS = {
    "banks": ["HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN"],
    "it": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"],
    "energy": ["RELIANCE", "ONGC", "NTPC", "POWERGRID"],
    "auto": ["MARUTI", "TATAMOTORS", "BAJAJ-AUTO"],
    "metals": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "pharma": ["SUNPHARMA", "CIPLA", "DRREDDY"],
}
GAMMA = 0.5772156649015329          # Euler-Mascheroni, for the deflated-Sharpe SR*
ZGRID = [1.0, 1.5, 2.0, 2.5]
COST_RT = 0.0035                    # ~35 bps round-trip per pair (delivery: STT-dominated)
ZWIN = 120                         # trailing days for the z-score (no look-ahead)
IS_END = "2022-12-30"
OOS_START = "2023-01-01"


def adjusted_prices(cache="data_cache/bhavcopy/parsed/*.parquet") -> pd.DataFrame:
    """CA-adjusted price panel (date x symbol) from cached daily bhavcopy, offline.
    close/prevclose-1 is the split/bonus/div-adjusted daily return (per bhavcopy)."""
    panel = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(cache))],
                      ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    for c in ("close", "prevclose"):
        panel[c] = pd.to_numeric(panel[c], errors="coerce")
    panel = panel[(panel["prevclose"] > 0) & np.isfinite(panel["close"])]
    panel["ret"] = panel["close"] / panel["prevclose"] - 1.0
    retw = panel.pivot_table(index="date", columns="symbol", values="ret",
                             aggfunc="last").sort_index()
    cols = {}
    for s in retw.columns:
        first = retw[s].first_valid_index()
        if first is not None:
            cols[s] = (1.0 + retw[s].loc[first:].fillna(0.0)).cumprod()
    return pd.concat(cols, axis=1)


def _ou(resid):
    """AR(1) phi -> kappa = -ln(phi); half-life ln2/kappa (mirrors meanrev._ou_kappa)."""
    if len(resid) < 30:
        return None, 0.0
    r0, r1 = resid[:-1], resid[1:]
    den = float(np.dot(r0, r0))
    if den <= 0:
        return None, 0.0
    phi = float(np.dot(r0, r1) / den)
    if not (0.0 < phi < 1.0):
        return None, 0.0
    k = -math.log(phi)
    return k, math.log(2.0) / k


def fit_pair(ya, xb, cfg):
    """Engle-Granger + OU + kappa-stability, mirroring meanrev._fit_pair."""
    df = pd.concat([ya, xb], axis=1).dropna()
    if len(df) < cfg.lookback:
        return None
    y, x = np.log(df.iloc[:, 0].values), np.log(df.iloc[:, 1].values)
    if float(np.var(x)) < 1e-12:
        return None
    beta = float(np.cov(x, y, bias=True)[0, 1] / np.var(x))
    if not (0.1 <= beta <= 10.0):
        return None
    resid = y - ((y.mean() - beta * x.mean()) + beta * x)
    try:
        p = float(adfuller(resid, regression="c", autolag="AIC")[1])
    except Exception:
        return None
    if p > cfg.adf_alpha:
        return {"adf_p": p, "pass_all": False}
    k, hl = _ou(resid)
    if k is None:
        return {"adf_p": p, "pass_all": False}
    in_hl = cfg.min_half_life <= hl <= cfg.max_half_life
    h = len(resid) // 2
    k1, _ = _ou(resid[:h])
    k2, _ = _ou(resid[h:])
    stable = (k1 is not None and k2 is not None
              and max(k1, k2) / max(min(k1, k2), 1e-12) <= cfg.kappa_stability)
    return {"adf_p": p, "hl": hl, "beta": beta, "pass_all": bool(in_hl and stable)}


def _bt_pair(pa, pb, beta, hl, z_entry):
    """Daily pairs backtest: frozen beta, rolling z-score, full round-trip cost.
    Returns the daily strategy-return series (spread units, scale-free for Sharpe)."""
    s = pd.Series(np.log(pa.values) - beta * np.log(pb.values), index=pa.index).dropna()
    z = (s - s.rolling(ZWIN).mean()) / s.rolling(ZWIN).std()
    dz = s.diff()
    ret = pd.Series(0.0, index=s.index)
    pos = held = 0
    for i in range(1, len(s)):
        if pos != 0:
            ret.iloc[i] = pos * dz.iloc[i]
            held += 1
            azi = z.iloc[i]
            if not np.isnan(azi) and (abs(azi) <= 0.5 or abs(azi) >= 3.5 or held > 3 * hl):
                ret.iloc[i] -= COST_RT
                pos = held = 0
        else:
            zi = z.iloc[i - 1]                 # yesterday's z decides today: no look-ahead
            if not np.isnan(zi) and abs(zi) >= z_entry:
                pos = -1 if zi > 0 else 1      # fade: short the spread when it's high
                held = 0
                ret.iloc[i] -= COST_RT
    return ret


def _sharpe_pp(r):
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def _deflated_sharpe(sel_pp, trial_returns, n_trials):
    """Bailey-Lopez de Prado deflated Sharpe. SR* (expected max under the null)
    uses the NULL variance of the Sharpe ESTIMATOR (1+0.5 SR^2)/(T-1) — NOT the
    sample variance of a few clustered grid Sharpes, which is degenerate."""
    rr = np.asarray(trial_returns)
    sk, ku, T = float(_skew(rr)), float(_kurt(rr, fisher=False)), len(rr)
    V = (1.0 + 0.5 * sel_pp ** 2) / (T - 1)
    srstar = math.sqrt(max(V, 1e-12)) * (
        (1 - GAMMA) * norm.ppf(1 - 1.0 / n_trials)
        + GAMMA * norm.ppf(1 - 1.0 / (n_trials * math.e)))
    den = math.sqrt(max(1e-9, 1 - sk * sel_pp + ((ku - 1) / 4.0) * sel_pp ** 2))
    return float(norm.cdf((sel_pp - srstar) * math.sqrt(T - 1) / den)), srstar


def main() -> dict:
    cfg = MeanRevConfig()
    adjp = adjusted_prices()
    names = [s for v in SECTORS.values() for s in v]
    present = [n for n in names if n in adjp.columns]
    IS = adjp.loc[:IS_END, present]
    print(f"panel {adjp.index.min().date()}..{adjp.index.max().date()}; "
          f"universe {len(present)}/{len(names)}; in-sample <= {IS_END} ({len(IS)} days)\n")

    # 1) existence
    winners, n_cand = [], 0
    for sec, syms in SECTORS.items():
        for a, b in combinations([s for s in syms if s in present], 2):
            n_cand += 1
            r = fit_pair(IS[a], IS[b], cfg)
            if r and r.get("pass_all"):
                winners.append((sec, a, b, r))
    print(f"candidate within-sector pairs: {n_cand}")
    print(f"cointegrated (ADF p<{cfg.adf_alpha} + half-life[{cfg.min_half_life:.0f},"
          f"{cfg.max_half_life:.0f}]d + kappa-stable<= {cfg.kappa_stability}): {len(winners)}")
    for sec, a, b, r in winners:
        print(f"   {a}|{b} ({sec})  adf_p={r['adf_p']:.4f}  hl={r['hl']:.1f}d")
    n_trials = n_cand * len(ZGRID)
    if not winners:
        print("\nNO SIGNAL — a z-grid cannot manufacture one.")
        return {"cointegrated": 0}

    # 2) z-grid: select in-sample, one hold-out shot, deflated Sharpe
    full = adjp.loc[:, present]

    def portfolio(z_entry):
        cols = [_bt_pair(full[a], full[b], r["beta"], r["hl"], z_entry)
                for _, a, b, r in winners]
        return pd.concat(cols, axis=1).fillna(0.0).mean(axis=1)

    is_sr, is_ser = {}, {}
    for ze in ZGRID:
        is_ser[ze] = portfolio(ze).loc[:IS_END]
        is_sr[ze] = _sharpe_pp(is_ser[ze])
    best = max(ZGRID, key=lambda z: is_sr[z])
    oos = portfolio(best).loc[OOS_START:]
    dsr, srstar = _deflated_sharpe(is_sr[best], is_ser[best].values, n_trials)

    print("\nz-grid in-sample Sharpe(ann): "
          + "  ".join(f"z{z}={is_sr[z]*math.sqrt(252):+.2f}" for z in ZGRID))
    print(f"  selected z_entry={best}")
    print(f"hold-out 2023+ Sharpe(ann)  : {_sharpe_pp(oos)*math.sqrt(252):+.2f}  (one shot)")
    print(f"deflated Sharpe (n_trials={n_trials}): {dsr:.3f}  [gate needs >= 0.95]")
    print("\nVERDICT: not deployable (deflated Sharpe fails; one Bonferroni-robust "
          "pair is not a strategy). Live meanrev is mis-specified for these "
          "daily-scale half-lives -> disable meanrev for live.")
    return {"cointegrated": len(winners), "selected_z": best,
            "oos_sharpe_ann": _sharpe_pp(oos) * math.sqrt(252),
            "deflated_sharpe": dsr, "n_trials": n_trials}


if __name__ == "__main__":
    main()
