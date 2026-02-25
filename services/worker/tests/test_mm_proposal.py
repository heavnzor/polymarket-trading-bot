"""Tests for mm.proposal -- quote proposal pipeline."""

import pytest


class TestCreateBaseProposal:
    def test_both_sides(self):
        from mm.proposal import create_base_proposal
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        assert len(p.bids) == 1
        assert len(p.asks) == 1
        assert p.bids[0].price == 0.48
        assert p.asks[0].price == 0.52
        assert p.bids[0].side == "BUY"
        assert p.asks[0].side == "SELL"

    def test_bid_only(self):
        from mm.proposal import create_base_proposal
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 0.0, 0.50)
        assert len(p.bids) == 1
        assert len(p.asks) == 0

    def test_ask_only(self):
        from mm.proposal import create_base_proposal
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 0.0, 10.0, 0.50)
        assert len(p.bids) == 0
        assert len(p.asks) == 1

    def test_no_sides_zero_size(self):
        from mm.proposal import create_base_proposal
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 0.0, 0.0, 0.50)
        assert len(p.bids) == 0
        assert len(p.asks) == 0


class TestMultiLevel:
    def test_single_level_unchanged(self):
        from mm.proposal import create_base_proposal, apply_multi_level
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_multi_level(p, levels=1)
        assert len(p.bids) == 1
        assert len(p.asks) == 1

    def test_creates_n_levels(self):
        from mm.proposal import create_base_proposal, apply_multi_level
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_multi_level(p, levels=3, spread_mult=1.5, size_mult=2.0)
        assert len(p.bids) == 3
        assert len(p.asks) == 3

    def test_spreads_compound(self):
        from mm.proposal import create_base_proposal, apply_multi_level
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_multi_level(p, levels=3, spread_mult=1.5, size_mult=2.0)
        # Each level should be wider than the previous
        bid_prices = [b.price for b in p.bids]
        assert bid_prices[0] > bid_prices[1] > bid_prices[2]
        ask_prices = [a.price for a in p.asks]
        assert ask_prices[0] < ask_prices[1] < ask_prices[2]

    def test_sizes_compound(self):
        from mm.proposal import create_base_proposal, apply_multi_level
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_multi_level(p, levels=3, spread_mult=1.5, size_mult=2.0)
        assert p.bids[1].size == pytest.approx(20.0)
        assert p.bids[2].size == pytest.approx(40.0)


class TestVolAdjustment:
    def test_no_change_below_threshold(self):
        from mm.proposal import create_base_proposal, apply_vol_adjustment
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_vol_adjustment(p, vol_pts=3.0, threshold=5.0)
        assert p.bids[0].price == 0.48
        assert p.asks[0].price == 0.52

    def test_widens_above_threshold(self):
        from mm.proposal import create_base_proposal, apply_vol_adjustment
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_vol_adjustment(p, vol_pts=10.0, threshold=5.0)
        assert p.bids[0].price < 0.48
        assert p.asks[0].price > 0.52


class TestEventRisk:
    def test_no_change_without_warning(self):
        from mm.proposal import create_base_proposal, apply_event_risk
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_event_risk(p, guard_warning=False)
        assert p.bids[0].price == 0.48

    def test_widens_with_warning(self):
        from mm.proposal import create_base_proposal, apply_event_risk
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_event_risk(p, guard_warning=True, widen_pct=50.0)
        assert p.bids[0].price < 0.48
        assert p.asks[0].price > 0.52


class TestBudgetConstraint:
    def test_caps_sizes(self):
        from mm.proposal import create_base_proposal, apply_budget_constraint
        p = create_base_proposal("m1", "t1", 0.50, 0.50, 100.0, 100.0, 0.50)
        p = apply_budget_constraint(p, available_capital=10.0, committed=0.0)
        # With $10 budget, can afford at most 10/0.50=20 shares on bid
        for order in p.bids:
            assert order.size * order.price <= 10.0 + 0.01

    def test_removes_zero_budget(self):
        from mm.proposal import create_base_proposal, apply_budget_constraint
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_budget_constraint(p, available_capital=0.0, committed=0.0)
        assert len(p.bids) == 0
        assert len(p.asks) == 0

    def test_committed_reduces_budget(self):
        from mm.proposal import create_base_proposal, apply_budget_constraint
        p = create_base_proposal("m1", "t1", 0.50, 0.50, 100.0, 100.0, 0.50)
        p = apply_budget_constraint(p, available_capital=20.0, committed=15.0)
        # Only $5 remaining


class TestPostOnlyFilter:
    def test_prevents_crossing_bid(self):
        from mm.proposal import create_base_proposal, apply_post_only_filter
        # Bid at 0.52 would cross best_ask at 0.51
        p = create_base_proposal("m1", "t1", 0.52, 0.54, 10.0, 10.0, 0.53)
        p = apply_post_only_filter(p, best_bid=0.50, best_ask=0.51)
        assert p.bids[0].price < 0.51

    def test_prevents_crossing_ask(self):
        from mm.proposal import create_base_proposal, apply_post_only_filter
        # Ask at 0.48 would cross best_bid at 0.49
        p = create_base_proposal("m1", "t1", 0.46, 0.48, 10.0, 10.0, 0.47)
        p = apply_post_only_filter(p, best_bid=0.49, best_ask=0.51)
        assert p.asks[0].price > 0.49

    def test_no_change_when_ok(self):
        from mm.proposal import create_base_proposal, apply_post_only_filter
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50)
        p = apply_post_only_filter(p, best_bid=0.49, best_ask=0.51)
        assert p.bids[0].price == 0.48
        assert p.asks[0].price == 0.52


class TestFullPipeline:
    def test_integration(self):
        from mm.proposal import (
            create_base_proposal, apply_multi_level, apply_vol_adjustment,
            apply_event_risk, apply_budget_constraint, apply_post_only_filter,
        )
        p = create_base_proposal("m1", "t1", 0.48, 0.52, 10.0, 10.0, 0.50, 0.50)
        p = apply_multi_level(p, levels=2, spread_mult=1.5, size_mult=2.0)
        p = apply_vol_adjustment(p, vol_pts=3.0, threshold=5.0)
        p = apply_event_risk(p, guard_warning=False)
        p = apply_budget_constraint(p, available_capital=100.0, committed=0.0)
        p = apply_post_only_filter(p, best_bid=0.47, best_ask=0.53)
        # Should have 2 levels of bids and 2 levels of asks
        assert len(p.bids) == 2
        assert len(p.asks) == 2
        # All prices should be valid
        for order in p.bids + p.asks:
            assert 0.01 <= order.price <= 0.99
            assert order.size > 0
