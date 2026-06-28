"""Tests for core.options_math, core.registry, core.gex (pure logic, no DB)."""
import math

from core.options_math import bs_gamma, bs_call, implied_vol, norm_cdf
from core import registry
import core.signals  # noqa: F401  (registers 'mean_reversion')


def test_norm_cdf_known_values():
    assert abs(norm_cdf(0.0) - 0.5) < 1e-9
    assert abs(norm_cdf(1.96) - 0.975) < 1e-3


def test_bs_gamma_positive_and_peaks_atm():
    atm = bs_gamma(100, 100, 0.25, 0.2)
    otm = bs_gamma(100, 130, 0.25, 0.2)
    assert atm > 0 and atm > otm           # gamma highest near the money
    assert bs_gamma(100, 100, 0, 0.2) == 0.0  # guards


def test_implied_vol_recovers_sigma():
    S, K, T, sigma = 100, 105, 0.5, 0.35
    price = bs_call(S, K, T, sigma)
    iv = implied_vol(price, S, K, T)
    assert abs(iv - sigma) < 1e-2          # round-trips to the input vol


def test_registry_has_mean_reversion():
    assert "mean_reversion" in registry.names()
    assert callable(registry.get("mean_reversion"))


def test_registry_unknown_raises():
    try:
        registry.get("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass
