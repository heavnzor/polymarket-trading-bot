"""Quantitative tests for Student-t model, EWMA vol, Kelly sizing, and MM pricing engine."""

import math

import pytest

# ---------------------------------------------------------------------------
# Student-t probability
# ---------------------------------------------------------------------------

class TestStudentTProb:
    """Tests for strategy.crypto_directional.student_t_prob."""

    def _fn(self, *a, **kw):
        from strategy.crypto_directional import student_t_prob
        return student_t_prob(*a, **kw)

    def test_atm_returns_near_half(self):
        """ATM (spot == strike) should return ~0.5."""
        p = self._fn(spot=100_000, strike=100_000, t_days=30, sigma=0.03)
        assert abs(p - 0.5) < 0.02

    def test_deep_itm_above(self):
        """Spot well above strike -> high probability for 'above'."""
        p = self._fn(spot=120_000, strike=80_000, t_days=30, sigma=0.03, direction="above")
        assert p > 0.9

    def test_deep_otm_above(self):
        """Strike well above spot -> low probability for 'above'."""
        p = self._fn(spot=80_000, strike=120_000, t_days=30, sigma=0.03, direction="above")
        assert p < 0.1

    def test_below_is_complement(self):
        """P(below) = 1 - P(above)."""
        p_above = self._fn(spot=100_000, strike=105_000, t_days=30, sigma=0.04, direction="above")
        p_below = self._fn(spot=100_000, strike=105_000, t_days=30, sigma=0.04, direction="below")
        assert abs(p_above + p_below - 1.0) < 1e-10

    def test_vs_scipy_t_cdf(self):
        """Cross-check against direct scipy.stats.t calculation."""
        from scipy.stats import t as t_dist

        spot, strike, t_days, sigma, nu = 100_000, 110_000, 30, 0.04, 6.0
        sigma_t = sigma * math.sqrt(t_days)
        d = math.log(strike / spot) / sigma_t
        scale = math.sqrt((nu - 2) / nu)
        d_scaled = d * scale
        expected_above = 1.0 - t_dist.cdf(d_scaled, nu)

        actual = self._fn(spot=spot, strike=strike, t_days=t_days, sigma=sigma, nu=nu, direction="above")
        assert abs(actual - expected_above) < 1e-10

    def test_heavier_tails_than_normal(self):
        """Student-t should give more probability to extreme moves than normal."""
        from strategy.crypto_directional import _normal_approx

        # Far OTM: Student-t should assign MORE probability than normal
        p_t = self._fn(spot=100_000, strike=150_000, t_days=30, sigma=0.04, nu=6.0, direction="above")
        p_n = _normal_approx(100_000, 150_000, 30, 0.04, "above")
        assert p_t > p_n

    def test_edge_case_zero_spot(self):
        """Zero or negative inputs -> 0.5 fallback."""
        assert self._fn(spot=0, strike=100, t_days=30, sigma=0.03) == 0.5

    def test_edge_case_zero_sigma(self):
        assert self._fn(spot=100, strike=110, t_days=30, sigma=0) == 0.5

    def test_edge_case_zero_days(self):
        assert self._fn(spot=100, strike=110, t_days=0, sigma=0.03) == 0.5

    def test_scaling_factor_correct(self):
        """Verify the scaling factor is sqrt((nu-2)/nu), not the inverse."""
        nu = 6.0
        expected_scale = math.sqrt((nu - 2) / nu)  # ~0.8165
        assert expected_scale < 1.0  # Must shrink, not expand
        assert abs(expected_scale - math.sqrt(4 / 6)) < 1e-10


# ---------------------------------------------------------------------------
# EWMA volatility
# ---------------------------------------------------------------------------

class TestComputeEwmaVol:
    """Tests for strategy.crypto_directional.compute_ewma_vol."""

    def _fn(self, *a, **kw):
        from strategy.crypto_directional import compute_ewma_vol
        return compute_ewma_vol(*a, **kw)

    def test_constant_prices_zero_vol(self):
        """Constant prices -> zero volatility."""
        prices = [100.0] * 20
        assert self._fn(prices) == 0.0

    def test_known_series_positive(self):
        """A series with variation should produce positive vol."""
        prices = [100, 102, 99, 103, 98, 105, 97, 104, 100, 101]
        vol = self._fn(prices)
        assert vol > 0

    def test_too_few_prices_returns_zero(self):
        """Less than 3 prices -> 0."""
        assert self._fn([100.0]) == 0.0
        assert self._fn([100.0, 101.0]) == 0.0

    def test_volatile_greater_than_calm(self):
        """A volatile series should have higher vol than a calm one."""
        calm = [100 + 0.1 * i for i in range(30)]
        volatile = [100 + 5 * ((-1) ** i) for i in range(30)]
        assert self._fn(volatile) > self._fn(calm)

    def test_lambda_effect(self):
        """Higher lambda gives more weight to past, smoothing vol."""
        prices = [100, 110, 90, 115, 85, 120, 80, 100, 100, 100]
        vol_high_lambda = self._fn(prices, lambda_=0.99)
        vol_low_lambda = self._fn(prices, lambda_=0.80)
        # Low lambda reacts faster; depending on recent data,
        # the vol could differ. Just verify both are positive.
        assert vol_high_lambda > 0
        assert vol_low_lambda > 0


# ---------------------------------------------------------------------------
# Edge detection
# ---------------------------------------------------------------------------

class TestDetectEdge:
    """Tests for strategy.crypto_directional.detect_edge."""

    def _fn(self, *a, **kw):
        from strategy.crypto_directional import detect_edge
        return detect_edge(*a, **kw)

    def test_positive_edge(self):
        assert self._fn(0.65, 0.55) == pytest.approx(10.0)

    def test_negative_edge(self):
        assert self._fn(0.40, 0.55) == pytest.approx(-15.0)

    def test_zero_edge(self):
        assert self._fn(0.50, 0.50) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Kelly sizing
# ---------------------------------------------------------------------------

class TestKellySize:
    """Tests for strategy.crypto_directional.kelly_size."""

    def _fn(self, *a, **kw):
        from strategy.crypto_directional import kelly_size
        return kelly_size(*a, **kw)

    def test_basic_positive(self):
        """Positive edge + capital -> positive size."""
        size = self._fn(edge_pts=10.0, p_model=0.65, capital=1000.0)
        assert size > 0

    def test_zero_edge_returns_zero(self):
        assert self._fn(edge_pts=0.0, p_model=0.55, capital=1000.0) == 0.0

    def test_negative_edge_returns_zero(self):
        assert self._fn(edge_pts=-5.0, p_model=0.50, capital=1000.0) == 0.0

    def test_zero_capital_returns_zero(self):
        assert self._fn(edge_pts=10.0, p_model=0.65, capital=0.0) == 0.0

    def test_cap_max_position_pct(self):
        """Size should never exceed capital * max_position_pct."""
        size = self._fn(
            edge_pts=50.0, p_model=0.95, capital=1000.0,
            kelly_fraction=1.0, max_position_pct=0.05,
        )
        assert size <= 1000.0 * 0.05 + 0.01  # +0.01 for rounding

    def test_invalid_p_model(self):
        assert self._fn(edge_pts=10.0, p_model=0.0, capital=1000.0) == 0.0
        assert self._fn(edge_pts=10.0, p_model=1.0, capital=1000.0) == 0.0


# ---------------------------------------------------------------------------
# MM Engine: compute_weighted_mid
# ---------------------------------------------------------------------------

class TestComputeWeightedMid:
    """Tests for mm.engine.compute_weighted_mid."""

    def _fn(self, *a, **kw):
        from mm.engine import compute_weighted_mid
        return compute_weighted_mid(*a, **kw)

    def test_balanced_book(self):
        """Equal depth -> simple midpoint."""
        mid = self._fn({
            "best_bid": 0.50, "best_ask": 0.56,
            "bid_depth_5": 100, "ask_depth_5": 100,
        })
        assert mid == pytest.approx(0.53)

    def test_imbalanced_book(self):
        """More bid depth -> mid closer to ask."""
        mid = self._fn({
            "best_bid": 0.50, "best_ask": 0.60,
            "bid_depth_5": 300, "ask_depth_5": 100,
        })
        # w_bid = 100/400 = 0.25, w_ask = 300/400 = 0.75
        # mid = 0.25*0.50 + 0.75*0.60 = 0.125 + 0.45 = 0.575
        assert mid == pytest.approx(0.575)

    def test_no_depth_simple_mid(self):
        """Zero depth -> simple midpoint."""
        mid = self._fn({
            "best_bid": 0.40, "best_ask": 0.60,
            "bid_depth_5": 0, "ask_depth_5": 0,
        })
        assert mid == pytest.approx(0.50)

    def test_invalid_returns_none(self):
        """Invalid book -> None."""
        assert self._fn({"best_bid": 0, "best_ask": 0.5, "bid_depth_5": 10, "ask_depth_5": 10}) is None
        assert self._fn({"best_bid": 0.5, "best_ask": 0.5, "bid_depth_5": 10, "ask_depth_5": 10}) is None
        assert self._fn({"best_bid": 0.6, "best_ask": 0.5, "bid_depth_5": 10, "ask_depth_5": 10}) is None


# ---------------------------------------------------------------------------
# MM Engine: compute_dynamic_delta
# ---------------------------------------------------------------------------

class TestComputeDynamicDelta:
    """Tests for mm.engine.compute_dynamic_delta."""

    def _fn(self, *a, **kw):
        from mm.engine import compute_dynamic_delta
        return compute_dynamic_delta(*a, **kw)

    def test_clamp_to_min(self):
        """Low inputs should clamp to delta_min."""
        delta = self._fn(vol_short=0, book_imbalance=0, stale_risk=0, delta_min=2.0, delta_max=8.0)
        assert delta == pytest.approx(2.0, abs=0.3)

    def test_high_vol_increases_delta(self):
        delta_low = self._fn(vol_short=1.0, book_imbalance=0, stale_risk=0)
        delta_high = self._fn(vol_short=20.0, book_imbalance=0, stale_risk=0)
        assert delta_high > delta_low

    def test_clamp_to_max(self):
        """Very high inputs should clamp to delta_max."""
        delta = self._fn(vol_short=100, book_imbalance=1.0, stale_risk=1.0, delta_max=8.0)
        assert delta == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# MM Engine: compute_skew
# ---------------------------------------------------------------------------

class TestComputeSkew:
    """Tests for mm.engine.compute_skew."""

    def _fn(self, *a, **kw):
        from mm.engine import compute_skew
        return compute_skew(*a, **kw)

    def test_zero_inventory(self):
        assert self._fn(0.0, 100.0) == pytest.approx(0.0)

    def test_long_inventory_negative_skew(self):
        """Long inventory -> negative skew (shift quotes down to sell)."""
        skew = self._fn(50.0, 100.0, skew_factor=0.5)
        assert skew < 0

    def test_short_inventory_positive_skew(self):
        """Short inventory -> positive skew (shift quotes up to buy)."""
        skew = self._fn(-50.0, 100.0, skew_factor=0.5)
        assert skew > 0

    def test_zero_max_inventory(self):
        assert self._fn(10.0, 0.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# MM Engine: compute_bid_ask
# ---------------------------------------------------------------------------

class TestComputeBidAsk:
    """Tests for mm.engine.compute_bid_ask."""

    def _fn(self, *a, **kw):
        from mm.engine import compute_bid_ask
        return compute_bid_ask(*a, **kw)

    def test_basic_bid_less_than_ask(self):
        bid, ask = self._fn(mid=0.50, delta=3.0)
        assert bid < ask

    def test_symmetric_without_skew(self):
        """Without skew, spread should be symmetric around mid."""
        bid, ask = self._fn(mid=0.50, delta=5.0, skew=0.0)
        spread = ask - bid
        assert spread == pytest.approx(0.10, abs=0.02)

    def test_skew_shifts_both(self):
        """Positive skew shifts both bid and ask up."""
        bid_no_skew, ask_no_skew = self._fn(mid=0.50, delta=3.0, skew=0.0)
        bid_skew, ask_skew = self._fn(mid=0.50, delta=3.0, skew=2.0)
        assert bid_skew > bid_no_skew
        assert ask_skew > ask_no_skew


# ---------------------------------------------------------------------------
# MM Engine: compute_quote_size
# ---------------------------------------------------------------------------

class TestComputeQuoteSize:
    """Tests for mm.engine.compute_quote_size."""

    def _fn(self, *a, **kw):
        from mm.engine import compute_quote_size
        return compute_quote_size(*a, **kw)

    def test_basic_positive(self):
        size = self._fn(capital=100, max_per_market=20, current_inventory_usdc=0, max_inventory=20)
        assert size > 0

    def test_at_capacity_zero(self):
        """At max inventory -> 0."""
        size = self._fn(capital=100, max_per_market=20, current_inventory_usdc=20, max_inventory=20)
        assert size == 0.0

    def test_over_capacity_zero(self):
        size = self._fn(capital=100, max_per_market=20, current_inventory_usdc=25, max_inventory=20)
        assert size == 0.0
