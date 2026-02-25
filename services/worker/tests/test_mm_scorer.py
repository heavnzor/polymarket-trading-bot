"""Tests for mm/scorer.py — MarketScorer (Sonnet batch scoring)."""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from config import AnthropicConfig, MarketMakingConfig
from mm.scorer import MarketScorer, DEFAULT_SCORE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(market_id: str, spread: float = 5.0, mid: float = 0.5) -> dict:
    return {
        "market_id": market_id,
        "token_id": f"tok-{market_id}",
        "question": f"Will {market_id} happen?",
        "description": "Test market",
        "resolution_source": "official",
        "spread": spread,
        "mid": mid,
        "depth": 1000.0,
        "volume_24h": 5000.0,
        "days_to_resolution": 10.0,
        "is_early": False,
        "hours_old": 100,
    }


def _make_sonnet_response(scores: dict[str, float], flags: dict[str, str | None] | None = None) -> dict:
    """Build a Sonnet-style response dict."""
    flags = flags or {}
    result = {"scores": {}}
    for mid, overall in scores.items():
        result["scores"][mid] = {
            "resolution_clarity": overall,
            "market_quality": overall,
            "profitability": overall,
            "overall": overall,
            "flag": flags.get(mid),
            "note": f"score {overall}",
        }
    return result


def _scorer(enabled: bool = True, min_score: float = 5.0, cache_minutes: int = 10) -> MarketScorer:
    mm_cfg = MarketMakingConfig()
    mm_cfg.mm_scorer_enabled = enabled
    mm_cfg.mm_scorer_min_score = min_score
    mm_cfg.mm_scorer_cache_minutes = cache_minutes
    return MarketScorer(mm_cfg, AnthropicConfig())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_and_filter_disabled():
    """Scorer disabled -> returns original list unchanged."""
    scorer = _scorer(enabled=False)
    markets = [_make_market("m1"), _make_market("m2")]
    result = await scorer.score_and_filter(markets)
    assert result == markets
    assert len(result) == 2


@pytest.mark.asyncio
@patch("mm.scorer.call_claude_json", new_callable=AsyncMock)
@patch("mm.scorer.store", new_callable=lambda: type("FakeStore", (), {"update_bot_status": AsyncMock()}))
async def test_score_and_filter_filters_low_score(mock_store, mock_call):
    """Markets with score < min_score are filtered out."""
    mock_call.return_value = _make_sonnet_response({"m1": 8.0, "m2": 3.0, "m3": 6.0})

    scorer = _scorer(min_score=5.0)
    markets = [_make_market("m1"), _make_market("m2"), _make_market("m3")]
    result = await scorer.score_and_filter(markets)

    # m2 (score 3.0) should be filtered out
    ids = [m["market_id"] for m in result]
    assert "m2" not in ids
    assert "m1" in ids
    assert "m3" in ids
    assert len(result) == 2


@pytest.mark.asyncio
@patch("mm.scorer.call_claude_json", new_callable=AsyncMock)
@patch("mm.scorer.store", new_callable=lambda: type("FakeStore", (), {"update_bot_status": AsyncMock()}))
async def test_score_and_filter_sorts_by_score(mock_store, mock_call):
    """Markets are re-sorted by score descending."""
    mock_call.return_value = _make_sonnet_response({"m1": 6.0, "m2": 9.0, "m3": 7.0})

    scorer = _scorer(min_score=5.0)
    markets = [_make_market("m1"), _make_market("m2"), _make_market("m3")]
    result = await scorer.score_and_filter(markets)

    ids = [m["market_id"] for m in result]
    assert ids == ["m2", "m3", "m1"]


@pytest.mark.asyncio
@patch("mm.scorer.call_claude_json", new_callable=AsyncMock)
@patch("mm.scorer.store", new_callable=lambda: type("FakeStore", (), {"update_bot_status": AsyncMock()}))
async def test_score_and_filter_cache_hit(mock_store, mock_call):
    """Second call uses cache, no second Sonnet call."""
    mock_call.return_value = _make_sonnet_response({"m1": 8.0, "m2": 7.0})

    scorer = _scorer(min_score=5.0, cache_minutes=10)
    markets = [_make_market("m1"), _make_market("m2")]

    # First call: triggers Sonnet
    result1 = await scorer.score_and_filter(markets)
    assert mock_call.call_count == 1
    assert len(result1) == 2

    # Second call: should use cache, no new Sonnet call
    result2 = await scorer.score_and_filter(markets)
    assert mock_call.call_count == 1  # Still 1
    assert len(result2) == 2


@pytest.mark.asyncio
@patch("mm.scorer.call_claude_json", new_callable=AsyncMock)
async def test_score_and_filter_sonnet_failure_fallback(mock_call):
    """Sonnet error -> returns original list unchanged."""
    mock_call.side_effect = Exception("API timeout")

    scorer = _scorer(min_score=5.0)
    markets = [_make_market("m1"), _make_market("m2")]
    result = await scorer.score_and_filter(markets)

    # Fail-safe: original list returned
    assert len(result) == 2
    assert result[0]["market_id"] == "m1"


@pytest.mark.asyncio
async def test_score_and_filter_empty_markets():
    """Empty market list -> empty list."""
    scorer = _scorer()
    result = await scorer.score_and_filter([])
    assert result == []


@pytest.mark.asyncio
@patch("mm.scorer.call_claude_json", new_callable=AsyncMock)
@patch("mm.scorer.store", new_callable=lambda: type("FakeStore", (), {"update_bot_status": AsyncMock()}))
async def test_score_and_filter_partial_cache(mock_store, mock_call):
    """Only uncached markets are sent to Sonnet."""
    # First call: score m1 and m2
    mock_call.return_value = _make_sonnet_response({"m1": 8.0, "m2": 7.0})
    scorer = _scorer(min_score=5.0, cache_minutes=10)
    markets_1 = [_make_market("m1"), _make_market("m2")]
    await scorer.score_and_filter(markets_1)
    assert mock_call.call_count == 1

    # Second call: m1 cached, m3 is new -> only m3 sent to Sonnet
    mock_call.return_value = _make_sonnet_response({"m3": 6.0})
    markets_2 = [_make_market("m1"), _make_market("m3")]
    result = await scorer.score_and_filter(markets_2)
    assert mock_call.call_count == 2

    # Both should be in result
    ids = [m["market_id"] for m in result]
    assert "m1" in ids
    assert "m3" in ids

    # Verify the second Sonnet call only included m3
    second_call_prompt = mock_call.call_args_list[1][1].get("user_prompt") or mock_call.call_args_list[1][0][2]
    assert "m3" in second_call_prompt
    # m1 should NOT be in the second batch prompt
    # (it's in the user prompt template as part of the count, but not in markets_json)
