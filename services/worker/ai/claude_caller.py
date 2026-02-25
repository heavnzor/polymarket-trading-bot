"""Centralized Claude API caller with multi-model support.

Provides call_claude() and call_claude_json() for all new CD AI features.
Existing code (claude_guard, router, strategist) is NOT migrated — only new
features use this module.
"""

import asyncio
import json
import logging
import re
from enum import Enum

import anthropic

from config import AnthropicConfig

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"


def _resolve_model(config: AnthropicConfig, tier: ModelTier) -> str:
    """Map a ModelTier to the concrete model name from config."""
    if tier == ModelTier.SONNET:
        return config.model_sonnet
    elif tier == ModelTier.HAIKU:
        return config.model_haiku
    return config.model  # OPUS (default)


def _extract_json(text: str) -> dict | None:
    """Extract JSON from Claude response, with regex fallback.

    Consolidated from mm/claude_guard.py.
    """
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON in code block or bare object
    patterns = [
        re.compile(r"```json\s*\n?(.*?)\n?```", re.DOTALL),
        re.compile(r"(\{.*\})", re.DOTALL),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, IndexError):
                continue
    return None


async def call_claude(
    anthropic_config: AnthropicConfig,
    tier: ModelTier,
    user_prompt: str,
    system_prompt: str = "",
    max_tokens: int = 1024,
) -> str:
    """Make a single Claude API call and return the text response.

    Runs the synchronous anthropic SDK call in a thread to avoid blocking
    the async event loop (same pattern as mm/claude_guard.py).
    """
    if not anthropic_config.api_key:
        logger.warning("No Anthropic API key configured")
        return ""

    client_kwargs = {"api_key": anthropic_config.api_key}
    if anthropic_config.base_url:
        client_kwargs["base_url"] = anthropic_config.base_url

    client = anthropic.Anthropic(**client_kwargs)
    model = _resolve_model(anthropic_config, tier)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = await asyncio.to_thread(client.messages.create, **kwargs)

    text = response.content[0].text if response.content else ""
    return text


async def call_claude_json(
    anthropic_config: AnthropicConfig,
    tier: ModelTier,
    user_prompt: str,
    system_prompt: str = "",
    max_tokens: int = 1024,
) -> dict | None:
    """Call Claude and parse the response as JSON.

    Returns the parsed dict, or None if the response is not valid JSON.
    """
    text = await call_claude(
        anthropic_config, tier, user_prompt, system_prompt, max_tokens
    )
    if not text:
        return None
    return _extract_json(text)
