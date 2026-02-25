"""Tests for mm.as_engine — Avellaneda-Stoikov pricing engine."""

import time

import pytest


class TestReservationPrice:
    def test_zero_inventory_equals_mid(self):
        from mm.as_engine import compute_reservation_price
        r = compute_reservation_price(mid=0.50, inventory=0, max_inventory=100,
                                       gamma=0.1, vol=5.0, T=1.0)
        assert r == pytest.approx(0.50)

    def test_long_inventory_shifts_down(self):
        from mm.as_engine import compute_reservation_price
        r = compute_reservation_price(mid=0.50, inventory=50, max_inventory=100,
                                       gamma=0.1, vol=5.0, T=1.0)
        assert r < 0.50

    def test_short_inventory_shifts_up(self):
        from mm.as_engine import compute_reservation_price
        r = compute_reservation_price(mid=0.50, inventory=-50, max_inventory=100,
                                       gamma=0.1, vol=5.0, T=1.0)
        assert r > 0.50

    def test_zero_max_inventory(self):
        from mm.as_engine import compute_reservation_price
        r = compute_reservation_price(mid=0.50, inventory=10, max_inventory=0,
                                       gamma=0.1, vol=5.0, T=1.0)
        assert r == 0.50


class TestOptimalSpread:
    def test_increases_with_vol(self):
        from mm.as_engine import compute_optimal_spread
        s_low = compute_optimal_spread(gamma=0.1, vol=2.0, T=1.0, kappa=1.5)
        s_high = compute_optimal_spread(gamma=0.1, vol=10.0, T=1.0, kappa=1.5)
        assert s_high > s_low

    def test_arrival_component_decreases_with_gamma(self):
        """The arrival component (2/gamma)*ln(1+gamma/kappa) decreases
        with gamma, so at low vol where inventory component is negligible,
        higher gamma produces a tighter spread."""
        from mm.as_engine import compute_optimal_spread
        s_low_gamma = compute_optimal_spread(gamma=0.05, vol=1.0, T=1.0, kappa=1.5)
        s_high_gamma = compute_optimal_spread(gamma=5.0, vol=1.0, T=1.0, kappa=1.5)
        assert s_high_gamma < s_low_gamma

    def test_inventory_component_increases_with_gamma(self):
        """At high vol, the inventory component gamma*sigma^2*T dominates,
        so higher gamma produces a wider spread."""
        from mm.as_engine import compute_optimal_spread
        s_low_gamma = compute_optimal_spread(gamma=0.05, vol=50.0, T=1.0, kappa=1.5)
        s_high_gamma = compute_optimal_spread(gamma=5.0, vol=50.0, T=1.0, kappa=1.5)
        assert s_high_gamma > s_low_gamma

    def test_zero_gamma_fallback(self):
        from mm.as_engine import compute_optimal_spread
        s = compute_optimal_spread(gamma=0.0, vol=5.0, T=1.0, kappa=1.5)
        assert s == pytest.approx(0.02)


class TestDynamicGamma:
    def test_zero_inventory(self):
        from mm.as_engine import compute_dynamic_gamma
        g = compute_dynamic_gamma(gamma_base=0.1, alpha=0.5, inventory_ratio=0.0)
        assert g == pytest.approx(0.1)

    def test_max_inventory(self):
        from mm.as_engine import compute_dynamic_gamma
        g = compute_dynamic_gamma(gamma_base=0.1, alpha=0.5, inventory_ratio=1.0)
        assert g == pytest.approx(0.15)

    def test_half_inventory(self):
        from mm.as_engine import compute_dynamic_gamma
        g = compute_dynamic_gamma(gamma_base=0.1, alpha=0.5, inventory_ratio=0.5)
        assert g == pytest.approx(0.125)


class TestASQuotes:
    def test_never_sell_below_entry(self):
        from mm.as_engine import compute_as_quotes, ASParams
        params = ASParams(gamma_base=0.1, min_spread_pts=1.0, max_spread_pts=15.0)
        bid, ask = compute_as_quotes(
            mid=0.45, inventory=50, max_inventory=100,
            vol_pts=3.0, T=1.0, params=params, avg_entry_price=0.50,
        )
        assert ask >= 0.51

    def test_spread_clamped_min(self):
        from mm.as_engine import compute_as_quotes, ASParams
        params = ASParams(gamma_base=0.001, min_spread_pts=3.0, max_spread_pts=15.0, kappa=100.0)
        bid, ask = compute_as_quotes(
            mid=0.50, inventory=0, max_inventory=100,
            vol_pts=0.1, T=0.01, params=params,
        )
        spread_pts = (ask - bid) * 100
        assert spread_pts >= 2.9

    def test_spread_clamped_max(self):
        from mm.as_engine import compute_as_quotes, ASParams
        params = ASParams(gamma_base=5.0, min_spread_pts=1.0, max_spread_pts=10.0, kappa=0.5)
        bid, ask = compute_as_quotes(
            mid=0.50, inventory=0, max_inventory=100,
            vol_pts=20.0, T=1.0, params=params,
        )
        spread_pts = (ask - bid) * 100
        assert spread_pts <= 11.0

    def test_valid_price_range(self):
        from mm.as_engine import compute_as_quotes, ASParams
        params = ASParams()
        bid, ask = compute_as_quotes(
            mid=0.50, inventory=0, max_inventory=100,
            vol_pts=5.0, T=1.0, params=params,
        )
        assert 0.01 <= bid < ask <= 0.99


class TestTimeRemaining:
    def test_at_30_days(self):
        from mm.as_engine import estimate_time_remaining
        T = estimate_time_remaining(30.0, max_T=30.0)
        assert T == pytest.approx(1.0)

    def test_at_15_days(self):
        from mm.as_engine import estimate_time_remaining
        T = estimate_time_remaining(15.0, max_T=30.0)
        assert T == pytest.approx(0.5)

    def test_near_resolution(self):
        from mm.as_engine import estimate_time_remaining
        T = estimate_time_remaining(0.5, max_T=30.0)
        assert T < 0.05

    def test_zero_days(self):
        from mm.as_engine import estimate_time_remaining
        T = estimate_time_remaining(0.0)
        assert T == pytest.approx(0.01)

    def test_beyond_max(self):
        from mm.as_engine import estimate_time_remaining
        T = estimate_time_remaining(60.0, max_T=30.0)
        assert T == pytest.approx(1.0)


class TestKappaEstimator:
    def test_default_no_fills(self):
        from mm.as_engine import KappaEstimator
        ke = KappaEstimator(default_kappa=1.5)
        assert ke.get_kappa("m1") == 1.5

    def test_reflects_fills(self):
        from mm.as_engine import KappaEstimator
        ke = KappaEstimator(window_minutes=60, default_kappa=1.5)
        for i in range(10):
            ke.record_fill("m1")
            ke._fills["m1"][-1] = time.monotonic() - (9 - i) * 6
        kappa = ke.get_kappa("m1")
        assert kappa > 1.5

    def test_reset(self):
        from mm.as_engine import KappaEstimator
        ke = KappaEstimator()
        ke.record_fill("m1")
        ke.record_fill("m1")
        ke.reset("m1")
        assert ke.get_kappa("m1") == ke._default

    def test_single_fill_returns_default(self):
        from mm.as_engine import KappaEstimator
        ke = KappaEstimator(default_kappa=2.0)
        ke.record_fill("m1")
        assert ke.get_kappa("m1") == 2.0
