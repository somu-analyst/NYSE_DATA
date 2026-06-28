"""Pure Black-Scholes helpers — stdlib only (no scipy), so core/ stays light.

Mirrors the bot's _bs_gamma_hp / _bs_call_hp / _implied_vol_hp using math.erf for
the normal CDF instead of scipy.
"""
from __future__ import annotations

import math

_SQRT2PI = math.sqrt(2.0 * math.pi)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_gamma(S: float, K: float, T: float, sigma: float, r: float = 0.05) -> float:
    """Black-Scholes gamma (same for call/put)."""
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm_pdf(d1) / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return 0.0


def bs_call(S: float, K: float, T: float, sigma: float, r: float = 0.05) -> float:
    """Black-Scholes call price."""
    if sigma <= 0 or T <= 0:
        return max(S - K, 0.0)
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    except (ValueError, ZeroDivisionError):
        return max(S - K, 0.0)


def implied_vol(price: float, S: float, K: float, T: float, r: float = 0.05,
                tol: float = 1e-4) -> float:
    """Bisection IV solver — returns IV (0.30 default on bad input)."""
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.30
    if price <= max(S - K, 0.0) + 1e-5:
        return 0.001
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        val = bs_call(S, K, T, mid, r)
        if abs(val - price) < tol:
            return mid
        if val < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
