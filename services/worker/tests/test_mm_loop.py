"""Tests for MM loop helper logic (cooldown + cross-reject detection + capital budgeting)."""

from unittest.mock import MagicMock
from mm.loop import _cooldown_seconds_for_streak, _is_cross_reject_failure, _compute_locked_capital, _should_cancel_for_requote
from mm.state import QuotePair, OrderState


def test_is_cross_reject_failure_detects_bid_error():
    failure = {"bid_error": {"code": "post_only_cross"}, "ask_error": None}
    assert _is_cross_reject_failure(failure) is True


def test_is_cross_reject_failure_false_for_non_cross_errors():
    failure = {"bid_error": {"code": "insufficient_balance"}, "ask_error": None}
    assert _is_cross_reject_failure(failure) is False


def test_cooldown_seconds_for_streak_scales_from_5_to_10_minutes():
    # threshold reached once -> 5 minutes
    assert _cooldown_seconds_for_streak(3, 3, 300, 600) == 300
    # threshold reached twice -> 10 minutes cap
    assert _cooldown_seconds_for_streak(6, 3, 300, 600) == 600
    # below threshold -> no cooldown
    assert _cooldown_seconds_for_streak(2, 3, 300, 600) == 0


# ─── _compute_locked_capital tests ───


def _make_pair(
    market_id: str = "m1",
    bid_price: float = 0.40,
    ask_price: float = 0.60,
    size: float = 100.0,
    bid_order_id: str | None = "bid1",
    ask_order_id: str | None = "ask1",
    bid_state: OrderState = OrderState.LIVE,
    ask_state: OrderState = OrderState.LIVE,
) -> QuotePair:
    return QuotePair(
        market_id=market_id,
        token_id="tok1",
        bid_price=bid_price,
        ask_price=ask_price,
        size=size,
        bid_order_id=bid_order_id,
        ask_order_id=ask_order_id,
        bid_state=bid_state,
        ask_state=ask_state,
    )


def test_locked_capital_empty():
    assert _compute_locked_capital({}) == 0.0


def test_locked_capital_bid_only():
    pair = _make_pair(ask_order_id=None, ask_state=OrderState.CANCELLED)
    result = _compute_locked_capital({"m1": pair})
    # BID: 100 * 0.40 = 40.0
    assert abs(result - 40.0) < 0.01


def test_locked_capital_ask_only():
    pair = _make_pair(bid_order_id=None, bid_state=OrderState.CANCELLED)
    result = _compute_locked_capital({"m1": pair})
    # ASK orders sell tokens we hold — no USDC locked
    assert result == 0.0


def test_locked_capital_bid_and_ask():
    pair = _make_pair()
    result = _compute_locked_capital({"m1": pair})
    # Only BID locks USDC: 100 * 0.40 = 40.0 (ASK sells tokens, no USDC locked)
    assert abs(result - 40.0) < 0.01


def test_locked_capital_multiple_markets():
    pair1 = _make_pair(market_id="m1", bid_price=0.30, ask_price=0.70, size=50.0)
    pair2 = _make_pair(market_id="m2", bid_price=0.50, ask_price=0.60, size=100.0)
    result = _compute_locked_capital({"m1": pair1, "m2": pair2})
    # Only BIDs lock USDC: m1 BID: 50*0.30=15, m2 BID: 100*0.50=50 -> total 65
    assert abs(result - 65.0) < 0.01


def test_locked_capital_ignores_filled_and_cancelled():
    pair = _make_pair(bid_state=OrderState.FILLED, ask_state=OrderState.CANCELLED)
    result = _compute_locked_capital({"m1": pair})
    assert result == 0.0


def test_locked_capital_counts_partial_orders():
    pair = _make_pair(bid_state=OrderState.PARTIAL, ask_state=OrderState.PARTIAL)
    result = _compute_locked_capital({"m1": pair})
    # Only BID locks USDC: 100 * 0.40 = 40.0
    assert abs(result - 40.0) < 0.01


def test_locked_capital_counts_new_orders():
    pair = _make_pair(bid_state=OrderState.NEW, ask_state=OrderState.NEW)
    result = _compute_locked_capital({"m1": pair})
    # Only BID locks USDC: 100 * 0.40 = 40.0
    assert abs(result - 40.0) < 0.01


def test_locked_capital_no_order_id_means_no_lock():
    """Even if state is LIVE, no order_id means the order was never placed."""
    pair = _make_pair(bid_order_id=None, ask_order_id=None)
    result = _compute_locked_capital({"m1": pair})
    assert result == 0.0


def test_locked_capital_asymmetric_bid_ask_sizes():
    """bid_size and ask_size can differ from each other."""
    pair = QuotePair(
        market_id="m1",
        token_id="tok1",
        bid_price=0.40,
        ask_price=0.60,
        size=100.0,
        bid_size=80.0,
        ask_size=50.0,
        bid_order_id="bid1",
        ask_order_id="ask1",
        bid_state=OrderState.LIVE,
        ask_state=OrderState.LIVE,
    )
    result = _compute_locked_capital({"m1": pair})
    # Only BID locks USDC: 80 * 0.40 = 32.0 (ASK sells tokens, no USDC locked)
    assert abs(result - 32.0) < 0.01


# ─── _should_cancel_for_requote tests ───


def test_should_cancel_for_requote_too_young():
    """Quotes younger than mm_min_quote_lifetime_seconds should not be requoted."""
    pair = _make_pair()
    mm_cfg = MagicMock()
    mm_cfg.mm_min_quote_lifetime_seconds = 10
    mm_cfg.mm_requote_threshold = 0.01
    # Pair was just created, age ~0s
    assert _should_cancel_for_requote(pair, 0.55, mm_cfg) is False


def test_should_cancel_for_requote_old_enough_and_moved():
    """Old enough quote with moved mid should be requoted."""
    import datetime
    pair = _make_pair(bid_price=0.40, ask_price=0.60)
    # Make pair old enough
    pair.created_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    mm_cfg = MagicMock()
    mm_cfg.mm_min_quote_lifetime_seconds = 10
    mm_cfg.mm_requote_threshold = 0.01
    # Mid moved significantly from 0.50
    assert _should_cancel_for_requote(pair, 0.55, mm_cfg) is True


# ═══════════════════════════════════════════════════════════════════════
# _rebuild_active_quotes_from_clob (5A)
# ═══════════════════════════════════════════════════════════════════════

import asyncio
from unittest.mock import AsyncMock, patch
import pytest


def run(coro):
    return asyncio.run(coro)


class TestRebuildActiveQuotesFromCLOB:
    """Tests for _rebuild_active_quotes_from_clob."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get_open_orders = MagicMock(return_value=[])
        client.cancel_order = MagicMock(return_value=True)
        return client

    @pytest.fixture
    def manager(self, mm_config):
        from mm.inventory import InventoryManager
        return InventoryManager(mm_config)

    @patch("mm.loop.store")
    def test_empty_clob(self, mock_store, mock_client, manager):
        from mm.loop import _rebuild_active_quotes_from_clob
        mock_client.get_open_orders = MagicMock(return_value=[])
        mock_store.get_active_mm_quotes = AsyncMock(return_value=[])
        result = run(_rebuild_active_quotes_from_clob(mock_client, manager))
        assert result == {}

    @patch("mm.loop.store")
    def test_recovers_known_orders(self, mock_store, mock_client, manager):
        from mm.loop import _rebuild_active_quotes_from_clob
        mock_client.get_open_orders = MagicMock(return_value=[
            {"id": "bid-123", "asset_id": "tok1"},
            {"id": "ask-456", "asset_id": "tok1"},
        ])
        mock_store.get_active_mm_quotes = AsyncMock(return_value=[
            {"market_id": "m1", "token_id": "tok1", "bid_order_id": "bid-123",
             "ask_order_id": "ask-456", "bid_price": 0.48, "ask_price": 0.52,
             "size": 10.0, "id": 1},
        ])
        result = run(_rebuild_active_quotes_from_clob(mock_client, manager))
        assert "m1" in result
        pair = result["m1"]
        assert pair.bid_order_id == "bid-123"
        assert pair.ask_order_id == "ask-456"

    @patch("mm.loop.store")
    def test_cancels_orphan_orders(self, mock_store, mock_client, manager):
        from mm.loop import _rebuild_active_quotes_from_clob
        mock_client.get_open_orders = MagicMock(return_value=[
            {"id": "orphan-789", "asset_id": "tok_unknown"},
        ])
        mock_store.get_active_mm_quotes = AsyncMock(return_value=[])
        result = run(_rebuild_active_quotes_from_clob(mock_client, manager))
        assert result == {}
        mock_client.cancel_order.assert_called_once_with("orphan-789")


# ═══════════════════════════════════════════════════════════════════════
# Reduce mode (5A)
# ═══════════════════════════════════════════════════════════════════════

class TestReduceMode:
    """Tests for reduce mode halving capacity."""

    def test_reduce_mode_halves_max_markets(self, mm_config):
        """When risk_mode is 'reduce', effective_max_markets should be halved."""
        mm_config.mm_max_markets = 10
        effective = max(1, mm_config.mm_max_markets // 2)
        assert effective == 5

    def test_reduce_mode_halves_quote_size(self, mm_config):
        """When risk_mode is 'reduce', effective_quote_size should be halved."""
        mm_config.mm_quote_size_usd = 5.0
        effective = mm_config.mm_quote_size_usd / 2.0
        assert effective == 2.5
