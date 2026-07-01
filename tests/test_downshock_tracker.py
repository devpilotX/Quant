"""Down-shock forward-tracker core logic — synthetic, no data/network.

Verifies the FROZEN signal: a 4σ + high-volume down day followed by continued
idiosyncratic down-drift opens a market-neutral SHORT that books positive forward
P&L; and that events before the tracking-start date are excluded (so the forward
record is genuinely out-of-sample).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantsys.research.downshock_tracker import HOLD, down_events, forward_record


def _panel(seed=0):
    idx = pd.bdate_range("2020-01-01", periods=350)
    rng = np.random.default_rng(seed)
    cols = ["A", "B", "C"]
    rets = pd.DataFrame(rng.normal(0, 0.012, (350, 3)), index=idx, columns=cols)
    vol = pd.DataFrame(np.full((350, 3), 1e6), index=idx, columns=cols)
    d = 300
    rets.iloc[d, 0] = -0.09                 # ~7σ down shock
    vol.iloc[d, 0] = 5e6                    # 5x volume
    for k in range(1, HOLD + 1):
        rets.iloc[d + k, 0] = -0.005        # continued idiosyncratic down-drift
    return rets, vol, idx, d


def test_downshock_detected():
    rets, vol, idx, d = _panel()
    evs = down_events(rets, vol, list(rets.columns), idx)
    assert any(s == "A" and i == d for s, i in evs)


def test_forward_short_books_positive_pnl():
    rets, vol, idx, d = _panel()
    start = idx[d - 5]                       # event is AFTER tracking-start
    fwd, evs = forward_record(rets, vol, idx, start)
    assert any(s == "A" for s, _ in evs)
    assert fwd.sum() > 0                      # shorting a down-drifting stock profits


def test_events_before_tracking_start_excluded():
    rets, vol, idx, d = _panel()
    start = idx[d + 20]                       # tracking starts AFTER the event
    fwd, evs = forward_record(rets, vol, idx, start)
    assert evs == []                          # no forward events
    assert abs(float(fwd.sum())) < 1e-9
