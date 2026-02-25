"""Tests for monitor modules: RiskManager, PortfolioManager, PerformanceTracker.

Note: pytest-asyncio is not available in this environment. All async tests
use a helper ``run`` function that invokes ``asyncio.run()`` inside
synchronous test methods.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════
# RISK MANAGER
# ═══════════════════════════════════════════════════════════════════════

class TestRiskManager:
    """Tests for monitor.risk.RiskManager."""

    @pytest.fixture
    def risk_manager(self, trading_config, mm_config):
        from monitor.risk import RiskManager
        return RiskManager(trading_config, mm_config)

    # ------------------------------------------------------------------
    # check_stop_loss
    # ------------------------------------------------------------------
    def test_check_stop_loss_triggers(self, risk_manager):
        # stop_loss_percent=20, loss=25 on portfolio=100 -> 25% >= 20%
        triggered = run(risk_manager.check_stop_loss(-25.0, 100.0))
        assert triggered is True
        assert risk_manager.is_paused is True

    def test_check_stop_loss_does_not_trigger(self, risk_manager):
        triggered = run(risk_manager.check_stop_loss(-5.0, 100.0))
        assert triggered is False
        assert risk_manager.is_paused is False

    def test_check_stop_loss_positive_pnl(self, risk_manager):
        triggered = run(risk_manager.check_stop_loss(10.0, 100.0))
        assert triggered is False
        assert risk_manager.is_paused is False

    def test_check_stop_loss_zero_portfolio(self, risk_manager):
        triggered = run(risk_manager.check_stop_loss(-10.0, 0.0))
        assert triggered is False

    def test_check_stop_loss_at_boundary(self, risk_manager):
        # Exactly 20% loss should trigger (>= check)
        triggered = run(risk_manager.check_stop_loss(-20.0, 100.0))
        assert triggered is True
        assert risk_manager.is_paused is True

    # ------------------------------------------------------------------
    # check_drawdown_stop_loss
    # ------------------------------------------------------------------
    @patch("monitor.risk.get_high_water_mark", new_callable=AsyncMock, return_value={
        "peak_value": 100.0, "current_value": 70.0, "max_drawdown_pct": 30.0,
    })
    @patch("monitor.risk.update_high_water_mark", new_callable=AsyncMock)
    def test_drawdown_stop_loss_triggers(
        self, mock_update_hwm, mock_get_hwm, risk_manager,
    ):
        # drawdown_stop_loss_percent=25, drawdown = (100-70)/100 = 30% >= 25%
        triggered, dd_pct = run(risk_manager.check_drawdown_stop_loss(70.0))
        assert triggered is True
        assert dd_pct == pytest.approx(30.0)
        assert risk_manager.is_paused is True

    @patch("monitor.risk.get_high_water_mark", new_callable=AsyncMock, return_value={
        "peak_value": 100.0, "current_value": 90.0, "max_drawdown_pct": 10.0,
    })
    @patch("monitor.risk.update_high_water_mark", new_callable=AsyncMock)
    def test_drawdown_stop_loss_does_not_trigger(
        self, mock_update_hwm, mock_get_hwm, risk_manager,
    ):
        # drawdown = (100-90)/100 = 10% < 25%
        triggered, dd_pct = run(risk_manager.check_drawdown_stop_loss(90.0))
        assert triggered is False
        assert dd_pct == pytest.approx(10.0)
        assert risk_manager.is_paused is False

    # ------------------------------------------------------------------
    # resume_trading and is_paused
    # ------------------------------------------------------------------
    def test_resume_trading(self, risk_manager):
        risk_manager._paused = True
        risk_manager.resume_trading()
        assert risk_manager.is_paused is False

    def test_is_paused_property(self, risk_manager):
        assert risk_manager.is_paused is False
        risk_manager.is_paused = True
        assert risk_manager.is_paused is True
        risk_manager.is_paused = False
        assert risk_manager.is_paused is False

    # ------------------------------------------------------------------
    # validate_mm_quote
    # ------------------------------------------------------------------
    def test_validate_mm_quote_valid(self, risk_manager):
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.50, ask=0.56, mid=0.53, max_delta=5.0
        )
        assert ok is True
        assert reason == "OK"

    def test_validate_mm_quote_paused(self, risk_manager):
        risk_manager._paused = True
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.50, ask=0.56, mid=0.53, max_delta=5.0
        )
        assert ok is False
        assert "paused" in reason.lower()

    def test_validate_mm_quote_bid_gte_ask(self, risk_manager):
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.56, ask=0.50, mid=0.53, max_delta=5.0
        )
        assert ok is False
        assert "bid" in reason.lower() and "ask" in reason.lower()

    def test_validate_mm_quote_bid_equals_ask(self, risk_manager):
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.50, ask=0.50, mid=0.50, max_delta=5.0
        )
        assert ok is False
        assert "bid" in reason.lower()

    def test_validate_mm_quote_out_of_range_low(self, risk_manager):
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.005, ask=0.05, mid=0.03, max_delta=10.0
        )
        assert ok is False
        assert "range" in reason.lower()

    def test_validate_mm_quote_out_of_range_high(self, risk_manager):
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.95, ask=0.995, mid=0.97, max_delta=10.0
        )
        assert ok is False
        assert "range" in reason.lower()

    def test_validate_mm_quote_spread_too_wide(self, risk_manager):
        # spread = (0.55-0.40)*100 = 15pts, max = 2*5+1 = 11pts
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.40, ask=0.55, mid=0.50, max_delta=5.0
        )
        assert ok is False
        assert "wide" in reason.lower()

    def test_validate_mm_quote_per_side_hard_cap(self, risk_manager):
        # bid_delta=15pts, hard_cap=2*5=10pts (but spread catches it first)
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.35, ask=0.60, mid=0.50, max_delta=5.0
        )
        assert ok is False
        assert "wide" in reason.lower()

    def test_validate_mm_quote_skew_within_limits(self, risk_manager):
        # Asymmetric skew: bid_delta=1pt, ask_delta=3pts, total spread=4pts
        # max_spread = 2*5+1 = 11pts -> passes; hard_cap = 10pts -> passes
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.49, ask=0.53, mid=0.50, max_delta=5.0
        )
        assert ok is True

    def test_validate_mm_quote_spread_too_tight(self, risk_manager):
        # bid=0.500, ask=0.505 -> spread=0.5pts < 1.0pts minimum
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.500, ask=0.505, mid=0.5025, max_delta=5.0
        )
        assert ok is False
        assert "spread" in reason.lower()

    # ------------------------------------------------------------------
    # check_intraday_dd (uses HWM drawdown with MM-specific thresholds)
    # ------------------------------------------------------------------
    @patch("monitor.risk.get_high_water_mark")
    def test_intraday_dd_ok(self, mock_hwm, risk_manager):
        # peak=100, current=98 -> DD=2% < reduce(5%)
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(98.0))
        assert result == "ok"

    @patch("monitor.risk.get_high_water_mark")
    def test_intraday_dd_reduce(self, mock_hwm, risk_manager):
        # mm_dd_reduce_pct=5.0, peak=100, current=94 -> DD=6% >= 5%
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(94.0))
        assert result == "reduce"

    @patch("monitor.risk.get_high_water_mark")
    def test_intraday_dd_kill(self, mock_hwm, risk_manager):
        # mm_dd_kill_pct=10.0, peak=100, current=88 -> DD=12% >= 10%
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(88.0))
        assert result == "kill"
        assert risk_manager.is_paused is True

    @patch("monitor.risk.get_high_water_mark")
    def test_intraday_dd_above_peak(self, mock_hwm, risk_manager):
        # current > peak -> DD=0% -> ok
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(105.0))
        assert result == "ok"

    @patch("monitor.risk.get_high_water_mark")
    def test_intraday_dd_no_mm_config(self, mock_hwm, trading_config):
        from monitor.risk import RiskManager
        rm = RiskManager(trading_config, mm_config=None)
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(rm.check_intraday_dd(98.0))
        assert result == "ok"

    # ------------------------------------------------------------------
    # validate_mm_quote max_spread cap (5A-7)
    # ------------------------------------------------------------------
    def test_validate_mm_quote_max_spread_cap(self, risk_manager):
        """max_spread should be capped by mm_max_spread_pts config."""
        # With max_delta=10, old formula: 2*10+1=21. With cap=12, should use 12.
        risk_manager.mm_config.mm_max_spread_pts = 12.0
        # spread = (0.56-0.44)*100 = 12pts, exactly at cap -> should pass
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.44, ask=0.56, mid=0.50, max_delta=10.0
        )
        assert ok is True
        # spread = 13pts > cap=12 -> should fail
        ok, reason = risk_manager.validate_mm_quote(
            bid=0.435, ask=0.565, mid=0.50, max_delta=10.0
        )
        assert ok is False
        assert "wide" in reason.lower()

    # ------------------------------------------------------------------
    # risk_mode property (5A-10)
    # ------------------------------------------------------------------
    @patch("monitor.risk.get_high_water_mark")
    def test_risk_mode_ok(self, mock_hwm, risk_manager):
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(98.0))
        assert result == "ok"
        assert risk_manager.risk_mode == "ok"

    @patch("monitor.risk.get_high_water_mark")
    def test_risk_mode_reduce(self, mock_hwm, risk_manager):
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(94.0))
        assert result == "reduce"
        assert risk_manager.risk_mode == "reduce"

    @patch("monitor.risk.get_high_water_mark")
    def test_risk_mode_kill(self, mock_hwm, risk_manager):
        mock_hwm.return_value = {"peak_value": 100.0}
        result = run(risk_manager.check_intraday_dd(88.0))
        assert result == "kill"
        assert risk_manager.risk_mode == "kill"

    # ------------------------------------------------------------------
    # check_inventory_risk
    # ------------------------------------------------------------------
    def test_inventory_risk_ok(self, risk_manager):
        ok, reason = risk_manager.check_inventory_risk(5.0, 10.0)
        assert ok is True
        assert reason == "OK"

    def test_inventory_risk_warning(self, risk_manager):
        # 9.5 / 10 = 95% -> warning
        ok, reason = risk_manager.check_inventory_risk(9.5, 10.0)
        assert ok is True
        assert "WARNING" in reason

    def test_inventory_risk_exceeds_max(self, risk_manager):
        ok, reason = risk_manager.check_inventory_risk(11.0, 10.0)
        assert ok is False
        assert "exceeds" in reason.lower()

    def test_inventory_risk_negative_inventory(self, risk_manager):
        # Negative inventory with abs > max
        ok, reason = risk_manager.check_inventory_risk(-11.0, 10.0)
        assert ok is False
        assert "exceeds" in reason.lower()

    def test_inventory_risk_zero_max(self, risk_manager):
        # max_inventory=0, net_inventory=0 -> abs(0) > 0 is False
        ok, reason = risk_manager.check_inventory_risk(0.0, 0.0)
        assert ok is True

    # ------------------------------------------------------------------
    # validate_cd_trade
    # ------------------------------------------------------------------
    @patch("monitor.risk.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    def test_validate_cd_trade_passes(self, mock_daily, risk_manager):
        trade = {"size_usdc": 5.0, "edge_pts": 8.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is True
        assert reason == "OK"

    def test_validate_cd_trade_paused(self, risk_manager):
        risk_manager._paused = True
        trade = {"size_usdc": 5.0, "edge_pts": 8.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is False
        assert "paused" in reason.lower()

    def test_validate_cd_trade_zero_size(self, risk_manager):
        trade = {"size_usdc": 0.0, "edge_pts": 8.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is False
        assert "positive" in reason.lower()

    def test_validate_cd_trade_negative_size(self, risk_manager):
        trade = {"size_usdc": -5.0, "edge_pts": 8.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is False
        assert "positive" in reason.lower()

    def test_validate_cd_trade_insufficient_funds(self, risk_manager):
        trade = {"size_usdc": 50.0, "edge_pts": 8.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 10.0))
        assert ok is False
        assert "insufficient" in reason.lower()

    @patch("monitor.risk.get_daily_traded", new_callable=AsyncMock, return_value=40.0)
    def test_validate_cd_trade_daily_limit(self, mock_daily, risk_manager):
        # available=100, daily limit = 100*0.5=50, daily_traded=40, size=15 -> 55 > 50
        trade = {"size_usdc": 15.0, "edge_pts": 8.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is False
        assert "daily limit" in reason.lower()

    @patch("monitor.risk.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    def test_validate_cd_trade_edge_below_minimum(self, mock_daily, risk_manager):
        # Edge minimum is 5.0pts
        trade = {"size_usdc": 5.0, "edge_pts": 3.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is False
        assert "edge" in reason.lower()

    @patch("monitor.risk.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    def test_validate_cd_trade_no_edge(self, mock_daily, risk_manager):
        # edge_pts missing -> defaults to 0
        trade = {"size_usdc": 5.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is False
        assert "edge" in reason.lower()

    @patch("monitor.risk.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    def test_validate_cd_trade_edge_at_boundary(self, mock_daily, risk_manager):
        # edge_pts=5.0 -> exactly at minimum, should fail (< 5.0 check)
        trade = {"size_usdc": 5.0, "edge_pts": 5.0}
        ok, reason = run(risk_manager.validate_cd_trade(trade, 100.0))
        assert ok is True


# ═══════════════════════════════════════════════════════════════════════
# PORTFOLIO MANAGER
# ═══════════════════════════════════════════════════════════════════════

class TestPortfolioManager:
    """Tests for monitor.portfolio.PortfolioManager."""

    @pytest.fixture
    def portfolio_manager(self, mock_pm_client):
        from monitor.portfolio import PortfolioManager
        return PortfolioManager(mock_pm_client)

    # ------------------------------------------------------------------
    # 1. get_portfolio_state returns correct structure
    # ------------------------------------------------------------------
    @patch("monitor.portfolio.get_daily_traded", new_callable=AsyncMock, return_value=5.0)
    @patch("monitor.portfolio.get_trades", new_callable=AsyncMock, return_value=[])
    @patch("monitor.portfolio.get_open_positions", new_callable=AsyncMock, return_value=[
        {
            "token_id": "token-yes-123",
            "market_question": "Will BTC reach $100k?",
            "outcome": "Yes",
            "size": 10.0,
            "avg_price": 0.50,
            "market_id": "market-abc-123",
            "category": "crypto",
        },
    ])
    def test_portfolio_state_structure(
        self, mock_positions, mock_trades, mock_daily, portfolio_manager, mock_pm_client,
    ):
        # get_midpoint returns 0.55, get_onchain_balance returns 50.0
        state = run(portfolio_manager.get_portfolio_state())

        assert "available_usdc" in state
        assert "positions_count" in state
        assert "positions" in state
        assert "positions_summary" in state
        assert "daily_pnl" in state
        assert "daily_traded" in state
        assert "total_invested" in state
        assert "recent_trades" in state
        assert "portfolio_value" in state
        assert "onchain_balance" in state

        assert state["positions_count"] == 1
        assert state["daily_traded"] == 5.0
        # total_invested = 10 * 0.50 = 5.0
        assert state["total_invested"] == pytest.approx(5.0)
        # pnl = (0.55 - 0.50) * 10 = 0.5
        assert state["daily_pnl"] == pytest.approx(0.5)
        # available = onchain_balance = 50.0
        assert state["available_usdc"] == pytest.approx(50.0)
        # portfolio_value = available + total_invested + pnl = 50 + 5 + 0.5 = 55.5
        assert state["portfolio_value"] == pytest.approx(55.5)
        assert state["onchain_balance"] == 50.0
        assert "BTC" in state["positions_summary"]

    # ------------------------------------------------------------------
    # 2. Handles no positions
    # ------------------------------------------------------------------
    @patch("monitor.portfolio.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    @patch("monitor.portfolio.get_trades", new_callable=AsyncMock, return_value=[])
    @patch("monitor.portfolio.get_open_positions", new_callable=AsyncMock, return_value=[])
    def test_no_positions(
        self, mock_positions, mock_trades, mock_daily, portfolio_manager,
    ):
        state = run(portfolio_manager.get_portfolio_state())
        assert state["positions_count"] == 0
        assert state["total_invested"] == 0.0
        assert state["daily_pnl"] == 0.0
        assert state["positions_summary"] == "None"
        # portfolio_value = available(50) + invested(0) + pnl(0) = 50
        assert state["portfolio_value"] == pytest.approx(50.0)

    # ------------------------------------------------------------------
    # 3. On-chain balance is source of truth for available_usdc
    # ------------------------------------------------------------------
    @patch("monitor.portfolio.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    @patch("monitor.portfolio.get_trades", new_callable=AsyncMock, return_value=[])
    @patch("monitor.portfolio.get_open_positions", new_callable=AsyncMock, return_value=[])
    def test_onchain_balance_is_available(
        self, mock_positions, mock_trades, mock_daily, portfolio_manager, mock_pm_client,
    ):
        mock_pm_client.get_onchain_balance = MagicMock(return_value=75.0)
        state = run(portfolio_manager.get_portfolio_state())
        assert state["available_usdc"] == pytest.approx(75.0)
        assert state["onchain_balance"] == 75.0

    @patch("monitor.portfolio.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    @patch("monitor.portfolio.get_trades", new_callable=AsyncMock, return_value=[])
    @patch("monitor.portfolio.get_open_positions", new_callable=AsyncMock, return_value=[])
    def test_onchain_balance_none_no_cache(
        self, mock_positions, mock_trades, mock_daily, portfolio_manager, mock_pm_client,
    ):
        # onchain returns None, no cached value -> available = 0.0
        mock_pm_client.get_onchain_balance = MagicMock(return_value=None)
        portfolio_manager._last_onchain_balance = None
        state = run(portfolio_manager.get_portfolio_state())
        assert state["available_usdc"] == pytest.approx(0.0)
        assert state["onchain_balance"] is None

    @patch("monitor.portfolio.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    @patch("monitor.portfolio.get_trades", new_callable=AsyncMock, return_value=[])
    @patch("monitor.portfolio.get_open_positions", new_callable=AsyncMock, return_value=[])
    def test_onchain_balance_none_uses_cache(
        self, mock_positions, mock_trades, mock_daily, portfolio_manager, mock_pm_client,
    ):
        # onchain returns None, but cached value exists -> uses cache
        mock_pm_client.get_onchain_balance = MagicMock(return_value=None)
        portfolio_manager._last_onchain_balance = 60.0
        state = run(portfolio_manager.get_portfolio_state())
        assert state["available_usdc"] == pytest.approx(60.0)
        assert state["onchain_balance"] == 60.0

    @patch("monitor.portfolio.get_daily_traded", new_callable=AsyncMock, return_value=0.0)
    @patch("monitor.portfolio.get_trades", new_callable=AsyncMock, return_value=[])
    @patch("monitor.portfolio.get_open_positions", new_callable=AsyncMock, return_value=[
        {
            "token_id": "token-yes-123",
            "market_question": "Test market",
            "outcome": "Yes",
            "size": 10.0,
            "avg_price": 0.50,
            "market_id": "m1",
            "category": "crypto",
        },
        {
            "token_id": "token-no-456",
            "market_question": "Another market",
            "outcome": "No",
            "size": 5.0,
            "avg_price": 0.40,
            "market_id": "m2",
            "category": "politics",
        },
    ])
    def test_multiple_positions_pnl(
        self, mock_positions, mock_trades, mock_daily, portfolio_manager, mock_pm_client,
    ):
        """PnL sums across all positions."""
        # midpoint=0.55 for all (from mock_pm_client fixture)
        # pos1 pnl = (0.55-0.50)*10 = 0.5
        # pos2 pnl = (0.55-0.40)*5 = 0.75
        # total pnl = 1.25
        state = run(portfolio_manager.get_portfolio_state())
        assert state["daily_pnl"] == pytest.approx(1.25)
        assert state["positions_count"] == 2
        # total_invested = 10*0.50 + 5*0.40 = 5.0 + 2.0 = 7.0
        assert state["total_invested"] == pytest.approx(7.0)
        # portfolio_value = available(50) + invested(7) + pnl(1.25) = 58.25
        assert state["portfolio_value"] == pytest.approx(58.25)


# ═══════════════════════════════════════════════════════════════════════
# PERFORMANCE TRACKER
# ═══════════════════════════════════════════════════════════════════════

class TestPerformanceTracker:
    """Tests for monitor.performance.PerformanceTracker."""

    @pytest.fixture
    def perf_tracker(self, mock_pm_client):
        from monitor.performance import PerformanceTracker
        return PerformanceTracker(mock_pm_client)

    # ------------------------------------------------------------------
    # 1. check_resolutions with no unresolved markets
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_unresolved_market_ids", new_callable=AsyncMock, return_value=[])
    def test_check_resolutions_empty(self, mock_unresolved, perf_tracker):
        result = run(perf_tracker.check_resolutions())
        assert result == []

    # ------------------------------------------------------------------
    # 2. check_resolutions resolves market and closes positions
    # ------------------------------------------------------------------
    @patch("monitor.performance.update_daily_pnl", new_callable=AsyncMock)
    @patch("monitor.performance.close_position", new_callable=AsyncMock)
    @patch("monitor.performance.get_open_positions", new_callable=AsyncMock, return_value=[
        {"market_id": "market-abc-123", "token_id": "token-yes-123"},
        {"market_id": "other-market", "token_id": "token-other"},
    ])
    @patch("monitor.performance.resolve_performance", new_callable=AsyncMock, return_value={
        "count": 2, "pnl_net_total": 3.50,
    })
    @patch("monitor.performance.get_unresolved_market_ids", new_callable=AsyncMock, return_value=["market-abc-123"])
    def test_check_resolutions_resolves_market(
        self, mock_unresolved, mock_resolve, mock_positions,
        mock_close, mock_update_pnl, perf_tracker, mock_pm_client,
    ):
        mock_pm_client.check_market_resolved = MagicMock(return_value={
            "resolved": True, "outcome": "Yes",
        })

        result = run(perf_tracker.check_resolutions())
        assert len(result) == 1
        assert result[0]["market_id"] == "market-abc-123"
        assert result[0]["outcome"] == "Yes"
        assert result[0]["trades_resolved"] == 2

        mock_close.assert_called_once_with("market-abc-123", "token-yes-123")
        mock_update_pnl.assert_called_once_with(3.50)

    @patch("monitor.performance.update_daily_pnl", new_callable=AsyncMock)
    @patch("monitor.performance.close_position", new_callable=AsyncMock)
    @patch("monitor.performance.get_open_positions", new_callable=AsyncMock, return_value=[])
    @patch("monitor.performance.resolve_performance", new_callable=AsyncMock, return_value={
        "count": 0, "pnl_net_total": 0.0,
    })
    @patch("monitor.performance.get_unresolved_market_ids", new_callable=AsyncMock, return_value=["market-xyz"])
    def test_check_resolutions_zero_count_not_appended(
        self, mock_unresolved, mock_resolve, mock_positions,
        mock_close, mock_update_pnl, perf_tracker, mock_pm_client,
    ):
        """If resolve_performance returns count=0, market is not in resolved list."""
        mock_pm_client.check_market_resolved = MagicMock(return_value={
            "resolved": True, "outcome": "No",
        })
        result = run(perf_tracker.check_resolutions())
        assert result == []
        mock_close.assert_not_called()
        mock_update_pnl.assert_not_called()

    @patch("monitor.performance.get_unresolved_market_ids", new_callable=AsyncMock, return_value=["market-xyz"])
    def test_check_resolutions_not_resolved_yet(
        self, mock_unresolved, perf_tracker, mock_pm_client,
    ):
        """Market not yet resolved -- should return empty."""
        mock_pm_client.check_market_resolved = MagicMock(return_value=None)
        result = run(perf_tracker.check_resolutions())
        assert result == []

    # ------------------------------------------------------------------
    # 3. get_stats returns dict
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_performance_stats", new_callable=AsyncMock, return_value={
        "total_trades": 10, "resolved_trades": 5, "wins": 3, "losses": 2,
    })
    def test_get_stats(self, mock_stats, perf_tracker):
        stats = run(perf_tracker.get_stats())
        assert isinstance(stats, dict)
        assert stats["total_trades"] == 10

    # ------------------------------------------------------------------
    # 4. get_calibration_report returns None with < 10 records
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_calibration_data", new_callable=AsyncMock, return_value=[
        {"confidence": 0.8, "edge": 0.15, "outcome_bet": "yes", "was_correct": 1, "pnl_realized": 1.0}
        for _ in range(9)
    ])
    def test_calibration_report_insufficient_data(self, mock_data, perf_tracker):
        report = run(perf_tracker.get_calibration_report())
        assert report is None

    # ------------------------------------------------------------------
    # 5. get_calibration_report detects overconfidence bias
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_calibration_data", new_callable=AsyncMock)
    def test_calibration_detects_overconfidence(self, mock_data, perf_tracker):
        data = []
        # 6 high-confidence trades (conf >= 0.7) with low hit rate (1/6)
        for i in range(6):
            data.append({
                "confidence": 0.85,
                "edge": 0.15,
                "outcome_bet": "yes",
                "was_correct": 1 if i == 0 else 0,
                "pnl_realized": 2.0 if i == 0 else -5.0,
            })
        # 6 low-confidence trades (conf < 0.4) with high hit rate (5/6)
        for i in range(6):
            data.append({
                "confidence": 0.3,
                "edge": 0.10,
                "outcome_bet": "no",
                "was_correct": 0 if i == 0 else 1,
                "pnl_realized": -3.0 if i == 0 else 2.0,
            })
        mock_data.return_value = data

        report = run(perf_tracker.get_calibration_report())
        assert report is not None
        assert report["sample_size"] == 12
        assert any("OVERCONFIDENCE" in b for b in report["biases_detected"])

    # ------------------------------------------------------------------
    # 6. get_calibration_report detects yes bias
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_calibration_data", new_callable=AsyncMock)
    def test_calibration_detects_yes_bias(self, mock_data, perf_tracker):
        data = []
        # 12 Yes trades
        for _ in range(12):
            data.append({
                "confidence": 0.6,
                "edge": 0.10,
                "outcome_bet": "yes",
                "was_correct": 1,
                "pnl_realized": 1.0,
            })
        # 5 No trades (12 > 5*2=10)
        for _ in range(5):
            data.append({
                "confidence": 0.6,
                "edge": 0.10,
                "outcome_bet": "no",
                "was_correct": 1,
                "pnl_realized": 1.0,
            })
        mock_data.return_value = data

        report = run(perf_tracker.get_calibration_report())
        assert report is not None
        assert any("YES BIAS" in b for b in report["biases_detected"])

    # ------------------------------------------------------------------
    # 7. get_calibration_report detects edge illusion
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_calibration_data", new_callable=AsyncMock)
    def test_calibration_detects_edge_illusion(self, mock_data, perf_tracker):
        data = []
        # 6 high-edge trades (edge >= 0.15) with low hit rate (1/6)
        for i in range(6):
            data.append({
                "confidence": 0.6,
                "edge": 0.20,
                "outcome_bet": "yes",
                "was_correct": 1 if i == 0 else 0,
                "pnl_realized": 2.0 if i == 0 else -5.0,
            })
        # 6 low-edge trades (edge < 0.15) with high hit rate (5/6)
        for i in range(6):
            data.append({
                "confidence": 0.6,
                "edge": 0.08,
                "outcome_bet": "no",
                "was_correct": 0 if i == 0 else 1,
                "pnl_realized": -3.0 if i == 0 else 2.0,
            })
        mock_data.return_value = data

        report = run(perf_tracker.get_calibration_report())
        assert report is not None
        assert any("EDGE ILLUSION" in b for b in report["biases_detected"])

    # ------------------------------------------------------------------
    # No biases when data is balanced
    # ------------------------------------------------------------------
    @patch("monitor.performance.get_calibration_data", new_callable=AsyncMock)
    def test_calibration_no_bias(self, mock_data, perf_tracker):
        data = []
        # 6 high-conf with good hit rate
        for i in range(6):
            data.append({
                "confidence": 0.8,
                "edge": 0.20,
                "outcome_bet": "yes",
                "was_correct": 1,
                "pnl_realized": 2.0,
            })
        # 6 low-conf with worse hit rate
        for i in range(6):
            data.append({
                "confidence": 0.3,
                "edge": 0.08,
                "outcome_bet": "no",
                "was_correct": 0 if i < 3 else 1,
                "pnl_realized": -3.0 if i < 3 else 2.0,
            })
        mock_data.return_value = data

        report = run(perf_tracker.get_calibration_report())
        assert report is not None
        # high_conf hit_rate=1.0, low_conf hit_rate=0.5 -> no OVERCONFIDENCE
        assert "OVERCONFIDENCE" not in str(report["biases_detected"])
        # yes=6, no=6 -> no YES BIAS (6 is not > 6*2=12)
        assert "YES BIAS" not in str(report["biases_detected"])
        # high_edge hit_rate=1.0, low_edge hit_rate=0.5 -> no EDGE ILLUSION
        assert "EDGE ILLUSION" not in str(report["biases_detected"])

    # ------------------------------------------------------------------
    # 8. format_stats with resolved trades
    # ------------------------------------------------------------------
    def test_format_stats_with_resolved(self, perf_tracker):
        stats = {
            "total_trades": 20,
            "resolved_trades": 15,
            "pending_resolution": 5,
            "wins": 10,
            "losses": 5,
            "hit_rate": 0.667,
            "total_pnl": 12.50,
            "avg_pnl_per_trade": 0.83,
            "best_trade": 5.00,
            "worst_trade": -3.00,
            "total_wagered": 100.0,
            "roi_percent": 12.5,
            "current_streak": 3,
            "streak_type": "win",
        }
        result = perf_tracker.format_stats(stats)
        assert "15 resolved" in result
        assert "20 total" in result
        assert "66.7%" in result
        assert "10W" in result
        assert "5L" in result
        assert "+12.50" in result or "$+12.50" in result
        assert "+12.5%" in result
        assert "3 win" in result
        assert "5 trades" in result  # pending

    # ------------------------------------------------------------------
    # 9. format_stats with no resolved trades
    # ------------------------------------------------------------------
    def test_format_stats_no_resolved(self, perf_tracker):
        stats = {
            "total_trades": 5,
            "resolved_trades": 0,
            "pending_resolution": 5,
        }
        result = perf_tracker.format_stats(stats)
        assert "Total trades: 5" in result
        assert "Pending resolution: 5" in result
        assert "No resolved trades yet" in result

    # ------------------------------------------------------------------
    # Additional format_stats edge cases
    # ------------------------------------------------------------------
    def test_format_stats_negative_pnl(self, perf_tracker):
        stats = {
            "total_trades": 10,
            "resolved_trades": 8,
            "pending_resolution": 2,
            "wins": 3,
            "losses": 5,
            "hit_rate": 0.375,
            "total_pnl": -8.50,
            "avg_pnl_per_trade": -1.06,
            "best_trade": 2.00,
            "worst_trade": -5.00,
            "total_wagered": 50.0,
            "roi_percent": -17.0,
            "current_streak": 2,
            "streak_type": "loss",
        }
        result = perf_tracker.format_stats(stats)
        assert "37.5%" in result
        assert "-8.50" in result or "$-8.50" in result
        assert "2 loss" in result
