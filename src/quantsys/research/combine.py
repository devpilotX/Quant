"""Combine the two OOS-survivors (S1 BANKNIFTY-PCR, S2 factor momentum) and test,
ONCE, against the pre-registered gate (see docs/COMBINE_SURVIVORS.md).

Pure functions for each sleeve's daily return stream + an inverse-vol combiner +
an evaluator. No look-ahead: every signal is decided at close and earns the next
bar; weights use only trailing data. Costs come from the engine's real CostModel.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantsys.backtest.metrics import compute_metrics, deflated_sharpe, monte_carlo_resample
from quantsys.config.schema import CostConfig
from quantsys.core.types import Instrument, InstrumentKind
from quantsys.costs import CostModel
from quantsys.research import factors as F

IS_END = "2023-12-31"
OOS_START = "2024-01-01"
ANN = 252


# ----------------------------------------------------------------- cost helpers
def _one_way_frac(cost_model: CostModel, kind: InstrumentKind, delivery: bool) -> float:
    inst = Instrument(symbol="X", token="", exchange="NSE", kind=kind, lot_size=1,
                      tick_size=0.05, point_value=1.0, sector="x", adv=5_000_000, margin_rate=1.0)
    rt = cost_model.round_trip(inst, qty=10_000, price=500.0, delivery=delivery)
    return rt / (2.0 * 10_000 * 500.0)


# --------------------------------------------------------------------- S1 sleeve
def s1_positions(fno: pd.DataFrame, L: int = 60, thr: float = 1.0) -> pd.Series:
    """Contrarian PCR position decided at each close (no look-ahead): z of PCR over
    trailing L days; +1 when z>=thr (excess puts), -1 when z<=-thr (excess calls)."""
    df = fno.dropna(subset=["pcr"]).sort_values("date").set_index("date")
    pcr = df["pcr"]
    z = (pcr - pcr.rolling(L).mean()) / pcr.rolling(L).std()
    pos = pd.Series(0.0, index=df.index)
    pos[z >= thr] = 1.0
    pos[z <= -thr] = -1.0
    return pos


def s1_returns(fno: pd.DataFrame, *, L: int = 60, thr: float = 1.0,
               cost_model: CostModel | None = None) -> pd.Series:
    """BANKNIFTY PCR-OI contrarian on near-month futures (decided at close d,
    earned d+1; futures cost on position changes + monthly roll)."""
    cm = cost_model or CostModel(CostConfig())
    one_way = _one_way_frac(cm, InstrumentKind.FUTURE, delivery=False)
    df = fno.dropna(subset=["pcr", "fut_close"]).sort_values("date").set_index("date")
    pos = s1_positions(fno, L, thr).reindex(df.index)
    r_fut = df["fut_close"].pct_change()
    roll = df["fut_expiry"] != df["fut_expiry"].shift(1)
    r_fut = r_fut.mask(roll, 0.0)               # drop inter-contract basis gap
    held = pos.shift(1)
    base = held * r_fut
    trade = one_way * (pos.shift(1) - pos.shift(2)).abs()
    rollc = 2.0 * one_way * held.abs() * roll.astype(float)
    return (base - trade - rollc).dropna().rename("S1")


# --------------------------------------------------------------------- S2 sleeve
@dataclass(frozen=True)
class S2Config:
    top_k: int = 20
    top_n_universe: int = 200
    adv_window: int = 60
    min_price: float = 20.0
    short_financing_ann: float = 0.015     # 1.5%/yr conservative SSF roll+borrow drag


def _as_set(s) -> set:
    """ssf may round-trip from parquet as a numpy array, list, or NaN."""
    if s is None or (isinstance(s, float)):
        return set()
    try:
        return set(s)
    except TypeError:
        return set()


def _ssf_lookup(fno: pd.DataFrame) -> tuple[list[pd.Timestamp], dict]:
    f = fno.dropna(subset=["pcr"]).sort_values("date")
    dates = list(pd.to_datetime(f["date"]))
    sets = {pd.Timestamp(d): _as_set(s) for d, s in zip(f["date"], f["ssf"])}
    return dates, sets


def _ssf_asof(asof, dates, sets) -> set:
    prior = [d for d in dates if d <= asof]
    return sets.get(prior[-1], set()) if prior else set()


def s2_returns(panel: pd.DataFrame, fno: pd.DataFrame, cfg: S2Config | None = None,
               cost_model: CostModel | None = None, prepared: dict | None = None) -> pd.Series:
    """Market-neutral 12-1 momentum; shorts restricted to PIT SSF names, with
    futures cost + financing drag; longs delivery-equity. Daily return stream."""
    cfg = cfg or S2Config()
    cm = cost_model or CostModel(CostConfig())
    ow_long = _one_way_frac(cm, InstrumentKind.EQUITY, delivery=True)
    ow_short = _one_way_frac(cm, InstrumentKind.FUTURE, delivery=False)
    fin_daily = cfg.short_financing_ann / ANN

    from quantsys.research.xs_backtest import prepare_matrices
    P = prepared or prepare_matrices(panel)
    rets, adjp, turnover, close = P["rets"], P["adjp"], P["turnover"], P["close"]
    cal = rets.index
    rebal = F.month_end_dates(cal)
    ssf_dates, ssf_sets = _ssf_lookup(fno)

    w_prev = pd.Series(dtype=float)
    daily: list[tuple[pd.Timestamp, float]] = []
    for i, t in enumerate(rebal[:-1]):
        nxt = rebal[i + 1]
        uni = F.eligible_universe(turnover, close, t, top_n=cfg.top_n_universe,
                                  adv_window=cfg.adv_window, min_price=cfg.min_price)
        if len(uni) < cfg.top_k * 2:
            continue
        score = F.compute_factor("momentum", rets, adjp, turnover, t, uni).dropna().sort_values()
        if len(score) < cfg.top_k * 2:
            continue
        longs = list(score.tail(cfg.top_k).index)
        ssf = _ssf_asof(t, ssf_dates, ssf_sets)
        shorts = [s for s in score.index if s in ssf][: cfg.top_k]   # worst-ranked SSF names
        if not shorts:
            continue
        w = pd.Series(0.0, index=uni, dtype=float)
        w[longs] = 1.0 / len(longs)
        w[shorts] = -1.0 / len(shorts)                              # dollar-neutral

        dw = w.subtract(w_prev, fill_value=0.0)
        # rebalance turnover cost, charged per leg at its own rate
        cost_reb = (dw.reindex(longs).abs().sum() * ow_long
                    + dw.reindex(shorts).abs().sum() * ow_short)
        w_prev = w

        hold = cal[(cal > t) & (cal <= nxt)]
        short_gross = float(w[w < 0].abs().sum())
        for j, d in enumerate(hold):
            r = rets.loc[d].reindex(w.index).fillna(0.0)
            pr = float((w * r).sum()) - (cost_reb if j == 0 else 0.0) - short_gross * fin_daily
            daily.append((d, pr))

    if not daily:
        return pd.Series(dtype=float, name="S2")
    idx = [d for d, _ in daily]
    return pd.Series([r for _, r in daily], index=idx, name="S2")


# --------------------------------------------------------------------- combiner
def inverse_vol_combine(r1: pd.Series, r2: pd.Series, vol_lookback: int = 63) -> pd.Series:
    """Risk-parity (inverse-vol) blend, weights recomputed monthly from trailing
    `vol_lookback`-day vol, applied forward. No look-ahead."""
    df = pd.concat([r1.rename("S1"), r2.rename("S2")], axis=1, sort=True).dropna()
    if df.empty:
        return pd.Series(dtype=float, name="COMBO")
    rebal = set(F.month_end_dates(df.index))
    w1 = w2 = 0.5
    out = []
    for t in df.index:
        if t in rebal:
            v1 = df["S1"].loc[:t].tail(vol_lookback).std()
            v2 = df["S2"].loc[:t].tail(vol_lookback).std()
            if v1 > 0 and v2 > 0:
                i1, i2 = 1.0 / v1, 1.0 / v2
                w1, w2 = i1 / (i1 + i2), i2 / (i1 + i2)
        out.append(w1 * df["S1"].loc[t] + w2 * df["S2"].loc[t])
    return pd.Series(out, index=df.index, name="COMBO")


# -------------------------------------------------------------------- evaluation
def _curve(r: pd.Series, e0=1_000_000.0) -> list[tuple]:
    eq = e0 * (1.0 + r).cumprod()
    return [(d, v) for d, v in zip(eq.index, eq.values)]


def evaluate(r: pd.Series, market: pd.Series, n_trials: int = 3) -> dict:
    """IS/OOS Sharpe, OOS deflated, P(SR<0), net CAGR, maxDD, beta."""
    def sub(lo=None, hi=None):
        s = r
        if lo:
            s = s[s.index >= pd.Timestamp(lo)]
        if hi:
            s = s[s.index <= pd.Timestamp(hi)]
        return s
    is_r, oos_r = sub(hi=IS_END), sub(lo=OOS_START)
    m_oos = compute_metrics(_curve(oos_r), [], 0.0, 0.0, 1_000_000.0)
    is_sh = float(is_r.mean() / is_r.std() * np.sqrt(ANN)) if is_r.std() > 0 else None
    defl = deflated_sharpe(m_oos.get("sharpe"), m_oos.get("n_days", 0),
                           m_oos.get("skew", 0.0), m_oos.get("kurtosis", 3.0), n_trials)
    mc = monte_carlo_resample(_curve(oos_r))
    beta = None
    j = pd.concat([oos_r.rename("r"), market.rename("m")], axis=1, sort=True).dropna()
    if len(j) > 30 and j["m"].std() > 0:
        beta = float(np.cov(j["r"], j["m"])[0, 1] / np.var(j["m"]))
    return {"is_sharpe": is_sh, "oos_sharpe": m_oos.get("sharpe"),
            "oos_deflated": defl, "oos_p_sr_neg": mc.get("p_sharpe_negative"),
            "oos_cagr": m_oos.get("cagr"), "oos_maxdd": m_oos.get("max_dd"), "beta": beta}
