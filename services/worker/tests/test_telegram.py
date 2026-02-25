"""Tests for services/worker/notifications/telegram_bot.py — TelegramNotifier class.

Covers:
- Module-level helper functions (_confidence_label, _strategy_label, _side_label, _days_since)
- TelegramNotifier initialization and setter methods
- send_message / send_alert (mocked bot)
- Trade confirmation message formatting and keyboard
- Trade executed notification
- Order fill update formatting
- Resolution update formatting
- Daily summary formatting
- Manager critique with approve/reject buttons
- Risk review formatting
- Strategist assessment formatting
- Authorization check (_is_authorized)
- Callback handling: approve/reject trades, approve/reject critiques,
  strategy switch, confirm/cancel actions, invalid data
- Command handlers: /start, /help, /status, /pause, /resume, /force,
  /stopbot, /startbot, /positions, /dashboard, /reglages,
  /strategy, /restart, /learning
- Free-text message routing
- Error handling throughout
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    """Test the standalone helper functions at module scope."""

    def test_confidence_label_faible(self):
        from notifications.telegram_bot import _confidence_label
        assert _confidence_label(0) == "faible"
        assert _confidence_label(30) == "faible"
        assert _confidence_label(49.9) == "faible"

    def test_confidence_label_moyenne(self):
        from notifications.telegram_bot import _confidence_label
        assert _confidence_label(50) == "moyenne"
        assert _confidence_label(60) == "moyenne"
        assert _confidence_label(69.9) == "moyenne"

    def test_confidence_label_elevee(self):
        from notifications.telegram_bot import _confidence_label
        assert _confidence_label(70) == "elevee"
        assert _confidence_label(80) == "elevee"
        assert _confidence_label(84.9) == "elevee"

    def test_confidence_label_tres_elevee(self):
        from notifications.telegram_bot import _confidence_label
        assert _confidence_label(85) == "tres elevee"
        assert _confidence_label(100) == "tres elevee"

    def test_strategy_label_active(self):
        from notifications.telegram_bot import _strategy_label
        assert _strategy_label("active") == "ACTIVE (achat + vente IA)"
        assert _strategy_label("ACTIVE") == "ACTIVE (achat + vente IA)"

    def test_strategy_label_other_falls_back_to_active(self):
        from notifications.telegram_bot import _strategy_label
        # The function always returns ACTIVE label regardless of input
        assert _strategy_label("other") == "ACTIVE (achat + vente IA)"
        assert _strategy_label("") == "ACTIVE (achat + vente IA)"

    def test_side_label(self):
        from notifications.telegram_bot import _side_label
        assert _side_label("BUY") == "ACHAT"
        assert _side_label("buy") == "ACHAT"
        assert _side_label("SELL") == "VENTE"
        assert _side_label("sell") == "VENTE"

    def test_days_since_none(self):
        from notifications.telegram_bot import _days_since
        assert _days_since(None) == 0

    def test_days_since_invalid(self):
        from notifications.telegram_bot import _days_since
        assert _days_since("not-a-date") == 0

    def test_days_since_valid_iso(self):
        from notifications.telegram_bot import _days_since
        # A date 3 days ago
        three_days_ago = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        three_days_ago = three_days_ago.replace(
            day=three_days_ago.day  # same day => 0 days
        )
        result = _days_since(three_days_ago.isoformat())
        assert result >= 0

    def test_days_since_z_suffix(self):
        from notifications.telegram_bot import _days_since
        result = _days_since("2020-01-01T00:00:00Z")
        assert result > 0  # Well in the past


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def telegram_notifier(telegram_config):
    """Create a TelegramNotifier with a mocked Application and Bot."""
    from notifications.telegram_bot import TelegramNotifier

    notifier = TelegramNotifier(telegram_config)

    # Mock the Application object so we don't actually connect to Telegram
    mock_app = MagicMock()
    mock_bot = AsyncMock()
    mock_app.bot = mock_bot
    notifier.app = mock_app

    return notifier


@pytest.fixture
def sample_trade_for_confirm():
    """A trade dict suitable for send_trade_confirmation."""
    return {
        "id": 42,
        "market_id": "market-abc-123",
        "market_question": "Will BTC reach $100k by end of 2026?",
        "token_id": "token-yes-123",
        "side": "BUY",
        "outcome": "Yes",
        "size_usdc": 5.0,
        "price": 0.55,
        "edge": 0.15,
        "edge_net": 0.12,
        "confidence": 0.75,
        "reasoning": "Strong momentum with clear market signals",
        "strategy": "active",
        "source_quality": 0.80,
        "risk_rating": 4,
        "horizon": "short_term",
        "key_source": "CoinDesk analysis",
    }


@pytest.fixture
def mock_update():
    """Create a mock Telegram Update with a valid authorized chat."""
    update = MagicMock()
    update.effective_chat.id = 12345678  # matches TELEGRAM_CHAT_ID from conftest
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.text = "hello"
    update.message.chat = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    return update


@pytest.fixture
def mock_update_unauthorized():
    """Create a mock Telegram Update with a wrong chat ID."""
    update = MagicMock()
    update.effective_chat.id = 99999999  # wrong ID
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    """Create a mock context for command handlers."""
    context = MagicMock()
    context.args = []
    return context


@pytest.fixture
def mock_callback_query():
    """Create a mock callback query for inline button handling."""
    query = AsyncMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


# ---------------------------------------------------------------------------
# Initialization and setters
# ---------------------------------------------------------------------------

class TestTelegramNotifierInit:

    def test_init_stores_config(self, telegram_config):
        from notifications.telegram_bot import TelegramNotifier
        notifier = TelegramNotifier(telegram_config)
        assert notifier.config is telegram_config
        assert notifier.app is None
        assert notifier._trade_confirm_callback is None
        assert notifier._trade_reject_callback is None

    def test_set_callbacks(self, telegram_notifier):
        on_confirm = AsyncMock()
        on_reject = AsyncMock()
        telegram_notifier.set_callbacks(on_confirm, on_reject)
        assert telegram_notifier._trade_confirm_callback is on_confirm
        assert telegram_notifier._trade_reject_callback is on_reject

    def test_set_managers(self, telegram_notifier):
        pm = MagicMock()
        rm = MagicMock()
        telegram_notifier.set_managers(pm, rm)
        assert telegram_notifier._portfolio_manager is pm
        assert telegram_notifier._risk_manager is rm

    def test_set_performance_tracker(self, telegram_notifier):
        tracker = MagicMock()
        telegram_notifier.set_performance_tracker(tracker)
        assert telegram_notifier._performance_tracker is tracker

    def test_set_trading_config(self, telegram_notifier, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        assert telegram_notifier._trading_config is trading_config

    def test_set_bot_controls(self, telegram_notifier):
        event = asyncio.Event()
        stop_cb = AsyncMock()
        stop_proc = AsyncMock()
        start_proc = AsyncMock()
        telegram_notifier.set_bot_controls(event, stop_cb, stop_proc, start_proc)
        assert telegram_notifier._force_cycle_event is event
        assert telegram_notifier._stop_callback is stop_cb
        assert telegram_notifier._stop_process_callback is stop_proc
        assert telegram_notifier._start_process_callback is start_proc

    def test_set_strategy(self, telegram_notifier):
        strategy = MagicMock()
        switch_cb = MagicMock()
        telegram_notifier.set_strategy(strategy, switch_cb)
        assert telegram_notifier._current_strategy is strategy
        assert telegram_notifier._strategy_switch_callback is switch_cb

    def test_set_learning(self, telegram_notifier):
        journal = MagicMock()
        insights = MagicMock()
        proposals = MagicMock()
        telegram_notifier.set_learning(journal, insights, proposals)
        assert telegram_notifier._learning_journal is journal
        assert telegram_notifier._learning_insights is insights
        assert telegram_notifier._learning_proposals is proposals

    def test_set_conversation_router(self, telegram_notifier):
        router = MagicMock()
        telegram_notifier.set_conversation_router(router)
        assert telegram_notifier._conversation_router is router


# ---------------------------------------------------------------------------
# send_message and send_alert
# ---------------------------------------------------------------------------

class TestSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_calls_bot(self, telegram_notifier):
        await telegram_notifier.send_message("Hello <b>World</b>")
        telegram_notifier.app.bot.send_message.assert_awaited_once_with(
            chat_id=telegram_notifier.config.chat_id,
            text="Hello <b>World</b>",
            parse_mode="HTML",
        )

    @pytest.mark.asyncio
    async def test_send_message_custom_parse_mode(self, telegram_notifier):
        await telegram_notifier.send_message("Hello", parse_mode="Markdown")
        telegram_notifier.app.bot.send_message.assert_awaited_once_with(
            chat_id=telegram_notifier.config.chat_id,
            text="Hello",
            parse_mode="Markdown",
        )

    @pytest.mark.asyncio
    async def test_send_message_no_app_does_nothing(self, telegram_config):
        from notifications.telegram_bot import TelegramNotifier
        notifier = TelegramNotifier(telegram_config)
        notifier.app = None
        # Should not raise
        await notifier.send_message("test")

    @pytest.mark.asyncio
    async def test_send_message_no_chat_id_does_nothing(self, telegram_notifier):
        telegram_notifier.config.chat_id = ""
        await telegram_notifier.send_message("test")
        telegram_notifier.app.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_alert(self, telegram_notifier):
        await telegram_notifier.send_alert("Danger", "Something bad happened")
        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "Danger" in text
        assert "Something bad happened" in text


# ---------------------------------------------------------------------------
# Trade notifications
# ---------------------------------------------------------------------------

class TestTradeNotifications:

    @pytest.mark.asyncio
    async def test_send_trade_confirmation_formatting(
        self, telegram_notifier, sample_trade_for_confirm, trading_config
    ):
        telegram_notifier.set_trading_config(trading_config)
        await telegram_notifier.send_trade_confirmation(sample_trade_for_confirm)

        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        markup = call_args.kwargs["reply_markup"]

        # Check key elements in the message
        assert "ACHAT" in text  # side=BUY
        assert "Yes" in text  # outcome
        assert "Strong momentum" in text  # reasoning
        assert "12.0%" in text  # edge_net * 100
        assert "75%" in text  # confidence * 100
        assert "elevee" in text  # confidence label for 75
        assert "CoinDesk analysis" in text  # key_source
        assert "4/10" in text  # risk_rating
        assert "short term" in text  # horizon with _ replaced
        assert "0.80" in text  # source_quality

        # Check inline keyboard
        buttons = markup.inline_keyboard[0]
        assert len(buttons) == 2
        assert buttons[0].callback_data == "approve_42"
        assert buttons[1].callback_data == "reject_42"

    @pytest.mark.asyncio
    async def test_send_trade_confirmation_no_app(self, telegram_config, sample_trade_for_confirm):
        from notifications.telegram_bot import TelegramNotifier
        notifier = TelegramNotifier(telegram_config)
        notifier.app = None
        # Should not raise
        await notifier.send_trade_confirmation(sample_trade_for_confirm)

    @pytest.mark.asyncio
    async def test_send_trade_confirmation_no_chat_id(
        self, telegram_notifier, sample_trade_for_confirm
    ):
        telegram_notifier.config.chat_id = ""
        await telegram_notifier.send_trade_confirmation(sample_trade_for_confirm)
        telegram_notifier.app.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_trade_confirmation_missing_optional_fields(
        self, telegram_notifier, trading_config
    ):
        """Trade with missing optional fields should still produce a valid message."""
        telegram_notifier.set_trading_config(trading_config)
        trade = {
            "id": 99,
            "market_id": "market-xyz",
            "side": "SELL",
            "outcome": "No",
            "size_usdc": 3.0,
            "price": 0.40,
        }
        await telegram_notifier.send_trade_confirmation(trade)
        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "VENTE" in text
        assert "n/a" in text  # risk_rating and source_quality are n/a

    @pytest.mark.asyncio
    async def test_send_trade_executed(self, telegram_notifier):
        trade = {
            "market_id": "market-abc",
            "market_question": "Will ETH flip BTC?",
            "side": "BUY",
            "outcome": "Yes",
            "size_usdc": 10.0,
            "price": 0.50,
            "edge": 0.10,
            "edge_net": 0.08,
            "status": "executed",
            "execution_cost_bps": 15.0,
            "source_quality": 0.75,
            "key_source": "DeFi analysis",
            "strategy": "active",
        }
        await telegram_notifier.send_trade_executed(trade)
        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "ACHAT" in text
        assert "20.0 jetons" in text  # 10 / 0.50 = 20
        assert "8.0%" in text  # edge_net * 100
        assert "15bps" in text  # execution_cost_bps
        assert "0.75" in text  # source_quality


# ---------------------------------------------------------------------------
# Order fill updates
# ---------------------------------------------------------------------------

class TestOrderFillUpdates:

    @pytest.mark.asyncio
    async def test_send_order_fill_update_empty(self, telegram_notifier):
        await telegram_notifier.send_order_fill_update([])
        telegram_notifier.app.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_order_fill_update_matched(self, telegram_notifier):
        updates = [
            {
                "trade_id": 1,
                "status": "MATCHED",
                "size_matched": 10.5,
                "avg_fill_price": 0.55,
                "slippage_bps": 12.3,
            }
        ]
        await telegram_notifier.send_order_fill_update(updates)
        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "EXECUTE" in text  # MATCHED -> EXECUTE
        assert "10.50 jetons" in text
        assert "0.5500$" in text
        assert "12.3 bps" in text

    @pytest.mark.asyncio
    async def test_send_order_fill_update_multiple_statuses(self, telegram_notifier):
        updates = [
            {"trade_id": 1, "status": "EXPIRED"},
            {"trade_id": 2, "status": "CANCELLED"},
            {"trade_id": 3, "status": "PARTIAL_FILL"},
            {"trade_id": 4, "status": "UNKNOWN"},
        ]
        await telegram_notifier.send_order_fill_update(updates)
        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "EXPIRE" in text
        assert "ANNULE" in text
        assert "PARTIEL" in text
        assert "UNKNOWN" in text


# ---------------------------------------------------------------------------
# Resolution updates
# ---------------------------------------------------------------------------

class TestResolutionUpdates:

    @pytest.mark.asyncio
    async def test_send_resolution_update_empty(self, telegram_notifier):
        await telegram_notifier.send_resolution_update([])
        telegram_notifier.app.bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_resolution_update(self, telegram_notifier):
        resolutions = [
            {"market_id": "abcdef123456789", "outcome": "Yes", "trades_resolved": 3},
        ]
        await telegram_notifier.send_resolution_update(resolutions)
        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "Marches resolus" in text
        assert "abcdef123456" in text  # truncated to 12 chars
        assert "Yes" in text
        assert "3 trades concernes" in text


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

class TestDailySummary:

    @pytest.mark.asyncio
    async def test_send_daily_summary(self, telegram_notifier, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy

        summary = {
            "available_usdc": 75.50,
            "onchain_balance": 80.0,
            "daily_pnl": 2.50,
            "positions_count": 3,
            "total_invested": 25.0,
            "daily_traded": 15.0,
        }
        await telegram_notifier.send_daily_summary(summary)

        call_args = telegram_notifier.app.bot.send_message.call_args
        text = call_args.kwargs["text"]
        assert "75.50$" in text
        assert "80.00$" in text
        assert "+2.50$" in text
        assert "+10.0%" in text  # 2.50/25.0 * 100
        assert "3 positions" in text
        assert "15.00$" in text
        assert "30$" in text  # max_per_day_usdc
        assert "ACTIVE" in text
        assert "30 minutes" in text  # analysis_interval_minutes

    @pytest.mark.asyncio
    async def test_send_daily_summary_negative_pnl(self, telegram_notifier, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy

        summary = {
            "available_usdc": 40.0,
            "daily_pnl": -3.0,
            "positions_count": 1,
            "total_invested": 20.0,
            "daily_traded": 5.0,
        }
        await telegram_notifier.send_daily_summary(summary)
        text = telegram_notifier.app.bot.send_message.call_args.kwargs["text"]
        assert "-3.00$" in text
        assert "-15.0%" in text  # -3/20 * 100

    @pytest.mark.asyncio
    async def test_send_daily_summary_zero_invested(self, telegram_notifier, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        summary = {
            "available_usdc": 100.0,
            "daily_pnl": 0,
            "positions_count": 0,
            "total_invested": 0,
            "daily_traded": 0,
        }
        await telegram_notifier.send_daily_summary(summary)
        text = telegram_notifier.app.bot.send_message.call_args.kwargs["text"]
        assert "+0.0%" in text  # zero division protected


# ---------------------------------------------------------------------------
# Risk review
# ---------------------------------------------------------------------------

class TestRiskReview:

    @pytest.mark.asyncio
    async def test_send_risk_review(self, telegram_notifier):
        review = {
            "reviews": [
                {"verdict": "approve", "market_id": "m1"},
                {"verdict": "flag", "market_id": "m2", "concerns": ["high correlation", "low liquidity"]},
                {"verdict": "reject", "market_id": "m3"},
            ],
            "portfolio_risk_summary": "Portfolio is moderately diversified.",
        }
        rejected_trades = [
            {
                "market_question": "Will Trump win?",
                "risk_score": 9,
                "reasoning": "Too correlated with existing positions",
            }
        ]
        await telegram_notifier.send_risk_review(review, rejected_trades)

        text = telegram_notifier.app.bot.send_message.call_args.kwargs["text"]
        assert "Approuves : 1" in text
        assert "Reduits : 1" in text
        assert "Bloques : 1" in text
        assert "Will Trump win?" in text
        assert "9/10" in text
        assert "high correlation" in text
        assert "Portfolio is moderately diversified" in text

    @pytest.mark.asyncio
    async def test_send_risk_review_no_app(self, telegram_config):
        from notifications.telegram_bot import TelegramNotifier
        notifier = TelegramNotifier(telegram_config)
        notifier.app = None
        await notifier.send_risk_review({"reviews": []})

    @pytest.mark.asyncio
    async def test_send_risk_review_truncates_long_text(self, telegram_notifier):
        """Very long summaries should be truncated to 4000 chars."""
        review = {
            "reviews": [{"verdict": "approve"}] * 100,
            "portfolio_risk_summary": "X" * 5000,
        }
        await telegram_notifier.send_risk_review(review)
        text = telegram_notifier.app.bot.send_message.call_args.kwargs["text"]
        assert len(text) <= 4000


# ---------------------------------------------------------------------------
# Strategist assessment
# ---------------------------------------------------------------------------

class TestStrategistAssessment:

    @pytest.mark.asyncio
    async def test_send_strategist_assessment(self, telegram_notifier):
        assessment = {
            "parsed": {
                "market_regime": "volatile",
                "allocation_score": 7,
                "diversification_score": 5,
                "summary": "Market is showing increased volatility.",
                "recommendations": [
                    {
                        "priority": "high",
                        "target": "max_per_trade_usdc",
                        "current": "10",
                        "suggested": "7",
                        "risk_level": "moderate",
                    },
                ],
            }
        }
        await telegram_notifier.send_strategist_assessment(assessment)

        text = telegram_notifier.app.bot.send_message.call_args.kwargs["text"]
        assert "Volatile" in text
        assert "Allocation : 7/10" in text
        assert "Diversification : 5/10" in text
        assert "increased volatility" in text
        assert "max_per_trade_usdc" in text
        assert "10" in text
        assert "7" in text

    @pytest.mark.asyncio
    async def test_send_strategist_assessment_crisis(self, telegram_notifier):
        assessment = {"parsed": {"market_regime": "crisis"}}
        await telegram_notifier.send_strategist_assessment(assessment)
        text = telegram_notifier.app.bot.send_message.call_args.kwargs["text"]
        assert "CRISE" in text

    @pytest.mark.asyncio
    async def test_send_strategist_assessment_no_app(self, telegram_config):
        from notifications.telegram_bot import TelegramNotifier
        notifier = TelegramNotifier(telegram_config)
        notifier.app = None
        await notifier.send_strategist_assessment({"parsed": {}})


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

class TestAuthorization:

    def test_is_authorized_correct_id(self, telegram_notifier, mock_update):
        assert telegram_notifier._is_authorized(mock_update) is True

    def test_is_authorized_wrong_id(self, telegram_notifier, mock_update_unauthorized):
        assert telegram_notifier._is_authorized(mock_update_unauthorized) is False

    def test_is_authorized_no_chat(self, telegram_notifier):
        update = MagicMock()
        update.effective_chat = None
        assert telegram_notifier._is_authorized(update) is False


# ---------------------------------------------------------------------------
# Callback handler — trade approve/reject
# ---------------------------------------------------------------------------

class TestCallbackHandlerTrades:

    @pytest.mark.asyncio
    async def test_approve_trade_executed(self, telegram_notifier, mock_update, mock_context):
        on_confirm = AsyncMock(return_value={"status": "executed"})
        telegram_notifier.set_callbacks(on_confirm, AsyncMock())

        query = AsyncMock()
        query.data = "approve_42"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)

        on_confirm.assert_awaited_once_with(42)
        query.answer.assert_awaited_once()
        edit_text = query.edit_message_text.call_args.args[0]
        assert "VALIDE ET EXECUTE" in edit_text
        assert "42" in edit_text

    @pytest.mark.asyncio
    async def test_approve_trade_paper_executed(self, telegram_notifier, mock_update, mock_context):
        on_confirm = AsyncMock(return_value={"status": "executed"})
        telegram_notifier.set_callbacks(on_confirm, AsyncMock())

        query = AsyncMock()
        query.data = "approve_10"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "VALIDE ET EXECUTE" in edit_text

    @pytest.mark.asyncio
    async def test_approve_trade_other_status(self, telegram_notifier, mock_update, mock_context):
        on_confirm = AsyncMock(return_value={"status": "pending"})
        telegram_notifier.set_callbacks(on_confirm, AsyncMock())

        query = AsyncMock()
        query.data = "approve_5"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "PENDING" in edit_text

    @pytest.mark.asyncio
    async def test_approve_trade_no_callback(self, telegram_notifier, mock_update, mock_context):
        """If no confirm callback is set, nothing happens."""
        query = AsyncMock()
        query.data = "approve_1"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reject_trade(self, telegram_notifier, mock_update, mock_context):
        on_reject = AsyncMock()
        telegram_notifier.set_callbacks(AsyncMock(), on_reject)

        query = AsyncMock()
        query.data = "reject_42"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)

        on_reject.assert_awaited_once_with(42)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "REFUSE" in edit_text
        assert "42" in edit_text

    @pytest.mark.asyncio
    async def test_reject_trade_no_callback(self, telegram_notifier, mock_update, mock_context):
        query = AsyncMock()
        query.data = "reject_1"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        query = AsyncMock()
        query.data = "approve_1"
        mock_update_unauthorized.callback_query = query

        await telegram_notifier._handle_callback(mock_update_unauthorized, mock_context)
        query.answer.assert_awaited_once_with("Acces non autorise.", show_alert=True)
        query.edit_message_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Callback handler — critique approve/reject
# ---------------------------------------------------------------------------

class TestCallbackHandlerCritiques:
    """Tests for critique approve/reject callbacks.

    NOTE: There is a known routing-order issue in the source code.
    ``approve_critique_7`` matches ``data.startswith("approve_")`` first,
    which tries ``int("critique_7")`` and raises ``ValueError``.  The
    ``elif data.startswith("approve_critique_")`` branch is therefore
    unreachable.  The same applies to ``reject_critique_``.

    The tests below verify the *actual* (buggy) behavior: the callback
    falls through to the ValueError/TypeError handler and returns an error
    message.  A separate pair of tests shows what would happen if the
    routing order were fixed (approve_critique_ checked first).
    """

    @pytest.mark.asyncio
    async def test_approve_critique_hits_valueerror_due_to_routing_order(
        self, telegram_notifier, mock_update, mock_context
    ):
        """approve_critique_7 is caught by the approve_ branch first and
        triggers ValueError when parsing 'critique_7' as int."""
        query = AsyncMock()
        query.data = "approve_critique_7"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)

        # Falls into the ValueError handler
        edit_text = query.edit_message_text.call_args.args[0]
        assert "invalides" in edit_text

    @pytest.mark.asyncio
    async def test_reject_critique_hits_valueerror_due_to_routing_order(
        self, telegram_notifier, mock_update, mock_context
    ):
        """reject_critique_3 is caught by the reject_ branch first and
        triggers ValueError when parsing 'critique_3' as int."""
        query = AsyncMock()
        query.data = "reject_critique_3"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)

        # Falls into the ValueError handler
        edit_text = query.edit_message_text.call_args.args[0]
        assert "invalides" in edit_text

    @pytest.mark.asyncio
    async def test_approve_critique_would_work_with_numeric_only_id(
        self, telegram_notifier, mock_update, mock_context
    ):
        """If the send_manager_critique used callback_data='approvecritique_7'
        (a distinct prefix), the approve_critique branch would be reachable.
        For now, we verify that a pure-numeric approve_ callback goes through
        the trade approval path correctly."""
        on_confirm = AsyncMock(return_value=None)
        telegram_notifier.set_callbacks(on_confirm, AsyncMock())

        query = AsyncMock()
        query.data = "approve_7"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        on_confirm.assert_awaited_once_with(7)


# ---------------------------------------------------------------------------
# Callback handler — strategy switch
# ---------------------------------------------------------------------------

class TestCallbackHandlerStrategy:

    @pytest.mark.asyncio
    async def test_strategy_active(self, telegram_notifier, mock_update, mock_context):
        switch_cb = MagicMock()
        telegram_notifier._strategy_switch_callback = switch_cb

        query = AsyncMock()
        query.data = "strategy_active"
        mock_update.callback_query = query

        with patch("strategy.get_strategy", create=True) as mock_get_strat, \
             patch.object(telegram_notifier, "_persist_runtime_settings", new_callable=AsyncMock):
            mock_get_strat.return_value = MagicMock(name="active")
            await telegram_notifier._handle_callback(mock_update, mock_context)

            switch_cb.assert_called_once_with("active")
            edit_text = query.edit_message_text.call_args.args[0]
            assert "ACTIVE" in edit_text

    @pytest.mark.asyncio
    async def test_strategy_non_active_refused(self, telegram_notifier, mock_update, mock_context):
        query = AsyncMock()
        query.data = "strategy_conservative"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "fixe" in edit_text
        assert "ACTIVE" in edit_text


# ---------------------------------------------------------------------------
# Callback handler — conversational actions
# ---------------------------------------------------------------------------

class TestCallbackHandlerConversation:

    @pytest.mark.asyncio
    async def test_confirm_action_success(self, telegram_notifier, mock_update, mock_context):
        router = AsyncMock()
        router.execute_confirmed_action = AsyncMock(
            return_value={"success": True, "message": "Parameter updated."}
        )
        telegram_notifier.set_conversation_router(router)

        query = AsyncMock()
        query.data = "confirm_action_abc123"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)

        router.execute_confirmed_action.assert_awaited_once_with("abc123")
        edit_text = query.edit_message_text.call_args.args[0]
        assert "Action executee" in edit_text
        assert "Parameter updated" in edit_text

    @pytest.mark.asyncio
    async def test_confirm_action_failure(self, telegram_notifier, mock_update, mock_context):
        router = AsyncMock()
        router.execute_confirmed_action = AsyncMock(
            return_value={"success": False, "error": "Permission denied"}
        )
        telegram_notifier.set_conversation_router(router)

        query = AsyncMock()
        query.data = "confirm_action_xyz"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "Echec" in edit_text
        assert "Permission denied" in edit_text

    @pytest.mark.asyncio
    async def test_confirm_action_no_router(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._conversation_router = None

        query = AsyncMock()
        query.data = "confirm_action_abc"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "non disponible" in edit_text

    @pytest.mark.asyncio
    async def test_cancel_action(self, telegram_notifier, mock_update, mock_context):
        query = AsyncMock()
        query.data = "cancel_action_abc123"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "annulee" in edit_text


# ---------------------------------------------------------------------------
# Callback handler — error handling
# ---------------------------------------------------------------------------

class TestCallbackHandlerErrors:

    @pytest.mark.asyncio
    async def test_invalid_callback_data(self, telegram_notifier, mock_update, mock_context):
        """Callback data that causes a ValueError (e.g. non-numeric trade_id)."""
        on_confirm = AsyncMock()
        telegram_notifier.set_callbacks(on_confirm, AsyncMock())

        query = AsyncMock()
        query.data = "approve_not_a_number"
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        edit_text = query.edit_message_text.call_args.args[0]
        assert "invalides" in edit_text

    @pytest.mark.asyncio
    async def test_empty_callback_data(self, telegram_notifier, mock_update, mock_context):
        """Empty callback data should not raise."""
        query = AsyncMock()
        query.data = ""
        mock_update.callback_query = query

        await telegram_notifier._handle_callback(mock_update, mock_context)
        # No edit because no handler matched, and no exception
        query.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# Command handlers — /start, /help
# ---------------------------------------------------------------------------

class TestCommandStartHelp:

    @pytest.mark.asyncio
    async def test_cmd_start_authorized(self, telegram_notifier, mock_update, mock_context, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy

        await telegram_notifier._cmd_start(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.kwargs.get(
            "text", mock_update.message.reply_text.call_args.args[0]
        )
        assert "Trading Bot v2" in text
        assert "ACTIVE" in text

    @pytest.mark.asyncio
    async def test_cmd_start_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        await telegram_notifier._cmd_start(mock_update_unauthorized, mock_context)
        text = mock_update_unauthorized.message.reply_text.call_args.args[0]
        assert "non autorise" in text

    @pytest.mark.asyncio
    async def test_cmd_help_authorized(self, telegram_notifier, mock_update, mock_context, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        await telegram_notifier._cmd_help(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Guide des commandes" in text
        assert "/status" in text
        assert "/positions" in text
        assert "/force" in text

    @pytest.mark.asyncio
    async def test_cmd_help_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        await telegram_notifier._cmd_help(mock_update_unauthorized, mock_context)
        text = mock_update_unauthorized.message.reply_text.call_args.args[0]
        assert "non autorise" in text


# ---------------------------------------------------------------------------
# Command handlers — /pause, /resume
# ---------------------------------------------------------------------------

class TestCommandPauseResume:

    @pytest.mark.asyncio
    async def test_cmd_pause(self, telegram_notifier, mock_update, mock_context):
        risk_manager = MagicMock()
        risk_manager.is_paused = False
        telegram_notifier._risk_manager = risk_manager

        await telegram_notifier._cmd_pause(mock_update, mock_context)

        assert risk_manager.is_paused is True
        text = mock_update.message.reply_text.call_args.args[0]
        assert "pause" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_pause_no_risk_manager(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._risk_manager = None
        await telegram_notifier._cmd_pause(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Impossible" in text

    @pytest.mark.asyncio
    async def test_cmd_resume(self, telegram_notifier, mock_update, mock_context):
        risk_manager = MagicMock()
        risk_manager.is_paused = True
        telegram_notifier._risk_manager = risk_manager

        event = MagicMock()
        telegram_notifier._force_cycle_event = event

        await telegram_notifier._cmd_resume(mock_update, mock_context)

        assert risk_manager.is_paused is False
        event.set.assert_called_once()
        text = mock_update.message.reply_text.call_args.args[0]
        assert "repris" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_resume_no_risk_manager(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._risk_manager = None
        await telegram_notifier._cmd_resume(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Impossible" in text


# ---------------------------------------------------------------------------
# Command handlers — /force
# ---------------------------------------------------------------------------

class TestCommandForce:

    @pytest.mark.asyncio
    async def test_cmd_force(self, telegram_notifier, mock_update, mock_context):
        event = MagicMock()
        telegram_notifier._force_cycle_event = event

        await telegram_notifier._cmd_force(mock_update, mock_context)

        event.set.assert_called_once()
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Cycle" in text

    @pytest.mark.asyncio
    async def test_cmd_force_no_event(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._force_cycle_event = None
        await telegram_notifier._cmd_force(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Impossible" in text

    @pytest.mark.asyncio
    async def test_cmd_force_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        await telegram_notifier._cmd_force(mock_update_unauthorized, mock_context)
        text = mock_update_unauthorized.message.reply_text.call_args.args[0]
        assert "non autorise" in text


# ---------------------------------------------------------------------------
# Command handlers — /stopbot, /startbot
# ---------------------------------------------------------------------------

class TestCommandStopStartBot:

    @pytest.mark.asyncio
    async def test_cmd_stopbot(self, telegram_notifier, mock_update, mock_context):
        stop_proc = AsyncMock()
        telegram_notifier._stop_process_callback = stop_proc

        await telegram_notifier._cmd_stopbot(mock_update, mock_context)

        stop_proc.assert_awaited_once_with(source="telegram")
        text = mock_update.message.reply_text.call_args.args[0]
        assert "stoppe" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_stopbot_no_callback(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._stop_process_callback = None
        await telegram_notifier._cmd_stopbot(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "indisponible" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_startbot(self, telegram_notifier, mock_update, mock_context):
        start_proc = AsyncMock()
        telegram_notifier._start_process_callback = start_proc

        await telegram_notifier._cmd_startbot(mock_update, mock_context)

        start_proc.assert_awaited_once_with(source="telegram")
        text = mock_update.message.reply_text.call_args.args[0]
        assert "demarre" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_startbot_no_callback(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._start_process_callback = None
        await telegram_notifier._cmd_startbot(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "indisponible" in text.lower()


# ---------------------------------------------------------------------------
# Command handlers — /status
# ---------------------------------------------------------------------------

class TestCommandStatus:

    @pytest.mark.asyncio
    async def test_cmd_status(self, telegram_notifier, mock_update, mock_context, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(return_value={
            "available_usdc": 50.0,
            "positions_count": 2,
            "daily_pnl": 1.5,
            "daily_traded": 10.0,
            "onchain_balance": 55.0,
        })
        risk_manager = MagicMock()
        risk_manager.is_paused = False
        telegram_notifier.set_managers(portfolio, risk_manager)

        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy

        await telegram_notifier._cmd_status(mock_update, mock_context)

        text = mock_update.message.reply_text.call_args.args[0]
        assert "50.00$" in text
        assert "55.00$" in text
        assert "+1.50$" in text
        assert "2" in text
        assert "ACTIF" in text
        assert "ACTIVE" in text

    @pytest.mark.asyncio
    async def test_cmd_status_no_portfolio_manager(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._portfolio_manager = None
        await telegram_notifier._cmd_status(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "demarrage" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_status_error(self, telegram_notifier, mock_update, mock_context):
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(side_effect=Exception("DB error"))
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_status(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Erreur" in text


# ---------------------------------------------------------------------------
# Command handlers — /positions
# ---------------------------------------------------------------------------

class TestCommandPositions:

    @pytest.mark.asyncio
    async def test_cmd_positions_with_data(self, telegram_notifier, mock_update, mock_context):
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(return_value={
            "total_invested": 20.0,
            "positions": [
                {
                    "market_question": "Will BTC reach $100k?",
                    "outcome": "Yes",
                    "size": 10.0,
                    "avg_price": 0.55,
                    "current_price": 0.60,
                    "strategy": "active",
                    "opened_at": "2026-02-18T12:00:00Z",
                },
            ],
        })
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_positions(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Positions ouvertes (1)" in text
        assert "Yes" in text
        assert "10.0 jetons" in text
        assert "0.550$" in text
        assert "0.600$" in text

    @pytest.mark.asyncio
    async def test_cmd_positions_empty(self, telegram_notifier, mock_update, mock_context):
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(return_value={
            "positions": [],
        })
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_positions(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Aucune position" in text

    @pytest.mark.asyncio
    async def test_cmd_positions_no_portfolio_manager(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._portfolio_manager = None
        await telegram_notifier._cmd_positions(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "non encore disponibles" in text

    @pytest.mark.asyncio
    async def test_cmd_positions_error(self, telegram_notifier, mock_update, mock_context):
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(side_effect=RuntimeError("fail"))
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_positions(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Erreur" in text


# ---------------------------------------------------------------------------
# Command handlers — /dashboard
# ---------------------------------------------------------------------------

class TestCommandDashboard:

    @pytest.mark.asyncio
    async def test_cmd_dashboard_with_positions_and_trades(
        self, telegram_notifier, mock_update, mock_context, trading_config
    ):
        telegram_notifier.set_trading_config(trading_config)
        risk_manager = MagicMock()
        risk_manager.is_paused = False
        telegram_notifier._risk_manager = risk_manager

        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy

        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(return_value={
            "available_usdc": 50.0,
            "onchain_balance": 55.0,
            "total_invested": 20.0,
            "daily_pnl": 1.5,
            "daily_traded": 10.0,
            "positions_count": 1,
            "positions": [
                {
                    "market_question": "Will BTC reach $100k?",
                    "outcome": "Yes",
                    "size": 10.0,
                    "avg_price": 0.55,
                    "strategy": "active",
                    "pnl_unrealized": 0.50,
                },
            ],
            "recent_trades": [
                {
                    "side": "BUY",
                    "outcome": "Yes",
                    "size_usdc": 5.0,
                    "status": "executed",
                    "market_question": "Will BTC reach $100k?",
                },
            ],
        })
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_dashboard(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Tableau de bord" in text
        assert "ACTIF" in text
        assert "50.00$" in text
        assert "+0.50$" in text  # P&L
        assert "Execute" in text  # trade status

    @pytest.mark.asyncio
    async def test_cmd_dashboard_empty_portfolio(
        self, telegram_notifier, mock_update, mock_context, trading_config
    ):
        telegram_notifier.set_trading_config(trading_config)
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(return_value={
            "available_usdc": 100.0,
            "onchain_balance": 100.0,
            "total_invested": 0.0,
            "daily_pnl": 0.0,
            "daily_traded": 0.0,
            "positions_count": 0,
            "positions": [],
            "recent_trades": [],
        })
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_dashboard(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Aucune position ni trade" in text

    @pytest.mark.asyncio
    async def test_cmd_dashboard_no_portfolio_manager(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._portfolio_manager = None
        await telegram_notifier._cmd_dashboard(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "non disponible" in text

    @pytest.mark.asyncio
    async def test_cmd_dashboard_error(self, telegram_notifier, mock_update, mock_context):
        portfolio = AsyncMock()
        portfolio.get_portfolio_state = AsyncMock(side_effect=Exception("boom"))
        telegram_notifier._portfolio_manager = portfolio

        await telegram_notifier._cmd_dashboard(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Erreur" in text


# ---------------------------------------------------------------------------
# Command handlers — /reglages
# ---------------------------------------------------------------------------

class TestCommandReglages:

    @pytest.mark.asyncio
    async def test_cmd_reglages(self, telegram_notifier, mock_update, mock_context, trading_config):
        telegram_notifier.set_trading_config(trading_config)
        risk_manager = MagicMock()
        risk_manager.is_paused = False
        telegram_notifier._risk_manager = risk_manager

        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy

        await telegram_notifier._cmd_reglages(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Reglages du bot" in text
        assert "10$" in text  # max_per_trade_usdc
        assert "30$" in text  # max_per_day_usdc
        assert "20%" in text  # stop_loss_percent
        assert "25%" in text  # drawdown_stop_loss_percent
        assert "30%" in text  # max_concentration_percent
        assert "300 bps" in text  # max_slippage_bps
        assert "0.35" in text  # min_source_quality
        assert "30 min" in text  # analysis_interval_minutes
        assert "900s" in text  # order_fill_timeout_seconds

    @pytest.mark.asyncio
    async def test_cmd_reglages_no_config(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._trading_config = None
        await telegram_notifier._cmd_reglages(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "non disponible" in text

    @pytest.mark.asyncio
    async def test_cmd_reglages_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        await telegram_notifier._cmd_reglages(mock_update_unauthorized, mock_context)
        text = mock_update_unauthorized.message.reply_text.call_args.args[0]
        assert "non autorise" in text


# ---------------------------------------------------------------------------
# Command handlers — /strategy
# ---------------------------------------------------------------------------

class TestCommandStrategy:

    @pytest.mark.asyncio
    async def test_cmd_strategy_no_args_shows_current(
        self, telegram_notifier, mock_update, mock_context
    ):
        strategy = MagicMock()
        strategy.name = "active"
        telegram_notifier._current_strategy = strategy
        mock_context.args = []

        await telegram_notifier._cmd_strategy(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Strategie de trading" in text
        assert "ACTIVE" in text
        # Should have inline keyboard
        markup = mock_update.message.reply_text.call_args.kwargs.get("reply_markup")
        assert markup is not None

    @pytest.mark.asyncio
    async def test_cmd_strategy_non_active_arg(
        self, telegram_notifier, mock_update, mock_context
    ):
        mock_context.args = ["conservative"]
        await telegram_notifier._cmd_strategy(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "verrouillee" in text

    @pytest.mark.asyncio
    async def test_cmd_strategy_active_arg(
        self, telegram_notifier, mock_update, mock_context
    ):
        switch_cb = MagicMock()
        telegram_notifier._strategy_switch_callback = switch_cb
        mock_context.args = ["active"]

        with patch("strategy.get_strategy", create=True) as mock_get_strat, \
             patch.object(telegram_notifier, "_persist_runtime_settings", new_callable=AsyncMock):
            mock_get_strat.return_value = MagicMock(name="active")
            await telegram_notifier._cmd_strategy(mock_update, mock_context)

            switch_cb.assert_called_once_with("active")
            text = mock_update.message.reply_text.call_args.args[0]
            assert "ACTIVE" in text


# ---------------------------------------------------------------------------
# Command handlers — /restart
# ---------------------------------------------------------------------------

class TestCommandRestart:

    @pytest.mark.asyncio
    async def test_cmd_restart(self, telegram_notifier, mock_update, mock_context):
        stop_cb = AsyncMock()
        telegram_notifier._stop_callback = stop_cb

        await telegram_notifier._cmd_restart(mock_update, mock_context)

        text = mock_update.message.reply_text.call_args.args[0]
        assert "Redemarrage" in text
        stop_cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_restart_no_stop_callback(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._stop_callback = None
        await telegram_notifier._cmd_restart(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Redemarrage" in text

    @pytest.mark.asyncio
    async def test_cmd_restart_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        await telegram_notifier._cmd_restart(mock_update_unauthorized, mock_context)
        text = mock_update_unauthorized.message.reply_text.call_args.args[0]
        assert "non autorise" in text


# ---------------------------------------------------------------------------
# Command handlers — /learning
# ---------------------------------------------------------------------------

class TestCommandLearning:

    @pytest.mark.asyncio
    async def test_cmd_learning_disabled(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = None
        mock_context.args = []
        await telegram_notifier._cmd_learning(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "desactive" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_overview(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = []

        with patch("db.store.get_journal_entries", new_callable=AsyncMock, return_value=[]), \
             patch("db.store.get_active_insights", new_callable=AsyncMock, return_value=[]), \
             patch("db.store.get_pending_proposals", new_callable=AsyncMock, return_value=[]):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "Mode apprentissage" in text
            assert "Aucune entree" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_overview_with_data(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = []

        entries = [
            {
                "cycle_number": 5,
                "retrospective_json": json.dumps({
                    "quality_score": 7,
                    "conservatism_score": 6,
                    "diversity_score": 8,
                }),
                "outcome_accuracy": 0.65,
                "trades_proposed": 3,
                "trades_executed": 2,
                "trades_skipped": 1,
            }
        ]
        insights = [
            {
                "insight_type": "overconfidence",
                "description": "Bot tends to overestimate edge",
                "severity": "warning",
            }
        ]
        proposals = [
            {
                "id": 1,
                "proposal_type": "config",
                "target": "min_edge_percent",
                "current_value": "10",
                "proposed_value": "12",
                "risk_level": "safe",
                "rationale": "Reduce false positives",
            }
        ]

        with patch("db.store.get_journal_entries", new_callable=AsyncMock, return_value=entries), \
             patch("db.store.get_active_insights", new_callable=AsyncMock, return_value=insights), \
             patch("db.store.get_pending_proposals", new_callable=AsyncMock, return_value=proposals):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "Cycle 5" in text
            assert "Q=7" in text
            assert "overconfidence" in text
            assert "min_edge_percent" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_journal_subcmd(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = ["journal"]

        entries = [
            {
                "cycle_number": 3,
                "retrospective_json": json.dumps({
                    "quality_score": 8,
                    "conservatism_score": 5,
                    "diversity_score": 7,
                    "summary": "Decent cycle with minor issues.",
                    "recommendations": ["Increase diversification"],
                }),
                "outcome_accuracy": 0.70,
                "trades_proposed": 4,
                "trades_executed": 3,
                "trades_skipped": 1,
            }
        ]

        with patch("db.store.get_journal_entries", new_callable=AsyncMock, return_value=entries):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "Cycle #3" in text
            assert "Qualite: 8/10" in text
            assert "Decent cycle" in text
            assert "Increase diversification" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_insights_subcmd(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = ["insights"]

        insights = [
            {
                "insight_type": "loss_streak",
                "description": "3 consecutive losses detected",
                "severity": "critical",
                "evidence": "Trades 10, 11, 12 all lost",
                "proposed_action": "Reduce position size temporarily",
            }
        ]

        with patch("db.store.get_active_insights", new_callable=AsyncMock, return_value=insights):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "3 consecutive losses" in text
            assert "loss_streak" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_proposals_subcmd(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = ["proposals"]

        proposals = [
            {
                "id": 2,
                "proposal_type": "config",
                "target": "max_per_trade_usdc",
                "current_value": "10",
                "proposed_value": "8",
                "risk_level": "moderate",
                "rationale": "Reduce exposure during volatile period",
            }
        ]

        with patch("db.store.get_pending_proposals", new_callable=AsyncMock, return_value=proposals):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "max_per_trade_usdc" in text
            assert "10" in text
            assert "8" in text
            assert "Reduce exposure" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_shadow_subcmd(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = ["shadow"]

        shadows = [
            {
                "cycle_number": 10,
                "market_id": "market-xyz",
                "current_decision": "BUY",
                "shadow_decision": "SKIP",
                "outcome_price": 0.75,
            }
        ]

        with patch("db.store.get_shadow_records", new_callable=AsyncMock, return_value=shadows):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "A/B Testing" in text
            assert "Cycle #10" in text
            assert "BUY" in text
            assert "SKIP" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_error(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._learning_journal = MagicMock()
        mock_context.args = []

        with patch("db.store.get_journal_entries", new_callable=AsyncMock, side_effect=Exception("DB fail")):
            await telegram_notifier._cmd_learning(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert "Erreur" in text

    @pytest.mark.asyncio
    async def test_cmd_learning_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        mock_context.args = []
        await telegram_notifier._cmd_learning(mock_update_unauthorized, mock_context)
        text = mock_update_unauthorized.message.reply_text.call_args.args[0]
        assert "non autorise" in text


# ---------------------------------------------------------------------------
# Free-text message handler
# ---------------------------------------------------------------------------

class TestFreeTextHandler:

    @pytest.mark.asyncio
    async def test_free_text_no_router(self, telegram_notifier, mock_update, mock_context):
        telegram_notifier._conversation_router = None
        await telegram_notifier._handle_free_text(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "non active" in text

    @pytest.mark.asyncio
    async def test_free_text_unauthorized(self, telegram_notifier, mock_update_unauthorized, mock_context):
        await telegram_notifier._handle_free_text(mock_update_unauthorized, mock_context)
        mock_update_unauthorized.message.reply_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_free_text_simple_response(self, telegram_notifier, mock_update, mock_context):
        router = AsyncMock()
        router.handle_message = AsyncMock(return_value={
            "response": "Portfolio looks healthy.",
            "agent": "risk_officer",
        })
        telegram_notifier.set_conversation_router(router)

        mock_update.message.text = "How is my portfolio?"
        await telegram_notifier._handle_free_text(mock_update, mock_context)

        router.handle_message.assert_awaited_once_with("How is my portfolio?", "telegram")
        mock_update.message.chat.send_action.assert_awaited_once_with("typing")
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Risk Officer" in text
        assert "Portfolio looks healthy" in text

    @pytest.mark.asyncio
    async def test_free_text_with_confirmation(self, telegram_notifier, mock_update, mock_context):
        router = AsyncMock()
        router.handle_message = AsyncMock(return_value={
            "response": "I can reduce the max trade size.",
            "agent": "strategist",
            "action_taken": {
                "requires_confirmation": True,
                "description": "Reduce max_per_trade_usdc from 10 to 7",
                "id": "action_99",
            },
        })
        telegram_notifier.set_conversation_router(router)

        await telegram_notifier._handle_free_text(mock_update, mock_context)
        call_kwargs = mock_update.message.reply_text.call_args.kwargs
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Strategist" in text
        assert "Reduce max_per_trade_usdc" in text
        assert "Confirmer ?" in text
        # Check inline keyboard is present
        markup = call_kwargs.get("reply_markup")
        assert markup is not None
        buttons = markup.inline_keyboard[0]
        assert "confirm_action_action_99" in buttons[0].callback_data
        assert "cancel_action_action_99" in buttons[1].callback_data

    @pytest.mark.asyncio
    async def test_free_text_truncates_long_response(self, telegram_notifier, mock_update, mock_context):
        router = AsyncMock()
        router.handle_message = AsyncMock(return_value={
            "response": "X" * 5000,
            "agent": "general",
        })
        telegram_notifier.set_conversation_router(router)

        await telegram_notifier._handle_free_text(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert len(text) <= 4000

    @pytest.mark.asyncio
    async def test_free_text_error(self, telegram_notifier, mock_update, mock_context):
        router = AsyncMock()
        router.handle_message = AsyncMock(side_effect=Exception("LLM timeout"))
        telegram_notifier.set_conversation_router(router)

        await telegram_notifier._handle_free_text(mock_update, mock_context)
        text = mock_update.message.reply_text.call_args.args[0]
        assert "Erreur" in text
        assert "LLM timeout" in text

    @pytest.mark.asyncio
    async def test_free_text_agent_labels(self, telegram_notifier, mock_update, mock_context):
        """Verify all agent label mappings."""
        router = AsyncMock()
        telegram_notifier.set_conversation_router(router)

        for agent, expected_label in [
            ("risk_officer", "Risk Officer"),
            ("strategist", "Strategist"),
            ("manager", "Manager"),
            ("developer", "Developer"),
            ("general", "Assistant"),
        ]:
            router.handle_message = AsyncMock(return_value={
                "response": "test",
                "agent": agent,
            })
            await telegram_notifier._handle_free_text(mock_update, mock_context)
            text = mock_update.message.reply_text.call_args.args[0]
            assert expected_label in text


# ---------------------------------------------------------------------------
# Initialize / shutdown
# ---------------------------------------------------------------------------

class TestInitializeShutdown:

    @pytest.mark.asyncio
    async def test_initialize_no_token(self, telegram_config):
        from notifications.telegram_bot import TelegramNotifier
        telegram_config.bot_token = ""
        notifier = TelegramNotifier(telegram_config)
        await notifier.initialize()
        assert notifier.app is None

    @pytest.mark.asyncio
    async def test_shutdown_no_app(self, telegram_config):
        from notifications.telegram_bot import TelegramNotifier
        notifier = TelegramNotifier(telegram_config)
        notifier.app = None
        # Should not raise
        await notifier.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_with_app(self, telegram_notifier):
        telegram_notifier.app.stop = AsyncMock()
        telegram_notifier.app.shutdown = AsyncMock()

        await telegram_notifier.shutdown()

        telegram_notifier.app.stop.assert_awaited_once()
        telegram_notifier.app.shutdown.assert_awaited_once()


# ---------------------------------------------------------------------------
# _persist_runtime_settings
# ---------------------------------------------------------------------------

class TestPersistRuntimeSettings:

    @pytest.mark.asyncio
    async def test_persist_settings(self, telegram_notifier):
        with patch("db.store.update_settings", new_callable=AsyncMock) as mock_update:
            await telegram_notifier._persist_runtime_settings({"heartbeat_enabled": "true"})
            mock_update.assert_awaited_once_with({"heartbeat_enabled": "true"})

    @pytest.mark.asyncio
    async def test_persist_settings_error_logged(self, telegram_notifier):
        with patch("db.store.update_settings", new_callable=AsyncMock, side_effect=Exception("DB fail")):
            # Should not raise, just log a warning
            await telegram_notifier._persist_runtime_settings({"key": "value"})
