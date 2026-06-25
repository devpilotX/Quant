"""Event-study core (Pillar 4 foundation) — unit tests on synthetic data.

No market data, no network: we generate returns with a KNOWN market model and,
in the signal case, a KNOWN injected abnormal return, then check the engine
recovers it and that the significance tests behave (reject under signal, do not
reject under the null). Look-ahead protection is asserted structurally.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantsys.research.event_study import (
    Event,
    EventWindows,
    abnormal_returns,
    fit_market_model,
    run_event_study,
)


def test_market_model_recovers_alpha_beta():
    rng = np.random.default_rng(0)
    r_m = rng.normal(0.0004, 0.01, 4000)
    r_i = 0.0003 + 1.25 * r_m + rng.normal(0, 1e-4, 4000)
    m = fit_market_model(r_i, r_m)
    assert m.alpha == pytest.approx(0.0003, abs=5e-5)
    assert m.beta == pytest.approx(1.25, abs=0.02)
    assert m.resid_std == pytest.approx(1e-4, rel=0.1)


def test_abnormal_returns_zero_on_perfect_fit():
    rng = np.random.default_rng(1)
    r_m = rng.normal(0, 0.01, 500)
    r_i = 0.001 + 0.9 * r_m                      # no idiosyncratic noise
    m = fit_market_model(r_i, r_m)
    ar = abnormal_returns(r_i, r_m, m)
    assert np.allclose(ar, 0.0, atol=1e-12)


def test_event_windows_block_look_ahead():
    EventWindows(est=(-250, -30), event=(-1, 1))         # ok
    EventWindows(est=(-250, -2), event=(-1, 1))          # ends day before -> ok
    with pytest.raises(ValueError):
        EventWindows(est=(-250, -1), event=(-1, 1))      # estimation touches event
    with pytest.raises(ValueError):
        EventWindows(est=(-30, -250), event=(-1, 1))     # inverted bounds


def _make_events(n, drift, *, seed, noise=0.008, day0=270, length=300):
    """n synthetic events; `drift` abnormal return injected on the event day."""
    rng = np.random.default_rng(seed)
    events = []
    for _ in range(n):
        r_m = rng.normal(0.0004, 0.01, length)
        r_i = 0.0003 + 1.1 * r_m + rng.normal(0, noise, length)
        r_i[day0] += drift                       # inject on day 0 only
        events.append(Event("SYM", r_i, r_m, day0))
    return events


def test_signal_event_recovered_and_significant():
    events = _make_events(60, drift=0.02, seed=7)
    res = run_event_study(events, EventWindows(est=(-250, -30), event=(-1, 1)))
    assert res.n_events == 60
    assert res.caar == pytest.approx(0.02, abs=0.004)    # recovered the injection
    assert res.bmp_p < 1e-3                               # parametric: strongly significant
    assert res.rank_p < 0.05                              # non-parametric agrees
    assert res.significant
    assert res.frac_positive > 0.8


def test_null_event_not_significant():
    events = _make_events(60, drift=0.0, seed=3)
    res = run_event_study(events, EventWindows(est=(-250, -30), event=(-1, 1)))
    assert res.n_events == 60
    assert abs(res.caar) < 0.006                          # ~ zero
    assert res.bmp_p > 0.05                               # does NOT reject the null
    assert not res.significant


def test_insufficient_events_return_nan():
    # day0 too early: estimation window runs off the front -> all events skipped
    bad = _make_events(5, drift=0.02, seed=1, day0=50, length=120)
    res = run_event_study(bad, EventWindows(est=(-250, -30), event=(-1, 1)))
    assert res.n_events == 0
    assert np.isnan(res.bmp_t) and np.isnan(res.caar)


def test_drift_window_is_directional():
    # a positive post-event drift over [+2,+60] should be picked up there too
    rng = np.random.default_rng(11)
    events = []
    for _ in range(50):
        r_m = rng.normal(0.0004, 0.01, 400)
        r_i = 0.0003 + 1.0 * r_m + rng.normal(0, 0.006, 400)
        r_i[272:331] += 0.0008                  # small steady drift over +2..+60
        events.append(Event("SYM", r_i, r_m, 270))
    res = run_event_study(events, EventWindows(est=(-250, -30), event=(2, 60)))
    assert res.caar > 0.02                       # ~ 59 * 0.0008
    assert res.bmp_p < 0.05
