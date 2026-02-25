"""Shared fixtures for the Polymarket trading bot test suite."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure worker package is on sys.path
WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

# Set dummy env vars BEFORE importing config so __post_init__ does not rely on real secrets
os.environ.setdefault("POLYMARKET_PRIVATE_KEY", "0x" + "a1" * 32)
os.environ.setdefault("POLYMARKET_FUNDER_ADDRESS", "0x" + "b2" * 20)
os.environ.setdefault("POLYGON_RPC_URL", "https://polygon-rpc.com")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_FOUNDRY_BASE_URL", "https://test.anthropic.com")
os.environ.setdefault("ANTHROPIC_MODEL", "claude-opus-4-6")
os.environ.setdefault("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
os.environ.setdefault("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345678")
os.environ.setdefault("MIN_EDGE_PERCENT", "10")


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def polymarket_config():
    from config import PolymarketConfig
    return PolymarketConfig()


@pytest.fixture
def anthropic_config():
    from config import AnthropicConfig
    return AnthropicConfig()


@pytest.fixture
def telegram_config():
    from config import TelegramConfig
    return TelegramConfig()


@pytest.fixture
def trading_config():
    from config import TradingConfig
    cfg = TradingConfig()
    cfg.stop_loss_percent = 20.0
    cfg.drawdown_stop_loss_percent = 25.0
    # Fields accessed by telegram_bot._cmd_reglages / send_daily_summary
    # (not in TradingConfig dataclass but set dynamically at runtime)
    cfg.max_per_trade_usdc = 10.0
    cfg.max_per_day_usdc = 30.0
    cfg.min_edge_percent = 10.0
    cfg.min_net_edge_percent = 8.0
    cfg.max_concentration_percent = 30.0
    cfg.max_correlated_positions = 3
    cfg.max_slippage_bps = 300.0
    cfg.min_source_quality = 0.35
    cfg.estimated_fee_bps = 20.0
    cfg.order_fill_timeout_seconds = 900
    cfg.confirmation_threshold_usdc = 5.0
    cfg.analysis_interval_minutes = 30
    cfg.min_cycle_minutes = 15
    cfg.max_cycle_minutes = 60
    return cfg


@pytest.fixture
def mm_config():
    from config import MarketMakingConfig
    cfg = MarketMakingConfig()
    cfg.mm_dd_reduce_pct = 5.0
    cfg.mm_dd_kill_pct = 10.0
    return cfg


@pytest.fixture
def app_config(polymarket_config, anthropic_config, telegram_config, trading_config, mm_config):
    from config import AppConfig, CryptoDirectionalConfig, ClaudeGuardConfig
    return AppConfig(
        polymarket=polymarket_config,
        anthropic=anthropic_config,
        telegram=telegram_config,
        trading=trading_config,
        mm=mm_config,
        cd=CryptoDirectionalConfig(),
        guard=ClaudeGuardConfig(),
    )


# ---------------------------------------------------------------------------
# Database fixtures — each test gets its own fresh in-memory SQLite DB
# ---------------------------------------------------------------------------

@pytest.fixture
async def test_db(tmp_path):
    """Provide a fresh SQLite database for each test."""
    import db.store as store

    db_file = tmp_path / "test.db"
    # Override the module-level DB_PATH and reset singleton
    store.DB_PATH = db_file
    store._db = None

    await store.init_db()
    yield store
    await store.close_db()


# ---------------------------------------------------------------------------
# Mock Polymarket client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pm_client():
    """Return a mocked PolymarketClient."""
    client = MagicMock()
    client.get_midpoint = MagicMock(return_value=0.55)
    client.get_price = MagicMock(return_value=0.55)
    client.get_order_book = MagicMock(return_value=None)
    client.get_onchain_balance = MagicMock(return_value=50.0)
    client.place_limit_order = MagicMock(return_value={"orderID": "order-123"})
    client.is_order_filled = MagicMock(return_value=(True, "MATCHED", 10.0, {}))
    client.get_order = MagicMock(return_value={"status": "MATCHED", "size_matched": "10.0"})
    client.cancel_order = MagicMock(return_value=True)
    client.cancel_all_orders = MagicMock(return_value=True)
    client.get_open_orders = MagicMock(return_value=[])
    client.check_market_resolved = MagicMock(return_value=None)
    client.connect = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_market():
    from data.markets import Market
    return Market(
        id="market-abc-123",
        question="Will BTC reach $100k by end of 2026?",
        description="Bitcoin price prediction market",
        outcomes=["Yes", "No"],
        outcome_prices=[0.55, 0.45],
        token_ids=["token-yes-123", "token-no-456"],
        volume=50000.0,
        liquidity=15000.0,
        best_bid=0.54,
        best_ask=0.56,
        end_date="2026-12-31",
        active=True,
        accepting_orders=True,
        category="crypto",
    )


@pytest.fixture
def sample_trade():
    return {
        "market_id": "market-abc-123",
        "market_question": "Will BTC reach $100k by end of 2026?",
        "token_id": "token-yes-123",
        "category": "crypto",
        "side": "BUY",
        "outcome": "Yes",
        "size_usdc": 5.0,
        "price": 0.55,
        "edge": 0.15,
        "edge_net": 0.12,
        "confidence": 0.75,
        "reasoning": "Strong momentum",
        "strategy": "active",
        "source_quality": 0.8,
        "estimated_slippage_bps": 50.0,
        "liquidity_score": 8.0,
    }


@pytest.fixture
def sample_position():
    return {
        "market_id": "market-abc-123",
        "token_id": "token-yes-123",
        "market_question": "Will BTC reach $100k?",
        "outcome": "Yes",
        "size": 10.0,
        "avg_price": 0.55,
        "current_price": 0.60,
        "category": "crypto",
        "strategy": "active",
    }


@pytest.fixture
def sample_portfolio_state():
    return {
        "available_usdc": 50.0,
        "positions_count": 2,
        "positions": [],
        "positions_summary": "None",
        "daily_pnl": 1.5,
        "daily_traded": 10.0,
        "total_invested": 20.0,
        "recent_trades": [],
        "portfolio_value": 101.5,
        "onchain_balance": 50.0,
    }


# ---------------------------------------------------------------------------
# Mock Claude caller
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_claude_caller():
    """A callable that returns canned JSON from Claude."""
    def caller(system_prompt, user_prompt, max_tokens=4096):
        return '{"action": "SKIP", "reasoning": "test"}'
    return caller
