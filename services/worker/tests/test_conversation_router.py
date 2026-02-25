from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from conversation.router import ConversationRouter


class _DummyPortfolio:
    async def get_portfolio_state(self) -> dict:
        return {
            "available_usdc": 24.5,
            "positions_count": 1,
            "positions": [
                {
                    "market_question": "Will BTC be above $100k?",
                    "market_id": "m1",
                    "outcome": "Yes",
                    "size": 12.0,
                    "avg_price": 0.51,
                    "current_price": 0.54,
                    "pnl_unrealized": 0.36,
                }
            ],
            "daily_pnl": 0.7,
            "daily_traded": 5.0,
            "total_invested": 6.12,
            "portfolio_value": 39.7,
            "onchain_balance": 24.5,
        }


class _DummyRisk:
    def __init__(self):
        self.is_paused = False

    def resume_trading(self):
        self.is_paused = False


class _DummyPerformance:
    async def get_stats(self) -> dict:
        return {
            "resolved_trades": 3,
            "total_trades": 5,
            "pending_resolution": 2,
            "hit_rate": 0.66,
            "total_pnl": 1.2,
            "roi_percent": 3.1,
            "current_streak": 2,
            "streak_type": "wins",
        }


@pytest.fixture
def router():
    pm_client = MagicMock()
    pm_client.cancel_all_orders = MagicMock(return_value=True)

    cfg = SimpleNamespace(
        stop_loss_percent=15.0,
        drawdown_stop_loss_percent=20.0,
        heartbeat_enabled=True,
        conversation_enabled=True,
        conversation_max_history=20,
        risk_officer_enabled=False,
        strategist_enabled=False,
    )

    return ConversationRouter(
        pm_client=pm_client,
        portfolio_manager=_DummyPortfolio(),
        risk_manager=_DummyRisk(),
        performance_tracker=_DummyPerformance(),
        trading_config=cfg,
        anthropic_config=None,
    )


@pytest.mark.asyncio
async def test_status_message_persists_conversation(router, test_db):
    result = await router.handle_message("status", "telegram")

    assert result["agent"] == "manager"
    assert "Etat bot:" in result["response"]

    turns = await test_db.get_recent_conversations("telegram", limit=10)
    assert len(turns) == 2
    roles = {t["role"] for t in turns}
    assert "user" in roles
    assert "agent" in roles


@pytest.mark.asyncio
async def test_kill_requires_confirmation_and_executes(router):
    first = await router.handle_message("kill switch", "telegram")

    action = first.get("action_taken")
    assert action
    assert action["requires_confirmation"] is True
    action_id = action["id"]

    router.pm_client.cancel_all_orders.assert_not_called()
    done = await router.execute_confirmed_action(action_id)

    assert done["success"] is True
    router.pm_client.cancel_all_orders.assert_called_once()
    assert router.risk.is_paused is True


@pytest.mark.asyncio
async def test_dashboard_confirmation_by_text(router):
    first = await router.handle_message("kill", "dashboard")
    action_id = first["action_taken"]["id"]

    second = await router.handle_message(f"confirmer {action_id}", "dashboard")
    assert "execute" in second["response"].lower()
    router.pm_client.cancel_all_orders.assert_called_once()


@pytest.mark.asyncio
async def test_process_commands_chat_message(monkeypatch):
    import main as main_module

    bot = main_module.TradingBot()
    bot.conversation_router = SimpleNamespace(
        handle_message=AsyncMock(return_value={"agent": "manager", "response": "ok", "action_taken": None})
    )

    monkeypatch.setattr(
        main_module,
        "get_pending_commands",
        AsyncMock(return_value=[{"id": 1, "command": "chat_message", "payload": {"message": "status", "source": "dashboard"}}]),
    )
    executed = AsyncMock()
    failed = AsyncMock()
    monkeypatch.setattr(main_module, "mark_command_executed", executed)
    monkeypatch.setattr(main_module, "mark_command_failed", failed)

    await bot._process_commands()

    bot.conversation_router.handle_message.assert_awaited_once_with("status", "dashboard")
    executed.assert_awaited_once()
    failed.assert_not_awaited()


