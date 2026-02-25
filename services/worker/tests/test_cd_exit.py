"""Tests for the CD exit monitor (strategy/cd_exit.py)."""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ====================================================================
# Helpers
# ====================================================================

def _make_cd_position(**overrides) -> dict:
    base = {
        "id": 1,
        "market_id": "cd-market-1",
        "token_id": "cd-token-1",
        "coin": "BTC",
        "strike": 100000.0,
        "direction": "above",
        "entry_price": 0.50,
        "shares": 20.0,
        "order_id": "ord-1",
        "status": "open",
        "exit_price": None,
        "exit_order_id": None,
        "exit_reason": None,
        "pnl_realized": None,
        "created_at": "2026-01-01T00:00:00",
        "closed_at": None,
    }
    base.update(overrides)
    return base


# ====================================================================
# check_cd_exits
# ====================================================================

class TestCheckCdExits:
    """Tests for the check_cd_exits function."""

    async def test_no_open_positions(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        exits = await check_cd_exits(app_config, mock_pm_client)
        assert exits == []

    async def test_stop_loss_triggered(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        # Insert a position with entry_price=0.50
        await test_db.insert_cd_position({
            "market_id": "cd-market-1",
            "token_id": "cd-token-1",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 20.0,
            "order_id": "ord-1",
        })

        # Current price dropped to 0.30 -> loss = (0.50-0.30)*100 = 20pts >= 15pts SL
        mock_pm_client.get_midpoint = MagicMock(return_value=0.30)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.29})
        mock_pm_client.place_limit_order = MagicMock(return_value={"orderID": "exit-ord-1"})

        exits = await check_cd_exits(app_config, mock_pm_client)
        assert len(exits) == 1
        assert exits[0]["exit_reason"] == "stopped"
        assert exits[0]["coin"] == "BTC"

        # Position should be closed in DB
        open_pos = await test_db.get_open_cd_positions()
        assert len(open_pos) == 0

    async def test_take_profit_triggered(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-market-2",
            "token_id": "cd-token-2",
            "coin": "ETH",
            "strike": 5000.0,
            "direction": "above",
            "entry_price": 0.40,
            "shares": 30.0,
            "order_id": "ord-2",
        })

        # Current price rose to 0.65 -> profit = (0.65-0.40)*100 = 25pts >= 20pts TP
        mock_pm_client.get_midpoint = MagicMock(return_value=0.65)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.64})
        mock_pm_client.place_limit_order = MagicMock(return_value={"orderID": "exit-ord-2"})

        exits = await check_cd_exits(app_config, mock_pm_client)
        assert len(exits) == 1
        assert exits[0]["exit_reason"] == "took_profit"
        assert exits[0]["pnl"] > 0

    async def test_no_exit_when_in_range(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-market-3",
            "token_id": "cd-token-3",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 10.0,
            "order_id": "ord-3",
        })

        # Current price at 0.52 -> profit=2pts (< 20pts TP), loss is negative
        mock_pm_client.get_midpoint = MagicMock(return_value=0.52)

        # Mock edge recalculation to return positive edge (no reversal)
        with patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_edge:
            mock_edge.return_value = 5.0  # Positive edge, no reversal
            exits = await check_cd_exits(app_config, mock_pm_client)

        assert exits == []

        # Position should still be open
        open_pos = await test_db.get_open_cd_positions()
        assert len(open_pos) == 1

    async def test_edge_reversal_triggered(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-market-4",
            "token_id": "cd-token-4",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 15.0,
            "order_id": "ord-4",
        })

        # Price within range (no SL/TP)
        mock_pm_client.get_midpoint = MagicMock(return_value=0.48)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.47})
        mock_pm_client.place_limit_order = MagicMock(return_value={"orderID": "exit-ord-4"})

        ai_response = {"confirm_exit": True, "confidence": 0.90, "reason": "fundamental shift"}

        # Mock edge recalculation to return deeply negative edge
        with patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_edge, \
             patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=ai_response):
            mock_edge.return_value = -5.0  # Edge reversed below -3.0 threshold
            exits = await check_cd_exits(app_config, mock_pm_client)

        assert len(exits) == 1
        assert exits[0]["exit_reason"] == "closed"

    async def test_sell_order_failure_skips_exit(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-market-5",
            "token_id": "cd-token-5",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 20.0,
            "order_id": "ord-5",
        })

        # Stop-loss condition met, but SELL order fails
        mock_pm_client.get_midpoint = MagicMock(return_value=0.30)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.29})
        mock_pm_client.place_limit_order = MagicMock(return_value=None)

        exits = await check_cd_exits(app_config, mock_pm_client)
        assert exits == []

        # Position should still be open
        open_pos = await test_db.get_open_cd_positions()
        assert len(open_pos) == 1

    async def test_midpoint_none_skips_position(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-market-6",
            "token_id": "cd-token-6",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 20.0,
            "order_id": "ord-6",
        })

        mock_pm_client.get_midpoint = MagicMock(return_value=None)

        exits = await check_cd_exits(app_config, mock_pm_client)
        assert exits == []


# ====================================================================
# cd_exit_loop
# ====================================================================

class TestCdExitLoop:
    """Tests for the cd_exit_loop coroutine."""

    async def test_loop_disabled(self, app_config, mock_pm_client):
        from strategy.cd_exit import cd_exit_loop

        app_config.cd.cd_exit_enabled = False
        # Should return immediately without looping
        await cd_exit_loop(app_config, mock_pm_client)

    async def test_loop_runs_and_exits(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import cd_exit_loop

        app_config.cd.cd_exit_check_seconds = 0.1  # Fast cycle for testing

        # Run loop briefly then cancel
        task = asyncio.create_task(cd_exit_loop(app_config, mock_pm_client))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ====================================================================
# _recalculate_edge
# ====================================================================

class TestRecalculateEdge:
    """Tests for the edge recalculation helper."""

    async def test_unknown_coin_returns_none(self):
        from strategy.cd_exit import _recalculate_edge
        from config import CryptoDirectionalConfig

        cd_cfg = CryptoDirectionalConfig()
        result = await _recalculate_edge("DOGE", 1.0, "above", cd_cfg)
        assert result is None

    async def test_spot_price_failure_returns_none(self):
        from strategy.cd_exit import _recalculate_edge
        from config import CryptoDirectionalConfig

        cd_cfg = CryptoDirectionalConfig()

        with patch("strategy.cd_exit.get_spot_price", return_value=None):
            result = await _recalculate_edge("BTC", 100000.0, "above", cd_cfg)
        assert result is None

    async def test_valid_edge_recalculation(self):
        from strategy.cd_exit import _recalculate_edge
        from config import CryptoDirectionalConfig

        cd_cfg = CryptoDirectionalConfig()

        with patch("strategy.cd_exit.get_spot_price", return_value=100000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[95000 + i * 200 for i in range(30)]), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.65):
            result = await _recalculate_edge("BTC", 100000.0, "above", cd_cfg)

        assert result is not None
        # Edge = (0.65 - 0.5) * 100 = 15.0
        assert result == pytest.approx(15.0, abs=0.1)


# ====================================================================
# AI-confirmed exits (Feature 2)
# ====================================================================

class TestAIConfirmedExits:
    """Tests for AI confirmation of edge-reversal exits."""

    async def test_ai_confirms_exit_proceeds(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-ai-1",
            "token_id": "cd-ai-token-1",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 15.0,
            "order_id": "ord-ai-1",
        })

        mock_pm_client.get_midpoint = MagicMock(return_value=0.48)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.47})
        mock_pm_client.place_limit_order = MagicMock(return_value={"orderID": "exit-ai-1"})

        ai_response = {"confirm_exit": True, "confidence": 0.85, "reason": "fundamental shift"}

        with patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_edge, \
             patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=ai_response):
            mock_edge.return_value = -5.0
            exits = await check_cd_exits(app_config, mock_pm_client)

        assert len(exits) == 1
        assert exits[0]["exit_reason"] == "closed"

    async def test_ai_says_noise_skips_exit(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-ai-2",
            "token_id": "cd-ai-token-2",
            "coin": "ETH",
            "strike": 5000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 10.0,
            "order_id": "ord-ai-2",
        })

        mock_pm_client.get_midpoint = MagicMock(return_value=0.48)

        ai_response = {"confirm_exit": False, "confidence": 0.70, "reason": "temporary noise"}

        with patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_edge, \
             patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=ai_response):
            mock_edge.return_value = -5.0
            exits = await check_cd_exits(app_config, mock_pm_client)

        assert exits == []

        # Position should still be open
        open_pos = await test_db.get_open_cd_positions()
        assert len(open_pos) == 1

    async def test_ai_failure_defaults_to_exit(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        await test_db.insert_cd_position({
            "market_id": "cd-ai-3",
            "token_id": "cd-ai-token-3",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 15.0,
            "order_id": "ord-ai-3",
        })

        mock_pm_client.get_midpoint = MagicMock(return_value=0.48)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.47})
        mock_pm_client.place_limit_order = MagicMock(return_value={"orderID": "exit-ai-3"})

        with patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_edge, \
             patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, side_effect=Exception("API down")):
            mock_edge.return_value = -5.0
            exits = await check_cd_exits(app_config, mock_pm_client)

        # Fail-safe: should still exit
        assert len(exits) == 1

    async def test_ai_confirm_disabled_proceeds_directly(self, app_config, mock_pm_client, test_db):
        from strategy.cd_exit import check_cd_exits

        app_config.cd.cd_exit_ai_confirm_enabled = False

        await test_db.insert_cd_position({
            "market_id": "cd-ai-4",
            "token_id": "cd-ai-token-4",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 15.0,
            "order_id": "ord-ai-4",
        })

        mock_pm_client.get_midpoint = MagicMock(return_value=0.48)
        mock_pm_client.get_book_summary = MagicMock(return_value={"best_bid": 0.47})
        mock_pm_client.place_limit_order = MagicMock(return_value={"orderID": "exit-ai-4"})

        with patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_edge:
            mock_edge.return_value = -5.0
            # No AI mock needed — should proceed directly
            exits = await check_cd_exits(app_config, mock_pm_client)

        assert len(exits) == 1
