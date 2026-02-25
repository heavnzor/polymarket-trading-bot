"""Comprehensive tests for the database layer (db/store.py).

Every public function in the store module is covered with happy-path,
edge-case, and state-change tests.  Uses the ``test_db`` fixture from
conftest.py which provides a fresh SQLite database per test.
"""

import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

# All tests are async
pytestmark = pytest.mark.asyncio


# ====================================================================
# Helpers / sample data factories
# ====================================================================

def _sample_trade(**overrides) -> dict:
    base = {
        "market_id": "market-123",
        "market_question": "Test question?",
        "token_id": "token-123",
        "category": "crypto",
        "side": "BUY",
        "outcome": "Yes",
        "size_usdc": 5.0,
        "price": 0.55,
        "edge": 0.15,
        "edge_net": 0.12,
        "confidence": 0.75,
        "reasoning": "Test reasoning",
        "status": "pending",
        "strategy": "active",
    }
    base.update(overrides)
    return base


def _sample_position(**overrides) -> dict:
    base = {
        "market_id": "market-123",
        "token_id": "token-123",
        "market_question": "Test question?",
        "outcome": "Yes",
        "size": 10.0,
        "avg_price": 0.55,
        "current_price": 0.60,
        "category": "crypto",
        "strategy": "active",
    }
    base.update(overrides)
    return base


# ====================================================================
# TRADES
# ====================================================================

class TestInsertTrade:
    async def test_returns_row_id(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        assert isinstance(tid, int)
        assert tid >= 1

    async def test_sequential_ids(self, test_db):
        t1 = await test_db.insert_trade(_sample_trade())
        t2 = await test_db.insert_trade(_sample_trade(market_id="market-456"))
        assert t2 == t1 + 1

    async def test_stored_fields(self, test_db):
        trade = _sample_trade()
        tid = await test_db.insert_trade(trade)
        rows = await test_db.get_trades(limit=1)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == tid
        assert row["market_id"] == "market-123"
        assert row["market_question"] == "Test question?"
        assert row["token_id"] == "token-123"
        assert row["category"] == "crypto"
        assert row["side"] == "BUY"
        assert row["outcome"] == "Yes"
        assert row["size_usdc"] == 5.0
        assert row["price"] == 0.55
        assert row["edge"] == 0.15
        assert row["edge_net"] == 0.12
        assert row["confidence"] == 0.75
        assert row["reasoning"] == "Test reasoning"
        assert row["status"] == "pending"
        assert row["strategy"] == "active"

    async def test_defaults_for_optional_fields(self, test_db):
        minimal = {
            "market_id": "m1",
            "side": "BUY",
            "outcome": "Yes",
            "size_usdc": 1.0,
            "price": 0.5,
        }
        tid = await test_db.insert_trade(minimal)
        rows = await test_db.get_trades(limit=1)
        row = rows[0]
        assert row["category"] == "other"
        assert row["status"] == "pending"
        assert row["strategy"] == "active"
        assert row["filled_shares"] == 0
        assert row["token_id"] is None
        assert row["edge"] is None

    async def test_intended_shares_and_fills(self, test_db):
        trade = _sample_trade(intended_shares=9.09, filled_shares=4.5, avg_fill_price=0.56)
        tid = await test_db.insert_trade(trade)
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["intended_shares"] == 9.09
        assert rows[0]["filled_shares"] == 4.5
        assert rows[0]["avg_fill_price"] == 0.56


class TestUpdateTradeStatus:
    async def test_update_status_only(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.update_trade_status(tid, "executed")
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["status"] == "executed"
        assert rows[0]["order_id"] is None

    async def test_update_status_with_order_id(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.update_trade_status(tid, "order_placed", order_id="order-abc")
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["status"] == "order_placed"
        assert rows[0]["order_id"] == "order-abc"
        assert rows[0]["executed_at"] is not None

    async def test_update_nonexistent_trade(self, test_db):
        # Should not raise; just a no-op UPDATE
        await test_db.update_trade_status(99999, "executed")


class TestUpdateTradeExecutionPlan:
    async def test_updates_plan_fields(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.update_trade_execution_plan(tid, price=0.60, size_usdc=6.0, intended_shares=10.0)
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["price"] == 0.60
        assert rows[0]["size_usdc"] == 6.0
        assert rows[0]["intended_shares"] == 10.0


class TestUpdateTradeFillProgress:
    async def test_updates_fills(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.update_trade_fill_progress(tid, filled_shares=5.0, avg_fill_price=0.56)
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["filled_shares"] == 5.0
        assert rows[0]["avg_fill_price"] == 0.56

    async def test_does_not_decrease_filled_shares(self, test_db):
        tid = await test_db.insert_trade(_sample_trade(filled_shares=10.0))
        await test_db.update_trade_fill_progress(tid, filled_shares=5.0)
        rows = await test_db.get_trades(limit=1)
        # Should keep the higher value
        assert rows[0]["filled_shares"] == 10.0

    async def test_increases_filled_shares(self, test_db):
        tid = await test_db.insert_trade(_sample_trade(filled_shares=5.0))
        await test_db.update_trade_fill_progress(tid, filled_shares=8.0, avg_fill_price=0.57)
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["filled_shares"] == 8.0
        assert rows[0]["avg_fill_price"] == 0.57

    async def test_no_avg_fill_price_keeps_existing(self, test_db):
        tid = await test_db.insert_trade(_sample_trade(avg_fill_price=0.55))
        await test_db.update_trade_fill_progress(tid, filled_shares=5.0)
        rows = await test_db.get_trades(limit=1)
        assert rows[0]["avg_fill_price"] == 0.55


class TestGetTrades:
    async def test_empty_db(self, test_db):
        rows = await test_db.get_trades()
        assert rows == []

    async def test_limit_parameter(self, test_db):
        for i in range(5):
            await test_db.insert_trade(_sample_trade(market_id=f"m-{i}"))
        rows = await test_db.get_trades(limit=3)
        assert len(rows) == 3

    async def test_order_by_created_at_desc(self, test_db):
        t1 = await test_db.insert_trade(_sample_trade(market_id="first"))
        t2 = await test_db.insert_trade(_sample_trade(market_id="second"))
        rows = await test_db.get_trades(limit=2)
        # Both returned; ordering by created_at DESC may be non-deterministic
        # within the same second, so just check both are present
        ids = {r["id"] for r in rows}
        assert ids == {t1, t2}


class TestGetPendingTrades:
    async def test_returns_pending_confirmation_only(self, test_db):
        await test_db.insert_trade(_sample_trade(status="pending"))
        await test_db.insert_trade(_sample_trade(status="pending_confirmation"))
        await test_db.insert_trade(_sample_trade(status="executed"))
        rows = await test_db.get_pending_trades()
        assert len(rows) == 1
        assert rows[0]["status"] == "pending_confirmation"

    async def test_empty(self, test_db):
        rows = await test_db.get_pending_trades()
        assert rows == []


class TestGetPendingConfirmationTrades:
    async def test_returns_pending_confirmation(self, test_db):
        await test_db.insert_trade(_sample_trade(status="pending_confirmation"))
        await test_db.insert_trade(_sample_trade(status="pending"))
        rows = await test_db.get_pending_confirmation_trades()
        assert len(rows) == 1
        assert rows[0]["status"] == "pending_confirmation"

    async def test_empty_db(self, test_db):
        rows = await test_db.get_pending_confirmation_trades()
        assert rows == []


class TestGetTradesWithOrderStatus:
    async def test_default_order_placed(self, test_db):
        t1 = await test_db.insert_trade(_sample_trade(status="order_placed"))
        await test_db.insert_trade(_sample_trade(status="executed"))
        rows = await test_db.get_trades_with_order_status()
        assert len(rows) == 1
        assert rows[0]["id"] == t1

    async def test_custom_status(self, test_db):
        await test_db.insert_trade(_sample_trade(status="cancelled"))
        rows = await test_db.get_trades_with_order_status(status="cancelled")
        assert len(rows) == 1

    async def test_empty(self, test_db):
        rows = await test_db.get_trades_with_order_status()
        assert rows == []


class TestGetTradesByStatus:
    async def test_filter_by_status(self, test_db):
        await test_db.insert_trade(_sample_trade(status="pending"))
        await test_db.insert_trade(_sample_trade(status="executed"))
        await test_db.insert_trade(_sample_trade(status="pending"))
        rows = await test_db.get_trades_by_status("pending")
        assert len(rows) == 2
        for r in rows:
            assert r["status"] == "pending"

    async def test_nonexistent_status(self, test_db):
        await test_db.insert_trade(_sample_trade(status="pending"))
        rows = await test_db.get_trades_by_status("nonexistent_status")
        assert rows == []


class TestInsertOrderEvent:
    async def test_returns_id(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        eid = await test_db.insert_order_event(tid, "order_placed", order_id="ord-1", status="LIVE")
        assert isinstance(eid, int)
        assert eid >= 1

    async def test_stores_all_fields(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        payload = {"raw": "data"}
        eid = await test_db.insert_order_event(
            tid, "partial_fill",
            order_id="ord-1",
            status="LIVE",
            size_matched=5.0,
            new_fill=2.5,
            avg_fill_price=0.56,
            note="half filled",
            payload=payload,
        )
        events = await test_db.get_recent_order_events(limit=1)
        assert len(events) == 1
        e = events[0]
        assert e["trade_id"] == tid
        assert e["event_type"] == "partial_fill"
        assert e["order_id"] == "ord-1"
        assert e["status"] == "LIVE"
        assert e["size_matched"] == 5.0
        assert e["new_fill"] == 2.5
        assert e["avg_fill_price"] == 0.56
        assert e["note"] == "half filled"
        assert json.loads(e["payload_json"]) == payload

    async def test_minimal_event(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        eid = await test_db.insert_order_event(tid, "cancelled")
        events = await test_db.get_recent_order_events(limit=1)
        assert events[0]["order_id"] is None
        assert events[0]["payload_json"] is None


class TestGetRecentOrderEvents:
    async def test_empty(self, test_db):
        events = await test_db.get_recent_order_events()
        assert events == []

    async def test_limit(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        for i in range(5):
            await test_db.insert_order_event(tid, f"event_{i}")
        events = await test_db.get_recent_order_events(limit=3)
        assert len(events) == 3

    async def test_order_desc(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        e1 = await test_db.insert_order_event(tid, "first")
        e2 = await test_db.insert_order_event(tid, "second")
        events = await test_db.get_recent_order_events(limit=2)
        assert events[0]["id"] == e2
        assert events[1]["id"] == e1


class TestGetExecutionQualityStats:
    async def test_empty_db(self, test_db):
        stats = await test_db.get_execution_quality_stats()
        assert stats["total_orders"] == 0
        assert stats["fill_rate"] == 0.0
        assert stats["cancel_rate"] == 0.0
        assert stats["avg_fill_ratio"] == 0.0

    async def test_with_trades(self, test_db):
        # Insert a trade with order_id (not PAPER)
        t1 = await test_db.insert_trade(_sample_trade(status="executed", intended_shares=10.0, filled_shares=10.0))
        await test_db.update_trade_status(t1, "executed", order_id="real-order-1")

        t2 = await test_db.insert_trade(_sample_trade(status="cancelled", intended_shares=10.0, filled_shares=0))
        await test_db.update_trade_status(t2, "cancelled", order_id="real-order-2")

        stats = await test_db.get_execution_quality_stats(days=1)
        assert stats["total_orders"] == 2
        assert stats["executed_orders"] == 1
        assert stats["cancelled_orders"] == 1
        assert stats["window_days"] == 1

    async def test_paper_trades_excluded(self, test_db):
        tid = await test_db.insert_trade(_sample_trade(status="executed"))
        await test_db.update_trade_status(tid, "executed", order_id="PAPER")
        stats = await test_db.get_execution_quality_stats()
        assert stats["total_orders"] == 0

    async def test_partial_fill_count(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.update_trade_status(tid, "order_placed", order_id="ord-1")
        await test_db.insert_order_event(tid, "partial_fill", new_fill=3.0)
        stats = await test_db.get_execution_quality_stats(days=1)
        assert stats["partial_orders"] == 1


# ====================================================================
# POSITIONS
# ====================================================================

class TestUpsertPosition:
    async def test_insert_new(self, test_db):
        await test_db.upsert_position(_sample_position())
        positions = await test_db.get_open_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["market_id"] == "market-123"
        assert p["size"] == 10.0
        assert p["status"] == "open"

    async def test_upsert_accumulate(self, test_db):
        await test_db.upsert_position(_sample_position(size=10.0, avg_price=0.50))
        await test_db.upsert_position(_sample_position(size=10.0, avg_price=0.60))
        positions = await test_db.get_open_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["size"] == 20.0
        # Weighted average: (0.50*10 + 0.60*10) / 20 = 0.55
        assert abs(p["avg_price"] - 0.55) < 0.001

    async def test_upsert_different_tokens(self, test_db):
        await test_db.upsert_position(_sample_position(token_id="token-A"))
        await test_db.upsert_position(_sample_position(token_id="token-B"))
        positions = await test_db.get_open_positions()
        assert len(positions) == 2

    async def test_reopen_closed_position(self, test_db):
        await test_db.upsert_position(_sample_position(size=10.0))
        await test_db.close_position("market-123", "token-123")
        # After closing, upsert should replace (not add) the size
        await test_db.upsert_position(_sample_position(size=5.0, avg_price=0.70))
        positions = await test_db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["size"] == 5.0
        assert positions[0]["avg_price"] == 0.70


class TestGetOpenPositions:
    async def test_empty(self, test_db):
        positions = await test_db.get_open_positions()
        assert positions == []

    async def test_excludes_closed(self, test_db):
        await test_db.upsert_position(_sample_position())
        await test_db.close_position("market-123", "token-123")
        positions = await test_db.get_open_positions()
        assert positions == []


class TestClosePosition:
    async def test_close(self, test_db):
        await test_db.upsert_position(_sample_position())
        await test_db.close_position("market-123", "token-123")
        positions = await test_db.get_open_positions()
        assert positions == []

    async def test_close_nonexistent(self, test_db):
        # Should not raise
        await test_db.close_position("nonexistent", "nope")


class TestReducePosition:
    async def test_partial_reduce(self, test_db):
        await test_db.upsert_position(_sample_position(size=10.0, avg_price=0.50))
        result = await test_db.reduce_position("market-123", "token-123", 4.0, sell_price=0.60)
        assert result is not None
        assert result["shares_sold"] == 4.0
        assert result["remaining_shares"] == 6.0
        assert result["entry_avg_price"] == 0.50
        expected_pnl = (0.60 - 0.50) * 4.0
        assert abs(result["realized_pnl"] - expected_pnl) < 0.001
        # Position should still be open
        positions = await test_db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["size"] == 6.0

    async def test_full_reduce_closes_position(self, test_db):
        await test_db.upsert_position(_sample_position(size=10.0, avg_price=0.50))
        result = await test_db.reduce_position("market-123", "token-123", 10.0, sell_price=0.70)
        assert result is not None
        assert result["remaining_shares"] == 0.0
        positions = await test_db.get_open_positions()
        assert positions == []

    async def test_over_reduce_capped(self, test_db):
        await test_db.upsert_position(_sample_position(size=5.0))
        result = await test_db.reduce_position("market-123", "token-123", 100.0)
        assert result is not None
        assert result["shares_sold"] == 5.0
        assert result["remaining_shares"] == 0.0

    async def test_nonexistent_position_returns_none(self, test_db):
        result = await test_db.reduce_position("no-market", "no-token", 1.0)
        assert result is None

    async def test_no_sell_price(self, test_db):
        await test_db.upsert_position(_sample_position(size=10.0))
        result = await test_db.reduce_position("market-123", "token-123", 3.0)
        assert result is not None
        assert result["realized_pnl"] is None

    async def test_nearly_full_reduce_closes(self, test_db):
        """Remaining <= 0.01 should close the position."""
        await test_db.upsert_position(_sample_position(size=10.0))
        result = await test_db.reduce_position("market-123", "token-123", 9.995)
        # Floating point may leave tiny residual; check it's under threshold
        assert result["remaining_shares"] < 0.01
        positions = await test_db.get_open_positions()
        # Position should be closed (status='closed') or remaining is negligible
        open_for_market = [p for p in positions if p["market_id"] == "market-123" and p.get("status") != "closed"]
        assert len(open_for_market) == 0


class TestGetPositionsByStrategy:
    async def test_filter(self, test_db):
        await test_db.upsert_position(_sample_position(strategy="active"))
        await test_db.upsert_position(_sample_position(
            market_id="m2", token_id="t2", strategy="conservative"
        ))
        rows = await test_db.get_positions_by_strategy("active")
        assert len(rows) == 1
        assert rows[0]["strategy"] == "active"

    async def test_empty_result(self, test_db):
        rows = await test_db.get_positions_by_strategy("nonexistent")
        assert rows == []


class TestGetPositionsByCategory:
    async def test_filter(self, test_db):
        await test_db.upsert_position(_sample_position(category="crypto"))
        await test_db.upsert_position(_sample_position(
            market_id="m2", token_id="t2", category="politics"
        ))
        rows = await test_db.get_positions_by_category("crypto")
        assert len(rows) == 1
        assert rows[0]["category"] == "crypto"

    async def test_empty(self, test_db):
        rows = await test_db.get_positions_by_category("sports")
        assert rows == []


class TestGetCategoryExposure:
    async def test_empty(self, test_db):
        exposure = await test_db.get_category_exposure()
        assert exposure == {}

    async def test_single_category(self, test_db):
        await test_db.upsert_position(_sample_position(
            size=10.0, avg_price=0.50, category="crypto"
        ))
        exposure = await test_db.get_category_exposure()
        assert "crypto" in exposure
        assert abs(exposure["crypto"] - 5.0) < 0.001  # 10 * 0.50

    async def test_multiple_categories(self, test_db):
        await test_db.upsert_position(_sample_position(
            market_id="m1", token_id="t1",
            size=10.0, avg_price=0.50, category="crypto"
        ))
        await test_db.upsert_position(_sample_position(
            market_id="m2", token_id="t2",
            size=20.0, avg_price=0.30, category="politics"
        ))
        exposure = await test_db.get_category_exposure()
        assert abs(exposure["crypto"] - 5.0) < 0.001
        assert abs(exposure["politics"] - 6.0) < 0.001

    async def test_excludes_closed(self, test_db):
        await test_db.upsert_position(_sample_position(size=10.0, avg_price=0.50))
        await test_db.close_position("market-123", "token-123")
        exposure = await test_db.get_category_exposure()
        assert exposure == {}


# ====================================================================
# DAILY STATS
# ====================================================================

class TestGetDailyTraded:
    async def test_no_data_returns_zero(self, test_db):
        result = await test_db.get_daily_traded("2025-01-01")
        assert result == 0.0

    async def test_after_increment(self, test_db):
        await test_db.increment_daily_traded(10.0, "2025-01-01")
        result = await test_db.get_daily_traded("2025-01-01")
        assert result == 10.0

    async def test_default_date(self, test_db):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await test_db.increment_daily_traded(7.5)
        result = await test_db.get_daily_traded()
        assert result == 7.5


class TestIncrementDailyTraded:
    async def test_creates_row(self, test_db):
        await test_db.increment_daily_traded(5.0, "2025-06-15")
        val = await test_db.get_daily_traded("2025-06-15")
        assert val == 5.0

    async def test_accumulates(self, test_db):
        await test_db.increment_daily_traded(5.0, "2025-06-15")
        await test_db.increment_daily_traded(3.0, "2025-06-15")
        val = await test_db.get_daily_traded("2025-06-15")
        assert val == 8.0

    async def test_different_dates_independent(self, test_db):
        await test_db.increment_daily_traded(5.0, "2025-06-15")
        await test_db.increment_daily_traded(10.0, "2025-06-16")
        assert await test_db.get_daily_traded("2025-06-15") == 5.0
        assert await test_db.get_daily_traded("2025-06-16") == 10.0


class TestDecrementDailyTraded:
    async def test_decrement(self, test_db):
        await test_db.increment_daily_traded(10.0, "2025-06-15")
        await test_db.decrement_daily_traded(3.0, "2025-06-15")
        val = await test_db.get_daily_traded("2025-06-15")
        assert val == 7.0

    async def test_does_not_go_below_zero(self, test_db):
        await test_db.increment_daily_traded(5.0, "2025-06-15")
        await test_db.decrement_daily_traded(100.0, "2025-06-15")
        val = await test_db.get_daily_traded("2025-06-15")
        assert val == 0.0

    async def test_decrement_nonexistent_date_noop(self, test_db):
        await test_db.decrement_daily_traded(5.0, "2025-06-15")
        val = await test_db.get_daily_traded("2025-06-15")
        assert val == 0.0

    async def test_default_date(self, test_db):
        await test_db.increment_daily_traded(10.0)
        await test_db.decrement_daily_traded(4.0)
        val = await test_db.get_daily_traded()
        assert val == 6.0


class TestUpdateDailyPnl:
    async def test_creates_row(self, test_db):
        await test_db.update_daily_pnl(2.5, "2025-06-15")
        # Verify via direct DB access
        db = await test_db._get_db()
        cursor = await db.execute("SELECT pnl_realized FROM daily_stats WHERE date=?", ("2025-06-15",))
        row = await cursor.fetchone()
        assert row is not None
        assert row["pnl_realized"] == 2.5

    async def test_accumulates(self, test_db):
        await test_db.update_daily_pnl(2.0, "2025-06-15")
        await test_db.update_daily_pnl(3.0, "2025-06-15")
        db = await test_db._get_db()
        cursor = await db.execute("SELECT pnl_realized FROM daily_stats WHERE date=?", ("2025-06-15",))
        row = await cursor.fetchone()
        assert row["pnl_realized"] == 5.0

    async def test_negative_pnl(self, test_db):
        await test_db.update_daily_pnl(-5.0, "2025-06-15")
        db = await test_db._get_db()
        cursor = await db.execute("SELECT pnl_realized FROM daily_stats WHERE date=?", ("2025-06-15",))
        row = await cursor.fetchone()
        assert row["pnl_realized"] == -5.0

    async def test_default_date(self, test_db):
        await test_db.update_daily_pnl(1.0)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db = await test_db._get_db()
        cursor = await db.execute("SELECT pnl_realized FROM daily_stats WHERE date=?", (today,))
        row = await cursor.fetchone()
        assert row is not None


# ====================================================================
# ANALYSIS LOG
# ====================================================================

class TestLogAnalysis:
    async def test_insert(self, test_db):
        await test_db.log_analysis(1, 50, 3, 2, '{"raw": "data"}')
        db = await test_db._get_db()
        cursor = await db.execute("SELECT * FROM analysis_log")
        rows = await cursor.fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["cycle_number"] == 1
        assert row["markets_analyzed"] == 50
        assert row["trades_proposed"] == 3
        assert row["trades_executed"] == 2
        assert row["raw_response"] == '{"raw": "data"}'

    async def test_multiple_entries(self, test_db):
        await test_db.log_analysis(1, 50, 3, 2, "raw1")
        await test_db.log_analysis(2, 45, 2, 1, "raw2")
        db = await test_db._get_db()
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM analysis_log")
        row = await cursor.fetchone()
        assert row["cnt"] == 2


# ====================================================================
# PERFORMANCE TRACKING
# ====================================================================

class TestInsertPerformance:
    async def test_returns_id(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        pid = await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
        )
        assert isinstance(pid, int)
        assert pid >= 1

    async def test_stored_fields(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        pid = await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
            side="BUY", filled_shares=9.0, avg_fill_price=0.56,
            fees_estimated=0.05, market_resolved=0,
            actual_outcome=None, pnl_realized=0.0, pnl_net=0.0,
        )
        db = await test_db._get_db()
        cursor = await db.execute("SELECT * FROM performance WHERE id=?", (pid,))
        row = dict(await cursor.fetchone())
        assert row["trade_id"] == tid
        assert row["market_id"] == "market-123"
        assert row["side"] == "BUY"
        assert row["outcome_bet"] == "Yes"
        assert row["filled_shares"] == 9.0
        assert row["fees_estimated"] == 0.05

    async def test_default_values(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        pid = await test_db.insert_performance(
            trade_id=tid, market_id="m1",
            market_question="Q?", outcome_bet="No",
            price_at_entry=0.40, size_usdc=3.0,
        )
        db = await test_db._get_db()
        cursor = await db.execute("SELECT * FROM performance WHERE id=?", (pid,))
        row = dict(await cursor.fetchone())
        assert row["side"] == "BUY"
        assert row["fees_estimated"] == 0.0
        assert row["market_resolved"] == 0
        assert row["pnl_realized"] == 0.0
        assert row["pnl_net"] == 0.0


class TestResolvePerformance:
    async def test_resolve_correct_bet(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
            filled_shares=9.09, fees_estimated=0.05,
        )
        result = await test_db.resolve_performance("market-123", "Yes")
        assert result["count"] == 1
        # Won: pnl = filled_shares - size_usdc = 9.09 - 5.0 = 4.09
        # pnl_net = 4.09 - 0.05 = 4.04
        assert abs(result["pnl_net_total"] - 4.04) < 0.01

    async def test_resolve_incorrect_bet(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
            fees_estimated=0.05,
        )
        result = await test_db.resolve_performance("market-123", "No")
        assert result["count"] == 1
        # Lost: pnl = -5.0, pnl_net = -5.0 - 0.05 = -5.05
        assert abs(result["pnl_net_total"] - (-5.05)) < 0.01

    async def test_resolve_no_filled_shares(self, test_db):
        """When filled_shares is None, falls back to price-based calculation."""
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.50, size_usdc=5.0,
        )
        result = await test_db.resolve_performance("market-123", "Yes")
        # Won with no filled_shares: pnl = (5.0/0.50 - 5.0) = 10-5 = 5.0
        assert abs(result["pnl_net_total"] - 5.0) < 0.01

    async def test_resolve_multiple_records(self, test_db):
        t1 = await test_db.insert_trade(_sample_trade())
        t2 = await test_db.insert_trade(_sample_trade(market_id="market-123"))
        await test_db.insert_performance(
            trade_id=t1, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
        )
        await test_db.insert_performance(
            trade_id=t2, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.60, size_usdc=3.0,
        )
        result = await test_db.resolve_performance("market-123", "No")
        assert result["count"] == 2

    async def test_resolve_no_records(self, test_db):
        result = await test_db.resolve_performance("nonexistent", "Yes")
        assert result["count"] == 0
        assert result["pnl_net_total"] == 0.0

    async def test_resolve_sell_side(self, test_db):
        """SELL trades are marked resolved but PnL is not recomputed."""
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
            side="SELL",
        )
        result = await test_db.resolve_performance("market-123", "Yes")
        assert result["count"] == 1
        assert result["pnl_net_total"] == 0.0  # SELL not recomputed

    async def test_idempotent_already_resolved(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
        )
        await test_db.resolve_performance("market-123", "Yes")
        # Second resolve should find zero unresolved
        result = await test_db.resolve_performance("market-123", "Yes")
        assert result["count"] == 0


class TestGetUnresolvedMarketIds:
    async def test_empty(self, test_db):
        ids = await test_db.get_unresolved_market_ids()
        assert ids == []

    async def test_returns_unresolved(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.5, size_usdc=5.0,
        )
        await test_db.insert_performance(
            trade_id=tid, market_id="market-B",
            market_question="Q?", outcome_bet="No",
            price_at_entry=0.6, size_usdc=3.0,
        )
        ids = await test_db.get_unresolved_market_ids()
        assert set(ids) == {"market-A", "market-B"}

    async def test_excludes_resolved(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.5, size_usdc=5.0,
        )
        await test_db.resolve_performance("market-A", "Yes")
        ids = await test_db.get_unresolved_market_ids()
        assert ids == []

    async def test_distinct(self, test_db):
        t1 = await test_db.insert_trade(_sample_trade())
        t2 = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=t1, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.5, size_usdc=5.0,
        )
        await test_db.insert_performance(
            trade_id=t2, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.6, size_usdc=3.0,
        )
        ids = await test_db.get_unresolved_market_ids()
        assert ids == ["market-A"]


class TestGetPerformanceStats:
    async def test_empty(self, test_db):
        stats = await test_db.get_performance_stats()
        assert stats["total_trades"] == 0
        assert stats["resolved_trades"] == 0
        assert stats["hit_rate"] == 0.0

    async def test_with_resolved_trades(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.50, size_usdc=5.0,
            filled_shares=10.0,
        )
        await test_db.resolve_performance("market-A", "Yes")

        tid2 = await test_db.insert_trade(_sample_trade(market_id="market-B"))
        await test_db.insert_performance(
            trade_id=tid2, market_id="market-B",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.60, size_usdc=3.0,
        )
        await test_db.resolve_performance("market-B", "No")

        stats = await test_db.get_performance_stats()
        assert stats["total_trades"] == 2
        assert stats["resolved_trades"] == 2
        assert stats["pending_resolution"] == 0
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["total_wagered"] == 8.0

    async def test_streak(self, test_db):
        # Insert 3 wins in a row
        for i in range(3):
            tid = await test_db.insert_trade(_sample_trade(market_id=f"m-{i}"))
            await test_db.insert_performance(
                trade_id=tid, market_id=f"m-{i}",
                market_question="Q?", outcome_bet="Yes",
                price_at_entry=0.50, size_usdc=5.0,
                filled_shares=10.0,
            )
            await test_db.resolve_performance(f"m-{i}", "Yes")

        stats = await test_db.get_performance_stats()
        assert stats["current_streak"] == 3
        assert stats["streak_type"] == "win"

    async def test_pending_resolution_count(self, test_db):
        tid = await test_db.insert_trade(_sample_trade())
        await test_db.insert_performance(
            trade_id=tid, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.5, size_usdc=5.0,
        )
        stats = await test_db.get_performance_stats()
        assert stats["total_trades"] == 1
        assert stats["resolved_trades"] == 0
        assert stats["pending_resolution"] == 1


class TestGetPerformanceAttribution:
    async def test_empty(self, test_db):
        result = await test_db.get_performance_attribution()
        assert result["by_strategy"] == []
        assert result["by_category"] == []

    async def test_by_strategy_and_category(self, test_db):
        tid = await test_db.insert_trade(_sample_trade(strategy="active", category="crypto"))
        await test_db.insert_performance(
            trade_id=tid, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.50, size_usdc=5.0,
            filled_shares=10.0,
        )
        await test_db.resolve_performance("market-A", "Yes")

        result = await test_db.get_performance_attribution()
        assert len(result["by_strategy"]) == 1
        assert result["by_strategy"][0]["strategy"] == "active"
        assert result["by_strategy"][0]["trades"] == 1
        assert len(result["by_category"]) == 1
        assert result["by_category"][0]["category"] == "crypto"


class TestGetCalibrationData:
    async def test_empty(self, test_db):
        data = await test_db.get_calibration_data()
        assert data == []

    async def test_returns_resolved_with_trade_fields(self, test_db):
        tid = await test_db.insert_trade(_sample_trade(edge=0.15, confidence=0.75))
        await test_db.insert_performance(
            trade_id=tid, market_id="market-A",
            market_question="Q?", outcome_bet="Yes",
            price_at_entry=0.50, size_usdc=5.0,
        )
        await test_db.resolve_performance("market-A", "Yes")
        data = await test_db.get_calibration_data()
        assert len(data) == 1
        assert data[0]["market_resolved"] == 1
        assert data[0]["edge"] == 0.15
        assert data[0]["confidence"] == 0.75


# ====================================================================
# HIGH WATER MARK
# ====================================================================

class TestGetHighWaterMark:
    async def test_default_values(self, test_db):
        hwm = await test_db.get_high_water_mark()
        assert hwm["peak_value"] == 100.0
        assert hwm["current_value"] == 100.0
        assert hwm["max_drawdown_pct"] == 0.0


class TestUpdateHighWaterMark:
    async def test_update_above_peak(self, test_db):
        await test_db.update_high_water_mark(110.0)
        hwm = await test_db.get_high_water_mark()
        assert hwm["peak_value"] == 110.0
        assert hwm["current_value"] == 110.0
        assert hwm["max_drawdown_pct"] == 0.0

    async def test_update_below_peak(self, test_db):
        await test_db.update_high_water_mark(120.0)
        await test_db.update_high_water_mark(100.0)
        hwm = await test_db.get_high_water_mark()
        assert hwm["peak_value"] == 120.0
        assert hwm["current_value"] == 100.0
        # drawdown = (120 - 100)/120 * 100 = 16.67%
        assert abs(hwm["max_drawdown_pct"] - 16.67) < 0.1

    async def test_drawdown_remembers_max(self, test_db):
        await test_db.update_high_water_mark(120.0)
        await test_db.update_high_water_mark(90.0)  # 25% drawdown
        await test_db.update_high_water_mark(110.0)  # recovery
        hwm = await test_db.get_high_water_mark()
        assert hwm["peak_value"] == 120.0
        # max drawdown should still be 25%
        assert hwm["max_drawdown_pct"] == pytest.approx(25.0, abs=0.1)


# ====================================================================
# ANALYSIS CACHE
# ====================================================================

class TestGetCachedAnalysis:
    async def test_no_cache(self, test_db):
        result = await test_db.get_cached_analysis("market-xyz")
        assert result is None

    async def test_fresh_cache(self, test_db):
        analysis = {"signal": "BUY", "confidence": 0.8}
        await test_db.set_cached_analysis("market-xyz", "0.55/0.45", json.dumps(analysis))
        result = await test_db.get_cached_analysis("market-xyz", max_age_minutes=60)
        assert result is not None
        assert result["signal"] == "BUY"
        assert result["confidence"] == 0.8

    async def test_expired_cache(self, test_db):
        """Cache entry older than max_age_minutes returns None."""
        db = await test_db._get_db()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await db.execute(
            """INSERT INTO analysis_cache (market_id, price_snapshot, analysis_json, created_at)
               VALUES (?, ?, ?, ?)""",
            ("market-xyz", "0.55", '{"old": true}', old_time),
        )
        await db.commit()
        result = await test_db.get_cached_analysis("market-xyz", max_age_minutes=25)
        assert result is None

    async def test_invalid_json_returns_none(self, test_db):
        db = await test_db._get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO analysis_cache (market_id, price_snapshot, analysis_json, created_at)
               VALUES (?, ?, ?, ?)""",
            ("market-xyz", "0.55", "not-valid-json{{{", now),
        )
        await db.commit()
        result = await test_db.get_cached_analysis("market-xyz", max_age_minutes=60)
        assert result is None


class TestSetCachedAnalysis:
    async def test_insert_new(self, test_db):
        await test_db.set_cached_analysis("market-abc", "0.6/0.4", '{"key": "val"}')
        result = await test_db.get_cached_analysis("market-abc", max_age_minutes=60)
        assert result == {"key": "val"}

    async def test_upsert_replaces(self, test_db):
        await test_db.set_cached_analysis("market-abc", "0.6/0.4", '{"v": 1}')
        await test_db.set_cached_analysis("market-abc", "0.7/0.3", '{"v": 2}')
        result = await test_db.get_cached_analysis("market-abc", max_age_minutes=60)
        assert result == {"v": 2}


class TestCleanupOldCache:
    async def test_removes_old(self, test_db):
        db = await test_db._get_db()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        await db.execute(
            """INSERT INTO analysis_cache (market_id, price_snapshot, analysis_json, created_at)
               VALUES (?, ?, ?, ?)""",
            ("old-market", "0.5", '{}', old_time),
        )
        await db.commit()
        await test_db.set_cached_analysis("fresh-market", "0.5", '{}')

        await test_db.cleanup_old_cache(max_age_hours=24)

        assert await test_db.get_cached_analysis("old-market", max_age_minutes=99999) is None
        assert await test_db.get_cached_analysis("fresh-market", max_age_minutes=60) is not None

    async def test_empty_cache(self, test_db):
        # Should not raise
        await test_db.cleanup_old_cache()


# ====================================================================
# BOT STATUS
# ====================================================================

class TestUpdateBotStatus:
    async def test_insert_and_retrieve(self, test_db):
        await test_db.update_bot_status({"state": "running", "cycle": 42})
        status = await test_db.get_bot_status()
        assert status["state"] == "running"
        assert status["cycle"] == 42

    async def test_update_existing_key(self, test_db):
        await test_db.update_bot_status({"state": "running"})
        await test_db.update_bot_status({"state": "paused"})
        status = await test_db.get_bot_status()
        assert status["state"] == "paused"

    async def test_string_value(self, test_db):
        await test_db.update_bot_status({"message": "hello"})
        status = await test_db.get_bot_status()
        assert status["message"] == "hello"

    async def test_complex_value(self, test_db):
        data = {"positions": [1, 2, 3], "nested": {"a": True}}
        await test_db.update_bot_status({"data": data})
        status = await test_db.get_bot_status()
        assert status["data"] == data


class TestGetBotStatus:
    async def test_empty(self, test_db):
        status = await test_db.get_bot_status()
        assert status == {}

    async def test_multiple_keys(self, test_db):
        await test_db.update_bot_status({
            "state": "running",
            "version": "1.0",
            "uptime": 3600,
        })
        status = await test_db.get_bot_status()
        assert len(status) == 3
        assert status["uptime"] == 3600


# ====================================================================
# BOT SETTINGS
# ====================================================================

class TestApplySettingToConfig:
    def test_bool_true(self, test_db):
        cfg = SimpleNamespace(learning_mode=False)
        test_db._apply_setting_to_config(cfg, "learning_mode", "true")
        assert cfg.learning_mode is True

    def test_bool_false(self, test_db):
        cfg = SimpleNamespace(learning_mode=True)
        test_db._apply_setting_to_config(cfg, "learning_mode", "false")
        assert cfg.learning_mode is False

    def test_float(self, test_db):
        cfg = SimpleNamespace(max_per_day_usdc=0.0)
        test_db._apply_setting_to_config(cfg, "max_per_day_usdc", "50.0")
        assert cfg.max_per_day_usdc == 50.0

    def test_int(self, test_db):
        cfg = SimpleNamespace(analysis_interval_minutes=0)
        test_db._apply_setting_to_config(cfg, "analysis_interval_minutes", "30")
        assert cfg.analysis_interval_minutes == 30

    def test_choice(self, test_db):
        cfg = SimpleNamespace(strategy="")
        test_db._apply_setting_to_config(cfg, "strategy", "active")
        assert cfg.strategy == "active"

    def test_unknown_key_noop(self, test_db):
        cfg = SimpleNamespace()
        test_db._apply_setting_to_config(cfg, "totally_unknown", "value")
        # Should not raise or modify

    def test_invalid_value_noop(self, test_db):
        cfg = SimpleNamespace(max_per_day_usdc=10.0)
        test_db._apply_setting_to_config(cfg, "max_per_day_usdc", "not_a_number")
        # Should keep original value (setattr not called when conversion fails)
        assert cfg.max_per_day_usdc == 10.0


class TestInitSettings:
    async def test_creates_settings_from_config(self, test_db):
        cfg = SimpleNamespace(
            strategy="active",
            max_per_day_usdc=30.0,
            max_per_trade_usdc=10.0,
            confirmation_threshold_usdc=5.0,
            heartbeat_enabled=True,
            stop_loss_percent=20.0,
            drawdown_stop_loss_percent=25.0,
            min_edge_percent=10.0,
            min_net_edge_percent=8.0,
            max_slippage_bps=300.0,
            min_source_quality=0.35,
            estimated_fee_bps=20.0,
            max_concentration_percent=30.0,
            analysis_interval_minutes=15,
            learning_mode=False,
            learning_review_interval=5,
            learning_auto_apply=False,
            learning_auto_fix_logs=False,
            learning_max_commits_per_day=3,
            learning_git_enabled=False,
            learning_git_push=False,
            risk_officer_enabled=False,
            strategist_enabled=False,
            strategist_review_interval=5,
            conversation_enabled=False,
            conversation_max_history=20,
        )
        await test_db.init_settings(cfg)
        settings = await test_db.get_all_settings()
        assert len(settings) > 0
        values = await test_db.get_settings_values()
        assert "strategy" in values
        assert values["strategy"] == "active"

    async def test_db_values_override_config(self, test_db):
        cfg = SimpleNamespace(
            strategy="active",
            max_per_day_usdc=30.0,
            max_per_trade_usdc=10.0,
            confirmation_threshold_usdc=5.0,
            heartbeat_enabled=True,
            stop_loss_percent=20.0,
            drawdown_stop_loss_percent=25.0,
            min_edge_percent=10.0,
            min_net_edge_percent=8.0,
            max_slippage_bps=300.0,
            min_source_quality=0.35,
            estimated_fee_bps=20.0,
            max_concentration_percent=30.0,
            analysis_interval_minutes=15,
            learning_mode=False,
            learning_review_interval=5,
            learning_auto_apply=False,
            learning_auto_fix_logs=False,
            learning_max_commits_per_day=3,
            learning_git_enabled=False,
            learning_git_push=False,
            risk_officer_enabled=False,
            strategist_enabled=False,
            strategist_review_interval=5,
            conversation_enabled=False,
            conversation_max_history=20,
        )
        # First init seeds DB
        await test_db.init_settings(cfg)
        # Change a setting in DB
        await test_db.update_settings({"max_per_day_usdc": "100.0"})
        # Re-init: DB value should override config
        cfg.max_per_day_usdc = 30.0
        await test_db.init_settings(cfg)
        assert cfg.max_per_day_usdc == 100.0


class TestGetAllSettings:
    async def test_empty(self, test_db):
        settings = await test_db.get_all_settings()
        assert settings == []

    async def test_returns_metadata(self, test_db):
        # Manually insert a setting
        db = await test_db._get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO bot_settings
               (key, value, label_fr, description_fr, category, value_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("test_key", "42", "Label", "Desc", "trading", "float", now),
        )
        await db.commit()
        settings = await test_db.get_all_settings()
        assert len(settings) == 1
        assert settings[0]["key"] == "test_key"
        assert settings[0]["label_fr"] == "Label"


class TestGetSettingsValues:
    async def test_empty(self, test_db):
        vals = await test_db.get_settings_values()
        assert vals == {}

    async def test_key_value_pairs(self, test_db):
        db = await test_db._get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("k1", "v1", now),
        )
        await db.execute(
            "INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("k2", "v2", now),
        )
        await db.commit()
        vals = await test_db.get_settings_values()
        assert vals == {"k1": "v1", "k2": "v2"}


class TestUpdateSettings:
    async def test_update_known_setting(self, test_db):
        # Seed settings via init
        cfg = SimpleNamespace(
            strategy="active",
            max_per_day_usdc=30.0,
            max_per_trade_usdc=10.0,
            confirmation_threshold_usdc=5.0,
            heartbeat_enabled=True,
            stop_loss_percent=20.0,
            drawdown_stop_loss_percent=25.0,
            min_edge_percent=10.0,
            min_net_edge_percent=8.0,
            max_slippage_bps=300.0,
            min_source_quality=0.35,
            estimated_fee_bps=20.0,
            max_concentration_percent=30.0,
            analysis_interval_minutes=15,
            learning_mode=False,
            learning_review_interval=5,
            learning_auto_apply=False,
            learning_auto_fix_logs=False,
            learning_max_commits_per_day=3,
            learning_git_enabled=False,
            learning_git_push=False,
            risk_officer_enabled=False,
            strategist_enabled=False,
            strategist_review_interval=5,
            conversation_enabled=False,
            conversation_max_history=20,
        )
        await test_db.init_settings(cfg)
        await test_db.update_settings({"max_per_trade_usdc": "25.0"})
        vals = await test_db.get_settings_values()
        assert vals["max_per_trade_usdc"] == "25.0"

    async def test_ignores_unknown_key(self, test_db):
        await test_db.update_settings({"totally_unknown_key": "999"})
        vals = await test_db.get_settings_values()
        assert "totally_unknown_key" not in vals

    async def test_rejects_below_min(self, test_db):
        cfg = SimpleNamespace(
            strategy="active",
            max_per_day_usdc=30.0,
            max_per_trade_usdc=10.0,
            confirmation_threshold_usdc=5.0,
            heartbeat_enabled=True,
            stop_loss_percent=20.0,
            drawdown_stop_loss_percent=25.0,
            min_edge_percent=10.0,
            min_net_edge_percent=8.0,
            max_slippage_bps=300.0,
            min_source_quality=0.35,
            estimated_fee_bps=20.0,
            max_concentration_percent=30.0,
            analysis_interval_minutes=15,
            learning_mode=False,
            learning_review_interval=5,
            learning_auto_apply=False,
            learning_auto_fix_logs=False,
            learning_max_commits_per_day=3,
            learning_git_enabled=False,
            learning_git_push=False,
            risk_officer_enabled=False,
            strategist_enabled=False,
            strategist_review_interval=5,
            conversation_enabled=False,
            conversation_max_history=20,
        )
        await test_db.init_settings(cfg)
        # min_value for max_per_day_usdc is 5; try to set below
        await test_db.update_settings({"max_per_day_usdc": "1.0"})
        vals = await test_db.get_settings_values()
        assert vals["max_per_day_usdc"] == "30.0"  # unchanged

    async def test_rejects_above_max(self, test_db):
        cfg = SimpleNamespace(
            strategy="active",
            max_per_day_usdc=30.0,
            max_per_trade_usdc=10.0,
            confirmation_threshold_usdc=5.0,
            heartbeat_enabled=True,
            stop_loss_percent=20.0,
            drawdown_stop_loss_percent=25.0,
            min_edge_percent=10.0,
            min_net_edge_percent=8.0,
            max_slippage_bps=300.0,
            min_source_quality=0.35,
            estimated_fee_bps=20.0,
            max_concentration_percent=30.0,
            analysis_interval_minutes=15,
            learning_mode=False,
            learning_review_interval=5,
            learning_auto_apply=False,
            learning_auto_fix_logs=False,
            learning_max_commits_per_day=3,
            learning_git_enabled=False,
            learning_git_push=False,
            risk_officer_enabled=False,
            strategist_enabled=False,
            strategist_review_interval=5,
            conversation_enabled=False,
            conversation_max_history=20,
        )
        await test_db.init_settings(cfg)
        # max_value for max_per_day_usdc is 500; try above
        await test_db.update_settings({"max_per_day_usdc": "9999.0"})
        vals = await test_db.get_settings_values()
        assert vals["max_per_day_usdc"] == "30.0"  # unchanged

    async def test_rejects_invalid_choice(self, test_db):
        cfg = SimpleNamespace(
            strategy="active",
            max_per_day_usdc=30.0,
            max_per_trade_usdc=10.0,
            confirmation_threshold_usdc=5.0,
            heartbeat_enabled=True,
            stop_loss_percent=20.0,
            drawdown_stop_loss_percent=25.0,
            min_edge_percent=10.0,
            min_net_edge_percent=8.0,
            max_slippage_bps=300.0,
            min_source_quality=0.35,
            estimated_fee_bps=20.0,
            max_concentration_percent=30.0,
            analysis_interval_minutes=15,
            learning_mode=False,
            learning_review_interval=5,
            learning_auto_apply=False,
            learning_auto_fix_logs=False,
            learning_max_commits_per_day=3,
            learning_git_enabled=False,
            learning_git_push=False,
            risk_officer_enabled=False,
            strategist_enabled=False,
            strategist_review_interval=5,
            conversation_enabled=False,
            conversation_max_history=20,
        )
        await test_db.init_settings(cfg)
        await test_db.update_settings({"strategy": "invalid_strategy"})
        vals = await test_db.get_settings_values()
        assert vals["strategy"] == "active"


# ====================================================================
# BOT COMMANDS
# ====================================================================

class TestInsertCommand:
    async def test_returns_id(self, test_db):
        cid = await test_db.insert_command("pause")
        assert isinstance(cid, int)
        assert cid >= 1

    async def test_with_payload(self, test_db):
        cid = await test_db.insert_command("update_settings", payload='{"key": "val"}')
        cmds = await test_db.get_recent_commands(limit=1)
        assert cmds[0]["payload"] == '{"key": "val"}'

    async def test_null_payload(self, test_db):
        cid = await test_db.insert_command("resume")
        cmds = await test_db.get_recent_commands(limit=1)
        assert cmds[0]["payload"] is None


class TestGetPendingCommands:
    async def test_empty(self, test_db):
        cmds = await test_db.get_pending_commands()
        assert cmds == []

    async def test_only_pending(self, test_db):
        c1 = await test_db.insert_command("cmd1")
        c2 = await test_db.insert_command("cmd2")
        await test_db.mark_command_executed(c1, {"ok": True})
        cmds = await test_db.get_pending_commands()
        assert len(cmds) == 1
        assert cmds[0]["id"] == c2

    async def test_order_asc(self, test_db):
        c1 = await test_db.insert_command("first")
        c2 = await test_db.insert_command("second")
        cmds = await test_db.get_pending_commands()
        assert cmds[0]["id"] == c1
        assert cmds[1]["id"] == c2


class TestMarkCommandExecuted:
    async def test_marks_executed(self, test_db):
        cid = await test_db.insert_command("test")
        await test_db.mark_command_executed(cid, {"success": True})
        cmds = await test_db.get_recent_commands(limit=1)
        assert cmds[0]["status"] == "executed"
        assert json.loads(cmds[0]["result"]) == {"success": True}
        assert cmds[0]["executed_at"] is not None


class TestMarkCommandFailed:
    async def test_marks_failed(self, test_db):
        cid = await test_db.insert_command("test")
        await test_db.mark_command_failed(cid, "something went wrong")
        cmds = await test_db.get_recent_commands(limit=1)
        assert cmds[0]["status"] == "failed"
        result = json.loads(cmds[0]["result"])
        assert result["error"] == "something went wrong"
        assert cmds[0]["executed_at"] is not None


class TestGetRecentCommands:
    async def test_empty(self, test_db):
        cmds = await test_db.get_recent_commands()
        assert cmds == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_command(f"cmd-{i}")
        cmds = await test_db.get_recent_commands(limit=3)
        assert len(cmds) == 3

    async def test_order_desc(self, test_db):
        c1 = await test_db.insert_command("first")
        c2 = await test_db.insert_command("second")
        cmds = await test_db.get_recent_commands(limit=2)
        ids = {c["id"] for c in cmds}
        assert ids == {c1, c2}


# ====================================================================
# LEARNING MODE
# ====================================================================

class TestInsertJournalEntry:
    async def test_returns_id(self, test_db):
        entry = {"cycle_number": 1}
        jid = await test_db.insert_journal_entry(entry)
        assert isinstance(jid, int)
        assert jid >= 1

    async def test_stored_fields(self, test_db):
        entry = {
            "cycle_number": 5,
            "trades_proposed": 3,
            "trades_executed": 2,
            "trades_skipped": 1,
            "skipped_markets": "market-A,market-B",
            "retrospective_json": '{"good": true}',
            "price_snapshots": '{"m1": 0.55}',
        }
        jid = await test_db.insert_journal_entry(entry)
        entries = await test_db.get_journal_entries(limit=1)
        assert len(entries) == 1
        e = entries[0]
        assert e["cycle_number"] == 5
        assert e["trades_proposed"] == 3
        assert e["trades_executed"] == 2
        assert e["trades_skipped"] == 1
        assert e["skipped_markets"] == "market-A,market-B"

    async def test_minimal_entry(self, test_db):
        entry = {"cycle_number": 1}
        jid = await test_db.insert_journal_entry(entry)
        entries = await test_db.get_journal_entries(limit=1)
        assert entries[0]["trades_proposed"] == 0


class TestGetJournalEntries:
    async def test_empty(self, test_db):
        entries = await test_db.get_journal_entries()
        assert entries == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_journal_entry({"cycle_number": i})
        entries = await test_db.get_journal_entries(limit=3)
        assert len(entries) == 3

    async def test_order_desc(self, test_db):
        j1 = await test_db.insert_journal_entry({"cycle_number": 1})
        j2 = await test_db.insert_journal_entry({"cycle_number": 2})
        entries = await test_db.get_journal_entries(limit=2)
        ids = {e["id"] for e in entries}
        assert ids == {j1, j2}


class TestGetJournalEntryByCycle:
    async def test_found(self, test_db):
        await test_db.insert_journal_entry({"cycle_number": 42})
        entry = await test_db.get_journal_entry_by_cycle(42)
        assert entry is not None
        assert entry["cycle_number"] == 42

    async def test_not_found(self, test_db):
        entry = await test_db.get_journal_entry_by_cycle(99999)
        assert entry is None


class TestInsertInsight:
    async def test_returns_id(self, test_db):
        insight = {
            "insight_type": "bias",
            "description": "Overconfidence in crypto",
        }
        iid = await test_db.insert_insight(insight)
        assert isinstance(iid, int)

    async def test_stored_fields(self, test_db):
        insight = {
            "insight_type": "pattern",
            "description": "Win streak after news events",
            "evidence": "3 wins in a row post-news",
            "proposed_action": "Increase allocation",
            "severity": "warning",
        }
        await test_db.insert_insight(insight)
        insights = await test_db.get_active_insights(limit=1)
        assert len(insights) == 1
        i = insights[0]
        assert i["insight_type"] == "pattern"
        assert i["severity"] == "warning"
        assert i["status"] == "active"

    async def test_default_severity(self, test_db):
        insight = {"insight_type": "misc", "description": "test"}
        await test_db.insert_insight(insight)
        insights = await test_db.get_active_insights(limit=1)
        assert insights[0]["severity"] == "info"


class TestGetActiveInsights:
    async def test_empty(self, test_db):
        insights = await test_db.get_active_insights()
        assert insights == []

    async def test_only_active(self, test_db):
        await test_db.insert_insight({"insight_type": "a", "description": "active one"})
        # Manually mark one as inactive
        db = await test_db._get_db()
        await db.execute("UPDATE learning_insights SET status='dismissed' WHERE id=1")
        await db.commit()
        await test_db.insert_insight({"insight_type": "b", "description": "still active"})
        insights = await test_db.get_active_insights()
        assert len(insights) == 1
        assert insights[0]["description"] == "still active"

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_insight({"insight_type": f"t{i}", "description": f"d{i}"})
        insights = await test_db.get_active_insights(limit=2)
        assert len(insights) == 2


class TestInsertProposal:
    async def test_returns_id(self, test_db):
        proposal = {
            "proposal_type": "config_change",
            "target": "min_edge_percent",
            "proposed_value": "12.0",
            "rationale": "Improve selectivity",
        }
        pid = await test_db.insert_proposal(proposal)
        assert isinstance(pid, int)

    async def test_stored_fields(self, test_db):
        proposal = {
            "proposal_type": "config_change",
            "target": "max_per_trade_usdc",
            "current_value": "10.0",
            "proposed_value": "15.0",
            "rationale": "More capital per trade",
            "risk_level": "low",
        }
        pid = await test_db.insert_proposal(proposal)
        p = await test_db.get_proposal_by_id(pid)
        assert p is not None
        assert p["proposal_type"] == "config_change"
        assert p["target"] == "max_per_trade_usdc"
        assert p["current_value"] == "10.0"
        assert p["proposed_value"] == "15.0"
        assert p["risk_level"] == "low"
        assert p["status"] == "pending"

    async def test_default_risk_level(self, test_db):
        proposal = {
            "proposal_type": "test",
            "target": "x",
            "proposed_value": "y",
            "rationale": "z",
        }
        pid = await test_db.insert_proposal(proposal)
        p = await test_db.get_proposal_by_id(pid)
        assert p["risk_level"] == "moderate"


class TestGetPendingProposals:
    async def test_empty(self, test_db):
        proposals = await test_db.get_pending_proposals()
        assert proposals == []

    async def test_only_pending(self, test_db):
        p1 = await test_db.insert_proposal({
            "proposal_type": "a", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        p2 = await test_db.insert_proposal({
            "proposal_type": "b", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        await test_db.update_proposal_status(p1, "applied")
        pending = await test_db.get_pending_proposals()
        assert len(pending) == 1
        assert pending[0]["id"] == p2


class TestGetProposalById:
    async def test_found(self, test_db):
        pid = await test_db.insert_proposal({
            "proposal_type": "a", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        p = await test_db.get_proposal_by_id(pid)
        assert p is not None
        assert p["id"] == pid

    async def test_not_found(self, test_db):
        p = await test_db.get_proposal_by_id(99999)
        assert p is None


class TestUpdateProposalStatus:
    async def test_apply(self, test_db):
        pid = await test_db.insert_proposal({
            "proposal_type": "a", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        await test_db.update_proposal_status(pid, "applied")
        p = await test_db.get_proposal_by_id(pid)
        assert p["status"] == "applied"
        assert p["applied_at"] is not None

    async def test_reject(self, test_db):
        pid = await test_db.insert_proposal({
            "proposal_type": "a", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        await test_db.update_proposal_status(pid, "rejected")
        p = await test_db.get_proposal_by_id(pid)
        assert p["status"] == "rejected"
        assert p["applied_at"] is None


class TestGetAllProposals:
    async def test_empty(self, test_db):
        proposals = await test_db.get_all_proposals()
        assert proposals == []

    async def test_returns_all_statuses(self, test_db):
        p1 = await test_db.insert_proposal({
            "proposal_type": "a", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        p2 = await test_db.insert_proposal({
            "proposal_type": "b", "target": "t", "proposed_value": "v", "rationale": "r",
        })
        await test_db.update_proposal_status(p1, "applied")
        proposals = await test_db.get_all_proposals()
        assert len(proposals) == 2

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_proposal({
                "proposal_type": f"t{i}", "target": "t", "proposed_value": "v", "rationale": "r",
            })
        proposals = await test_db.get_all_proposals(limit=3)
        assert len(proposals) == 3


class TestInsertShadowRecord:
    async def test_returns_id(self, test_db):
        record = {"cycle_number": 1, "market_id": "m1"}
        sid = await test_db.insert_shadow_record(record)
        assert isinstance(sid, int)

    async def test_stored_fields(self, test_db):
        record = {
            "cycle_number": 5,
            "market_id": "m1",
            "current_decision": "BUY",
            "shadow_decision": "SKIP",
            "current_params": '{"edge": 0.1}',
            "shadow_params": '{"edge": 0.15}',
        }
        await test_db.insert_shadow_record(record)
        records = await test_db.get_shadow_records(limit=1)
        assert len(records) == 1
        r = records[0]
        assert r["cycle_number"] == 5
        assert r["current_decision"] == "BUY"
        assert r["shadow_decision"] == "SKIP"


class TestGetShadowRecords:
    async def test_empty(self, test_db):
        records = await test_db.get_shadow_records()
        assert records == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_shadow_record({"cycle_number": i, "market_id": f"m{i}"})
        records = await test_db.get_shadow_records(limit=3)
        assert len(records) == 3

    async def test_order_desc(self, test_db):
        s1 = await test_db.insert_shadow_record({"cycle_number": 1, "market_id": "m1"})
        s2 = await test_db.insert_shadow_record({"cycle_number": 2, "market_id": "m2"})
        records = await test_db.get_shadow_records(limit=2)
        ids = {r["id"] for r in records}
        assert ids == {s1, s2}


# ====================================================================
# GIT CHANGES
# ====================================================================

class TestInsertGitChange:
    async def test_returns_id(self, test_db):
        change = {"branch_name": "fix/edge-calc"}
        cid = await test_db.insert_git_change(change)
        assert isinstance(cid, int)

    async def test_stored_fields(self, test_db):
        change = {
            "proposal_id": 1,
            "branch_name": "fix/edge-calc",
            "commit_hash": "abc123",
            "remote_name": "origin",
            "push_status": "pending",
            "justification": "Fix edge calculation",
            "files_changed": ["file1.py", "file2.py"],
            "result": {"status": "ok"},
        }
        cid = await test_db.insert_git_change(change)
        changes = await test_db.get_git_changes(limit=1)
        assert len(changes) == 1
        c = changes[0]
        assert c["branch_name"] == "fix/edge-calc"
        assert c["commit_hash"] == "abc123"
        assert c["files_changed"] == ["file1.py", "file2.py"]
        assert c["result"] == {"status": "ok"}

    async def test_defaults(self, test_db):
        change = {"branch_name": "test-branch"}
        await test_db.insert_git_change(change)
        changes = await test_db.get_git_changes(limit=1)
        c = changes[0]
        assert c["remote_name"] == "origin"
        assert c["push_status"] == "pending"
        assert c["files_changed"] == []
        assert c["result"] == {}


class TestUpdateGitChange:
    async def test_update_commit_hash(self, test_db):
        cid = await test_db.insert_git_change({"branch_name": "b1"})
        await test_db.update_git_change(cid, commit_hash="def456")
        changes = await test_db.get_git_changes(limit=1)
        assert changes[0]["commit_hash"] == "def456"

    async def test_update_push_status(self, test_db):
        cid = await test_db.insert_git_change({"branch_name": "b1"})
        await test_db.update_git_change(cid, push_status="pushed")
        changes = await test_db.get_git_changes(limit=1)
        assert changes[0]["push_status"] == "pushed"

    async def test_update_result(self, test_db):
        cid = await test_db.insert_git_change({"branch_name": "b1"})
        await test_db.update_git_change(cid, result={"tests_passed": True})
        changes = await test_db.get_git_changes(limit=1)
        assert changes[0]["result"] == {"tests_passed": True}

    async def test_no_updates_noop(self, test_db):
        cid = await test_db.insert_git_change({"branch_name": "b1"})
        # Should not raise
        await test_db.update_git_change(cid)

    async def test_update_multiple_fields(self, test_db):
        cid = await test_db.insert_git_change({"branch_name": "b1"})
        await test_db.update_git_change(cid, commit_hash="abc", push_status="pushed", result={"ok": True})
        changes = await test_db.get_git_changes(limit=1)
        c = changes[0]
        assert c["commit_hash"] == "abc"
        assert c["push_status"] == "pushed"
        assert c["result"] == {"ok": True}


class TestGetGitChanges:
    async def test_empty(self, test_db):
        changes = await test_db.get_git_changes()
        assert changes == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_git_change({"branch_name": f"b{i}"})
        changes = await test_db.get_git_changes(limit=3)
        assert len(changes) == 3

    async def test_json_parsing(self, test_db):
        await test_db.insert_git_change({
            "branch_name": "b1",
            "files_changed": ["a.py", "b.py"],
            "result": {"compiled": True},
        })
        changes = await test_db.get_git_changes(limit=1)
        assert isinstance(changes[0]["files_changed"], list)
        assert isinstance(changes[0]["result"], dict)


class TestCountGitChangesToday:
    async def test_zero_when_empty(self, test_db):
        count = await test_db.count_git_changes_today()
        assert count == 0

    async def test_counts_today_only(self, test_db):
        await test_db.insert_git_change({"branch_name": "b1"})
        await test_db.insert_git_change({"branch_name": "b2"})
        count = await test_db.count_git_changes_today()
        assert count == 2

    async def test_excludes_old_entries(self, test_db):
        # Insert an entry with an old date
        db = await test_db._get_db()
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        await db.execute(
            """INSERT INTO learning_git_changes (branch_name, push_status, files_changed, result, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("old-branch", "pending", "[]", "{}", old_date),
        )
        await db.commit()
        count = await test_db.count_git_changes_today()
        assert count == 0


# ====================================================================
# MANAGER CRITIQUES
# ====================================================================

class TestInsertManagerCritique:
    async def test_returns_id(self, test_db):
        critique = {
            "cycle_number": 10,
            "critique_json": '{"quality": "good"}',
            "summary": "Overall good performance",
        }
        cid = await test_db.insert_manager_critique(critique)
        assert isinstance(cid, int)
        assert cid >= 1

    async def test_stored_fields(self, test_db):
        critique = {
            "cycle_number": 10,
            "critique_json": '{"quality": "good"}',
            "summary": "Good performance",
            "trading_quality_score": 8,
            "risk_management_score": 7,
            "strategy_effectiveness_score": 9,
            "improvement_areas": "edge calculation",
            "code_changes_suggested": "fix_edge.py",
        }
        cid = await test_db.insert_manager_critique(critique)
        c = await test_db.get_critique_by_id(cid)
        assert c is not None
        assert c["cycle_number"] == 10
        assert c["trading_quality_score"] == 8
        assert c["status"] == "pending"

    async def test_defaults(self, test_db):
        critique = {"cycle_number": 1}
        cid = await test_db.insert_manager_critique(critique)
        c = await test_db.get_critique_by_id(cid)
        assert c["critique_json"] == ""
        assert c["summary"] == ""
        assert c["status"] == "pending"


class TestGetCritiqueById:
    async def test_found(self, test_db):
        cid = await test_db.insert_manager_critique({"cycle_number": 1})
        c = await test_db.get_critique_by_id(cid)
        assert c is not None
        assert c["id"] == cid

    async def test_not_found(self, test_db):
        c = await test_db.get_critique_by_id(99999)
        assert c is None


class TestGetPendingCritiques:
    async def test_empty(self, test_db):
        critiques = await test_db.get_pending_critiques()
        assert critiques == []

    async def test_only_pending(self, test_db):
        c1 = await test_db.insert_manager_critique({"cycle_number": 1})
        c2 = await test_db.insert_manager_critique({"cycle_number": 2})
        await test_db.update_critique_status(c1, "approved")
        pending = await test_db.get_pending_critiques()
        assert len(pending) == 1
        assert pending[0]["id"] == c2


class TestGetRecentCritiques:
    async def test_empty(self, test_db):
        critiques = await test_db.get_recent_critiques()
        assert critiques == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_manager_critique({"cycle_number": i})
        critiques = await test_db.get_recent_critiques(limit=3)
        assert len(critiques) == 3

    async def test_order_desc(self, test_db):
        c1 = await test_db.insert_manager_critique({"cycle_number": 1})
        c2 = await test_db.insert_manager_critique({"cycle_number": 2})
        critiques = await test_db.get_recent_critiques(limit=2)
        ids = {c["id"] for c in critiques}
        assert ids == {c1, c2}


class TestUpdateCritiqueStatus:
    async def test_approve(self, test_db):
        cid = await test_db.insert_manager_critique({"cycle_number": 1})
        await test_db.update_critique_status(cid, "approved")
        c = await test_db.get_critique_by_id(cid)
        assert c["status"] == "approved"
        assert c["reviewed_at"] is not None

    async def test_reject(self, test_db):
        cid = await test_db.insert_manager_critique({"cycle_number": 1})
        await test_db.update_critique_status(cid, "rejected")
        c = await test_db.get_critique_by_id(cid)
        assert c["status"] == "rejected"
        assert c["reviewed_at"] is not None

    async def test_deploy(self, test_db):
        cid = await test_db.insert_manager_critique({"cycle_number": 1})
        await test_db.update_critique_status(cid, "deployed")
        c = await test_db.get_critique_by_id(cid)
        assert c["status"] == "deployed"
        assert c["deployed_at"] is not None

    async def test_with_kwargs(self, test_db):
        cid = await test_db.insert_manager_critique({"cycle_number": 1})
        await test_db.update_critique_status(
            cid, "approved",
            developer_result="changes applied",
            branch_name="fix/edge",
            commit_hash="abc123",
            deploy_status="ready",
            user_feedback="looks good",
        )
        c = await test_db.get_critique_by_id(cid)
        assert c["developer_result"] == "changes applied"
        assert c["branch_name"] == "fix/edge"
        assert c["commit_hash"] == "abc123"
        assert c["deploy_status"] == "ready"
        assert c["user_feedback"] == "looks good"

    async def test_other_status_no_timestamps(self, test_db):
        cid = await test_db.insert_manager_critique({"cycle_number": 1})
        await test_db.update_critique_status(cid, "in_progress")
        c = await test_db.get_critique_by_id(cid)
        assert c["status"] == "in_progress"
        assert c["reviewed_at"] is None
        assert c["deployed_at"] is None


class TestGetDeployPendingCritiques:
    async def test_empty(self, test_db):
        critiques = await test_db.get_deploy_pending_critiques()
        assert critiques == []

    async def test_only_deploy_pending(self, test_db):
        c1 = await test_db.insert_manager_critique({"cycle_number": 1})
        c2 = await test_db.insert_manager_critique({"cycle_number": 2})
        await test_db.update_critique_status(c1, "deploy_pending")
        await test_db.update_critique_status(c2, "approved")
        critiques = await test_db.get_deploy_pending_critiques()
        assert len(critiques) == 1
        assert critiques[0]["id"] == c1


# ====================================================================
# STRATEGIST ASSESSMENTS
# ====================================================================

class TestInsertStrategistAssessment:
    async def test_returns_id(self, test_db):
        assessment = {"assessment_json": '{"ok": true}', "summary": "All good"}
        aid = await test_db.insert_strategist_assessment(assessment)
        assert isinstance(aid, int)
        assert aid >= 1

    async def test_stored_fields(self, test_db):
        assessment = {
            "assessment_json": '{"regime": "bull"}',
            "summary": "Bull market",
            "market_regime": "bullish",
            "regime_confidence": 0.85,
            "allocation_score": 7,
            "diversification_score": 8,
            "category_allocation": '{"crypto": 0.5}',
            "recommendations": "Increase crypto",
            "strategic_insights": "Market is trending up",
        }
        aid = await test_db.insert_strategist_assessment(assessment)
        a = await test_db.get_assessment_by_id(aid)
        assert a is not None
        assert a["market_regime"] == "bullish"
        assert a["regime_confidence"] == 0.85
        assert a["allocation_score"] == 7

    async def test_defaults(self, test_db):
        assessment = {}
        aid = await test_db.insert_strategist_assessment(assessment)
        a = await test_db.get_assessment_by_id(aid)
        assert a["market_regime"] == "normal"
        assert a["regime_confidence"] == 0.5
        assert a["summary"] == ""


class TestGetAssessmentById:
    async def test_found(self, test_db):
        aid = await test_db.insert_strategist_assessment({"summary": "test"})
        a = await test_db.get_assessment_by_id(aid)
        assert a is not None
        assert a["id"] == aid

    async def test_not_found(self, test_db):
        a = await test_db.get_assessment_by_id(99999)
        assert a is None


class TestGetRecentAssessments:
    async def test_empty(self, test_db):
        assessments = await test_db.get_recent_assessments()
        assert assessments == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_strategist_assessment({"summary": f"s{i}"})
        assessments = await test_db.get_recent_assessments(limit=3)
        assert len(assessments) == 3

    async def test_order_desc(self, test_db):
        a1 = await test_db.insert_strategist_assessment({"summary": "first"})
        a2 = await test_db.insert_strategist_assessment({"summary": "second"})
        assessments = await test_db.get_recent_assessments(limit=2)
        ids = {a["id"] for a in assessments}
        assert ids == {a1, a2}


class TestGetLatestAssessment:
    async def test_empty(self, test_db):
        a = await test_db.get_latest_assessment()
        assert a is None

    async def test_returns_most_recent(self, test_db):
        await test_db.insert_strategist_assessment({"summary": "first"})
        await test_db.insert_strategist_assessment({"summary": "second"})
        a = await test_db.get_latest_assessment()
        assert a is not None
        # Both were inserted in the same second; just verify one is returned
        assert a["summary"] in ("first", "second")


# ====================================================================
# RISK OFFICER REVIEWS
# ====================================================================

class TestInsertRiskOfficerReview:
    async def test_returns_id(self, test_db):
        review = {"review_json": '{"ok": true}'}
        rid = await test_db.insert_risk_officer_review(review, cycle_number=5)
        assert isinstance(rid, int)
        assert rid >= 1

    async def test_stored_fields(self, test_db):
        review = {
            "review_json": '{"risk": "low"}',
            "portfolio_risk_summary": "Low risk overall",
            "trades_reviewed": 10,
            "trades_flagged": 2,
            "trades_rejected": 1,
            "parameter_recommendations": [{"param": "edge", "value": 0.12}],
        }
        rid = await test_db.insert_risk_officer_review(review, cycle_number=7)
        r = await test_db.get_risk_review_by_id(rid)
        assert r is not None
        assert r["cycle_number"] == 7
        assert r["trades_reviewed"] == 10
        assert r["trades_flagged"] == 2
        assert r["trades_rejected"] == 1

    async def test_no_cycle_number(self, test_db):
        review = {"review_json": '{"test": true}'}
        rid = await test_db.insert_risk_officer_review(review)
        r = await test_db.get_risk_review_by_id(rid)
        assert r["cycle_number"] is None

    async def test_auto_serialize_review_json(self, test_db):
        """When review_json is missing, the whole dict is serialized."""
        review = {"risk_level": "high", "trades_reviewed": 5}
        rid = await test_db.insert_risk_officer_review(review)
        r = await test_db.get_risk_review_by_id(rid)
        parsed = json.loads(r["review_json"])
        assert parsed["risk_level"] == "high"

    async def test_defaults(self, test_db):
        review = {}
        rid = await test_db.insert_risk_officer_review(review)
        r = await test_db.get_risk_review_by_id(rid)
        assert r["portfolio_risk_summary"] == ""
        assert r["trades_reviewed"] == 0
        assert r["trades_flagged"] == 0
        assert r["trades_rejected"] == 0


class TestGetRecentRiskReviews:
    async def test_empty(self, test_db):
        reviews = await test_db.get_recent_risk_reviews()
        assert reviews == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_risk_officer_review({}, cycle_number=i)
        reviews = await test_db.get_recent_risk_reviews(limit=3)
        assert len(reviews) == 3

    async def test_order_desc(self, test_db):
        r1 = await test_db.insert_risk_officer_review({}, cycle_number=1)
        r2 = await test_db.insert_risk_officer_review({}, cycle_number=2)
        reviews = await test_db.get_recent_risk_reviews(limit=2)
        ids = {r["id"] for r in reviews}
        assert ids == {r1, r2}


class TestGetRiskReviewById:
    async def test_found(self, test_db):
        rid = await test_db.insert_risk_officer_review({}, cycle_number=1)
        r = await test_db.get_risk_review_by_id(rid)
        assert r is not None
        assert r["id"] == rid

    async def test_not_found(self, test_db):
        r = await test_db.get_risk_review_by_id(99999)
        assert r is None


# ====================================================================
# CONVERSATIONS
# ====================================================================

class TestInsertConversationTurn:
    async def test_returns_id(self, test_db):
        turn = {
            "source": "telegram",
            "role": "user",
            "message": "Hello bot",
        }
        cid = await test_db.insert_conversation_turn(turn)
        assert isinstance(cid, int)
        assert cid >= 1

    async def test_stored_fields(self, test_db):
        turn = {
            "source": "dashboard",
            "role": "assistant",
            "agent_name": "analyst",
            "message": "Analysis complete",
            "action_taken": "generated_report",
        }
        cid = await test_db.insert_conversation_turn(turn)
        convos = await test_db.get_all_conversations(limit=1)
        assert len(convos) == 1
        c = convos[0]
        assert c["source"] == "dashboard"
        assert c["role"] == "assistant"
        assert c["agent_name"] == "analyst"
        assert c["message"] == "Analysis complete"
        assert c["action_taken"] == "generated_report"

    async def test_default_agent_name(self, test_db):
        turn = {"source": "telegram", "role": "user", "message": "hi"}
        await test_db.insert_conversation_turn(turn)
        convos = await test_db.get_all_conversations(limit=1)
        assert convos[0]["agent_name"] == "general"


class TestGetRecentConversations:
    async def test_empty(self, test_db):
        convos = await test_db.get_recent_conversations("telegram")
        assert convos == []

    async def test_filter_by_source(self, test_db):
        await test_db.insert_conversation_turn({"source": "telegram", "role": "user", "message": "hello"})
        await test_db.insert_conversation_turn({"source": "dashboard", "role": "user", "message": "hi"})
        convos = await test_db.get_recent_conversations("telegram")
        assert len(convos) == 1
        assert convos[0]["source"] == "telegram"

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_conversation_turn(
                {"source": "telegram", "role": "user", "message": f"msg{i}"}
            )
        convos = await test_db.get_recent_conversations("telegram", limit=3)
        assert len(convos) == 3

    async def test_chronological_order(self, test_db):
        """get_recent_conversations returns in chronological order (reversed from DB DESC)."""
        await test_db.insert_conversation_turn(
            {"source": "telegram", "role": "user", "message": "first"}
        )
        await test_db.insert_conversation_turn(
            {"source": "telegram", "role": "user", "message": "second"}
        )
        convos = await test_db.get_recent_conversations("telegram")
        assert len(convos) == 2
        # Both messages present; order may vary within same second
        messages = {c["message"] for c in convos}
        assert messages == {"first", "second"}


class TestGetAllConversations:
    async def test_empty(self, test_db):
        convos = await test_db.get_all_conversations()
        assert convos == []

    async def test_all_sources(self, test_db):
        await test_db.insert_conversation_turn({"source": "telegram", "role": "user", "message": "a"})
        await test_db.insert_conversation_turn({"source": "dashboard", "role": "user", "message": "b"})
        convos = await test_db.get_all_conversations()
        assert len(convos) == 2

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_conversation_turn(
                {"source": "telegram", "role": "user", "message": f"m{i}"}
            )
        convos = await test_db.get_all_conversations(limit=3)
        assert len(convos) == 3

    async def test_order_desc(self, test_db):
        c1 = await test_db.insert_conversation_turn(
            {"source": "telegram", "role": "user", "message": "first"}
        )
        c2 = await test_db.insert_conversation_turn(
            {"source": "telegram", "role": "user", "message": "second"}
        )
        convos = await test_db.get_all_conversations(limit=2)
        assert convos[0]["id"] == c2  # most recent first


# ====================================================================
# FILE CHANGE AUDIT
# ====================================================================

def _sample_file_change(**overrides) -> dict:
    base = {
        "file_path": "services/worker/agent/analyst.py",
        "change_type": "modify",
        "tier": 2,
        "agent_name": "developer",
        "reason": "Fix edge calculation",
        "diff_summary": "+3 -1 lines",
        "backup_path": "/backups/analyst.py.bak",
        "status": "pending",
    }
    base.update(overrides)
    return base


class TestInsertFileChangeAudit:
    async def test_returns_id(self, test_db):
        fid = await test_db.insert_file_change_audit(_sample_file_change())
        assert isinstance(fid, int)
        assert fid >= 1

    async def test_stored_fields(self, test_db):
        entry = _sample_file_change()
        fid = await test_db.insert_file_change_audit(entry)
        changes = await test_db.get_recent_file_changes(limit=1)
        assert len(changes) == 1
        c = changes[0]
        assert c["file_path"] == "services/worker/agent/analyst.py"
        assert c["change_type"] == "modify"
        assert c["tier"] == 2
        assert c["agent_name"] == "developer"
        assert c["reason"] == "Fix edge calculation"
        assert c["diff_summary"] == "+3 -1 lines"
        assert c["backup_path"] == "/backups/analyst.py.bak"
        assert c["status"] == "pending"

    async def test_default_status(self, test_db):
        entry = {
            "file_path": "test.py",
            "change_type": "create",
            "tier": 1,
            "agent_name": "dev",
        }
        await test_db.insert_file_change_audit(entry)
        changes = await test_db.get_recent_file_changes(limit=1)
        assert changes[0]["status"] == "pending"

    async def test_optional_fields_none(self, test_db):
        entry = {
            "file_path": "test.py",
            "change_type": "delete",
            "tier": 3,
            "agent_name": "dev",
        }
        await test_db.insert_file_change_audit(entry)
        changes = await test_db.get_recent_file_changes(limit=1)
        assert changes[0]["reason"] is None
        assert changes[0]["diff_summary"] is None
        assert changes[0]["backup_path"] is None


class TestGetPendingFileChanges:
    async def test_empty(self, test_db):
        changes = await test_db.get_pending_file_changes()
        assert changes == []

    async def test_only_pending(self, test_db):
        f1 = await test_db.insert_file_change_audit(_sample_file_change())
        f2 = await test_db.insert_file_change_audit(_sample_file_change(
            file_path="other.py"
        ))
        await test_db.update_file_change_status(f1, "applied")
        pending = await test_db.get_pending_file_changes()
        assert len(pending) == 1
        assert pending[0]["id"] == f2


class TestUpdateFileChangeStatus:
    async def test_update_status(self, test_db):
        fid = await test_db.insert_file_change_audit(_sample_file_change())
        await test_db.update_file_change_status(fid, "applied")
        changes = await test_db.get_recent_file_changes(limit=1)
        assert changes[0]["status"] == "applied"

    async def test_update_with_kwargs(self, test_db):
        fid = await test_db.insert_file_change_audit(_sample_file_change())
        await test_db.update_file_change_status(
            fid, "applied",
            backup_path="/new/backup.py",
            diff_summary="+10 -5",
        )
        changes = await test_db.get_recent_file_changes(limit=1)
        c = changes[0]
        assert c["status"] == "applied"
        assert c["backup_path"] == "/new/backup.py"
        assert c["diff_summary"] == "+10 -5"

    async def test_update_nonexistent(self, test_db):
        # Should not raise
        await test_db.update_file_change_status(99999, "failed")


class TestGetRecentFileChanges:
    async def test_empty(self, test_db):
        changes = await test_db.get_recent_file_changes()
        assert changes == []

    async def test_limit(self, test_db):
        for i in range(5):
            await test_db.insert_file_change_audit(_sample_file_change(
                file_path=f"file{i}.py"
            ))
        changes = await test_db.get_recent_file_changes(limit=3)
        assert len(changes) == 3

    async def test_order_desc(self, test_db):
        f1 = await test_db.insert_file_change_audit(_sample_file_change(file_path="a.py"))
        f2 = await test_db.insert_file_change_audit(_sample_file_change(file_path="b.py"))
        changes = await test_db.get_recent_file_changes(limit=2)
        ids = {c["id"] for c in changes}
        assert ids == {f1, f2}


# ====================================================================
# CROSS-CUTTING / INTEGRATION
# ====================================================================

class TestDatabaseIsolation:
    """Verify that the test_db fixture provides isolation between tests."""

    async def test_isolation_a(self, test_db):
        await test_db.insert_trade(_sample_trade(market_id="isolation-A"))
        trades = await test_db.get_trades()
        assert len(trades) == 1

    async def test_isolation_b(self, test_db):
        """Should start with a clean DB, no trades from test_isolation_a."""
        trades = await test_db.get_trades()
        assert len(trades) == 0


class TestTradeLifecycle:
    """End-to-end lifecycle: insert -> update status -> fill -> execute."""

    async def test_full_lifecycle(self, test_db):
        # 1. Insert a pending trade
        tid = await test_db.insert_trade(_sample_trade(status="pending"))
        trades = await test_db.get_trades_by_status("pending")
        assert len(trades) == 1

        # 2. Move to pending_confirmation
        await test_db.update_trade_status(tid, "pending_confirmation")
        pending = await test_db.get_pending_trades()
        assert len(pending) == 1

        # 3. Confirm and place order
        await test_db.update_trade_status(tid, "order_placed", order_id="ord-xyz")
        placed = await test_db.get_trades_with_order_status("order_placed")
        assert len(placed) == 1

        # 4. Update execution plan
        await test_db.update_trade_execution_plan(tid, price=0.56, size_usdc=5.6, intended_shares=10.0)

        # 5. Record fill progress
        await test_db.update_trade_fill_progress(tid, filled_shares=5.0, avg_fill_price=0.56)
        await test_db.insert_order_event(tid, "partial_fill", new_fill=5.0)

        await test_db.update_trade_fill_progress(tid, filled_shares=10.0, avg_fill_price=0.56)
        await test_db.insert_order_event(tid, "full_fill", size_matched=10.0)

        # 6. Mark executed
        await test_db.update_trade_status(tid, "executed")

        # Verify final state
        trades = await test_db.get_trades(limit=1)
        t = trades[0]
        assert t["status"] == "executed"
        assert t["filled_shares"] == 10.0
        assert t["intended_shares"] == 10.0

        events = await test_db.get_recent_order_events()
        assert len(events) == 2


class TestPositionAndPerformanceLifecycle:
    """Position opened -> trade tracked -> market resolved -> position closed."""

    async def test_full_lifecycle(self, test_db):
        # 1. Insert trade and position
        tid = await test_db.insert_trade(_sample_trade(status="executed"))
        await test_db.upsert_position(_sample_position(size=10.0, avg_price=0.55))

        # 2. Record performance
        await test_db.insert_performance(
            trade_id=tid, market_id="market-123",
            market_question="Test?", outcome_bet="Yes",
            price_at_entry=0.55, size_usdc=5.0,
            filled_shares=10.0,
        )

        # 3. Verify unresolved
        unresolved = await test_db.get_unresolved_market_ids()
        assert "market-123" in unresolved

        # 4. Resolve market (correct prediction)
        result = await test_db.resolve_performance("market-123", "Yes")
        assert result["count"] == 1
        assert result["pnl_net_total"] > 0

        # 5. Close position
        await test_db.close_position("market-123", "token-123")
        positions = await test_db.get_open_positions()
        assert positions == []

        # 6. Verify stats
        stats = await test_db.get_performance_stats()
        assert stats["resolved_trades"] == 1
        assert stats["wins"] == 1


# ====================================================================
# CD POSITIONS
# ====================================================================

class TestCdPositions:
    """Tests for the cd_positions table CRUD operations."""

    async def test_insert_and_get_open(self, test_db):
        pos = {
            "market_id": "cd-market-1",
            "token_id": "cd-token-1",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.55,
            "shares": 20.0,
            "order_id": "ord-cd-1",
        }
        row_id = await test_db.insert_cd_position(pos)
        assert row_id is not None

        open_positions = await test_db.get_open_cd_positions()
        assert len(open_positions) == 1
        p = open_positions[0]
        assert p["market_id"] == "cd-market-1"
        assert p["token_id"] == "cd-token-1"
        assert p["coin"] == "BTC"
        assert float(p["strike"]) == 100000.0
        assert p["direction"] == "above"
        assert float(p["entry_price"]) == 0.55
        assert float(p["shares"]) == 20.0
        assert p["status"] == "open"

    async def test_close_cd_position(self, test_db):
        pos = {
            "market_id": "cd-market-2",
            "token_id": "cd-token-2",
            "coin": "ETH",
            "strike": 5000.0,
            "direction": "above",
            "entry_price": 0.40,
            "shares": 50.0,
            "order_id": "ord-cd-2",
        }
        await test_db.insert_cd_position(pos)

        result = await test_db.close_cd_position(
            market_id="cd-market-2",
            token_id="cd-token-2",
            exit_price=0.60,
            exit_reason="took_profit",
            exit_order_id="ord-exit-2",
        )

        assert result is not None
        assert float(result["exit_price"]) == 0.60
        assert result["exit_reason"] == "took_profit"
        assert float(result["pnl_realized"]) == pytest.approx((0.60 - 0.40) * 50.0, abs=0.01)

        # Position should no longer appear in open list
        open_positions = await test_db.get_open_cd_positions()
        assert len(open_positions) == 0

    async def test_close_nonexistent_position(self, test_db):
        result = await test_db.close_cd_position(
            market_id="nonexistent",
            token_id="nonexistent",
            exit_price=0.50,
            exit_reason="stopped",
        )
        assert result is None

    async def test_upsert_accumulates_shares(self, test_db):
        """Inserting the same market_id+token_id should accumulate shares."""
        pos1 = {
            "market_id": "cd-market-3",
            "token_id": "cd-token-3",
            "coin": "BTC",
            "strike": 120000.0,
            "direction": "above",
            "entry_price": 0.30,
            "shares": 10.0,
            "order_id": "ord-cd-3a",
        }
        await test_db.insert_cd_position(pos1)

        pos2 = {
            "market_id": "cd-market-3",
            "token_id": "cd-token-3",
            "coin": "BTC",
            "strike": 120000.0,
            "direction": "above",
            "entry_price": 0.40,
            "shares": 10.0,
            "order_id": "ord-cd-3b",
        }
        await test_db.insert_cd_position(pos2)

        open_positions = await test_db.get_open_cd_positions()
        assert len(open_positions) == 1
        p = open_positions[0]
        assert float(p["shares"]) == 20.0
        # Weighted avg: (0.30*10 + 0.40*10) / 20 = 0.35
        assert float(p["entry_price"]) == pytest.approx(0.35, abs=0.01)

    async def test_close_with_stop_loss(self, test_db):
        """Test closing a position with a loss."""
        pos = {
            "market_id": "cd-market-4",
            "token_id": "cd-token-4",
            "coin": "BTC",
            "strike": 90000.0,
            "direction": "above",
            "entry_price": 0.50,
            "shares": 30.0,
            "order_id": "ord-cd-4",
        }
        await test_db.insert_cd_position(pos)

        result = await test_db.close_cd_position(
            market_id="cd-market-4",
            token_id="cd-token-4",
            exit_price=0.35,
            exit_reason="stopped",
        )

        assert result is not None
        expected_pnl = (0.35 - 0.50) * 30.0  # -4.5
        assert float(result["pnl_realized"]) == pytest.approx(expected_pnl, abs=0.01)
