"""Unit tests for the Pillar-2 research package (network-free, deterministic)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quantsys.research import bhavcopy as bc
from quantsys.research import factors as F
from quantsys.research import validation as V

# --------------------------------------------------------------- bhavcopy parse
_UDIFF = (
    "TradDt,Sgmt,FinInstrmTp,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
    "PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
    "2025-06-02,CM,STK,INE001,AAA,EQ,100,110,99,105,100,1000,105000\n"
    "2025-06-02,CM,STK,INE002,BBB,BE,50,52,49,51,50,500,25500\n"      # BE: dropped
    "2025-06-02,CM,IDX,INE003,NIFTY,EQ,,,,,,,\n"                       # index junk
)
_LEGACY = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
    "AAA,EQ,100,110,99,105,105,100,1000,105000,01-JAN-2020,50,INE001,\n"
    "CCC,BE,10,11,9,10,10,10,5,50,01-JAN-2020,2,INE004,\n"            # BE: dropped
)


def test_parse_udiff_keeps_only_eq_and_normalises():
    rows = bc.parse_udiff(_UDIFF, date(2025, 6, 2))
    assert [r["symbol"] for r in rows] == ["AAA"]
    r = rows[0]
    assert r["close"] == 105 and r["prevclose"] == 100 and r["turnover"] == 105000
    assert r["isin"] == "INE001"


def test_parse_legacy_keeps_only_eq():
    rows = bc.parse_legacy(_LEGACY, date(2020, 1, 1))
    assert [r["symbol"] for r in rows] == ["AAA"]
    assert rows[0]["volume"] == 1000


def test_source_format_selection_by_date():
    assert bc._sources_for(date(2020, 1, 1))[0].fmt == "legacy"
    assert bc._sources_for(date(2025, 1, 1))[0].fmt == "udiff"


# ------------------------------------------------------- corporate-action return
def test_ca_adjusted_return_uses_intraday_on_bonus_day():
    # Day 2 is a 1:1 bonus: prevclose 2000 but open/close ~1000 (true ret ~ +1%)
    tidy = pd.DataFrame([
        {"date": pd.Timestamp("2024-01-01"), "symbol": "X", "open": 1980, "high": 2010,
         "low": 1970, "close": 2000, "prevclose": 1975, "volume": 1, "turnover": 1, "isin": "I"},
        {"date": pd.Timestamp("2024-01-02"), "symbol": "X", "open": 1000, "high": 1020,
         "low": 990, "close": 1010, "prevclose": 2000, "volume": 1, "turnover": 1, "isin": "I"},
    ])
    rets = F.daily_returns(tidy, ca_band=0.20)
    # naive close/prevclose would be ~ -49.5%; CA-aware should be intraday +1%
    assert rets.loc[pd.Timestamp("2024-01-02"), "X"] == pytest.approx(0.01, abs=1e-6)


def test_returns_winsorized_to_band():
    tidy = pd.DataFrame([
        {"date": pd.Timestamp("2024-01-01"), "symbol": "X", "open": 100, "high": 100,
         "low": 100, "close": 100, "prevclose": 100, "volume": 1, "turnover": 1, "isin": "I"},
        {"date": pd.Timestamp("2024-01-02"), "symbol": "X", "open": 100, "high": 100,
         "low": 100, "close": 130, "prevclose": 100, "volume": 1, "turnover": 1, "isin": "I"},
    ])
    # +30% with no opening gap is treated as a real (winsorized) move, capped at band
    assert rets_max(F.daily_returns(tidy, ca_band=0.20)) == pytest.approx(0.20)


def rets_max(df):
    return float(np.nanmax(df.values))


# --------------------------------------------------------------- factor sign
def _ramp_panel():
    idx = pd.bdate_range("2022-01-01", periods=300)
    # WIN ramps up, LOSE ramps down -> momentum(WIN) > momentum(LOSE)
    adjp = pd.DataFrame({
        "WIN": np.linspace(100, 300, len(idx)),
        "LOSE": np.linspace(300, 100, len(idx)),
        "FLAT": np.full(len(idx), 100.0),
    }, index=idx)
    return adjp


def test_momentum_sign():
    adjp = _ramp_panel()
    mom = F.f_momentum(adjp, adjp.index[-1], lookback=252, skip=21)
    assert mom["WIN"] > mom["FLAT"] > mom["LOSE"]


def test_reversal_sign():
    adjp = _ramp_panel()
    rev = F.f_reversal(adjp, adjp.index[-1], lookback=21)
    # recent winner gets a NEGATIVE reversal score (expected to mean-revert down)
    assert rev["WIN"] < rev["LOSE"]


def test_zscore_zero_mean_unit_scale():
    z = F.zscore(pd.Series([1.0, 2, 3, 4, 5]))
    assert z.mean() == pytest.approx(0.0, abs=1e-9)
    assert z.std(ddof=0) == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------- PBO
def test_pbo_high_for_pure_noise():
    rng = np.random.default_rng(0)
    R = rng.standard_normal((400, 20))   # 20 zero-skill configs
    out = V.pbo_cscv(R, n_splits=10)
    assert 0.30 <= out["pbo"] <= 0.70    # noise => selection is a coin-flip-ish


def test_pbo_low_for_one_genuine_edge():
    rng = np.random.default_rng(1)
    R = rng.standard_normal((400, 20)) * 0.01
    R[:, 0] += 0.02                      # config 0 has a real, persistent edge
    out = V.pbo_cscv(R, n_splits=10)
    assert out["pbo"] < 0.10


# ------------------------------------------------------------- purged K-fold
def test_purged_kfold_partitions_and_embargoes():
    splits = V.purged_kfold(100, n_splits=5, embargo_pct=0.05, label_span=1)
    assert len(splits) == 5
    all_test = np.concatenate([te for _, te in splits])
    assert sorted(all_test.tolist()) == list(range(100))   # test folds partition
    for train, test in splits:
        # no train index within label_span+embargo after the test fold
        assert test.max() + 1 not in train  # purged neighbour
        assert len(np.intersect1d(train, test)) == 0


# --------------------------------------------- cross-sectional backtester (e2e)
def _synth_panel(n_sym=60, n_days=420, edge=0.0, seed=0):
    """Tidy panel; each symbol has persistent drift q_i*edge so momentum both
    measures and predicts q_i (a genuine factor when edge>0, noise when edge=0)."""
    from quantsys.research.xs_backtest import BTConfig  # noqa: F401
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n_days)
    q = rng.standard_normal(n_sym)
    recs = []
    for j in range(n_sym):
        ret = q[j] * edge + rng.standard_normal(n_days) * 0.012
        price = 200.0 * np.exp(np.cumsum(ret))
        prev = np.concatenate([[200.0], price[:-1]])
        for d, c, pc in zip(idx, price, prev):
            recs.append({"date": d, "symbol": f"S{j:02d}", "open": pc, "high": max(c, pc) * 1.001,
                         "low": min(c, pc) * 0.999, "close": c, "prevclose": pc,
                         "volume": 1e5, "turnover": 1e8, "isin": f"I{j}"})
    return pd.DataFrame(recs)


def test_xs_backtest_recovers_injected_momentum_edge():
    from quantsys.research.xs_backtest import BTConfig, run_xs_backtest
    panel = _synth_panel(edge=0.0020, seed=3)
    cfg = BTConfig(factor=("momentum",), side="long_short", top_k=8,
                   top_n_universe=60, adv_window=40, min_price=1.0)
    res = run_xs_backtest(panel, cfg)
    assert res.metrics["sharpe"] > 0.8                      # edge is recovered
    assert abs(res.realized_beta) < 0.6                     # ~market-neutral


def test_xs_backtest_no_edge_on_noise():
    from quantsys.research.xs_backtest import BTConfig, run_xs_backtest
    edge = run_xs_backtest(_synth_panel(edge=0.0020, seed=3),
                           BTConfig(side="long_short", top_k=8, top_n_universe=60, adv_window=40, min_price=1.0))
    noise = run_xs_backtest(_synth_panel(edge=0.0, seed=5),
                            BTConfig(side="long_short", top_k=8, top_n_universe=60, adv_window=40, min_price=1.0))
    assert noise.metrics["sharpe"] < edge.metrics["sharpe"]  # noise underperforms the real edge
