"""Tests for mm/engine.py — pricing, skew, VolTracker, StaleTracker."""

import time

import pytest
from mm.engine import (
    compute_skew,
    compute_dynamic_delta,
    compute_bid_ask,
    compute_quote_size,
    round_to_tick,
    VolTracker,
)


class TestComputeSkewNonLinear:
    """Tests for non-linear skew with quadratic component."""

    def test_zero_inventory_zero_skew(self):
        assert compute_skew(0, 100) == 0.0

    def test_zero_max_inventory_zero_skew(self):
        assert compute_skew(50, 0) == 0.0

    def test_positive_inventory_negative_skew(self):
        """Long position should push quotes lower (negative skew)."""
        skew = compute_skew(50, 100, skew_factor=0.5, quadratic_factor=0.3)
        assert skew < 0

    def test_negative_inventory_positive_skew(self):
        """Short position should push quotes higher (positive skew)."""
        skew = compute_skew(-50, 100, skew_factor=0.5, quadratic_factor=0.3)
        assert skew > 0

    def test_quadratic_makes_extreme_skew_larger(self):
        """At extreme inventory, quadratic component should amplify skew."""
        linear_only = compute_skew(100, 100, skew_factor=0.5, quadratic_factor=0.0)
        with_quadratic = compute_skew(100, 100, skew_factor=0.5, quadratic_factor=0.3)
        # Both negative (long position), but quadratic should be more negative
        assert abs(with_quadratic) > abs(linear_only)

    def test_quadratic_minimal_at_small_inventory(self):
        """At small inventory, linear and quadratic should be similar."""
        linear_only = compute_skew(10, 100, skew_factor=0.5, quadratic_factor=0.0)
        with_quadratic = compute_skew(10, 100, skew_factor=0.5, quadratic_factor=0.3)
        # Difference should be small (quadratic of 0.1 is 0.01, times 0.3 = 0.003)
        assert abs(with_quadratic - linear_only) < 0.01

    def test_symmetric_positive_negative(self):
        """Skew should be symmetric for equal positive and negative inventory."""
        positive = compute_skew(50, 100, skew_factor=0.5, quadratic_factor=0.3)
        negative = compute_skew(-50, 100, skew_factor=0.5, quadratic_factor=0.3)
        assert abs(positive + negative) < 0.001  # Should be equal and opposite

    def test_clamped_to_range(self):
        """Inventory ratio should be clamped to [-1, 1]."""
        normal = compute_skew(100, 100, skew_factor=0.5, quadratic_factor=0.3)
        oversize = compute_skew(200, 100, skew_factor=0.5, quadratic_factor=0.3)
        assert abs(normal - oversize) < 0.001  # Both clamped at ratio=1.0


class TestComputeDynamicDeltaTrackedVol:
    """Tests for tracked_vol parameter in compute_dynamic_delta."""

    def test_tracked_vol_overrides_vol_short(self):
        """When tracked_vol > 0, it should override vol_short."""
        delta_with_proxy = compute_dynamic_delta(
            vol_short=2.0, book_imbalance=0, stale_risk=0,
            tracked_vol=0.0,
        )
        delta_with_tracked = compute_dynamic_delta(
            vol_short=2.0, book_imbalance=0, stale_risk=0,
            tracked_vol=5.0,  # Higher vol -> higher delta
        )
        assert delta_with_tracked > delta_with_proxy

    def test_tracked_vol_zero_uses_proxy(self):
        """When tracked_vol=0, vol_short is used."""
        delta1 = compute_dynamic_delta(
            vol_short=3.0, book_imbalance=0, stale_risk=0,
            tracked_vol=0.0,
        )
        delta2 = compute_dynamic_delta(
            vol_short=3.0, book_imbalance=0, stale_risk=0,
        )
        assert delta1 == delta2

    def test_delta_respects_min_max(self):
        """Delta should always be within [delta_min, delta_max]."""
        delta = compute_dynamic_delta(
            vol_short=100.0, book_imbalance=1.0, stale_risk=1.0,
            delta_min=2.0, delta_max=6.0, tracked_vol=50.0,
        )
        assert 2.0 <= delta <= 6.0


class TestVolTracker:
    """Tests for EWMA volatility tracker."""

    def test_first_observation_returns_zero(self):
        vt = VolTracker(halflife=20)
        vol = vt.update("m1", 0.55)
        assert vol == 0.0  # No previous obs

    def test_second_observation_returns_vol(self):
        vt = VolTracker(halflife=20)
        vt.update("m1", 0.55)
        vol = vt.update("m1", 0.56)
        assert vol > 0  # 1pt change

    def test_stable_prices_low_vol(self):
        vt = VolTracker(halflife=5)
        for _ in range(20):
            vt.update("m1", 0.50)
        vol = vt.get_vol("m1")
        assert vol < 0.1  # Very low vol for stable price

    def test_volatile_prices_high_vol(self):
        vt = VolTracker(halflife=5)
        prices = [0.50, 0.55, 0.45, 0.60, 0.40, 0.55, 0.45]
        for p in prices:
            vt.update("m1", p)
        vol = vt.get_vol("m1")
        assert vol > 1.0  # High vol for swinging prices

    def test_independent_markets(self):
        vt = VolTracker(halflife=10)
        vt.update("m1", 0.50)
        vt.update("m1", 0.55)
        vt.update("m2", 0.50)
        vt.update("m2", 0.50)
        assert vt.get_vol("m1") > vt.get_vol("m2")

    def test_reset_clears_market(self):
        vt = VolTracker(halflife=10)
        vt.update("m1", 0.50)
        vt.update("m1", 0.55)
        vt.reset("m1")
        assert vt.get_vol("m1") == 0.0
        # Next update should return 0 (first observation after reset)
        vol = vt.update("m1", 0.60)
        assert vol == 0.0

    def test_zero_mid_ignored(self):
        vt = VolTracker(halflife=10)
        vt.update("m1", 0.50)
        vol = vt.update("m1", 0.0)
        assert vol == 0.0  # Zero mid is ignored

    def test_get_vol_unknown_market(self):
        vt = VolTracker(halflife=10)
        assert vt.get_vol("unknown") == 0.0


# ═══════════════════════════════════════════════════════════════════════
# StaleTracker (5A)
# ═══════════════════════════════════════════════════════════════════════

class TestStaleTracker:
    """Tests for mm.engine.StaleTracker."""

    @pytest.fixture
    def tracker(self):
        from mm.engine import StaleTracker
        return StaleTracker(threshold_seconds=60.0)

    def test_fresh_after_update(self, tracker):
        tracker.update_if_changed("m1", 0.50)
        assert tracker.get_staleness("m1") == pytest.approx(0.0, abs=0.05)

    def test_stale_after_threshold(self, tracker):
        tracker.update_if_changed("m1", 0.50)
        # Simulate time passing by manipulating internal state
        tracker._last_change["m1"] = time.monotonic() - 60.0
        assert tracker.get_staleness("m1") == pytest.approx(1.0, abs=0.05)

    def test_half_stale(self, tracker):
        tracker.update_if_changed("m1", 0.50)
        tracker._last_change["m1"] = time.monotonic() - 30.0
        staleness = tracker.get_staleness("m1")
        assert 0.4 <= staleness <= 0.6

    def test_reset_clears(self, tracker):
        tracker.update_if_changed("m1", 0.50)
        tracker.reset("m1")
        assert tracker.get_staleness("m1") == 0.0

    def test_price_change_resets_staleness(self, tracker):
        tracker.update_if_changed("m1", 0.50)
        tracker._last_change["m1"] = time.monotonic() - 60.0
        assert tracker.get_staleness("m1") >= 0.9
        # Price changes -> should reset to fresh
        tracker.update_if_changed("m1", 0.55)
        assert tracker.get_staleness("m1") == pytest.approx(0.0, abs=0.05)

    def test_unknown_market(self, tracker):
        assert tracker.get_staleness("unknown") == 0.0


# ═══════════════════════════════════════════════════════════════════════
# VWAP Mid (5A)
# ═══════════════════════════════════════════════════════════════════════

class TestVWAPMid:
    """Tests for compute_weighted_mid."""

    def test_balanced_book(self):
        from mm.engine import compute_weighted_mid
        book = {"best_bid": 0.50, "best_ask": 0.52, "bid_depth_5": 100, "ask_depth_5": 100}
        mid = compute_weighted_mid(book)
        assert mid == pytest.approx(0.51)

    def test_heavier_bid_side(self):
        from mm.engine import compute_weighted_mid
        book = {"best_bid": 0.50, "best_ask": 0.52, "bid_depth_5": 200, "ask_depth_5": 100}
        mid = compute_weighted_mid(book)
        # More bid depth -> mid closer to ask
        assert mid is not None
        assert mid > 0.51

    def test_no_depth_returns_simple_mid(self):
        from mm.engine import compute_weighted_mid
        book = {"best_bid": 0.50, "best_ask": 0.52, "bid_depth_5": 0, "ask_depth_5": 0}
        mid = compute_weighted_mid(book)
        assert mid == pytest.approx(0.51)

    def test_invalid_prices_returns_none(self):
        from mm.engine import compute_weighted_mid
        book = {"best_bid": 0, "best_ask": 0, "bid_depth_5": 100, "ask_depth_5": 100}
        assert compute_weighted_mid(book) is None

    def test_crossed_book_returns_none(self):
        from mm.engine import compute_weighted_mid
        book = {"best_bid": 0.55, "best_ask": 0.50, "bid_depth_5": 100, "ask_depth_5": 100}
        assert compute_weighted_mid(book) is None


# ═══════════════════════════════════════════════════════════════════════
# VolTracker additional tests (5A)
# ═══════════════════════════════════════════════════════════════════════

class TestVolTrackerAdditional:
    """Additional VolTracker tests for 5A coverage."""

    def test_first_observation_returns_zero(self):
        from mm.engine import VolTracker
        vt = VolTracker(halflife=20)
        assert vt.update("m1", 0.50) == 0.0

    def test_second_observation_returns_positive(self):
        from mm.engine import VolTracker
        vt = VolTracker(halflife=20)
        vt.update("m1", 0.50)
        vol = vt.update("m1", 0.52)
        assert vol > 0

    def test_reset_clears(self):
        from mm.engine import VolTracker
        vt = VolTracker(halflife=20)
        vt.update("m1", 0.50)
        vt.update("m1", 0.55)
        vt.reset("m1")
        assert vt.get_vol("m1") == 0.0
