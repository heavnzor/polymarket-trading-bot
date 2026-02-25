"""Tests for mm/inventory.py — YES + NO inventory tracking."""

import pytest
from mm.inventory import InventoryManager, MarketInventory
from config import MarketMakingConfig


@pytest.fixture
def inv_mgr():
    return InventoryManager(MarketMakingConfig())


class TestMarketInventory:
    def test_mergeable_pairs_both_positive(self):
        inv = MarketInventory(market_id="m1", token_id="t1", net_position=10.0, no_position=7.0)
        assert inv.mergeable_pairs == 7.0

    def test_mergeable_pairs_one_zero(self):
        inv = MarketInventory(market_id="m1", token_id="t1", net_position=10.0, no_position=0.0)
        assert inv.mergeable_pairs == 0.0

    def test_mergeable_pairs_both_zero(self):
        inv = MarketInventory(market_id="m1", token_id="t1")
        assert inv.mergeable_pairs == 0.0


class TestInventoryManagerGet:
    def test_creates_new_inventory(self, inv_mgr):
        inv = inv_mgr.get("m1")
        assert inv.market_id == "m1"
        assert inv.net_position == 0.0
        assert inv.no_position == 0.0

    def test_returns_existing_inventory(self, inv_mgr):
        inv1 = inv_mgr.get("m1")
        inv1.net_position = 5.0
        inv2 = inv_mgr.get("m1")
        assert inv2.net_position == 5.0


class TestProcessFill:
    def test_buy_yes_updates_position(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok-yes", "BUY", 0.50, 10.0)
        inv = inv_mgr.get("m1")
        assert inv.net_position == 10.0
        assert inv.avg_entry_price == 0.50
        assert inv.token_id == "tok-yes"

    def test_buy_no_updates_no_position(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok-no", "BUY", 0.40, 8.0, is_no_token=True)
        inv = inv_mgr.get("m1")
        assert inv.no_position == 8.0
        assert inv.no_avg_entry_price == 0.40
        assert inv.no_token_id == "tok-no"
        # YES side unchanged
        assert inv.net_position == 0.0

    def test_sell_yes_computes_pnl(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok-yes", "BUY", 0.50, 10.0)
        inv_mgr.process_fill("m1", "tok-yes", "SELL", 0.60, 5.0)
        inv = inv_mgr.get("m1")
        assert inv.net_position == 5.0
        # PnL: 5 * (0.60 - 0.50) = 0.50
        assert inv.realized_pnl == pytest.approx(0.50)

    def test_sell_no_computes_pnl(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok-no", "BUY", 0.40, 10.0, is_no_token=True)
        inv_mgr.process_fill("m1", "tok-no", "SELL", 0.55, 4.0, is_no_token=True)
        inv = inv_mgr.get("m1")
        assert inv.no_position == 6.0
        # PnL: 4 * (0.55 - 0.40) = 0.60
        assert inv.no_realized_pnl == pytest.approx(0.60)

    def test_multiple_buys_average_price(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok-yes", "BUY", 0.50, 10.0)
        inv_mgr.process_fill("m1", "tok-yes", "BUY", 0.60, 10.0)
        inv = inv_mgr.get("m1")
        assert inv.net_position == 20.0
        # Avg: (0.50*10 + 0.60*10) / 20 = 0.55
        assert inv.avg_entry_price == pytest.approx(0.55)


class TestProcessMerge:
    def test_merge_reduces_both_positions(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        inv.no_position = 8.0
        inv_mgr.process_merge("m1", 5.0)
        assert inv.net_position == 5.0
        assert inv.no_position == 3.0

    def test_merge_fails_if_insufficient(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 3.0
        inv.no_position = 8.0
        # Can't merge 5 when YES only has 3
        inv_mgr.process_merge("m1", 5.0)
        # Positions unchanged
        assert inv.net_position == 3.0
        assert inv.no_position == 8.0


class TestProcessSplit:
    def test_split_adds_to_both_positions(self, inv_mgr):
        inv_mgr.process_split("m1", 10.0, "yes-tok", "no-tok")
        inv = inv_mgr.get("m1")
        assert inv.net_position == 10.0
        assert inv.no_position == 10.0
        assert inv.token_id == "yes-tok"
        assert inv.no_token_id == "no-tok"

    def test_split_sets_avg_entry_to_half(self, inv_mgr):
        inv_mgr.process_split("m1", 10.0, "yes-tok", "no-tok")
        inv = inv_mgr.get("m1")
        # Split is at $1 per pair, so each side costs $0.50
        assert inv.avg_entry_price == 0.50
        assert inv.no_avg_entry_price == 0.50


class TestExposureAndPnl:
    def test_total_exposure_yes_and_no(self, inv_mgr):
        inv_mgr.process_split("m1", 10.0, "yes-tok", "no-tok")
        # YES: 10 * 0.50 = 5.0, NO: 10 * 0.50 = 5.0 -> total = 10.0
        assert inv_mgr.get_total_exposure() == pytest.approx(10.0)

    def test_total_realized_pnl(self, inv_mgr):
        inv_mgr.process_fill("m1", "yes", "BUY", 0.50, 10.0)
        inv_mgr.process_fill("m1", "yes", "SELL", 0.60, 10.0)
        inv_mgr.process_fill("m1", "no", "BUY", 0.40, 10.0, is_no_token=True)
        inv_mgr.process_fill("m1", "no", "SELL", 0.50, 10.0, is_no_token=True)
        # YES PnL: 10 * (0.60 - 0.50) = 1.0
        # NO PnL: 10 * (0.50 - 0.40) = 1.0
        assert inv_mgr.get_total_realized_pnl() == pytest.approx(2.0)


class TestCapacityAndSkew:
    def test_is_at_capacity_with_mid_fallback(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 20.0
        # avg_entry_price is 0, so it falls back to mid
        result = inv_mgr.is_at_capacity("m1", max_per_market=5.0, mid=0.50)
        # 20 * 0.50 = 10.0 >= 5.0 -> at capacity
        assert result is True

    def test_is_at_capacity_with_avg_entry(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 5.0
        inv.avg_entry_price = 0.40
        # 5 * 0.40 = 2.0 < 5.0 -> not at capacity
        assert inv_mgr.is_at_capacity("m1", max_per_market=5.0) is False

    def test_skew_direction_long_yes(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        inv.avg_entry_price = 0.50
        inv.no_position = 2.0
        inv.no_avg_entry_price = 0.50
        # skew = (10*0.50 - 2*0.50) / 10 = 0.40
        assert inv_mgr.get_skew_direction("m1", 10.0) == pytest.approx(0.40)

    def test_skew_direction_long_no(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 2.0
        inv.avg_entry_price = 0.50
        inv.no_position = 10.0
        inv.no_avg_entry_price = 0.50
        # skew = (2*0.50 - 10*0.50) / 10 = -0.40
        assert inv_mgr.get_skew_direction("m1", 10.0) == pytest.approx(-0.40)


class TestGetMergeAmount:
    def test_merge_amount_min_of_positions(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        inv.no_position = 7.0
        assert inv_mgr.get_merge_amount("m1") == 7.0

    def test_merge_amount_zero_when_one_empty(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        inv.no_position = 0.0
        assert inv_mgr.get_merge_amount("m1") == 0.0


class TestGetAllPositions:
    def test_includes_yes_and_no_data(self, inv_mgr):
        inv_mgr.process_split("m1", 10.0, "yes-tok", "no-tok")
        positions = inv_mgr.get_all_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert pos["yes_position"] == 10.0
        assert pos["no_position"] == 10.0
        assert pos["yes_avg_entry"] == 0.50
        assert pos["no_avg_entry"] == 0.50
        assert pos["no_token_id"] == "no-tok"
        assert pos["mergeable_pairs"] == 10.0

    def test_empty_when_no_positions(self, inv_mgr):
        assert inv_mgr.get_all_positions() == []


class TestPositionAge:
    def test_age_zero_when_no_position(self, inv_mgr):
        inv = inv_mgr.get("m1")
        assert inv.position_age_hours() == 0.0

    def test_age_zero_when_no_opened_at(self, inv_mgr):
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        assert inv.position_age_hours() == 0.0  # opened_at is None

    def test_age_positive_after_buy(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok", "BUY", 0.50, 10.0)
        inv = inv_mgr.get("m1")
        assert inv.opened_at is not None
        # Just opened, age should be very small
        assert inv.position_age_hours() < 0.01

    def test_opened_at_set_on_first_buy(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok", "BUY", 0.50, 10.0)
        first_opened = inv_mgr.get("m1").opened_at
        # Second buy should NOT reset opened_at
        inv_mgr.process_fill("m1", "tok", "BUY", 0.60, 5.0)
        assert inv_mgr.get("m1").opened_at == first_opened

    def test_opened_at_reset_on_full_close(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok", "BUY", 0.50, 10.0)
        assert inv_mgr.get("m1").opened_at is not None
        inv_mgr.process_fill("m1", "tok", "SELL", 0.60, 10.0)
        assert inv_mgr.get("m1").opened_at is None

    def test_opened_at_persists_on_partial_close(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok", "BUY", 0.50, 10.0)
        inv_mgr.process_fill("m1", "tok", "SELL", 0.60, 5.0)
        assert inv_mgr.get("m1").opened_at is not None

    def test_no_token_buy_sets_opened_at(self, inv_mgr):
        inv_mgr.process_fill("m1", "no-tok", "BUY", 0.40, 8.0, is_no_token=True)
        assert inv_mgr.get("m1").opened_at is not None


class TestUnwindUrgency:
    def test_urgency_zero_for_unknown_market(self, inv_mgr):
        assert inv_mgr.get_unwind_urgency("unknown") == 0.0

    def test_urgency_zero_for_fresh_position(self, inv_mgr):
        inv_mgr.process_fill("m1", "tok", "BUY", 0.50, 10.0)
        # Just opened, urgency should be near zero
        assert inv_mgr.get_unwind_urgency("m1") < 0.01

    def test_urgency_capped_at_one(self, inv_mgr):
        import datetime
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        inv.opened_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        # Very old position, urgency should be capped at 1.0
        assert inv_mgr.get_unwind_urgency("m1", max_hours=24.0) == 1.0

    def test_urgency_scales_linearly(self, inv_mgr):
        import datetime
        inv = inv_mgr.get("m1")
        inv.net_position = 10.0
        # Set opened_at to 12 hours ago
        inv.opened_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=12)
        urgency = inv_mgr.get_unwind_urgency("m1", max_hours=24.0)
        assert 0.45 <= urgency <= 0.55  # Should be ~0.5


# ═══════════════════════════════════════════════════════════════════════
# load_from_db YES+NO grouping (5A)
# ═══════════════════════════════════════════════════════════════════════

class TestLoadFromDB:
    """Tests for InventoryManager.load_from_db."""

    @pytest.fixture
    def manager(self, mm_config):
        from mm.inventory import InventoryManager
        return InventoryManager(mm_config)

    def test_load_single_record(self, manager):
        """Single record per market -> YES only."""
        db_inv = [
            {"market_id": "m1", "token_id": "yes_tok", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.5},
        ]
        manager.load_from_db(db_inv)
        inv = manager.get("m1")
        assert inv.net_position == 10.0
        assert inv.token_id == "yes_tok"
        assert inv.avg_entry_price == 0.50
        assert inv.no_position == 0.0

    def test_load_yes_and_no(self, manager):
        """Two records for same market -> YES + NO restored."""
        db_inv = [
            {"market_id": "m1", "token_id": "yes_tok", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.5},
            {"market_id": "m1", "token_id": "no_tok", "net_position": 8.0,
             "avg_entry_price": 0.45, "realized_pnl": 0.2},
        ]
        manager.load_from_db(db_inv)
        inv = manager.get("m1")
        assert inv.net_position == 10.0
        assert inv.token_id == "yes_tok"
        assert inv.no_position == 8.0
        assert inv.no_token_id == "no_tok"
        assert inv.no_avg_entry_price == 0.45

    def test_load_multiple_markets(self, manager):
        """Multiple markets each with single record."""
        db_inv = [
            {"market_id": "m1", "token_id": "t1", "net_position": 5.0,
             "avg_entry_price": 0.40, "realized_pnl": 0.0},
            {"market_id": "m2", "token_id": "t2", "net_position": 3.0,
             "avg_entry_price": 0.60, "realized_pnl": 1.0},
        ]
        manager.load_from_db(db_inv)
        assert manager.get("m1").net_position == 5.0
        assert manager.get("m2").net_position == 3.0


# ═══════════════════════════════════════════════════════════════════════
# reconcile_with_clob YES+NO (5A)
# ═══════════════════════════════════════════════════════════════════════

class TestReconcileWithCLOB:
    """Tests for InventoryManager.reconcile_with_clob."""

    @pytest.fixture
    def manager(self, mm_config):
        from mm.inventory import InventoryManager
        return InventoryManager(mm_config)

    def test_no_divergence(self, manager):
        """Matching positions should return no divergences."""
        manager.load_from_db([
            {"market_id": "m1", "token_id": "t1", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.0},
        ])
        db_inv = [
            {"market_id": "m1", "token_id": "t1", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.0},
        ]
        divs = manager.reconcile_with_clob(db_inv)
        assert len(divs) == 0

    def test_yes_divergence_corrected(self, manager):
        """YES position divergence should be auto-corrected."""
        manager.load_from_db([
            {"market_id": "m1", "token_id": "t1", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.0},
        ])
        db_inv = [
            {"market_id": "m1", "token_id": "t1", "net_position": 15.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.0},
        ]
        divs = manager.reconcile_with_clob(db_inv)
        assert len(divs) == 1
        assert divs[0]["side"] == "YES"
        assert manager.get("m1").net_position == 15.0

    def test_no_divergence_corrected(self, manager):
        """NO position divergence should be auto-corrected."""
        manager.load_from_db([
            {"market_id": "m1", "token_id": "yes_tok", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.0},
            {"market_id": "m1", "token_id": "no_tok", "net_position": 5.0,
             "avg_entry_price": 0.45, "realized_pnl": 0.0},
        ])
        db_inv = [
            {"market_id": "m1", "token_id": "yes_tok", "net_position": 10.0,
             "avg_entry_price": 0.50, "realized_pnl": 0.0},
            {"market_id": "m1", "token_id": "no_tok", "net_position": 8.0,
             "avg_entry_price": 0.45, "realized_pnl": 0.0},
        ]
        divs = manager.reconcile_with_clob(db_inv)
        assert len(divs) == 1
        assert divs[0]["side"] == "NO"
        assert manager.get("m1").no_position == 8.0
