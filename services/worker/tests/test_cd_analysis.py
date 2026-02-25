"""Tests for the CD post-trade analysis loop (Feature 3)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestRunCdAnalysis:
    """Tests for run_cd_analysis()."""

    async def test_no_data_returns_none(self, app_config, test_db):
        from strategy.cd_analysis import run_cd_analysis

        result = await run_cd_analysis(app_config)
        assert result is None

    async def test_successful_analysis(self, app_config, test_db):
        from strategy.cd_analysis import run_cd_analysis

        # Insert a signal
        await test_db.insert_cd_signal({
            "market_id": "m-1", "token_id": "t-1", "coin": "BTC",
            "strike": 100000, "expiry_days": 30, "spot_price": 95000,
            "vol_ewma": 0.04, "p_model": 0.65, "p_market": 0.55,
            "edge_pts": 10.0, "action": "trade",
        })

        # Insert and close a position
        await test_db.insert_cd_position({
            "market_id": "m-1", "token_id": "t-1", "coin": "BTC",
            "strike": 100000, "direction": "above",
            "entry_price": 0.55, "shares": 10.0, "order_id": "o-1",
        })
        await test_db.close_cd_position("m-1", "t-1", 0.65, "took_profit")

        claude_response = {
            "accuracy_score": 7,
            "entry_quality_score": 6,
            "exit_quality_score": 8,
            "model_fitness_score": 7,
            "overall_score": 7,
            "parameter_suggestions": {"cd_min_edge_pts": None, "cd_kelly_fraction": 0.20},
            "insights": ["Good TP discipline", "Consider tighter stops"],
            "summary": "Performance correcte, ajuster Kelly fraction.",
        }

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            result = await run_cd_analysis(app_config)

        assert result is not None
        assert result["overall_score"] == 7
        assert result["signals_analyzed"] == 1
        assert result["positions_analyzed"] == 1

        # Check it was stored in DB
        analyses = await test_db.get_recent_cd_analyses(limit=5)
        assert len(analyses) == 1
        assert analyses[0]["overall_score"] == 7

    async def test_claude_failure_returns_none(self, app_config, test_db):
        from strategy.cd_analysis import run_cd_analysis

        await test_db.insert_cd_signal({
            "market_id": "m-1", "token_id": "t-1", "coin": "BTC",
            "strike": 100000, "expiry_days": 30, "spot_price": 95000,
            "vol_ewma": 0.04, "p_model": 0.65, "p_market": 0.55,
            "edge_pts": 10.0, "action": "trade",
        })

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=None):
            result = await run_cd_analysis(app_config)

        assert result is None

    async def test_claude_exception_returns_none(self, app_config, test_db):
        from strategy.cd_analysis import run_cd_analysis

        await test_db.insert_cd_signal({
            "market_id": "m-1", "token_id": "t-1", "coin": "BTC",
            "strike": 100000, "expiry_days": 30, "spot_price": 95000,
            "vol_ewma": 0.04, "p_model": 0.65, "p_market": 0.55,
            "edge_pts": 10.0, "action": "trade",
        })

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, side_effect=Exception("API error")):
            result = await run_cd_analysis(app_config)

        assert result is None


class TestCdAnalysisLoop:
    """Tests for the cd_analysis_loop coroutine."""

    async def test_loop_disabled(self, app_config):
        from strategy.cd_analysis import cd_analysis_loop

        app_config.cd.cd_analysis_enabled = False
        await cd_analysis_loop(app_config)


class TestDbFunctions:
    """Tests for the new DB functions."""

    async def test_insert_and_get_analysis(self, test_db):
        analysis = {
            "analysis_type": "periodic",
            "signals_analyzed": 10,
            "positions_analyzed": 5,
            "accuracy_score": 7.5,
            "entry_quality_score": 6.0,
            "exit_quality_score": 8.0,
            "model_fitness_score": 7.0,
            "overall_score": 7.0,
            "parameter_suggestions": {"cd_min_edge_pts": 6.0},
            "insights": ["insight1"],
            "summary": "Test summary",
            "raw_response": '{"test": true}',
        }
        row_id = await test_db.insert_cd_trade_analysis(analysis)
        assert row_id > 0

        rows = await test_db.get_recent_cd_analyses(limit=5)
        assert len(rows) == 1
        assert rows[0]["overall_score"] == 7.0

    async def test_get_closed_cd_positions(self, test_db):
        # Insert and close a position
        await test_db.insert_cd_position({
            "market_id": "m-1", "token_id": "t-1", "coin": "BTC",
            "strike": 100000, "direction": "above",
            "entry_price": 0.55, "shares": 10.0, "order_id": "o-1",
        })
        await test_db.close_cd_position("m-1", "t-1", 0.65, "took_profit")

        closed = await test_db.get_closed_cd_positions(limit=10)
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "took_profit"
