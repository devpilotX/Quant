"""Cross-sectional equity-factor library (Pillar 2), price/volume only.

Everything here is computable from the bhavcopy panel with NO fundamentals, so it
is honest about what free point-in-time data supports. Factors implemented:

  * momentum    — 12-1 month total return (skip the most recent month)
  * low_vol     — negative trailing daily-return volatility (low-risk anomaly)
  * reversal    — negative last-month return (short-term mean reversion)
  * illiquidity — Amihud |ret|/turnover (illiquidity premium)

Factors NOT implemented (require point-in-time fundamentals that free data does
not give without look-ahead/survivorship bias): value (P/B, P/E), quality (ROE,
accruals), and true size (market cap needs shares outstanding). These are left as
documented stubs rather than fabricated — see `UNAVAILABLE_FACTORS`.

Corporate actions: bhavcopy prices are UNADJUSTED. NSE enforces ~±20% intraday
price bands, so any overnight gap beyond the band is a split/bonus, not a return.
On such days we use the intraday close/open return and treat the gap as the CA
adjustment; otherwise close/prevclose. A final winsorize at the band catches
residual data errors. This removes split/bonus contamination while preserving
genuine circuit-limit moves (verified on the RELIANCE 1:1 bonus, 2024-10-28).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

UNAVAILABLE_FACTORS = {
    "value": "needs PIT P/B or P/E (fundamentals); not in bhavcopy",
    "quality": "needs PIT ROE / accruals / leverage (fundamentals)",
    "size": "needs shares outstanding for market cap (fundamentals)",
}


# ----------------------------------------------------------- panel preparation
def daily_returns(tidy: pd.DataFrame, ca_band: float = 0.20) -> pd.DataFrame:
    """CA-adjusted daily simple returns, wide (date × symbol).

    ret = close/open-1 on corporate-action days (|open/prevclose-1| > band),
    else close/prevclose-1; winsorized to ±band as a backstop."""
    df = tidy.copy()
    o, c, pc = df["open"], df["close"], df["prevclose"]
    gap = o / pc - 1.0
    intraday = c / o - 1.0
    normal = c / pc - 1.0
    is_ca = gap.abs() > ca_band
    df["ret"] = np.where(is_ca & np.isfinite(o) & (o > 0), intraday, normal)
    wide = df.pivot_table(index="date", columns="symbol", values="ret", aggfunc="last")
    return wide.clip(-ca_band, ca_band).sort_index()


def adjusted_prices(rets: pd.DataFrame) -> pd.DataFrame:
    """Back-adjusted price index from CA-adjusted returns (starts at 1.0)."""
    return (1.0 + rets.fillna(0.0)).cumprod()


def wide(tidy: pd.DataFrame, field: str) -> pd.DataFrame:
    return tidy.pivot_table(index="date", columns="symbol", values=field, aggfunc="last").sort_index()


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Last available trading date in each calendar month."""
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).last().values)


# ------------------------------------------------------------ point-in-time uni
def eligible_universe(
    turnover: pd.DataFrame,
    close: pd.DataFrame,
    asof: pd.Timestamp,
    *,
    top_n: int = 200,
    adv_window: int = 60,
    min_price: float = 20.0,
    min_history: int = 252,
) -> list[str]:
    """Point-in-time tradeable set at `asof`: the `top_n` names by trailing median
    ₹-turnover, with price >= min_price and >= min_history days of data. Uses only
    data up to and including `asof`, so it is survivorship-bias-free."""
    hist = turnover.loc[:asof]
    if len(hist) < adv_window:
        return []
    adv = hist.tail(adv_window).median()
    px = close.loc[:asof].tail(1).squeeze()
    n_obs = close.loc[:asof].notna().sum()
    ok = adv.index[
        (adv > 0)
        & (px.reindex(adv.index) >= min_price)
        & (n_obs.reindex(adv.index).fillna(0) >= min_history)
    ]
    ranked = adv.loc[ok].sort_values(ascending=False)
    return list(ranked.head(top_n).index)


# --------------------------------------------------------------------- factors
# Convention: higher factor value => more attractive LONG.
def f_momentum(adjp: pd.DataFrame, asof: pd.Timestamp, lookback: int = 252, skip: int = 21) -> pd.Series:
    h = adjp.loc[:asof]
    if len(h) <= lookback:
        return pd.Series(dtype=float)
    p_skip = h.iloc[-1 - skip]
    p_start = h.iloc[-1 - lookback]
    return (p_skip / p_start - 1.0).replace([np.inf, -np.inf], np.nan)


def f_lowvol(rets: pd.DataFrame, asof: pd.Timestamp, lookback: int = 252) -> pd.Series:
    h = rets.loc[:asof].tail(lookback)
    if len(h) < lookback // 2:
        return pd.Series(dtype=float)
    return -h.std()  # low vol = attractive


def f_reversal(adjp: pd.DataFrame, asof: pd.Timestamp, lookback: int = 21) -> pd.Series:
    h = adjp.loc[:asof]
    if len(h) <= lookback:
        return pd.Series(dtype=float)
    return -(h.iloc[-1] / h.iloc[-1 - lookback] - 1.0).replace([np.inf, -np.inf], np.nan)


def f_illiquidity(rets: pd.DataFrame, turnover: pd.DataFrame, asof: pd.Timestamp, lookback: int = 60) -> pd.Series:
    r = rets.loc[:asof].tail(lookback).abs()
    t = turnover.loc[:asof].tail(lookback)
    amihud = (r / t.replace(0.0, np.nan)).mean()
    return amihud.replace([np.inf, -np.inf], np.nan)  # high illiquidity = attractive


FACTORS = {
    "momentum": lambda P: f_momentum(P["adjp"], P["asof"]),
    "lowvol": lambda P: f_lowvol(P["rets"], P["asof"]),
    "reversal": lambda P: f_reversal(P["adjp"], P["asof"]),
    "illiquidity": lambda P: f_illiquidity(P["rets"], P["turnover"], P["asof"]),
}


# ------------------------------------------------------------------- transforms
def zscore(s: pd.Series, winsor: float = 3.0) -> pd.Series:
    s = s.dropna()
    if len(s) < 3 or s.std(ddof=0) == 0:
        return pd.Series(0.0, index=s.index)
    z = (s - s.mean()) / s.std(ddof=0)
    return z.clip(-winsor, winsor)


def compute_factor(name: str, rets, adjp, turnover, asof, universe: list[str]) -> pd.Series:
    """One factor's cross-section at `asof`, restricted to `universe`, z-scored."""
    raw = FACTORS[name]({"rets": rets, "adjp": adjp, "turnover": turnover, "asof": asof})
    raw = raw.reindex(universe).dropna()
    return zscore(raw)
