"""Tests for mm/arbitrage.py — complete-set arbitrage detection and execution."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from config import MarketMakingConfig
from mm.arbitrage import (
    ArbOpportunity,
    MIN_ARB_SIZE,
    _ask_depth_shares,
    _bid_depth_shares,
    _extract_order_id,
    execute_arbitrage,
    scan_for_arbitrage,
)
from mm.inventory import InventoryManager


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

MARKET_ID = "market-arb-001"
CONDITION_ID = "cond-arb-001"
YES_TOKEN = "tok-yes-arb"
NO_TOKEN = "tok-no-arb"


@pytest.fixture
def mm_cfg():
    cfg = MarketMakingConfig()
    cfg.mm_arb_enabled = True
    cfg.mm_arb_min_profit_pct = 0.5
    cfg.mm_arb_max_size_usd = 50.0
    cfg.mm_arb_gas_cost_usd = 0.005
    return cfg


@pytest.fixture
def inv_mgr():
    return InventoryManager(MarketMakingConfig())


@pytest.fixture
def mock_client():
    """Return a MagicMock mimicking PolymarketClient for arb tests."""
    client = MagicMock()
    client.place_limit_order = MagicMock(return_value={"orderID": "order-123"})
    client.is_order_filled = MagicMock(return_value=(True, "MATCHED", 20.0, {}))
    client.cancel_order = MagicMock(return_value=True)
    client.merge_positions = MagicMock(return_value=True)
    client.split_position = MagicMock(return_value=True)
    return client


def _make_book(best_bid=0.0, best_ask=1.0, bid_depth_5=0, ask_depth_5=0):
    """Helper to create a book summary dict."""
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_depth_5": bid_depth_5,
        "ask_depth_5": ask_depth_5,
    }


def _make_opp(arb_type="buy_merge", yes_price=0.45, no_price=0.48, max_size=20.0):
    """Helper to create an ArbOpportunity."""
    if arb_type == "buy_merge":
        buy_cost = yes_price + no_price
        gross_profit_pct = ((1.0 - buy_cost) / buy_cost) * 100
        net_profit_pct = gross_profit_pct - 0.01  # approximate
    else:
        sell_revenue = yes_price + no_price
        gross_profit_pct = (sell_revenue - 1.0) * 100
        net_profit_pct = gross_profit_pct - 0.01
    return ArbOpportunity(
        market_id=MARKET_ID,
        condition_id=CONDITION_ID,
        yes_token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        arb_type=arb_type,
        yes_price=yes_price,
        no_price=no_price,
        gross_profit_pct=gross_profit_pct,
        net_profit_pct=net_profit_pct,
        max_size=max_size,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════


class TestAskDepthShares:
    def test_normal_depth(self):
        book = _make_book(best_ask=0.50, ask_depth_5=100)
        assert _ask_depth_shares(book) == pytest.approx(200.0)

    def test_zero_ask_depth(self):
        book = _make_book(best_ask=0.50, ask_depth_5=0)
        assert _ask_depth_shares(book) == 0.0

    def test_zero_best_ask(self):
        book = _make_book(best_ask=0.0, ask_depth_5=100)
        assert _ask_depth_shares(book) == 0.0

    def test_high_price(self):
        book = _make_book(best_ask=0.95, ask_depth_5=95)
        assert _ask_depth_shares(book) == pytest.approx(100.0)

    def test_missing_keys_returns_zero(self):
        book = {}
        assert _ask_depth_shares(book) == 0.0

    def test_default_values_return_zero(self):
        # best_ask defaults to 1.0, ask_depth_5 defaults to 0
        book = {"best_ask": 1.0}
        assert _ask_depth_shares(book) == 0.0


class TestBidDepthShares:
    def test_normal_depth(self):
        book = _make_book(best_bid=0.50, bid_depth_5=100)
        assert _bid_depth_shares(book) == pytest.approx(200.0)

    def test_zero_bid_depth(self):
        book = _make_book(best_bid=0.50, bid_depth_5=0)
        assert _bid_depth_shares(book) == 0.0

    def test_zero_best_bid(self):
        book = _make_book(best_bid=0.0, bid_depth_5=100)
        assert _bid_depth_shares(book) == 0.0

    def test_missing_keys_returns_zero(self):
        book = {}
        assert _bid_depth_shares(book) == 0.0

    def test_default_values_return_zero(self):
        # best_bid defaults to 0, so should return 0
        book = {"bid_depth_5": 100}
        assert _bid_depth_shares(book) == 0.0


class TestExtractOrderId:
    def test_none_input(self):
        assert _extract_order_id(None) is None

    def test_dict_with_orderID(self):
        assert _extract_order_id({"orderID": "abc-123"}) == "abc-123"

    def test_dict_with_id(self):
        assert _extract_order_id({"id": "xyz-789"}) == "xyz-789"

    def test_dict_orderID_takes_precedence_over_id(self):
        assert _extract_order_id({"orderID": "abc", "id": "xyz"}) == "abc"

    def test_empty_dict(self):
        assert _extract_order_id({}) is None

    def test_object_with_orderID_attribute(self):
        class FakeResponse:
            orderID = "obj-order-1"
        assert _extract_order_id(FakeResponse()) == "obj-order-1"

    def test_object_with_id_attribute(self):
        class FakeResponse:
            id = "obj-id-1"
        assert _extract_order_id(FakeResponse()) == "obj-id-1"

    def test_dict_with_empty_orderID_falls_to_id(self):
        # Empty string is falsy, should fall through to 'id'
        assert _extract_order_id({"orderID": "", "id": "fallback"}) == "fallback"


# ═══════════════════════════════════════════════════════════════════════════════
# scan_for_arbitrage
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanForArbitrageNoOpportunity:
    """Cases where scan should return None."""

    def test_no_opportunity_fair_prices(self):
        """YES_ask + NO_ask >= 1.0 and YES_bid + NO_bid <= 1.0 => no arb."""
        yes_book = _make_book(best_bid=0.48, best_ask=0.52, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.45, best_ask=0.50, bid_depth_5=100, ask_depth_5=100)
        # YES_ask + NO_ask = 0.52 + 0.50 = 1.02 > 1.0 => no buy-merge
        # YES_bid + NO_bid = 0.48 + 0.45 = 0.93 < 1.0 => no split-sell
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_none_yes_book(self):
        no_book = _make_book(best_ask=0.40, ask_depth_5=100)
        result = scan_for_arbitrage(
            None, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_none_no_book(self):
        yes_book = _make_book(best_ask=0.40, ask_depth_5=100)
        result = scan_for_arbitrage(
            yes_book, None, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_both_books_none(self):
        result = scan_for_arbitrage(
            None, None, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_empty_books(self):
        # Empty dicts: best_ask defaults to 1.0, best_bid to 0.0
        # 1.0 + 1.0 = 2.0 > 1.0 => buy_cost not < 1.0
        # 0.0 + 0.0 = 0.0 < 1.0 => sell_revenue not > 1.0
        # Also both bids are 0, which fails the price validation
        result = scan_for_arbitrage(
            {}, {}, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_zero_prices_rejected(self):
        """Prices <= 0 should be rejected by the validation."""
        yes_book = _make_book(best_bid=0.0, best_ask=0.0, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.50, best_ask=0.50, bid_depth_5=100, ask_depth_5=100)
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None


class TestScanForArbitrageBuyMerge:
    """Detect buy-merge opportunities: YES_ask + NO_ask < 1.0."""

    def test_buy_merge_detected(self):
        """Clear buy-merge opportunity with sufficient depth."""
        # YES ask=0.45, NO ask=0.48 => cost=0.93 < 1.0
        yes_book = _make_book(best_bid=0.43, best_ask=0.45, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.46, best_ask=0.48, bid_depth_5=50, ask_depth_5=50)
        # YES ask_depth_shares = 50 / 0.45 = 111.1
        # NO ask_depth_shares = 50 / 0.48 = 104.2
        # max_size = min(111.1, 104.2) = 104.2 > MIN_ARB_SIZE
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.5,
        )
        assert result is not None
        assert result.arb_type == "buy_merge"
        assert result.yes_price == 0.45
        assert result.no_price == 0.48
        assert result.market_id == MARKET_ID
        assert result.condition_id == CONDITION_ID
        assert result.yes_token_id == YES_TOKEN
        assert result.no_token_id == NO_TOKEN
        assert result.gross_profit_pct > 0
        assert result.net_profit_pct > 0

    def test_buy_merge_insufficient_depth(self):
        """Opportunity exists but depth is too small."""
        # YES ask=0.45, NO ask=0.48 => cost=0.93 < 1.0
        # But depth too small: ask_depth=1 / 0.45 = 2.2 < MIN_ARB_SIZE(5)
        yes_book = _make_book(best_bid=0.43, best_ask=0.45, bid_depth_5=1, ask_depth_5=1)
        no_book = _make_book(best_bid=0.46, best_ask=0.48, bid_depth_5=1, ask_depth_5=1)
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_buy_merge_below_min_profit(self):
        """Opportunity exists but net profit below min_profit_pct threshold."""
        # YES ask=0.498, NO ask=0.498 => cost=0.996 < 1.0 (very thin margin)
        yes_book = _make_book(best_bid=0.49, best_ask=0.498, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.49, best_ask=0.498, bid_depth_5=50, ask_depth_5=50)
        # gross_profit = 1.0 - 0.996 = 0.004 per pair
        # max_size ~= 50/0.498 = 100.4
        # net_profit = 0.004 * 100.4 - 0.005 = 0.397
        # net_profit_pct = 0.397 / (0.996 * 100.4) * 100 = 0.397%
        # With min_profit_pct=0.5, this should fail
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.5,
        )
        assert result is None

    def test_buy_merge_exactly_at_threshold(self):
        """Cost exactly 1.0 => no opportunity (buy_cost < 1.0 is strict)."""
        yes_book = _make_book(best_bid=0.49, best_ask=0.50, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.49, best_ask=0.50, bid_depth_5=100, ask_depth_5=100)
        # cost = 0.50 + 0.50 = 1.00, NOT < 1.0
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_buy_merge_max_size_limited_by_weaker_side(self):
        """max_size should be the minimum of both sides' depth."""
        # YES has large depth, NO has small depth
        yes_book = _make_book(best_bid=0.43, best_ask=0.45, bid_depth_5=500, ask_depth_5=500)
        no_book = _make_book(best_bid=0.46, best_ask=0.48, bid_depth_5=10, ask_depth_5=10)
        # YES depth = 500/0.45 = 1111
        # NO depth = 10/0.48 = 20.8
        # max_size should be ~20.8
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is not None
        assert result.max_size == pytest.approx(10.0 / 0.48, abs=0.1)


class TestScanForArbitrageSplitSell:
    """Detect split-sell opportunities: YES_bid + NO_bid > 1.0."""

    def test_split_sell_detected(self):
        """Clear split-sell opportunity with sufficient depth."""
        # YES bid=0.55, NO bid=0.52 => revenue=1.07 > 1.0
        yes_book = _make_book(best_bid=0.55, best_ask=0.57, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.52, best_ask=0.54, bid_depth_5=50, ask_depth_5=50)
        # YES bid_depth_shares = 50 / 0.55 = 90.9
        # NO bid_depth_shares = 50 / 0.52 = 96.2
        # max_size = min(90.9, 96.2) = 90.9 > MIN_ARB_SIZE
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.5,
        )
        assert result is not None
        assert result.arb_type == "split_sell"
        assert result.yes_price == 0.55
        assert result.no_price == 0.52
        assert result.gross_profit_pct > 0
        assert result.net_profit_pct > 0

    def test_split_sell_insufficient_depth(self):
        """Opportunity exists but depth too small."""
        yes_book = _make_book(best_bid=0.55, best_ask=0.57, bid_depth_5=2, ask_depth_5=2)
        no_book = _make_book(best_bid=0.52, best_ask=0.54, bid_depth_5=2, ask_depth_5=2)
        # YES bid_depth_shares = 2 / 0.55 = 3.6 < MIN_ARB_SIZE(5)
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None

    def test_split_sell_below_min_profit(self):
        """Thin margin below threshold."""
        # YES bid=0.501, NO bid=0.501 => revenue=1.002 > 1.0 (very thin)
        yes_book = _make_book(best_bid=0.501, best_ask=0.52, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.501, best_ask=0.52, bid_depth_5=50, ask_depth_5=50)
        # gross_profit = 0.002 per pair
        # max_size ~= 50/0.501 = 99.8
        # net_profit = 0.002 * 99.8 - 0.005 = 0.195
        # net_profit_pct = 0.195 / 99.8 * 100 = 0.195%
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.5,
        )
        assert result is None

    def test_split_sell_exactly_at_threshold(self):
        """Revenue exactly 1.0 => no opportunity (sell_revenue > 1.0 is strict)."""
        yes_book = _make_book(best_bid=0.50, best_ask=0.52, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.50, best_ask=0.52, bid_depth_5=100, ask_depth_5=100)
        # revenue = 0.50 + 0.50 = 1.00, NOT > 1.0
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is None


class TestScanForArbitrageEdgeCases:
    """Edge cases and parameter variations."""

    def test_custom_gas_cost(self):
        """Higher gas cost reduces net profit, may kill opportunity."""
        yes_book = _make_book(best_bid=0.43, best_ask=0.45, bid_depth_5=10, ask_depth_5=10)
        no_book = _make_book(best_bid=0.46, best_ask=0.48, bid_depth_5=10, ask_depth_5=10)
        # With tiny depth and very high gas cost, net profit should be negative
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            gas_cost_usd=100.0,
            min_profit_pct=0.5,
        )
        assert result is None

    def test_custom_min_profit_pct_zero(self):
        """With min_profit_pct=0, even thin margins should pass."""
        # Very thin buy-merge margin
        yes_book = _make_book(best_bid=0.49, best_ask=0.495, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.49, best_ask=0.495, bid_depth_5=100, ask_depth_5=100)
        # cost = 0.495 + 0.495 = 0.99 < 1.0
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.0,
        )
        assert result is not None
        assert result.arb_type == "buy_merge"

    def test_buy_merge_preferred_over_split_sell(self):
        """When both could theoretically exist, buy-merge is checked first."""
        # Construct a case where buy-merge exists
        yes_book = _make_book(best_bid=0.55, best_ask=0.40, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.55, best_ask=0.40, bid_depth_5=100, ask_depth_5=100)
        # buy_cost = 0.40 + 0.40 = 0.80 < 1.0 => buy-merge
        # sell_revenue = 0.55 + 0.55 = 1.10 > 1.0 => split-sell also possible
        # But code checks buy-merge first and returns immediately
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.0,
        )
        assert result is not None
        assert result.arb_type == "buy_merge"

    def test_prices_at_extremes(self):
        """Prices near 0 or near 1 edge case."""
        # YES ask at 0.01, NO ask at 0.01 => cost = 0.02 << 1.0
        yes_book = _make_book(best_bid=0.005, best_ask=0.01, bid_depth_5=10, ask_depth_5=10)
        no_book = _make_book(best_bid=0.005, best_ask=0.01, bid_depth_5=10, ask_depth_5=10)
        # ask_depth_shares = 10 / 0.01 = 1000
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
            min_profit_pct=0.5,
        )
        assert result is not None
        assert result.arb_type == "buy_merge"
        assert result.gross_profit_pct > 4000  # huge percentage profit

    def test_arb_opportunity_has_detected_at(self):
        """Verify detected_at is populated."""
        yes_book = _make_book(best_bid=0.43, best_ask=0.45, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.46, best_ask=0.48, bid_depth_5=50, ask_depth_5=50)
        result = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert result is not None
        assert isinstance(result.detected_at, datetime)
        assert result.detected_at.tzinfo is not None


# ═══════════════════════════════════════════════════════════════════════════════
# execute_arbitrage
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecuteArbitrage:
    """Tests for the execute_arbitrage orchestrator."""

    @pytest.mark.asyncio
    async def test_dispatches_buy_merge(self, mock_client, inv_mgr, mm_cfg):
        """Should call _execute_buy_merge for buy_merge type."""
        opp = _make_opp(arb_type="buy_merge", max_size=20.0)

        with patch("mm.arbitrage._execute_buy_merge", return_value={"success": True}) as mock_bm:
            result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)

        mock_bm.assert_called_once()
        assert result["arb_type"] == "buy_merge"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_dispatches_split_sell(self, mock_client, inv_mgr, mm_cfg):
        """Should call _execute_split_sell for split_sell type."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        with patch("mm.arbitrage._execute_split_sell", return_value={"success": True}) as mock_ss:
            result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)

        mock_ss.assert_called_once()
        assert result["arb_type"] == "split_sell"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_size_capped_at_max(self, mock_client, inv_mgr, mm_cfg):
        """Size should be capped at mm_arb_max_size_usd."""
        mm_cfg.mm_arb_max_size_usd = 10.0
        opp = _make_opp(arb_type="buy_merge", max_size=100.0)

        with patch("mm.arbitrage._execute_buy_merge", return_value={"success": True}) as mock_bm:
            result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)

        # Verify size was capped
        assert result["size"] == 10.0
        # The capped size should be passed to _execute_buy_merge
        call_args = mock_bm.call_args
        assert call_args[0][3] == 10.0  # size arg

    @pytest.mark.asyncio
    async def test_size_uses_max_size_when_smaller(self, mock_client, inv_mgr, mm_cfg):
        """When max_size < mm_arb_max_size_usd, use max_size."""
        mm_cfg.mm_arb_max_size_usd = 50.0
        opp = _make_opp(arb_type="buy_merge", max_size=15.0)

        with patch("mm.arbitrage._execute_buy_merge", return_value={"success": True}) as mock_bm:
            result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)

        assert result["size"] == 15.0

    @pytest.mark.asyncio
    async def test_result_includes_metadata(self, mock_client, inv_mgr, mm_cfg):
        """Result should include market_id, prices, profit info."""
        opp = _make_opp(arb_type="buy_merge")

        with patch("mm.arbitrage._execute_buy_merge", return_value={"success": True}):
            result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)

        assert result["market_id"] == MARKET_ID
        assert result["yes_price"] == opp.yes_price
        assert result["no_price"] == opp.no_price
        assert "executed_at" in result
        assert "gross_profit_pct" in result
        assert "net_profit_pct" in result


# ═══════════════════════════════════════════════════════════════════════════════
# _execute_buy_merge
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecuteBuyMerge:
    """Tests for the buy-merge execution flow."""

    @pytest.mark.asyncio
    async def test_successful_buy_merge(self, mock_client, inv_mgr):
        """Happy path: both buys fill, merge succeeds."""
        opp = _make_opp(arb_type="buy_merge", yes_price=0.45, no_price=0.48, max_size=20.0)

        # Both orders fill completely
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 20.0, {}),   # YES order
            (True, "MATCHED", 20.0, {}),   # NO order
        ]
        mock_client.merge_positions.return_value = True

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is True
        assert result["merged"] == 20.0
        expected_profit = (1.0 - 0.45 - 0.48) * 20.0
        assert result["profit_usd"] == pytest.approx(expected_profit, abs=0.01)

        # Verify merge was called
        mock_client.merge_positions.assert_called_once_with(CONDITION_ID, 20.0)

        # Verify inventory was updated (process_fill for YES and NO, then process_merge)
        inv = inv_mgr.get(MARKET_ID)
        # After merge, positions should be 0 (bought 20, merged 20)
        assert inv.net_position == 0.0
        assert inv.no_position == 0.0

    @pytest.mark.asyncio
    async def test_yes_buy_fails(self, mock_client, inv_mgr):
        """YES buy fails => return error immediately."""
        opp = _make_opp(arb_type="buy_merge")

        mock_client.place_limit_order.return_value = None

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "yes_buy_failed"
        # No cancel should be called since there's nothing to cancel
        mock_client.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_buy_fails_cancels_yes(self, mock_client, inv_mgr):
        """NO buy fails => cancel YES order and return error."""
        opp = _make_opp(arb_type="buy_merge")

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},  # YES succeeds
            None,                         # NO fails
        ]

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "no_buy_failed"

    @pytest.mark.asyncio
    async def test_partial_fills_below_min(self, mock_client, inv_mgr):
        """Both orders fill partially (below MIN_ARB_SIZE) => cancel remainder, track inventory."""
        opp = _make_opp(arb_type="buy_merge", yes_price=0.45, no_price=0.48, max_size=20.0)

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        # Partial fills: 3 shares each (below MIN_ARB_SIZE=5)
        mock_client.is_order_filled.side_effect = [
            (False, "LIVE", 3.0, {}),  # YES partial
            (False, "LIVE", 3.0, {}),  # NO partial
        ]

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "insufficient_fills"
        assert result["yes_filled"] == 3.0
        assert result["no_filled"] == 3.0

        # Cancels should have been called for both unfilled orders
        assert mock_client.cancel_order.call_count == 2

        # Partial fills should be tracked in inventory
        inv = inv_mgr.get(MARKET_ID)
        assert inv.net_position == 3.0
        assert inv.no_position == 3.0

    @pytest.mark.asyncio
    async def test_asymmetric_partial_fills(self, mock_client, inv_mgr):
        """YES fills fully, NO fills partially => merge_amount limited to min."""
        opp = _make_opp(arb_type="buy_merge", yes_price=0.45, no_price=0.48, max_size=20.0)

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 20.0, {}),  # YES fully filled
            (False, "LIVE", 2.0, {}),      # NO barely filled (< MIN_ARB_SIZE)
        ]

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        # merge_amount = min(20, 2) = 2 < MIN_ARB_SIZE => insufficient
        assert result["success"] is False
        assert result["error"] == "insufficient_fills"

    @pytest.mark.asyncio
    async def test_merge_fails_after_buys(self, mock_client, inv_mgr):
        """Both buys succeed but merge fails => tokens held in inventory."""
        opp = _make_opp(arb_type="buy_merge", yes_price=0.45, no_price=0.48, max_size=20.0)

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 20.0, {}),
            (True, "MATCHED", 20.0, {}),
        ]
        mock_client.merge_positions.return_value = False  # Merge fails

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "merge_failed"
        assert result["tokens_held"] == 20.0

        # Tokens should still be tracked in inventory
        inv = inv_mgr.get(MARKET_ID)
        assert inv.net_position == 20.0
        assert inv.no_position == 20.0

    @pytest.mark.asyncio
    async def test_size_rounding(self, mock_client, inv_mgr):
        """Size should be rounded to 1 decimal."""
        opp = _make_opp(arb_type="buy_merge", max_size=20.0)

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 15.6, {}),
            (True, "MATCHED", 15.6, {}),
        ]
        mock_client.merge_positions.return_value = True

        from mm.arbitrage import _execute_buy_merge
        # Pass a non-round size
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 15.567)

        assert result["success"] is True
        # The shares arg should have been rounded
        call_args = mock_client.place_limit_order.call_args_list[0]
        assert call_args[0][2] == 15.6  # size arg rounded to 1 decimal

    @pytest.mark.asyncio
    async def test_zero_filled_yes(self, mock_client, inv_mgr):
        """YES fill is 0, NO fill > 0 => merge_amount = 0 < MIN_ARB_SIZE."""
        opp = _make_opp(arb_type="buy_merge", max_size=20.0)

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (False, "LIVE", 0.0, {}),   # YES: no fill
            (False, "LIVE", 10.0, {}),   # NO: partial
        ]

        from mm.arbitrage import _execute_buy_merge
        result = await _execute_buy_merge(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "insufficient_fills"
        assert result["yes_filled"] == 0.0
        assert result["no_filled"] == 10.0

        # Only NO partial should be in inventory (YES was 0)
        inv = inv_mgr.get(MARKET_ID)
        assert inv.net_position == 0.0
        assert inv.no_position == 10.0


# ═══════════════════════════════════════════════════════════════════════════════
# _execute_split_sell
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecuteSplitSell:
    """Tests for the split-sell execution flow."""

    @pytest.mark.asyncio
    async def test_successful_split_sell(self, mock_client, inv_mgr):
        """Happy path: split succeeds, both sells fill fully."""
        opp = _make_opp(
            arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0,
        )

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-sell-1"},
            {"orderID": "no-sell-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 20.0, {}),  # YES sell
            (True, "MATCHED", 20.0, {}),  # NO sell
        ]

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is True
        assert result["yes_sold"] == 20.0
        assert result["no_sold"] == 20.0
        revenue = 20.0 * 0.55 + 20.0 * 0.52
        cost = 20.0
        expected_profit = revenue - cost
        assert result["profit_usd"] == pytest.approx(expected_profit, abs=0.01)

        # Verify split was called
        mock_client.split_position.assert_called_once_with(CONDITION_ID, 20.0)

    @pytest.mark.asyncio
    async def test_split_fails(self, mock_client, inv_mgr):
        """Split fails => return error immediately."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52)

        mock_client.split_position.return_value = False

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "split_failed"
        # No orders should have been placed
        mock_client.place_limit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_sells(self, mock_client, inv_mgr):
        """Split succeeds but sells are partial => partial_fills error."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-sell-1"},
            {"orderID": "no-sell-1"},
        ]
        # Only 50% filled each (below 90% threshold for success)
        mock_client.is_order_filled.side_effect = [
            (False, "LIVE", 10.0, {}),  # YES: 50% fill
            (False, "LIVE", 10.0, {}),  # NO: 50% fill
        ]

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "partial_fills"
        assert result["yes_sold"] == 10.0
        assert result["no_sold"] == 10.0
        # Profit still calculated
        assert "profit_usd" in result

    @pytest.mark.asyncio
    async def test_yes_sell_order_fails(self, mock_client, inv_mgr):
        """YES sell order returns None but NO sell succeeds."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            None,                         # YES sell fails
            {"orderID": "no-sell-1"},    # NO sell succeeds
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 20.0, {}),  # NO sell fills
        ]

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        # YES sold 0, NO sold 20 => partial
        assert result["success"] is False
        assert result["error"] == "partial_fills"
        assert result["yes_sold"] == 0.0

    @pytest.mark.asyncio
    async def test_both_sell_orders_fail(self, mock_client, inv_mgr):
        """Both sell orders fail — tokens stuck from split."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [None, None]

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is False
        assert result["error"] == "partial_fills"
        assert result["yes_sold"] == 0.0
        assert result["no_sold"] == 0.0

    @pytest.mark.asyncio
    async def test_split_sell_nearly_full_fills_succeeds(self, mock_client, inv_mgr):
        """Fills at >= 90% of amount should count as success."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-sell-1"},
            {"orderID": "no-sell-1"},
        ]
        # 95% fill — above the 90% threshold
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 19.0, {}),   # YES: 95%
            (True, "MATCHED", 18.5, {}),   # NO: 92.5%
        ]

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_split_sell_inventory_tracking(self, mock_client, inv_mgr):
        """Verify inventory is updated with split and sell fills."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-sell-1"},
            {"orderID": "no-sell-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 20.0, {}),
            (True, "MATCHED", 20.0, {}),
        ]

        from mm.arbitrage import _execute_split_sell
        await _execute_split_sell(opp, mock_client, inv_mgr, 20.0)

        inv = inv_mgr.get(MARKET_ID)
        # Split adds 20 to both sides, sells remove 20 from both sides
        assert inv.net_position == 0.0
        assert inv.no_position == 0.0

    @pytest.mark.asyncio
    async def test_split_sell_size_rounding(self, mock_client, inv_mgr):
        """Amount should be rounded to 1 decimal."""
        opp = _make_opp(arb_type="split_sell", yes_price=0.55, no_price=0.52, max_size=20.0)

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-sell-1"},
            {"orderID": "no-sell-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 12.3, {}),
            (True, "MATCHED", 12.3, {}),
        ]

        from mm.arbitrage import _execute_split_sell
        result = await _execute_split_sell(opp, mock_client, inv_mgr, 12.345)

        # Split should have been called with rounded amount
        mock_client.split_position.assert_called_once_with(CONDITION_ID, 12.3)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration-style tests (scan then execute)
# ═══════════════════════════════════════════════════════════════════════════════


class TestScanAndExecuteIntegration:
    """Verify scan + execute work together correctly."""

    @pytest.mark.asyncio
    async def test_scan_and_execute_buy_merge(self, mock_client, inv_mgr, mm_cfg):
        """Full flow: scan finds buy-merge, execute completes it."""
        yes_book = _make_book(best_bid=0.43, best_ask=0.45, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.46, best_ask=0.48, bid_depth_5=50, ask_depth_5=50)

        opp = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert opp is not None
        assert opp.arb_type == "buy_merge"

        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-order-1"},
            {"orderID": "no-order-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 50.0, {}),
            (True, "MATCHED", 50.0, {}),
        ]
        mock_client.merge_positions.return_value = True

        result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)
        assert result["success"] is True
        assert result["arb_type"] == "buy_merge"

    @pytest.mark.asyncio
    async def test_scan_and_execute_split_sell(self, mock_client, inv_mgr, mm_cfg):
        """Full flow: scan finds split-sell, execute completes it."""
        yes_book = _make_book(best_bid=0.55, best_ask=0.57, bid_depth_5=50, ask_depth_5=50)
        no_book = _make_book(best_bid=0.52, best_ask=0.54, bid_depth_5=50, ask_depth_5=50)

        opp = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert opp is not None
        assert opp.arb_type == "split_sell"

        mock_client.split_position.return_value = True
        mock_client.place_limit_order.side_effect = [
            {"orderID": "yes-sell-1"},
            {"orderID": "no-sell-1"},
        ]
        mock_client.is_order_filled.side_effect = [
            (True, "MATCHED", 50.0, {}),
            (True, "MATCHED", 50.0, {}),
        ]

        result = await execute_arbitrage(opp, mock_client, inv_mgr, mm_cfg)
        assert result["success"] is True
        assert result["arb_type"] == "split_sell"

    @pytest.mark.asyncio
    async def test_no_opportunity_means_no_execution(self, mock_client, inv_mgr, mm_cfg):
        """When scan returns None, there is nothing to execute."""
        yes_book = _make_book(best_bid=0.48, best_ask=0.52, bid_depth_5=100, ask_depth_5=100)
        no_book = _make_book(best_bid=0.45, best_ask=0.50, bid_depth_5=100, ask_depth_5=100)

        opp = scan_for_arbitrage(
            yes_book, no_book, MARKET_ID, CONDITION_ID, YES_TOKEN, NO_TOKEN,
        )
        assert opp is None
        # Nothing to execute — the bot would skip this market


# ═══════════════════════════════════════════════════════════════════════════════
# ArbOpportunity dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestArbOpportunityDataclass:
    def test_fields_populated(self):
        opp = ArbOpportunity(
            market_id="m1",
            condition_id="c1",
            yes_token_id="yt1",
            no_token_id="nt1",
            arb_type="buy_merge",
            yes_price=0.45,
            no_price=0.48,
            gross_profit_pct=7.53,
            net_profit_pct=7.50,
            max_size=100.0,
        )
        assert opp.market_id == "m1"
        assert opp.arb_type == "buy_merge"
        assert opp.detected_at is not None

    def test_default_detected_at(self):
        opp = ArbOpportunity(
            market_id="m1",
            condition_id="c1",
            yes_token_id="yt1",
            no_token_id="nt1",
            arb_type="split_sell",
            yes_price=0.55,
            no_price=0.52,
            gross_profit_pct=7.0,
            net_profit_pct=6.5,
            max_size=50.0,
        )
        # detected_at should be auto-populated with UTC datetime
        assert isinstance(opp.detected_at, datetime)
        assert opp.detected_at.tzinfo is not None
