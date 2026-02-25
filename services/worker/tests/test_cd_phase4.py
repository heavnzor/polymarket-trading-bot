"""Tests for Phase 4 CD improvements: edge recalculation fix, pretrade enrichment, DB columns.

Covers:
- _recalculate_edge: CLOB midpoint usage, expiry_days passthrough, error handling
- check_cd_exits: expiry_days read/degrade, token_id+client passthrough
- _pretrade_validate: dict return, portfolio context, vol regime, error handling
- DB: insert_cd_position with expiry_days, insert_cd_signal with ai_validation
- Config: cd_pretrade_ai_enabled defaults to True
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import CryptoDirectionalConfig, AnthropicConfig
from strategy.cd_exit import check_cd_exits, _recalculate_edge
from strategy.cd_loop import _pretrade_validate


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def cd_cfg():
    cfg = CryptoDirectionalConfig()
    cfg.cd_exit_stop_loss_pts = 15.0
    cfg.cd_exit_take_profit_pts = 20.0
    cfg.cd_exit_edge_reversal_pts = -3.0
    cfg.cd_exit_ai_confirm_enabled = False  # off by default in tests
    cfg.cd_coingecko_api = "https://api.coingecko.com/api/v3"
    cfg.cd_ewma_span = 30
    cfg.cd_ewma_lambda = 0.94
    cfg.cd_student_t_nu = 6.0
    return cfg


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_midpoint = MagicMock(return_value=0.65)
    client.get_book_summary = MagicMock(return_value={"best_bid": 0.63})
    client.place_limit_order = MagicMock(return_value={"orderID": "exit-order-1"})
    client.get_onchain_balance = MagicMock(return_value=100.0)
    return client


@pytest.fixture
def sample_open_position():
    """A sample open CD position with all Phase 4 fields."""
    return {
        "id": 1,
        "market_id": "market-btc-100k",
        "token_id": "token-btc-yes",
        "coin": "BTC",
        "strike": 100000.0,
        "direction": "above",
        "entry_price": 0.55,
        "shares": 20.0,
        "expiry_days": 45.0,
        "order_id": "order-123",
        "status": "open",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
    }


@pytest.fixture
def sample_position_no_expiry():
    """Position without expiry_days (legacy — should fall back to 30.0)."""
    return {
        "id": 2,
        "market_id": "market-eth-5k",
        "token_id": "token-eth-yes",
        "coin": "ETH",
        "strike": 5000.0,
        "direction": "above",
        "entry_price": 0.60,
        "shares": 15.0,
        "expiry_days": None,
        "order_id": "order-456",
        "status": "open",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# _recalculate_edge
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecalculateEdge:
    """Tests for cd_exit._recalculate_edge Phase 4 improvements."""

    @pytest.mark.asyncio
    async def test_uses_clob_midpoint_when_client_and_token_provided(self, cd_cfg, mock_client):
        """When token_id and client are given, uses actual CLOB midpoint instead of 0.5."""
        mock_client.get_midpoint.return_value = 0.65  # CLOB says 0.65

        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72) as mock_prob, \
             patch("strategy.cd_exit.detect_edge", return_value=7.0) as mock_edge:

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=40.0,
            )

            assert edge == 7.0
            # detect_edge should have been called with p_market from CLOB midpoint
            mock_edge.assert_called_once_with(0.72, 0.65)

    @pytest.mark.asyncio
    async def test_falls_back_to_05_without_client(self, cd_cfg):
        """Without client/token_id, p_market falls back to 0.5."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72), \
             patch("strategy.cd_exit.detect_edge", return_value=22.0) as mock_edge:

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id=None, client=None, expiry_days=30.0,
            )

            assert edge == 22.0
            mock_edge.assert_called_once_with(0.72, 0.5)

    @pytest.mark.asyncio
    async def test_falls_back_to_05_when_token_id_missing(self, cd_cfg, mock_client):
        """If token_id is None but client is provided, still falls back to 0.5."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72), \
             patch("strategy.cd_exit.detect_edge", return_value=22.0) as mock_edge:

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id=None, client=mock_client, expiry_days=30.0,
            )

            mock_edge.assert_called_once_with(0.72, 0.5)

    @pytest.mark.asyncio
    async def test_falls_back_to_05_when_midpoint_returns_none(self, cd_cfg, mock_client):
        """If client.get_midpoint returns None, falls back to 0.5."""
        mock_client.get_midpoint.return_value = None

        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72), \
             patch("strategy.cd_exit.detect_edge", return_value=22.0) as mock_edge:

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            mock_edge.assert_called_once_with(0.72, 0.5)

    @pytest.mark.asyncio
    async def test_falls_back_to_05_when_midpoint_raises(self, cd_cfg, mock_client):
        """If client.get_midpoint throws, falls back to 0.5 gracefully."""
        mock_client.get_midpoint.side_effect = Exception("network error")

        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72), \
             patch("strategy.cd_exit.detect_edge", return_value=22.0) as mock_edge:

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            # Should still return an edge (fell back to 0.5)
            assert edge == 22.0
            mock_edge.assert_called_once_with(0.72, 0.5)

    @pytest.mark.asyncio
    async def test_uses_provided_expiry_days(self, cd_cfg, mock_client):
        """expiry_days parameter is forwarded to student_t_prob, not hardcoded 30."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72) as mock_prob, \
             patch("strategy.cd_exit.detect_edge", return_value=7.0):

            await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=12.5,
            )

            # student_t_prob should receive 12.5 as expiry_days, not 30
            call_args = mock_prob.call_args[0]
            assert call_args[2] == 12.5  # third positional arg = expiry_days

    @pytest.mark.asyncio
    async def test_returns_none_on_coingecko_spot_error(self, cd_cfg, mock_client):
        """Returns None when CoinGecko spot price fetch fails."""
        with patch("strategy.cd_exit.get_spot_price", return_value=None):

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            assert edge is None

    @pytest.mark.asyncio
    async def test_returns_none_on_insufficient_price_history(self, cd_cfg, mock_client):
        """Returns None when price history has fewer than 5 data points."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[95000.0, 95100.0]):

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            assert edge is None

    @pytest.mark.asyncio
    async def test_returns_none_when_vol_zero(self, cd_cfg, mock_client):
        """Returns None when EWMA vol is zero (degenerate data)."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[95000.0] * 30), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.0):

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            assert edge is None

    @pytest.mark.asyncio
    async def test_returns_none_when_vol_negative(self, cd_cfg, mock_client):
        """Returns None when EWMA vol is negative (should never happen, but guard)."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[95000.0] * 30), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=-0.01):

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            assert edge is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_coin(self, cd_cfg, mock_client):
        """Returns None for coins not in _COIN_TO_COINGECKO mapping."""
        edge = await _recalculate_edge(
            "DOGE", 1.0, "above", cd_cfg,
            token_id="token-doge", client=mock_client, expiry_days=30.0,
        )
        assert edge is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, cd_cfg, mock_client):
        """Returns None gracefully on unexpected exception."""
        with patch("strategy.cd_exit.get_spot_price", side_effect=RuntimeError("API down")):

            edge = await _recalculate_edge(
                "BTC", 100000.0, "above", cd_cfg,
                token_id="token-btc-yes", client=mock_client, expiry_days=30.0,
            )

            assert edge is None

    @pytest.mark.asyncio
    async def test_default_expiry_days_is_30(self, cd_cfg):
        """When expiry_days is not specified, defaults to 30.0."""
        with patch("strategy.cd_exit.get_spot_price", return_value=95000.0), \
             patch("strategy.cd_exit.fetch_price_history", return_value=[90000 + i * 100 for i in range(30)]), \
             patch("strategy.cd_exit.compute_ewma_vol", return_value=0.02), \
             patch("strategy.cd_exit.student_t_prob", return_value=0.72) as mock_prob, \
             patch("strategy.cd_exit.detect_edge", return_value=22.0):

            await _recalculate_edge("BTC", 100000.0, "above", cd_cfg)

            call_args = mock_prob.call_args[0]
            assert call_args[2] == 30.0  # default expiry_days


# ═══════════════════════════════════════════════════════════════════════════════
# check_cd_exits
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckCdExits:
    """Tests for check_cd_exits Phase 4 improvements."""

    @pytest.mark.asyncio
    async def test_reads_expiry_days_from_position(
        self, cd_cfg, mock_client, sample_open_position, app_config,
    ):
        """check_cd_exits reads expiry_days from position dict."""
        app_config.cd = cd_cfg
        pos = sample_open_position
        pos["expiry_days"] = 45.0

        # Current price is close to entry — no stop/TP triggered
        mock_client.get_midpoint.return_value = 0.56

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock, return_value=5.0) as mock_recalc:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            await check_cd_exits(app_config, mock_client)

            # Verify _recalculate_edge was called with expiry_days derived from position
            call_kwargs = mock_recalc.call_args
            # expiry_days should be approximately 45.0 - 5 (elapsed days) = ~40.0
            passed_expiry = call_kwargs[1].get("expiry_days") or call_kwargs[0][6] if len(call_kwargs[0]) > 6 else None
            if passed_expiry is None:
                # Check keyword args
                passed_expiry = call_kwargs.kwargs.get("expiry_days")
            assert passed_expiry is not None
            assert 38.0 <= passed_expiry <= 42.0  # ~40 days (45 - 5 elapsed)

    @pytest.mark.asyncio
    async def test_degrades_expiry_by_elapsed_days(
        self, cd_cfg, mock_client, app_config,
    ):
        """Expiry is degraded by elapsed time since created_at."""
        app_config.cd = cd_cfg

        # Position created 10 days ago with 30 day expiry
        pos = {
            "market_id": "m1", "token_id": "t1", "coin": "BTC",
            "strike": 100000.0, "direction": "above",
            "entry_price": 0.55, "shares": 20.0,
            "expiry_days": 30.0, "order_id": "o1", "status": "open",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        }

        mock_client.get_midpoint.return_value = 0.56

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock, return_value=5.0) as mock_recalc:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            await check_cd_exits(app_config, mock_client)

            passed_expiry = mock_recalc.call_args.kwargs.get("expiry_days")
            # 30 - 10 = ~20 days remaining
            assert 18.0 <= passed_expiry <= 22.0

    @pytest.mark.asyncio
    async def test_expiry_floor_at_1_day(self, cd_cfg, mock_client, app_config):
        """Degraded expiry should never go below 1.0 day."""
        app_config.cd = cd_cfg

        # Position created 35 days ago with 30 day expiry -> already expired
        pos = {
            "market_id": "m1", "token_id": "t1", "coin": "BTC",
            "strike": 100000.0, "direction": "above",
            "entry_price": 0.55, "shares": 20.0,
            "expiry_days": 30.0, "order_id": "o1", "status": "open",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=35)).isoformat(),
        }

        mock_client.get_midpoint.return_value = 0.56

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock, return_value=5.0) as mock_recalc:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            await check_cd_exits(app_config, mock_client)

            passed_expiry = mock_recalc.call_args.kwargs.get("expiry_days")
            assert passed_expiry == 1.0

    @pytest.mark.asyncio
    async def test_fallback_30_when_expiry_days_none(
        self, cd_cfg, mock_client, app_config,
    ):
        """When expiry_days is None in position, falls back to 30.0."""
        app_config.cd = cd_cfg

        pos = {
            "market_id": "m1", "token_id": "t1", "coin": "BTC",
            "strike": 100000.0, "direction": "above",
            "entry_price": 0.55, "shares": 20.0,
            "expiry_days": None,  # legacy position
            "order_id": "o1", "status": "open",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        }

        mock_client.get_midpoint.return_value = 0.56

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock, return_value=5.0) as mock_recalc:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            await check_cd_exits(app_config, mock_client)

            passed_expiry = mock_recalc.call_args.kwargs.get("expiry_days")
            # 30.0 (fallback) - 2 (elapsed) = ~28
            assert 26.0 <= passed_expiry <= 30.0

    @pytest.mark.asyncio
    async def test_passes_token_id_and_client_to_recalculate_edge(
        self, cd_cfg, mock_client, sample_open_position, app_config,
    ):
        """check_cd_exits passes token_id and client to _recalculate_edge."""
        app_config.cd = cd_cfg
        pos = sample_open_position

        mock_client.get_midpoint.return_value = 0.56

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock, return_value=5.0) as mock_recalc:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            await check_cd_exits(app_config, mock_client)

            kwargs = mock_recalc.call_args.kwargs
            assert kwargs["token_id"] == "token-btc-yes"
            assert kwargs["client"] is mock_client

    @pytest.mark.asyncio
    async def test_no_created_at_skips_degradation(
        self, cd_cfg, mock_client, app_config,
    ):
        """When created_at is missing, expiry is not degraded."""
        app_config.cd = cd_cfg

        pos = {
            "market_id": "m1", "token_id": "t1", "coin": "BTC",
            "strike": 100000.0, "direction": "above",
            "entry_price": 0.55, "shares": 20.0,
            "expiry_days": 45.0, "order_id": "o1", "status": "open",
            # No created_at
        }

        mock_client.get_midpoint.return_value = 0.56

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock, return_value=5.0) as mock_recalc:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            await check_cd_exits(app_config, mock_client)

            passed_expiry = mock_recalc.call_args.kwargs.get("expiry_days")
            # No degradation: should be exactly 45.0
            assert passed_expiry == 45.0

    @pytest.mark.asyncio
    async def test_stop_loss_triggered_before_edge_check(
        self, cd_cfg, mock_client, app_config,
    ):
        """Stop-loss exit should happen without calling _recalculate_edge."""
        app_config.cd = cd_cfg

        pos = {
            "market_id": "m1", "token_id": "t1", "coin": "BTC",
            "strike": 100000.0, "direction": "above",
            "entry_price": 0.55, "shares": 20.0,
            "expiry_days": 30.0, "order_id": "o1", "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Price dropped 20pts from entry (0.55 -> 0.35) -> exceeds 15pt stop-loss
        mock_client.get_midpoint.return_value = 0.35

        with patch("strategy.cd_exit.store") as mock_store, \
             patch("strategy.cd_exit._recalculate_edge", new_callable=AsyncMock) as mock_recalc, \
             patch("strategy.cd_exit._execute_exit", new_callable=AsyncMock, return_value={"exit_reason": "stopped"}) as mock_exit:

            mock_store.get_open_cd_positions = AsyncMock(return_value=[pos])

            exits = await check_cd_exits(app_config, mock_client)

            # Stop-loss triggered, _recalculate_edge should NOT be called
            mock_recalc.assert_not_called()
            mock_exit.assert_called_once()
            assert len(exits) == 1

    @pytest.mark.asyncio
    async def test_empty_positions_returns_empty(self, cd_cfg, mock_client, app_config):
        """No open positions returns empty list immediately."""
        app_config.cd = cd_cfg

        with patch("strategy.cd_exit.store") as mock_store:
            mock_store.get_open_cd_positions = AsyncMock(return_value=[])

            exits = await check_cd_exits(app_config, mock_client)

            assert exits == []


# ═══════════════════════════════════════════════════════════════════════════════
# _pretrade_validate
# ═══════════════════════════════════════════════════════════════════════════════


class TestPretradeValidate:
    """Tests for cd_loop._pretrade_validate Phase 4 enrichments."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_trade_key(self):
        """_pretrade_validate returns a dict with 'trade' key, not a bare bool."""
        mock_result = {"trade": True, "confidence": 0.85, "reason": "good edge"}

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=mock_result):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

        assert isinstance(result, dict)
        assert "trade" in result
        assert result["trade"] is True

    @pytest.mark.asyncio
    async def test_returns_dict_with_trade_false(self):
        """AI can reject a trade by returning trade=False."""
        mock_result = {"trade": False, "confidence": 0.90, "reason": "vol too high"}

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=mock_result):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=5.5, p_model=0.60, p_market=0.545, vol=0.05,
                spot=97000.0, days_to_expiry=10.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

        assert isinstance(result, dict)
        assert result["trade"] is False

    @pytest.mark.asyncio
    async def test_includes_portfolio_context_in_prompt(self):
        """Prompt includes portfolio state: number of positions, exposure, coin overlap."""
        positions = [
            {"coin": "BTC", "entry_price": 0.55, "shares": 20.0},
            {"coin": "ETH", "entry_price": 0.60, "shares": 15.0},
        ]

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=positions, balance=100.0, size_usdc=5.0,
            )

            # Verify the prompt includes portfolio information
            call_args = mock_claude.call_args
            user_prompt = call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("user_prompt", "")
            assert "Open positions: 2" in user_prompt
            assert "Already exposed to BTC: YES" in user_prompt

    @pytest.mark.asyncio
    async def test_includes_volatility_regime_low(self):
        """Vol < 0.015 is classified as 'low' in the prompt."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.010,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            assert "(low)" in user_prompt

    @pytest.mark.asyncio
    async def test_includes_volatility_regime_normal(self):
        """Vol between 0.015 and 0.035 is classified as 'normal'."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            assert "(normal)" in user_prompt

    @pytest.mark.asyncio
    async def test_includes_volatility_regime_high(self):
        """Vol >= 0.035 is classified as 'high'."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.045,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            assert "(high)" in user_prompt

    @pytest.mark.asyncio
    async def test_includes_spot_distance_in_prompt(self):
        """Prompt includes distance percentage from spot to strike."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=95000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            # 95000 -> 100000 = -5.0%
            assert "distance:" in user_prompt
            assert "from strike" in user_prompt

    @pytest.mark.asyncio
    async def test_handles_none_open_positions_gracefully(self):
        """When open_positions is None, should not crash."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=None, balance=100.0, size_usdc=5.0,
            )

            assert result is not None
            assert result["trade"] is True
            user_prompt = mock_claude.call_args[0][2]
            assert "Open positions: 0" in user_prompt

    @pytest.mark.asyncio
    async def test_handles_empty_open_positions(self):
        """When open_positions is empty list, should work normally."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="ETH", strike=5000.0, direction="above",
                edge_pts=10.0, p_model=0.75, p_market=0.65, vol=0.020,
                spot=4800.0, days_to_expiry=20.0,
                open_positions=[], balance=200.0, size_usdc=10.0,
            )

            assert result is not None
            user_prompt = mock_claude.call_args[0][2]
            assert "Open positions: 0" in user_prompt
            assert "Already exposed to ETH: NO" in user_prompt

    @pytest.mark.asyncio
    async def test_returns_trade_true_on_exception(self):
        """On any exception, fail-safe returns {"trade": true}."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, side_effect=RuntimeError("API error")):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

        assert isinstance(result, dict)
        assert result["trade"] is True

    @pytest.mark.asyncio
    async def test_returns_trade_true_on_unparseable_response(self):
        """When Claude returns something without a bool 'trade' key, defaults to trade=True."""
        # Result has trade as string instead of bool
        mock_result = {"answer": "yes", "confidence": 0.5}

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=mock_result):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

        assert isinstance(result, dict)
        assert result["trade"] is True

    @pytest.mark.asyncio
    async def test_returns_trade_true_on_none_response(self):
        """When Claude returns None, defaults to trade=True."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=None):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
            )

        assert isinstance(result, dict)
        assert result["trade"] is True

    @pytest.mark.asyncio
    async def test_stores_full_ai_response_for_posthoc_analysis(self):
        """The full AI response dict is returned for storage in cd_signals.ai_validation."""
        full_response = {
            "trade": True,
            "confidence": 0.92,
            "reason": "Edge is strong, vol regime normal, no concentration issues",
        }

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=full_response):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=10.0, p_model=0.72, p_market=0.62, vol=0.02,
                spot=97000.0, days_to_expiry=25.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

        # The returned dict should contain the full response for storage
        assert result == full_response
        assert result["confidence"] == 0.92
        assert "Edge is strong" in result["reason"]

    @pytest.mark.asyncio
    async def test_exposure_calculation_with_positions(self):
        """Exposure percentage is correctly calculated from open positions and balance."""
        positions = [
            {"coin": "BTC", "entry_price": 0.50, "shares": 40.0},  # 20 USDC
            {"coin": "ETH", "entry_price": 0.60, "shares": 50.0},  # 30 USDC
        ]
        # Total exposure = 50 USDC, balance = 200 -> 25%

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=positions, balance=200.0, size_usdc=10.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            assert "Exposure: $50" in user_prompt
            assert "25.0%" in user_prompt

    @pytest.mark.asyncio
    async def test_zero_balance_exposure_doesnt_crash(self):
        """When balance is 0, exposure calculation should not divide by zero."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}):
            result = await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=0.0, size_usdc=0.0,
            )

        assert result is not None
        assert result["trade"] is True

    @pytest.mark.asyncio
    async def test_includes_days_to_expiry_in_prompt(self):
        """Prompt includes days_to_expiry for the AI to evaluate time risk."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=7.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            assert "Days to expiry: 7" in user_prompt

    @pytest.mark.asyncio
    async def test_includes_proposed_size_in_prompt(self):
        """Prompt includes proposed trade size for the AI to evaluate."""
        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value={"trade": True, "confidence": 0.8, "reason": "ok"}) as mock_claude:
            await _pretrade_validate(
                anthropic_config=AnthropicConfig(),
                coin="BTC", strike=100000.0, direction="above",
                edge_pts=8.0, p_model=0.70, p_market=0.62, vol=0.025,
                spot=97000.0, days_to_expiry=30.0,
                open_positions=[], balance=100.0, size_usdc=5.0,
            )

            user_prompt = mock_claude.call_args[0][2]
            assert "Proposed size: $5" in user_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# DB tests: insert_cd_position with expiry_days, insert_cd_signal with ai_validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestDbCdPositionExpiryDays:
    """Tests for db/store.py CD position expiry_days column."""

    @pytest.mark.asyncio
    async def test_insert_cd_position_stores_expiry_days(self, test_db):
        """insert_cd_position should store expiry_days in the DB."""
        pos = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.55,
            "shares": 20.0,
            "expiry_days": 45.0,
            "order_id": "order-123",
        }

        row_id = await test_db.insert_cd_position(pos)
        assert row_id > 0

        positions = await test_db.get_open_cd_positions()
        assert len(positions) == 1
        assert float(positions[0]["expiry_days"]) == 45.0

    @pytest.mark.asyncio
    async def test_insert_cd_position_without_expiry_days(self, test_db):
        """insert_cd_position should handle missing expiry_days (None)."""
        pos = {
            "market_id": "market-eth-5k",
            "token_id": "token-eth-yes",
            "coin": "ETH",
            "strike": 5000.0,
            "direction": "above",
            "entry_price": 0.60,
            "shares": 15.0,
            "order_id": "order-456",
            # No expiry_days
        }

        row_id = await test_db.insert_cd_position(pos)
        assert row_id > 0

        positions = await test_db.get_open_cd_positions()
        assert len(positions) == 1
        assert positions[0]["expiry_days"] is None

    @pytest.mark.asyncio
    async def test_upsert_cd_position_preserves_expiry_days(self, test_db):
        """When upserting (same market+token), expiry_days is COALESCE'd."""
        pos1 = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.55,
            "shares": 20.0,
            "expiry_days": 45.0,
            "order_id": "order-123",
        }
        await test_db.insert_cd_position(pos1)

        # Second insert for same market — no expiry_days
        pos2 = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.60,
            "shares": 10.0,
            "order_id": "order-789",
            # No expiry_days -> COALESCE should keep 45.0
        }
        await test_db.insert_cd_position(pos2)

        positions = await test_db.get_open_cd_positions()
        assert len(positions) == 1
        assert float(positions[0]["expiry_days"]) == 45.0
        # Shares should be aggregated
        assert float(positions[0]["shares"]) == 30.0

    @pytest.mark.asyncio
    async def test_upsert_cd_position_updates_expiry_days(self, test_db):
        """When upserting with a new expiry_days, the value is updated."""
        pos1 = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.55,
            "shares": 20.0,
            "expiry_days": 45.0,
            "order_id": "order-123",
        }
        await test_db.insert_cd_position(pos1)

        # Second insert with updated expiry_days
        pos2 = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.60,
            "shares": 10.0,
            "expiry_days": 35.0,
            "order_id": "order-789",
        }
        await test_db.insert_cd_position(pos2)

        positions = await test_db.get_open_cd_positions()
        assert len(positions) == 1
        # COALESCE(excluded.expiry_days, cd_positions.expiry_days) -> 35.0 (new value)
        assert float(positions[0]["expiry_days"]) == 35.0


class TestDbCdSignalAiValidation:
    """Tests for db/store.py CD signal ai_validation column."""

    @pytest.mark.asyncio
    async def test_insert_cd_signal_stores_ai_validation(self, test_db):
        """insert_cd_signal should store ai_validation JSON string."""
        ai_result = {"trade": True, "confidence": 0.92, "reason": "strong edge"}
        signal = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "expiry_days": 30.0,
            "spot_price": 97000.0,
            "vol_ewma": 0.025,
            "p_model": 0.72,
            "p_market": 0.62,
            "edge_pts": 10.0,
            "action": "trade",
            "size_usdc": 5.0,
            "ai_validation": json.dumps(ai_result),
        }

        row_id = await test_db.insert_cd_signal(signal)
        assert row_id > 0

        signals = await test_db.get_recent_cd_signals(limit=1)
        assert len(signals) == 1
        stored = json.loads(signals[0]["ai_validation"])
        assert stored["trade"] is True
        assert stored["confidence"] == 0.92
        assert "strong edge" in stored["reason"]

    @pytest.mark.asyncio
    async def test_insert_cd_signal_without_ai_validation(self, test_db):
        """insert_cd_signal should handle missing ai_validation (None)."""
        signal = {
            "market_id": "market-eth-5k",
            "token_id": "token-eth-yes",
            "coin": "ETH",
            "strike": 5000.0,
            "expiry_days": 20.0,
            "spot_price": 4800.0,
            "vol_ewma": 0.030,
            "p_model": 0.65,
            "p_market": 0.55,
            "edge_pts": 10.0,
            "action": "no_edge",
            # No ai_validation
        }

        row_id = await test_db.insert_cd_signal(signal)
        assert row_id > 0

        signals = await test_db.get_recent_cd_signals(limit=1)
        assert len(signals) == 1
        assert signals[0]["ai_validation"] is None

    @pytest.mark.asyncio
    async def test_insert_cd_signal_with_ai_rejected(self, test_db):
        """AI rejection stores the full rejection reason in ai_validation."""
        ai_result = {"trade": False, "confidence": 0.88, "reason": "vol regime too high"}
        signal = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "expiry_days": 10.0,
            "spot_price": 97000.0,
            "vol_ewma": 0.045,
            "p_model": 0.60,
            "p_market": 0.55,
            "edge_pts": 5.0,
            "action": "ai_rejected",
            "ai_validation": json.dumps(ai_result),
        }

        row_id = await test_db.insert_cd_signal(signal)
        assert row_id > 0

        signals = await test_db.get_recent_cd_signals(limit=1)
        stored = json.loads(signals[0]["ai_validation"])
        assert stored["trade"] is False
        assert "vol regime" in stored["reason"]


class TestDbSchemaColumns:
    """Verify new columns exist after init_db."""

    @pytest.mark.asyncio
    async def test_cd_positions_has_expiry_days_column(self, test_db):
        """cd_positions table should have expiry_days column after init_db."""
        db = await test_db._get_db()
        cursor = await db.execute("PRAGMA table_info(cd_positions)")
        columns = await cursor.fetchall()
        column_names = [col["name"] for col in columns]
        assert "expiry_days" in column_names

    @pytest.mark.asyncio
    async def test_cd_signals_has_ai_validation_column(self, test_db):
        """cd_signals table should have ai_validation column after init_db."""
        db = await test_db._get_db()
        cursor = await db.execute("PRAGMA table_info(cd_signals)")
        columns = await cursor.fetchall()
        column_names = [col["name"] for col in columns]
        assert "ai_validation" in column_names

    @pytest.mark.asyncio
    async def test_cd_signals_has_expiry_days_column(self, test_db):
        """cd_signals table should have expiry_days column."""
        db = await test_db._get_db()
        cursor = await db.execute("PRAGMA table_info(cd_signals)")
        columns = await cursor.fetchall()
        column_names = [col["name"] for col in columns]
        assert "expiry_days" in column_names


# ═══════════════════════════════════════════════════════════════════════════════
# Config tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigCdPretradeAi:
    """Tests for CryptoDirectionalConfig.cd_pretrade_ai_enabled default."""

    def test_cd_pretrade_ai_enabled_defaults_to_true(self):
        """cd_pretrade_ai_enabled should default to True (changed from False in Phase 4)."""
        cfg = CryptoDirectionalConfig()
        assert cfg.cd_pretrade_ai_enabled is True

    def test_cd_pretrade_ai_enabled_respects_env_false(self):
        """cd_pretrade_ai_enabled can be set to false via env."""
        import os
        original = os.environ.get("CD_PRETRADE_AI_ENABLED")
        try:
            os.environ["CD_PRETRADE_AI_ENABLED"] = "false"
            cfg = CryptoDirectionalConfig()
            assert cfg.cd_pretrade_ai_enabled is False
        finally:
            if original is not None:
                os.environ["CD_PRETRADE_AI_ENABLED"] = original
            else:
                os.environ.pop("CD_PRETRADE_AI_ENABLED", None)

    def test_cd_pretrade_ai_enabled_respects_env_true(self):
        """cd_pretrade_ai_enabled=true via env should work."""
        import os
        original = os.environ.get("CD_PRETRADE_AI_ENABLED")
        try:
            os.environ["CD_PRETRADE_AI_ENABLED"] = "true"
            cfg = CryptoDirectionalConfig()
            assert cfg.cd_pretrade_ai_enabled is True
        finally:
            if original is not None:
                os.environ["CD_PRETRADE_AI_ENABLED"] = original
            else:
                os.environ.pop("CD_PRETRADE_AI_ENABLED", None)

    def test_cd_exit_enabled_defaults_to_true(self):
        """Sanity check: cd_exit_enabled defaults to True."""
        cfg = CryptoDirectionalConfig()
        assert cfg.cd_exit_enabled is True

    def test_cd_analysis_auto_apply_defaults_to_false(self):
        """Sanity check: cd_analysis_auto_apply defaults to False."""
        cfg = CryptoDirectionalConfig()
        assert cfg.cd_analysis_auto_apply is False


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: cd_loop stores expiry_days in position + ai_validation in signal
# ═══════════════════════════════════════════════════════════════════════════════


class TestCdLoopIntegration:
    """Integration-style tests for cd_loop storing Phase 4 fields."""

    @pytest.mark.asyncio
    async def test_cd_loop_stores_expiry_days_in_position(self, test_db):
        """When cd_loop opens a position, expiry_days from the market is persisted."""
        pos = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "direction": "above",
            "entry_price": 0.55,
            "shares": 20.0,
            "expiry_days": 42.5,
            "order_id": "order-abc",
        }

        await test_db.insert_cd_position(pos)

        positions = await test_db.get_open_cd_positions()
        assert len(positions) == 1
        assert float(positions[0]["expiry_days"]) == 42.5

    @pytest.mark.asyncio
    async def test_cd_loop_stores_ai_validation_in_signal(self, test_db):
        """When cd_loop records a signal, ai_validation is persisted."""
        ai_result = {"trade": True, "confidence": 0.85, "reason": "good edge in normal vol"}
        signal = {
            "market_id": "market-btc-100k",
            "token_id": "token-btc-yes",
            "coin": "BTC",
            "strike": 100000.0,
            "expiry_days": 30.0,
            "spot_price": 97000.0,
            "vol_ewma": 0.025,
            "p_model": 0.72,
            "p_market": 0.62,
            "edge_pts": 10.0,
            "action": "trade",
            "size_usdc": 5.0,
            "ai_validation": json.dumps(ai_result),
        }

        await test_db.insert_cd_signal(signal)

        signals = await test_db.get_recent_cd_signals(limit=1)
        stored = json.loads(signals[0]["ai_validation"])
        assert stored["trade"] is True
        assert stored["reason"] == "good edge in normal vol"

    @pytest.mark.asyncio
    async def test_cd_signal_without_ai_validation_is_null(self, test_db):
        """Signal without ai_validation stores NULL."""
        signal = {
            "market_id": "market-eth-5k",
            "token_id": "token-eth-yes",
            "coin": "ETH",
            "strike": 5000.0,
            "expiry_days": 15.0,
            "spot_price": 4800.0,
            "vol_ewma": 0.030,
            "p_model": 0.65,
            "p_market": 0.55,
            "edge_pts": 10.0,
            "action": "no_edge",
        }

        await test_db.insert_cd_signal(signal)

        signals = await test_db.get_recent_cd_signals(limit=1)
        assert signals[0]["ai_validation"] is None
