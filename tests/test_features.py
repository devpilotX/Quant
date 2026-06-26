import numpy as np
import pandas as pd
import pytest

from quantsys.data import features as F


def test_ema_matches_pandas():
    x = np.random.default_rng(0).normal(100, 5, 300)
    ours = F.ema(x, span=21)
    ref = pd.Series(x).ewm(span=21, adjust=False).mean().to_numpy()
    np.testing.assert_allclose(ours, ref, rtol=1e-10)


def test_ewma_vol_matches_manual():
    r = np.random.default_rng(1).normal(0, 0.01, 500)
    hl = 30.0
    lam = F.ewma_lambda(hl)
    w = lam ** np.arange(len(r) - 1, -1, -1)
    w /= w.sum()
    expected = np.sqrt(np.sum(w * r * r))
    assert F.ewma_vol(r, hl) == pytest.approx(expected, rel=1e-12)
    assert 0.005 < F.ewma_vol(r, hl) < 0.02


def test_atr_wilder_reference():
    rng = np.random.default_rng(2)
    c = 100 + np.cumsum(rng.normal(0, 1, 300))
    h, lo = c + 1.0, c - 1.0
    ours = F.atr_series(h, lo, c, n=14)
    tr = pd.Series(F.true_range(h, lo, c))
    ref = tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    # Wilder seeding differs early; the difference decays as (1-1/14)^t
    np.testing.assert_allclose(ours[250:], ref[250:], rtol=1e-4)
    assert np.isfinite(F.atr(h, lo, c, 14))


def test_donchian_excludes_current_bar():
    h = np.array([10, 11, 12, 13, 99.0])
    lo = np.array([9, 8, 7, 6, 1.0])
    hh, ll = F.donchian(h, lo, n=4)
    assert hh == 13.0 and ll == 6.0


def test_ewma_cov_psd_and_shrinkage():
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.01, (500, 1))
    R = np.hstack([base + rng.normal(0, 0.002, (500, 1)),
                   base + rng.normal(0, 0.002, (500, 1)),
                   rng.normal(0, 0.01, (500, 1))])
    S0 = F.ewma_cov(R, halflife=100, shrink=0.0)
    S5 = F.ewma_cov(R, halflife=100, shrink=0.5)
    assert np.allclose(S0, S0.T)
    assert np.linalg.eigvalsh(S5).min() > 0
    np.testing.assert_allclose(np.diag(S0), np.diag(S5), rtol=1e-12)
    assert abs(S5[0, 1]) < abs(S0[0, 1])  # shrinkage damps correlations
    C = F.corr_from_cov(S0)
    assert C[0, 1] > 0.9 and abs(C[0, 2]) < 0.3


def test_log_returns():
    np.testing.assert_allclose(F.log_returns(np.array([100.0, 110.0])),
                               [np.log(1.1)], rtol=1e-12)
