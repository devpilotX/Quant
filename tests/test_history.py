from datetime import datetime, timedelta

import numpy as np

from quantsys.core.types import Bar
from quantsys.data.history import BarHistory


def _bar(i: int, c: float) -> Bar:
    t = datetime(2026, 1, 5, 9, 15) + timedelta(minutes=5 * i)
    return Bar(ts=t, open=c - 1, high=c + 2, low=c - 2, close=c, volume=10 * i)


def test_append_and_views():
    h = BarHistory(capacity=100)
    for i in range(10):
        h.append(_bar(i, 100 + i))
    assert len(h) == 10
    assert h.close[-1] == 109
    assert h.last_close == 109
    np.testing.assert_allclose(h.close, 100 + np.arange(10))


def test_capacity_keeps_newest_and_compacts():
    h = BarHistory(capacity=50)
    for i in range(500):  # forces multiple compactions
        h.append(_bar(i, float(i)))
    assert len(h) == 50
    np.testing.assert_allclose(h.close, np.arange(450, 500, dtype=float))


def test_resample_end_aligned():
    h = BarHistory(capacity=100)
    for i in range(10):
        h.append(_bar(i, 100 + i))
    rs = h.resampled(3)  # 10 bars -> last 9 used, 3 groups: [1..3],[4..6],[7..9]
    np.testing.assert_allclose(rs["close"], [103, 106, 109])
    np.testing.assert_allclose(rs["open"], [100, 103, 106])  # open of each group
    np.testing.assert_allclose(rs["high"], [105, 108, 111])  # close+2 of last in group
    np.testing.assert_allclose(rs["low"], [99, 102, 105])    # close-2 of first in group
    np.testing.assert_allclose(rs["volume"], [60, 150, 240])
    assert h.resampled(11) == {}
