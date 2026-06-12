"""Option-chain abstraction.

The strategy consumes an OptionChain (strikes, expiries, quotes) via a
ChainProvider it reads from MarketState.extra['option_chains']. The live data
layer publishes real chains there; SyntheticChainProvider builds a BSM-priced
chain from the underlying for tests and dry runs (clearly not market data).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from quantsys.options.blackscholes import bs_price


@dataclass(frozen=True)
class OptionQuote:
    symbol: str            # broker tradingsymbol, e.g. NIFTY24JUN24000CE
    underlying: str
    strike: float
    expiry: date
    is_call: bool
    ltp: float
    lot_size: int
    token: str = ""
    bid: float = 0.0
    ask: float = 0.0
    iv: float | None = None     # provider-supplied IV if available


@dataclass
class OptionChain:
    underlying: str
    spot: float
    asof: datetime
    quotes: list[OptionQuote] = field(default_factory=list)

    def expiries(self) -> list[date]:
        return sorted({q.expiry for q in self.quotes})

    def nearest_expiry(self, min_days: int = 1) -> date | None:
        days = [(e, (e - self.asof.date()).days) for e in self.expiries()]
        valid = [e for e, d in days if d >= min_days]
        return valid[0] if valid else None

    def for_expiry(self, expiry: date) -> list[OptionQuote]:
        return [q for q in self.quotes if q.expiry == expiry]

    def strike_nearest(self, expiry: date, target: float, is_call: bool
                       ) -> OptionQuote | None:
        cands = [q for q in self.for_expiry(expiry) if q.is_call == is_call]
        if not cands:
            return None
        return min(cands, key=lambda q: abs(q.strike - target))


class ChainProvider(Protocol):
    def chain(self, underlying: str, spot: float, asof: datetime
              ) -> OptionChain | None: ...


class SyntheticChainProvider:
    """BSM-priced synthetic chain. NOT market data — for tests/dry-runs only.
    Builds a symmetric strike ladder around spot at a configured IV."""

    def __init__(self, *, iv: float = 0.15, r: float = 0.066,
                 lot_size: int = 75, n_strikes: int = 11, strike_step: float = 100.0,
                 days_to_expiry: int = 7):
        self.iv = iv
        self.r = r
        self.lot_size = lot_size
        self.n_strikes = n_strikes
        self.strike_step = strike_step
        self.dte = days_to_expiry

    def chain(self, underlying: str, spot: float, asof: datetime) -> OptionChain:
        from datetime import timedelta

        expiry = (asof + timedelta(days=self.dte)).date()
        T = self.dte / 365.0
        atm = round(spot / self.strike_step) * self.strike_step
        quotes: list[OptionQuote] = []
        half = self.n_strikes // 2
        for i in range(-half, half + 1):
            K = atm + i * self.strike_step
            if K <= 0:
                continue
            for is_call in (True, False):
                px = bs_price(spot, K, T, self.r, self.iv, is_call)
                cp = "CE" if is_call else "PE"
                quotes.append(OptionQuote(
                    symbol=f"{underlying}{expiry:%d%b%y}{int(K)}{cp}".upper(),
                    underlying=underlying, strike=K, expiry=expiry,
                    is_call=is_call, ltp=round(px, 2), lot_size=self.lot_size,
                    iv=self.iv,
                ))
        return OptionChain(underlying=underlying, spot=spot, asof=asof, quotes=quotes)
