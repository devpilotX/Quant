"""Black-Scholes-Merton for European index/stock options (NSE style).

Closed-form price + greeks; implied vol by bracketed bisection (robust, no
derivative needed, can't diverge). Continuous dividend yield q supported.
All vols annualised; T in years. Prices in absolute INR per unit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    vol_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    return d1, d1 - vol_t


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             is_call: bool, q: float = 0.0) -> float:
    """Intrinsic value at expiry/degenerate inputs; BSM otherwise."""
    if T <= 0 or sigma <= 0:
        intrinsic = (S - K) if is_call else (K - S)
        return max(0.0, intrinsic)
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    if is_call:
        return S * df_q * _N.cdf(d1) - K * df_r * _N.cdf(d2)
    return K * df_r * _N.cdf(-d2) - S * df_q * _N.cdf(-d1)


def bs_delta(S, K, T, r, sigma, is_call, q=0.0) -> float:
    d1, _ = _d1_d2(S, K, T, r, sigma, q)
    if d1 is None:
        return (1.0 if S > K else 0.0) * (1 if is_call else -1)
    df_q = math.exp(-q * T)
    return df_q * (_N.cdf(d1) if is_call else _N.cdf(d1) - 1.0)


def bs_gamma(S, K, T, r, sigma, q=0.0) -> float:
    d1, _ = _d1_d2(S, K, T, r, sigma, q)
    if d1 is None:
        return 0.0
    return math.exp(-q * T) * _pdf(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S, K, T, r, sigma, q=0.0) -> float:
    """Per 1.00 (i.e. 100 vol points) change in sigma."""
    d1, _ = _d1_d2(S, K, T, r, sigma, q)
    if d1 is None:
        return 0.0
    return S * math.exp(-q * T) * _pdf(d1) * math.sqrt(T)


def bs_theta(S, K, T, r, sigma, is_call, q=0.0) -> float:
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if d1 is None:
        return 0.0
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    term1 = -(S * df_q * _pdf(d1) * sigma) / (2 * math.sqrt(T))
    if is_call:
        return (term1 - r * K * df_r * _N.cdf(d2)
                + q * S * df_q * _N.cdf(d1)) / 365.0
    return (term1 + r * K * df_r * _N.cdf(-d2)
            - q * S * df_q * _N.cdf(-d1)) / 365.0


@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float


def bs_greeks(S, K, T, r, sigma, is_call, q=0.0) -> Greeks:
    return Greeks(
        price=bs_price(S, K, T, r, sigma, is_call, q),
        delta=bs_delta(S, K, T, r, sigma, is_call, q),
        gamma=bs_gamma(S, K, T, r, sigma, q),
        vega=bs_vega(S, K, T, r, sigma, q),
        theta=bs_theta(S, K, T, r, sigma, is_call, q),
    )


def implied_vol(price: float, S: float, K: float, T: float, r: float,
                is_call: bool, q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """Bracketed bisection. Returns None if the price is outside the no-arb
    band (below intrinsic or above the forward bound) — never guesses."""
    if T <= 0 or price <= 0:
        return None
    intrinsic = max(0.0, (S - K) if is_call else (K - S)) * math.exp(-r * T)
    upper = S * math.exp(-q * T) if is_call else K * math.exp(-r * T)
    if price < intrinsic - tol or price > upper + tol:
        return None
    flo = bs_price(S, K, T, r, lo, is_call, q) - price
    fhi = bs_price(S, K, T, r, hi, is_call, q) - price
    if flo * fhi > 0:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        fm = bs_price(S, K, T, r, mid, is_call, q) - price
        if abs(fm) < tol:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)
