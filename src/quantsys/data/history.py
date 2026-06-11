"""Fixed-capacity bar storage with contiguous numpy views and resampling.

A ``BarHistory`` holds the decision-clock bars for one instrument. Strategies
that operate on slower clocks resample by an integer factor, END-ALIGNED so the
most recent strategy bar always closes on the most recent decision bar (no
look-ahead, no partial last bucket).
"""

from __future__ import annotations

import numpy as np

from quantsys.core.types import Bar

_COLS = 6  # ts, open, high, low, close, volume


class BarHistory:
    def __init__(self, capacity: int = 20_000):
        if capacity < 8:
            raise ValueError("capacity too small")
        self._cap = capacity
        self._buf = np.full((2 * capacity, _COLS), np.nan)
        self._start = 0
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def append(self, bar: Bar) -> None:
        if self._start + self._n == self._buf.shape[0]:
            # compact: keep the newest `cap` rows at the front (amortised O(1))
            keep = min(self._n, self._cap)
            self._buf[:keep] = self._buf[self._start + self._n - keep : self._start + self._n]
            self._start, self._n = 0, keep
        row = self._start + self._n
        self._buf[row, 0] = bar.ts.timestamp()
        self._buf[row, 1:6] = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if self._n < self._cap:
            self._n += 1
        else:
            self._start += 1

    def _col(self, i: int) -> np.ndarray:
        return self._buf[self._start : self._start + self._n, i]

    @property
    def ts(self) -> np.ndarray:
        return self._col(0)

    @property
    def open(self) -> np.ndarray:
        return self._col(1)

    @property
    def high(self) -> np.ndarray:
        return self._col(2)

    @property
    def low(self) -> np.ndarray:
        return self._col(3)

    @property
    def close(self) -> np.ndarray:
        return self._col(4)

    @property
    def volume(self) -> np.ndarray:
        return self._col(5)

    @property
    def last_close(self) -> float:
        return float(self._col(4)[-1]) if self._n else float("nan")

    def resampled(self, k: int) -> dict[str, np.ndarray]:
        """OHLCV at a k-decision-bar clock, end-aligned. Empty dict if < k bars."""
        if k <= 0:
            raise ValueError("k must be positive")
        m = self._n // k
        if m == 0:
            return {}
        tail = self._buf[self._start + self._n - m * k : self._start + self._n]
        o = tail[:, 1].reshape(m, k)
        h = tail[:, 2].reshape(m, k)
        l = tail[:, 3].reshape(m, k)
        c = tail[:, 4].reshape(m, k)
        v = tail[:, 5].reshape(m, k)
        return {
            "ts": tail[::k, 0].copy(),
            "open": o[:, 0].copy(),
            "high": h.max(axis=1),
            "low": l.min(axis=1),
            "close": c[:, -1].copy(),
            "volume": v.sum(axis=1),
        }

    @classmethod
    def from_arrays(cls, ts: np.ndarray, o, h, l, c, v=None, capacity: int | None = None) -> "BarHistory":
        n = len(c)
        hist = cls(capacity or max(n + 16, 256))
        end = hist._start
        block = np.column_stack(
            [ts, o, h, l, c, v if v is not None else np.zeros(n)]
        )
        hist._buf[end : end + n] = block
        hist._n = n
        return hist
