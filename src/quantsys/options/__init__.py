"""Options / volatility sleeve.

Self-contained Black-Scholes (price, greeks, implied-vol solver) and an option
chain provider interface, so the volatility strategy is a registry drop-in like
any other alpha — it emits defined-risk multi-leg Signals the existing sizing
and risk stack handle unchanged.

No exotic dependencies; everything is closed-form or a bounded bisection.
"""

from quantsys.options.blackscholes import (
    bs_delta,
    bs_gamma,
    bs_greeks,
    bs_price,
    bs_vega,
    implied_vol,
)
from quantsys.options.chain import (
    ChainProvider,
    OptionChain,
    OptionQuote,
    SyntheticChainProvider,
)

__all__ = [
    "ChainProvider",
    "OptionChain",
    "OptionQuote",
    "SyntheticChainProvider",
    "bs_delta",
    "bs_gamma",
    "bs_greeks",
    "bs_price",
    "bs_vega",
    "implied_vol",
]
