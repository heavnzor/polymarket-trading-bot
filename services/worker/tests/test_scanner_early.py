"""Tests for early market detection in the MarketScanner."""

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from config import MarketMakingConfig, PolymarketConfig
from mm.scanner import MarketScanner


def _make_market(
    market_id: str = "test-market-1",
    question: str = "Will BTC reach $100k?",
    hours_old: float = 12,
    volume_24h: float = 5000,
    liquidity: float = 10000,
    yes_price: float = 0.55,
    end_days: int = 30,
    enable_order_book: bool = True,
    token_id: str = "token-123",
    description: str = "Test market description",
    resolution_source: str = "Official source",
) -> dict:
    """Create a fake Gamma API market dict."""
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(hours=hours_old)).isoformat()
    end_date = (now + timedelta(days=end_days)).isoformat()

    return {
        "id": market_id,
        "question": question,
        "description": description,
        "resolutionSource": resolution_source,
        "enableOrderBook": enable_order_book,
        "outcomePrices": json.dumps([yes_price, 1 - yes_price]),
        "volume24hr": volume_24h,
        "liquidity": liquidity,
        "createdAt": created_at,
        "endDate": end_date,
        "clobTokenIds": json.dumps([token_id]),
        "tokens": [{"token_id": token_id}],
    }


@pytest.fixture
def mm_config():
    cfg = MarketMakingConfig()
    cfg.mm_min_spread_pts = 3.0
    cfg.mm_min_depth_usd = 500.0
    cfg.mm_min_activity_per_min = 1.0
    cfg.mm_max_markets = 10
    cfg.mm_scanner_refresh_minutes = 5
    cfg.mm_early_market_hours = 48
    cfg.mm_early_market_min_depth_usd = 100.0
    cfg.mm_early_market_min_activity = 0.1
    cfg.mm_early_market_boost = 3
    cfg.mm_early_market_max_slots = 3
    return cfg


@pytest.fixture
def poly_config():
    return PolymarketConfig()


@pytest.fixture
def scanner(mm_config, poly_config):
    return MarketScanner(mm_config, poly_config)


class TestEarlyMarketDetection:
    """Tests for _is_early_market()."""

    def test_early_market_detected_within_threshold(self, scanner):
        """Market created 12h ago should be flagged as early (threshold=48h)."""
        market = _make_market(hours_old=12)
        is_early, hours_old = scanner._is_early_market(market)
        assert is_early is True
        assert 11 <= hours_old <= 13

    def test_old_market_not_flagged_early(self, scanner):
        """Market created 96h ago should NOT be flagged as early."""
        market = _make_market(hours_old=96)
        is_early, hours_old = scanner._is_early_market(market)
        assert is_early is False
        assert 95 <= hours_old <= 97

    def test_boundary_near_threshold(self, scanner):
        """Market created just under threshold (47h) should be early."""
        market = _make_market(hours_old=47)
        is_early, hours_old = scanner._is_early_market(market)
        assert is_early is True

    def test_missing_created_at(self, scanner):
        """Market without createdAt returns (False, -1)."""
        market = _make_market(hours_old=12)
        del market["createdAt"]
        is_early, hours_old = scanner._is_early_market(market)
        assert is_early is False
        assert hours_old == -1


class TestPrefilterEarlyMarkets:
    """Tests for early market handling in _prefilter_candidates()."""

    def test_early_market_passes_with_low_activity(self, scanner):
        """Early market with low volume should pass prefilter (relaxed threshold)."""
        # volume_24h=200 → activity_per_min = 200/1440 ≈ 0.14 > 0.1 (early threshold)
        market = _make_market(hours_old=12, volume_24h=200)
        candidates = scanner._prefilter_candidates([market])
        assert len(candidates) == 1
        assert candidates[0]["_is_early"] is True

    def test_old_market_filtered_with_low_activity(self, scanner):
        """Old market with same low volume should be filtered out (normal threshold)."""
        # volume_24h=200 → activity_per_min ≈ 0.14 < 1.0 (normal threshold)
        market = _make_market(hours_old=96, volume_24h=200)
        candidates = scanner._prefilter_candidates([market])
        assert len(candidates) == 0

    def test_early_tag_propagated(self, scanner):
        """Prefilter should tag markets with _is_early and _hours_old."""
        market = _make_market(hours_old=6)
        candidates = scanner._prefilter_candidates([market])
        assert len(candidates) == 1
        assert candidates[0]["_is_early"] is True
        assert candidates[0]["_hours_old"] > 0


class TestEvaluateEarlyFields:
    """Tests for early market fields in _evaluate_market() output."""

    def test_evaluate_returns_early_fields(self, scanner):
        """Evaluated market should include is_early, hours_old, description."""
        market = _make_market(hours_old=12)
        market["_is_early"] = True
        market["_hours_old"] = 12.0

        # Use no client → falls back to Gamma prices (spread=4.0)
        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert result["is_early"] is True
        assert result["hours_old"] == 12.0
        assert "description" in result
        assert "resolution_source" in result

    def test_evaluate_old_market_fields(self, scanner):
        """Old market should have is_early=False."""
        market = _make_market(hours_old=96, volume_24h=5000)
        market["_is_early"] = False
        market["_hours_old"] = 96.0

        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert result["is_early"] is False

    def test_early_market_relaxed_depth(self, scanner):
        """Early market should pass with lower depth (100$ vs 500$)."""
        market = _make_market(hours_old=12)
        market["_is_early"] = True
        market["_hours_old"] = 12.0

        mock_client = MagicMock()
        mock_client.get_book_summary.return_value = {
            "spread": 0.05,  # 5 pts
            "mid": 0.55,
            "bid_depth_5": 80,
            "ask_depth_5": 80,
        }

        result = scanner._evaluate_market(market, client=mock_client)
        assert result is not None  # 160$ depth > 100$ early threshold

    def test_old_market_filtered_by_depth(self, scanner):
        """Old market with same depth should be filtered (500$ normal threshold)."""
        market = _make_market(hours_old=96, volume_24h=5000)
        market["_is_early"] = False
        market["_hours_old"] = 96.0

        mock_client = MagicMock()
        mock_client.get_book_summary.return_value = {
            "spread": 0.05,
            "mid": 0.55,
            "bid_depth_5": 80,
            "ask_depth_5": 80,
        }

        result = scanner._evaluate_market(market, client=mock_client)
        assert result is None  # 160$ depth < 500$ normal threshold

    def test_question_expanded_to_200_chars(self, scanner):
        """Question should be truncated at 200 chars, not 100."""
        long_q = "A" * 250
        market = _make_market(hours_old=12, question=long_q)
        market["_is_early"] = True
        market["_hours_old"] = 12.0

        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert len(result["question"]) == 200


class TestScanEarlySlotCap:
    """Tests for early market slot cap in scan()."""

    def test_early_market_slot_cap(self, scanner, mm_config):
        """Only max_slots early markets should appear in final results."""
        # Create 6 early markets + 4 old markets
        early_markets = [
            _make_market(
                market_id=f"early-{i}",
                hours_old=12,
                volume_24h=200,
                token_id=f"tok-early-{i}",
            )
            for i in range(6)
        ]
        old_markets = [
            _make_market(
                market_id=f"old-{i}",
                hours_old=96,
                volume_24h=5000,
                token_id=f"tok-old-{i}",
            )
            for i in range(4)
        ]

        all_markets = early_markets + old_markets

        with patch.object(scanner, "_fetch_active_markets", return_value=all_markets):
            results = run(scanner.scan(client=None))

        early_in_results = [m for m in results if m.get("is_early")]
        assert len(early_in_results) <= mm_config.mm_early_market_max_slots

    def test_boost_sorting(self, scanner, mm_config):
        """Early market with lower spread should rank higher due to boost."""
        # Early market: spread=4.0 + boost=3 = effective 7.0
        early = _make_market(market_id="early-1", hours_old=12, volume_24h=200, token_id="tok-e")
        # Old market: spread=4.0 + 0 = effective 4.0
        old = _make_market(market_id="old-1", hours_old=96, volume_24h=5000, token_id="tok-o")

        with patch.object(scanner, "_fetch_active_markets", return_value=[old, early]):
            results = run(scanner.scan(client=None))

        if len(results) >= 2:
            # Early market should rank first (effective spread 7 > 4)
            assert results[0]["is_early"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# YES + NO token extraction and condition_id
# ═══════════════════════════════════════════════════════════════════════════════


def _make_dual_token_market(
    market_id: str = "dual-market-1",
    yes_token_id: str = "yes-tok-123",
    no_token_id: str = "no-tok-456",
    condition_id: str = "cond-abc-789",
    hours_old: float = 12,
    volume_24h: float = 5000,
    yes_price: float = 0.55,
    end_days: int = 30,
) -> dict:
    """Create a market dict with both YES and NO tokens."""
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(hours=hours_old)).isoformat()
    end_date = (now + timedelta(days=end_days)).isoformat()

    return {
        "id": market_id,
        "question": "Will ETH reach $10k?",
        "description": "ETH prediction market",
        "resolutionSource": "Official source",
        "enableOrderBook": True,
        "outcomePrices": json.dumps([yes_price, 1 - yes_price]),
        "volume24hr": volume_24h,
        "liquidity": 10000,
        "createdAt": created_at,
        "endDate": end_date,
        "conditionId": condition_id,
        "clobTokenIds": json.dumps([yes_token_id, no_token_id]),
        "tokens": [
            {"token_id": yes_token_id},
            {"token_id": no_token_id},
        ],
    }


class TestDualTokenExtraction:
    """Tests for YES + NO token extraction in _evaluate_market()."""

    def test_extracts_yes_and_no_token_ids(self, scanner):
        market = _make_dual_token_market()
        market["_is_early"] = True
        market["_hours_old"] = 12.0
        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert result["yes_token_id"] == "yes-tok-123"
        assert result["no_token_id"] == "no-tok-456"
        # Backward compat: token_id should equal yes_token_id
        assert result["token_id"] == "yes-tok-123"

    def test_extracts_condition_id(self, scanner):
        market = _make_dual_token_market(condition_id="0x" + "ab" * 32)
        market["_is_early"] = True
        market["_hours_old"] = 12.0
        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert result["condition_id"] == "0x" + "ab" * 32

    def test_single_token_sets_no_to_none(self, scanner):
        """Market with only 1 token should have no_token_id=None."""
        market = _make_market(hours_old=12)
        market["_is_early"] = True
        market["_hours_old"] = 12.0
        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert result["no_token_id"] is None

    def test_fallback_to_clob_token_ids(self, scanner):
        """When tokens list is absent, falls back to clobTokenIds."""
        market = _make_dual_token_market()
        market["_is_early"] = True
        market["_hours_old"] = 12.0
        del market["tokens"]  # Force fallback to clobTokenIds
        result = scanner._evaluate_market(market, client=None)
        assert result is not None
        assert result["yes_token_id"] == "yes-tok-123"
        assert result["no_token_id"] == "no-tok-456"
