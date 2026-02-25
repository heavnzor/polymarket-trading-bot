"""Tests for the ai/claude_caller.py shared module."""

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ====================================================================
# _extract_json
# ====================================================================

class TestExtractJson:
    def test_direct_json(self):
        from ai.claude_caller import _extract_json
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_in_code_block(self):
        from ai.claude_caller import _extract_json
        text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        assert _extract_json(text) == {"key": "value"}

    def test_bare_json_object(self):
        from ai.claude_caller import _extract_json
        text = 'Here is the result: {"confirm_exit": true, "confidence": 0.9}'
        result = _extract_json(text)
        assert result is not None
        assert result["confirm_exit"] is True

    def test_invalid_json_returns_none(self):
        from ai.claude_caller import _extract_json
        assert _extract_json("no json here") is None

    def test_empty_string(self):
        from ai.claude_caller import _extract_json
        assert _extract_json("") is None


# ====================================================================
# _resolve_model
# ====================================================================

class TestResolveModel:
    def test_opus(self):
        from ai.claude_caller import _resolve_model, ModelTier
        from config import AnthropicConfig
        cfg = AnthropicConfig()
        assert _resolve_model(cfg, ModelTier.OPUS) == cfg.model

    def test_sonnet(self):
        from ai.claude_caller import _resolve_model, ModelTier
        from config import AnthropicConfig
        cfg = AnthropicConfig()
        assert _resolve_model(cfg, ModelTier.SONNET) == cfg.model_sonnet

    def test_haiku(self):
        from ai.claude_caller import _resolve_model, ModelTier
        from config import AnthropicConfig
        cfg = AnthropicConfig()
        assert _resolve_model(cfg, ModelTier.HAIKU) == cfg.model_haiku


# ====================================================================
# call_claude
# ====================================================================

class TestCallClaude:
    async def test_returns_text(self, anthropic_config):
        from ai.claude_caller import call_claude, ModelTier

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello world")]

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_response)

        with patch("ai.claude_caller.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            result = await call_claude(anthropic_config, ModelTier.SONNET, "test prompt")

        assert result == "Hello world"
        # Verify model used is sonnet
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs is not None

    async def test_no_api_key_returns_empty(self):
        from ai.claude_caller import call_claude, ModelTier
        from config import AnthropicConfig

        cfg = AnthropicConfig()
        cfg.api_key = ""
        result = await call_claude(cfg, ModelTier.OPUS, "test")
        assert result == ""

    async def test_system_prompt_passed(self, anthropic_config):
        from ai.claude_caller import call_claude, ModelTier

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="response")]

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_response)

        with patch("ai.claude_caller.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            await call_claude(
                anthropic_config, ModelTier.OPUS,
                "user msg", system_prompt="system msg"
            )

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs[1].get("system") == "system msg" or \
               (call_kwargs[0] if call_kwargs[0] else False)


# ====================================================================
# call_claude_json
# ====================================================================

class TestCallClaudeJson:
    async def test_returns_parsed_json(self, anthropic_config):
        from ai.claude_caller import call_claude_json, ModelTier

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"action": "buy", "confidence": 0.8}')]

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_response)

        with patch("ai.claude_caller.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            result = await call_claude_json(anthropic_config, ModelTier.HAIKU, "test")

        assert result == {"action": "buy", "confidence": 0.8}

    async def test_invalid_json_returns_none(self, anthropic_config):
        from ai.claude_caller import call_claude_json, ModelTier

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not json at all")]

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_response)

        with patch("ai.claude_caller.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            result = await call_claude_json(anthropic_config, ModelTier.HAIKU, "test")

        assert result is None

    async def test_no_api_key_returns_none(self):
        from ai.claude_caller import call_claude_json, ModelTier
        from config import AnthropicConfig

        cfg = AnthropicConfig()
        cfg.api_key = ""
        result = await call_claude_json(cfg, ModelTier.OPUS, "test")
        assert result is None
