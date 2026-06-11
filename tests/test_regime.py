import numpy as np

from quantsys.config.schema import RegimeConfig
from quantsys.core.types import InstrumentKind
from quantsys.regime.detector import RegimeDetector
from quantsys.regime.hmm import filtered_probs, fit_hmm
from tests.conftest import gbm, make_hist, make_inst, make_state


def test_hmm_recovers_two_state_structure():
    rng = np.random.default_rng(42)
    # 2 states: low-vol around +0.5, high-vol around -0.5 (1-D)
    z = (rng.random(600) < 0.5).astype(int)
    # make persistent regimes (sticky chain), not iid switches
    for i in range(1, 600):
        if rng.random() < 0.95:
            z[i] = z[i - 1]
    X = np.where(z == 0, rng.normal(0.5, 0.2, 600), rng.normal(-0.5, 0.6, 600))[:, None]
    params = fit_hmm(X, n_states=2, seed=0, n_restarts=3)
    assert params.converged and not params.degenerate
    means = sorted(params.means[:, 0])
    assert abs(means[0] - (-0.5)) < 0.15
    assert abs(means[1] - 0.5) < 0.15
    # filtered probs are a valid distribution and decisive on clean data
    p = filtered_probs(params, X)
    assert p.sum() == np.float64(1.0) or abs(p.sum() - 1.0) < 1e-9
    assert p.max() > 0.6


def test_hmm_transition_stickiness_learned():
    rng = np.random.default_rng(7)
    z = np.zeros(800, dtype=int)
    for i in range(1, 800):
        z[i] = z[i - 1] if rng.random() < 0.97 else 1 - z[i - 1]
    X = np.where(z == 0, rng.normal(1.0, 0.3, 800), rng.normal(-1.0, 0.3, 800))[:, None]
    params = fit_hmm(X, n_states=2, seed=1, n_restarts=3)
    assert np.diag(params.transmat).min() > 0.85


def _detector_state(closes, cfg):
    nifty = make_inst("NIFTY", kind=InstrumentKind.INDEX)
    bars = {"NIFTY": make_hist(closes)}
    return make_state(bars, {"NIFTY": nifty})


def test_detector_flags_turbulence_and_cuts_risk():
    cfg = RegimeConfig(timeframe_bars=1, train_window=300, refit_every=50,
                       n_restarts=2, n_iter=80)
    det = RegimeDetector(cfg, "NIFTY")
    calm = gbm(400, vol=0.001, seed=3)
    turb = calm[-1] * np.exp(np.cumsum(np.random.default_rng(4).normal(0, 0.012, 80)))
    closes = np.concatenate([calm, turb])
    rs = det.update(_detector_state(closes, cfg))
    assert rs.source in ("hmm", "fallback")
    assert rs.probs["turbulent"] > 0.5
    assert rs.risk_scaler < 0.7  # turbulent regime de-leverages


def test_detector_warmup_is_neutral_and_fallback_safe():
    cfg = RegimeConfig(timeframe_bars=1, train_window=300)
    det = RegimeDetector(cfg, "NIFTY")
    rs = det.update(_detector_state(gbm(20, seed=5), cfg))
    assert rs.source == "warmup"
    assert abs(sum(rs.probs.values()) - 1.0) < 1e-9


def test_detector_state_roundtrip():
    cfg = RegimeConfig(timeframe_bars=1, train_window=200, refit_every=40,
                       n_restarts=2, n_iter=60)
    det = RegimeDetector(cfg, "NIFTY")
    closes = gbm(500, vol=0.004, seed=6)
    st = _detector_state(closes, cfg)
    r1 = det.update(st)
    det2 = RegimeDetector(cfg, "NIFTY")
    det2.load_state(det.state_dict())
    r2 = det2.update(st)
    assert r1.label == r2.label
    assert abs(r1.risk_scaler - r2.risk_scaler) < 0.15
