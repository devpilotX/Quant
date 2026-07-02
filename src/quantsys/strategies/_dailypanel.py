"""Shared self-seeding daily-panel machinery for cross-sectional sleeves.

Same contract the factor sleeve proved out: seeded once at startup from broker
daily candles (``seed_daily``), extended live by aggregating the decision bars
into a running daily row that is finalized when the session date rolls. Rows
carry volume as well (the down-shock event filter needs it); factor keeps its
own original implementation untouched for Study-1 state compatibility.

Sleeves built on this are safe no-ops when unseeded (the backtest never seeds
them — identical precedent to factor), so enabling them cannot corrupt an
intraday replay.
"""

from __future__ import annotations

import math
from collections import deque

from quantsys.core.market_state import MarketState
from quantsys.core.types import InstrumentKind
from quantsys.strategies.base import Strategy


class DailyPanelStrategy(Strategy):
    """Base: per-symbol daily (date, high, low, close, volume) deque panel."""

    def __init__(self, name: str, depth: int):
        super().__init__(name)
        self._depth = depth
        self._panel: dict[str, deque] = {}   # sym -> deque[(iso_date, h, l, c, v)]
        self._today: dict[str, list] = {}    # sym -> [iso_date, h, l, c, v_sum]
        self._cur_date: str | None = None

    # ------------------------------------------------------------- seeding
    def seed_daily(self, symbol: str, rows) -> None:
        """Load daily history: rows of (ts_or_date, open, high, low, close,
        volume) ascending — the broker candle tuple shape."""
        dq = deque(maxlen=self._depth)
        for r in rows:
            ts = r[0]
            d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
            h, lo, c = float(r[2]), float(r[3]), float(r[4])
            v = float(r[5]) if len(r) > 5 and r[5] is not None else 0.0
            if math.isfinite(c) and c > 0:
                dq.append((d, h, lo, c, v))
        if dq:
            self._panel[symbol] = dq

    def seed_days_needed(self) -> int:
        """Calendar days of daily candles the startup seeder should fetch."""
        return int(self._depth * 1.6) + 15

    def warmup_bars(self) -> int:
        # panel is seeded out-of-band; intraday bars only feed today's row
        return 8

    # ------------------------------------------------------------ live roll
    def _ingest_daily(self, state: MarketState) -> bool:
        """Track today's OHLCV row per EQUITY symbol; finalize yesterday's row
        into the panel when the session date rolls. Returns True on a roll
        (i.e. exactly once per new session date)."""
        d = state.ts.date().isoformat()
        rolled = False
        if self._cur_date is None:
            self._cur_date = d
        elif d != self._cur_date:
            self._roll_day()
            self._cur_date = d
            rolled = True

        for sym in state.bars:
            inst = state.instruments.get(sym)
            if inst is None or inst.kind != InstrumentKind.EQUITY:
                continue
            px = state.price(sym)
            if not (math.isfinite(px) and px > 0):
                continue
            vol = state.bars[sym].volume
            bar_v = float(vol[-1]) if len(vol) else 0.0
            t = self._today.get(sym)
            if t is None or t[0] != d:
                self._today[sym] = [d, px, px, px, bar_v]
            else:
                t[1] = max(t[1], px)
                t[2] = min(t[2], px)
                t[3] = px
                t[4] += bar_v
        return rolled

    def _roll_day(self) -> None:
        for sym, (d, h, lo, c, v) in self._today.items():
            dq = self._panel.setdefault(sym, deque(maxlen=self._depth))
            if dq and dq[-1][0] >= d:
                continue
            dq.append((d, h, lo, c, v))
        self._today.clear()

    # ----------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        return {
            "panel": {s: [list(r) for r in dq] for s, dq in self._panel.items()},
            "today": {s: list(v) for s, v in self._today.items()},
            "cur_date": self._cur_date,
        }

    def load_state(self, d: dict) -> None:
        self._panel = {s: deque([tuple(r) for r in rows], maxlen=self._depth)
                       for s, rows in d.get("panel", {}).items()}
        self._today = {s: list(v) for s, v in d.get("today", {}).items()}
        self._cur_date = d.get("cur_date")


def atr_from_rows(rows, n: int) -> float:
    """Mean true range over the last n daily rows of (d, high, low, close, v)."""
    if len(rows) < n + 1:
        return float("nan")
    tail = list(rows)[-(n + 1):]
    tr = []
    for prev, cur in zip(tail[:-1], tail[1:]):
        prev_c = prev[3]
        tr.append(max(cur[1], prev_c) - min(cur[2], prev_c))
    return float(sum(tr) / len(tr)) if tr else float("nan")
