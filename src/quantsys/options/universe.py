"""OptionUniverseManager — bridges chain data into the engine's instrument/bar
model so the sizer can size option legs exactly like any other instrument.

Before each decision the runner calls register(): for every configured
underlying's chain it registers near-ATM CE/PE (within a strike window, nearest
valid expiry) as Instrument(kind=OPTION, point_value=1, lot from the quote) and
appends a one-bar premium history (the option LTP as OHLC). The VolOptions
strategy reads the SAME chain, so the legs it references are guaranteed present.

point_value=1.0: option premia are already per-unit INR; lot multiplication
happens at sizing via lot_size, matching equities/futures.
"""

from __future__ import annotations

from datetime import datetime

from quantsys.core.types import Bar, Instrument, InstrumentKind
from quantsys.data.history import BarHistory
from quantsys.options.chain import OptionChain


class OptionUniverseManager:
    def __init__(self, strike_window: int = 6):
        self.strike_window = strike_window
        self._hist: dict[str, BarHistory] = {}

    def register(self, bars: dict[str, BarHistory],
                 instruments: dict[str, Instrument],
                 chains: dict[str, OptionChain], asof: datetime) -> set[str]:
        """Mutates bars + instruments in place; returns the registered symbols."""
        registered: set[str] = set()
        for underlying, chain in chains.items():
            expiry = chain.nearest_expiry()
            if expiry is None:
                continue
            quotes = chain.for_expiry(expiry)
            if not quotes:
                continue
            atm = min(quotes, key=lambda q: abs(q.strike - chain.spot)).strike
            step = _infer_step(quotes)
            lo, hi = atm - self.strike_window * step, atm + self.strike_window * step
            for q in quotes:
                if not (lo <= q.strike <= hi) or q.ltp <= 0:
                    continue
                instruments[q.symbol] = Instrument(
                    symbol=q.symbol, token=q.token, exchange="NFO",
                    kind=InstrumentKind.OPTION, lot_size=q.lot_size,
                    tick_size=0.05, point_value=1.0,
                    sector=f"opt:{underlying}",
                )
                h = self._hist.get(q.symbol)
                if h is None:
                    h = BarHistory(capacity=64)
                    self._hist[q.symbol] = h
                h.append(Bar(ts=asof, open=q.ltp, high=q.ltp, low=q.ltp,
                             close=q.ltp, volume=0.0))
                bars[q.symbol] = h
                registered.add(q.symbol)
        return registered


def _infer_step(quotes) -> float:
    strikes = sorted({q.strike for q in quotes})
    diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return min(diffs) if diffs else 100.0
