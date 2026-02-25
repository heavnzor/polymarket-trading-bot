"""Tests for services/worker/config.py — all config dataclasses and env loading."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the worker package is importable
WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_fresh(name: str):
    """Import (or re-import) a module from the worker package, bypassing cache.

    Patches ``dotenv.load_dotenv`` to a no-op so that re-importing the module
    does not re-read a real ``.env`` file and pollute the patched environment.
    """
    mod_key = name
    if mod_key in sys.modules:
        del sys.modules[mod_key]
    import importlib
    with patch("dotenv.load_dotenv", return_value=None):
        return importlib.import_module(name)


# =========================================================================
# PolymarketConfig
# =========================================================================

class TestPolymarketConfigDefaults:
    """When no env vars are set the dataclass should fall back to defaults."""

    def test_defaults_no_env(self):
        env_override = {
            "POLYMARKET_PRIVATE_KEY": "",
            "POLYMARKET_FUNDER_ADDRESS": "",
            "POLYGON_RPC_URL": "",
        }
        # Remove the keys so getenv falls through to default
        cleaned = os.environ.copy()
        for k in env_override:
            cleaned.pop(k, None)

        with patch.dict(os.environ, cleaned, clear=True):
            mod = _import_fresh("config")
            cfg = mod.PolymarketConfig()

        assert cfg.host == "https://clob.polymarket.com"
        assert cfg.gamma_api == "https://gamma-api.polymarket.com"
        assert cfg.chain_id == 137
        assert cfg.private_key == ""
        assert cfg.funder_address == ""
        assert cfg.rpc_url == "https://polygon-rpc.com"
        assert cfg.signature_type == 1
        assert cfg.usdc_e_address == "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


class TestPolymarketConfigFromEnv:
    """When env vars are present they override defaults."""

    def test_loads_from_env(self):
        env = {
            "POLYMARKET_PRIVATE_KEY": "0xdeadbeef",
            "POLYMARKET_FUNDER_ADDRESS": "0xcafebabe",
            "POLYGON_RPC_URL": "https://my-rpc.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            cfg = mod.PolymarketConfig()

        assert cfg.private_key == "0xdeadbeef"
        assert cfg.funder_address == "0xcafebabe"
        assert cfg.rpc_url == "https://my-rpc.example.com"


# =========================================================================
# AnthropicConfig
# =========================================================================

class TestAnthropicConfigDefaults:

    def test_defaults_no_env(self):
        keys = [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_FOUNDRY_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_API_VERSION",
        ]
        cleaned = os.environ.copy()
        for k in keys:
            cleaned.pop(k, None)

        with patch.dict(os.environ, cleaned, clear=True):
            mod = _import_fresh("config")
            cfg = mod.AnthropicConfig()

        assert cfg.api_key == ""
        assert cfg.base_url == ""
        assert cfg.model == "claude-opus-4-6"
        assert cfg.api_version == "2024-05-01-preview"


class TestAnthropicConfigFromEnv:

    def test_loads_from_env(self):
        env = {
            "ANTHROPIC_API_KEY": "sk-test-key-123",
            "ANTHROPIC_FOUNDRY_BASE_URL": "https://foundry.example.com",
            "ANTHROPIC_MODEL": "claude-sonnet-4-5",
            "ANTHROPIC_API_VERSION": "2025-01-01",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            cfg = mod.AnthropicConfig()

        assert cfg.api_key == "sk-test-key-123"
        assert cfg.base_url == "https://foundry.example.com"
        assert cfg.model == "claude-sonnet-4-5"
        assert cfg.api_version == "2025-01-01"


# =========================================================================
# TelegramConfig
# =========================================================================

class TestTelegramConfigDefaults:

    def test_defaults_no_env(self):
        cleaned = os.environ.copy()
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            cleaned.pop(k, None)

        with patch.dict(os.environ, cleaned, clear=True):
            mod = _import_fresh("config")
            cfg = mod.TelegramConfig()

        assert cfg.bot_token == ""
        assert cfg.chat_id == ""


class TestTelegramConfigFromEnv:

    def test_loads_from_env(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "999888:XYZ",
            "TELEGRAM_CHAT_ID": "42",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TelegramConfig()

        assert cfg.bot_token == "999888:XYZ"
        assert cfg.chat_id == "42"


# =========================================================================
# TradingConfig — defaults
# =========================================================================

class TestTradingConfigDefaults:

    def _make_config_no_env(self):
        """Create a TradingConfig with all trading-related env vars removed."""
        keys_to_remove = [
            "STOP_LOSS_PERCENT", "DRAWDOWN_STOP_LOSS_PERCENT",
            "HEARTBEAT_ENABLED", "HEARTBEAT_INTERVAL_SECONDS",
            "RISK_OFFICER_ENABLED", "STRATEGIST_ENABLED",
            "CONVERSATION_ENABLED", "CONVERSATION_MAX_HISTORY",
        ]
        cleaned = os.environ.copy()
        for k in keys_to_remove:
            cleaned.pop(k, None)

        with patch.dict(os.environ, cleaned, clear=True):
            mod = _import_fresh("config")
            return mod.TradingConfig()

    def test_float_defaults(self):
        cfg = self._make_config_no_env()
        assert cfg.stop_loss_percent == 20.0
        assert cfg.drawdown_stop_loss_percent == 25.0

    def test_boolean_defaults(self):
        cfg = self._make_config_no_env()
        # All boolean fields default to True (env default is "true")
        assert cfg.heartbeat_enabled is True
        assert cfg.risk_officer_enabled is True
        assert cfg.strategist_enabled is True
        assert cfg.conversation_enabled is True

    def test_int_defaults(self):
        cfg = self._make_config_no_env()
        assert cfg.heartbeat_interval_seconds == 5
        assert cfg.conversation_max_history == 20


# =========================================================================
# TradingConfig — loading from env
# =========================================================================

class TestTradingConfigFromEnv:

    def test_float_fields_from_env(self):
        env = {
            "STOP_LOSS_PERCENT": "30.0",
            "DRAWDOWN_STOP_LOSS_PERCENT": "35.0",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()

        assert cfg.stop_loss_percent == 30.0
        assert cfg.drawdown_stop_loss_percent == 35.0

    def test_int_fields_from_env(self):
        env = {
            "HEARTBEAT_INTERVAL_SECONDS": "10",
            "CONVERSATION_MAX_HISTORY": "50",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()

        assert cfg.heartbeat_interval_seconds == 10
        assert cfg.conversation_max_history == 50

    def test_boolean_fields_from_env(self):
        env = {
            "HEARTBEAT_ENABLED": "false",
            "RISK_OFFICER_ENABLED": "false",
            "STRATEGIST_ENABLED": "false",
            "CONVERSATION_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()

        assert cfg.heartbeat_enabled is False
        assert cfg.risk_officer_enabled is False
        assert cfg.strategist_enabled is False
        assert cfg.conversation_enabled is False


# =========================================================================
# TradingConfig — boolean type conversion from various string values
# =========================================================================

class TestTradingConfigBoolConversion:
    """
    Boolean fields use the pattern:
        os.getenv("KEY", "true").lower() in ("true", "1", "yes")
    Test all accepted truthy values and several falsy values.
    """

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "Yes", "YES"])
    def test_heartbeat_enabled_truthy(self, value):
        with patch.dict(os.environ, {"HEARTBEAT_ENABLED": value}, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()
        assert cfg.heartbeat_enabled is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "No", "", "random"])
    def test_heartbeat_enabled_falsy(self, value):
        with patch.dict(os.environ, {"HEARTBEAT_ENABLED": value}, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()
        assert cfg.heartbeat_enabled is False

    @pytest.mark.parametrize("field,env_key", [
        ("risk_officer_enabled", "RISK_OFFICER_ENABLED"),
        ("strategist_enabled", "STRATEGIST_ENABLED"),
        ("conversation_enabled", "CONVERSATION_ENABLED"),
    ])
    def test_default_true_fields_set_false(self, field, env_key):
        """Fields that default to True can be disabled with 'false'."""
        with patch.dict(os.environ, {env_key: "false"}, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()
        assert getattr(cfg, field) is False

    @pytest.mark.parametrize("field,env_key", [
        ("heartbeat_enabled", "HEARTBEAT_ENABLED"),
        ("risk_officer_enabled", "RISK_OFFICER_ENABLED"),
        ("strategist_enabled", "STRATEGIST_ENABLED"),
        ("conversation_enabled", "CONVERSATION_ENABLED"),
    ])
    def test_default_true_fields_set_true(self, field, env_key):
        """Fields that default to True remain True when explicitly set."""
        with patch.dict(os.environ, {env_key: "true"}, clear=False):
            mod = _import_fresh("config")
            cfg = mod.TradingConfig()
        assert getattr(cfg, field) is True


# =========================================================================
# AppConfig.load()
# =========================================================================

class TestAppConfigLoad:

    def test_load_creates_all_sub_configs(self):
        mod = _import_fresh("config")
        app = mod.AppConfig.load()

        assert isinstance(app.polymarket, mod.PolymarketConfig)
        assert isinstance(app.anthropic, mod.AnthropicConfig)
        assert isinstance(app.telegram, mod.TelegramConfig)
        assert isinstance(app.trading, mod.TradingConfig)
        assert isinstance(app.mm, mod.MarketMakingConfig)
        assert isinstance(app.cd, mod.CryptoDirectionalConfig)
        assert isinstance(app.guard, mod.ClaudeGuardConfig)

    def test_load_sub_configs_have_correct_types(self):
        mod = _import_fresh("config")
        app = mod.AppConfig.load()

        assert isinstance(app.polymarket.chain_id, int)
        assert isinstance(app.trading.heartbeat_enabled, bool)
        assert isinstance(app.trading.heartbeat_interval_seconds, int)
        assert isinstance(app.anthropic.model, str)
        assert isinstance(app.telegram.bot_token, str)
        assert isinstance(app.mm.mm_enabled, bool)
        assert isinstance(app.cd.cd_enabled, bool)
        assert isinstance(app.guard.guard_enabled, bool)

    def test_load_respects_env_vars(self):
        env = {
            "POLYMARKET_PRIVATE_KEY": "0x1234",
            "ANTHROPIC_API_KEY": "sk-from-env",
            "TELEGRAM_BOT_TOKEN": "tok-from-env",
        }
        with patch.dict(os.environ, env, clear=False):
            mod = _import_fresh("config")
            app = mod.AppConfig.load()

        assert app.polymarket.private_key == "0x1234"
        assert app.anthropic.api_key == "sk-from-env"
        assert app.telegram.bot_token == "tok-from-env"
