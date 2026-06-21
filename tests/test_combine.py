"""Unit tests for the S1×S2 combiner (network-free, deterministic)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantsys.research import combine as C


def _fno(pcr_vals, fut_vals=None):
    idx = pd.bdate_range("2022-01-01", periods=len(pcr_vals))
    fut = fut_vals if fut_vals is not None else np.linspace(100, 200, len(pcr_vals))
    return pd.DataFrame({"date": idx, "pcr": pcr_vals, "fut_close": fut,
                         "fut_expiry": pd.Timestamp("2030-01-01"), "ssf": [[]] * len(idx)})


def test_s1_contrarian_direction_long_on_high_pcr():
    # PCR steps UP after a flat warmup -> big +z -> contrarian LONG
    pcr = np.concatenate([np.full(80, 1.0), np.full(40, 5.0)])
    pos = C.s1_positions(_fno(pcr), L=60, thr=1.0)
    assert pos.iloc[85] == 1.0          # just after the up-step: long
    assert (pos.iloc[81:90] == 1.0).any()


def test_s1_contrarian_direction_short_on_low_pcr():
    pcr = np.concatenate([np.full(80, 2.0), np.full(40, 0.2)])
    pos = C.s1_positions(_fno(pcr), L=60, thr=1.0)
    assert pos.iloc[85] == -1.0         # excess calls -> short


def test_s1_no_lookahead_return_uses_prior_position():
    # return on day t must use position decided at t-1; first earned day is shifted
    pcr = np.concatenate([np.full(80, 1.0), np.full(40, 5.0)])
    r = C.s1_returns(_fno(pcr))
    assert r.index[0] > _fno(pcr)["date"].iloc[0]   # shifted (no day-0 return)
    assert np.isfinite(r.values).all()


def test_inverse_vol_downweights_the_high_vol_sleeve():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2021-01-01", periods=400)
    r_low = pd.Series(rng.normal(0.0005, 0.002, 400), index=idx)    # low vol
    r_high = pd.Series(rng.normal(0.0005, 0.02, 400), index=idx)    # 10x vol
    combo = C.inverse_vol_combine(r_low, r_high, vol_lookback=63)
    # inverse-vol puts ~0.9 weight on the low-vol sleeve, so combo vol sits far
    # closer to the low-vol sleeve than to the high-vol one (equal risk contrib).
    cv, lv, hv = combo.std(), r_low.std(), r_high.std()
    assert abs(cv - lv) < abs(cv - hv)
    assert cv < hv / 3


def test_combine_of_identical_streams_is_that_stream():
    idx = pd.bdate_range("2021-01-01", periods=200)
    r = pd.Series(np.linspace(-0.01, 0.01, 200), index=idx)
    combo = C.inverse_vol_combine(r, r.copy(), vol_lookback=63)
    assert np.allclose(combo.values, r.values, atol=1e-9)


def test_ssf_asof_returns_membership_at_or_before_date():
    fno = pd.DataFrame({
        "date": pd.to_datetime(["2023-01-31", "2023-02-28"]),
        "pcr": [1.0, 1.0],
        "ssf": [["AAA", "BBB"], ["AAA", "BBB", "CCC"]],
    })
    dates, sets = C._ssf_lookup(fno)
    assert C._ssf_asof(pd.Timestamp("2023-02-15"), dates, sets) == {"AAA", "BBB"}
    assert C._ssf_asof(pd.Timestamp("2023-03-15"), dates, sets) == {"AAA", "BBB", "CCC"}
