"""Strategy contract + online edge statistics for the Kelly allocator.

Adding a new alpha = subclass Strategy, decorate with @register("name"),
add a config block. Nothing in portfolio/risk/engine changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from quantsys.core.market_state import MarketState
from quantsys.core.types import Signal


class OnlineEdgeStats:
    """EWMA mean/variance of a strategy's unit returns with a zero-mean prior.

    Exact exponentially-weighted moments via running sums
    (S0, S1, S2) = (sum w, sum w*r, sum w*r^2), w geometric in age.
    ``n_eff`` = S0 is the effective number of observations; the mean is shrunk
    toward 0 by n_eff/(n_eff + prior_obs) so a young or lucky strategy cannot
    grab capital it has not earned. mu/var of unit returns is invariant to the
    bar frequency (both scale with dt), which is what Kelly f = mu/var needs.
    """

    def __init__(self, halflife: float, prior_obs: float, var_floor: float = 1e-10):
        if halflife <= 0:
            raise ValueError("halflife must be positive")
        self.lam = 0.5 ** (1.0 / halflife)
        self.prior_obs = prior_obs
        self.var_floor = var_floor
        self.s0 = 0.0
        self.s1 = 0.0
        self.s2 = 0.0

    def update(self, r: float, weight: float = 1.0) -> None:
        """weight < 1 soft-assigns the observation (e.g. by regime
        probability); weight=1.0 is the classic unweighted update."""
        self.s0 = weight + self.lam * self.s0
        self.s1 = weight * r + self.lam * self.s1
        self.s2 = weight * r * r + self.lam * self.s2

    @property
    def n_eff(self) -> float:
        return self.s0

    @property
    def raw_mean(self) -> float:
        return self.s1 / self.s0 if self.s0 > 0 else 0.0

    @property
    def mean(self) -> float:
        """Shrunk mean — what the allocator must use."""
        if self.s0 <= 0:
            return 0.0
        return self.raw_mean * (self.s0 / (self.s0 + self.prior_obs))

    @property
    def var(self) -> float:
        if self.s0 <= 1.0:
            return self.var_floor
        m = self.raw_mean
        return max(self.s2 / self.s0 - m * m, self.var_floor)

    def to_dict(self) -> dict:
        return {"s0": self.s0, "s1": self.s1, "s2": self.s2}

    def load(self, d: dict) -> None:
        self.s0, self.s1, self.s2 = d["s0"], d["s1"], d["s2"]


class Strategy(ABC):
    """Uniform strategy interface. Implementations must be deterministic
    functions of (MarketState, own persisted state) — no I/O, no clocks,
    no randomness — so backtest and live are bit-identical."""

    name: str

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, state: MarketState) -> list[Signal]: ...

    @abstractmethod
    def warmup_bars(self) -> int:
        """Decision bars required before signals are meaningful."""

    def state_dict(self) -> dict:
        return {}

    def load_state(self, d: dict) -> None:
        pass


REGISTRY: dict[str, Callable[..., Strategy]] = {}


def register(name: str):
    def deco(cls):
        REGISTRY[name] = cls
        cls.registry_name = name
        return cls

    return deco
