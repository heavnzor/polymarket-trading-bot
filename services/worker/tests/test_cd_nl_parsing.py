"""Tests for NL market parsing with Claude Sonnet (Feature 1)."""

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_raw_market(question, market_id="m-1", token_id="t-1", price=0.55, end_date=None):
    """Build a minimal raw market dict matching Gamma API response shape."""
    return {
        "id": market_id,
        "question": question,
        "tokens": [{"token_id": token_id}],
        "outcomePrices": [str(price), str(1 - price)],
        "endDate": end_date,
    }


class TestParseMarketsBatch:
    """Tests for parse_markets_batch with Claude NL + regex fallback."""

    async def test_claude_parses_standard_question(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will BTC be above $100,000 on June 30?")]

        claude_response = {
            "results": {
                "0": {"coin": "BTC", "coingecko_id": "bitcoin", "strike": 100000.0, "direction": "above"}
            }
        }

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        assert len(markets) == 1
        assert markets[0]["coin"] == "BTC"
        assert markets[0]["strike"] == 100000.0
        assert markets[0]["direction"] == "above"
        assert markets[0]["coingecko_id"] == "bitcoin"

    async def test_claude_failure_falls_back_to_regex(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will BTC be above $100,000 on June 30?")]

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, side_effect=Exception("API error")):
            markets = await parse_markets_batch(raw, anthropic_config)

        # Regex should still parse it
        assert len(markets) == 1
        assert markets[0]["coin"] == "BTC"
        assert markets[0]["strike"] == 100000.0

    async def test_claude_returns_null_falls_back_to_regex(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will Bitcoin exceed $80k by year end?")]

        claude_response = {"results": {"0": None}}

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        # Regex should catch "Bitcoin exceed $80k" -- function should not crash
        assert isinstance(markets, list)

    async def test_no_anthropic_config_uses_regex_only(self):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will BTC be above $100,000 on June 30?")]

        markets = await parse_markets_batch(raw, anthropic_config=None)

        assert len(markets) == 1
        assert markets[0]["coin"] == "BTC"

    async def test_non_crypto_market_filtered(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will Trump win the election?")]

        claude_response = {"results": {"0": None}}

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        assert markets == []

    async def test_batch_multiple_markets(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [
            _make_raw_market("Will BTC be above $100k?", market_id="m-1", token_id="t-1"),
            _make_raw_market("Will ETH hit $5,000?", market_id="m-2", token_id="t-2"),
            _make_raw_market("Will Trump win?", market_id="m-3", token_id="t-3"),
        ]

        claude_response = {
            "results": {
                "0": {"coin": "BTC", "coingecko_id": "bitcoin", "strike": 100000.0, "direction": "above"},
                "1": {"coin": "ETH", "coingecko_id": "ethereum", "strike": 5000.0, "direction": "above"},
                "2": None,
            }
        }

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        assert len(markets) == 2
        coins = {m["coin"] for m in markets}
        assert "BTC" in coins
        assert "ETH" in coins

    async def test_implausible_strike_rejected(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will BTC be above $5?")]

        claude_response = {
            "results": {
                "0": {"coin": "BTC", "coingecko_id": "bitcoin", "strike": 5.0, "direction": "above"}
            }
        }

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        # Strike $5 for BTC is implausible (min=1000), should be rejected by both Claude validation and regex
        assert markets == []

    async def test_market_entry_fields_populated(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will ETH be above $5,000?", market_id="m-eth", token_id="t-eth", price=0.40)]

        claude_response = {
            "results": {
                "0": {"coin": "ETH", "coingecko_id": "ethereum", "strike": 5000.0, "direction": "above"}
            }
        }

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        assert len(markets) == 1
        m = markets[0]
        assert m["market_id"] == "m-eth"
        assert m["token_id"] == "t-eth"
        assert m["p_market"] == pytest.approx(0.40)
        assert m["question"] == "Will ETH be above $5,000?"
        assert m["days_to_expiry"] == 30  # default when no endDate

    async def test_claude_returns_empty_results(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will BTC be above $100,000 on June 30?")]

        claude_response = {"results": {}}

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=claude_response):
            markets = await parse_markets_batch(raw, anthropic_config)

        # Should fallback to regex for all markets
        assert len(markets) == 1
        assert markets[0]["coin"] == "BTC"

    async def test_claude_returns_none(self, anthropic_config):
        from strategy.crypto_directional import parse_markets_batch

        raw = [_make_raw_market("Will BTC be above $100,000 on June 30?")]

        with patch("ai.claude_caller.call_claude_json", new_callable=AsyncMock, return_value=None):
            markets = await parse_markets_batch(raw, anthropic_config)

        # Should fallback to regex
        assert len(markets) == 1
        assert markets[0]["coin"] == "BTC"


class TestFetchRawCryptoMarkets:
    """Tests for fetch_raw_crypto_markets."""

    def test_returns_list(self):
        from strategy.crypto_directional import fetch_raw_crypto_markets

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "m1", "question": "test"}]
        mock_resp.raise_for_status = MagicMock()

        with patch("strategy.crypto_directional.requests.get", return_value=mock_resp):
            result = fetch_raw_crypto_markets()

        assert len(result) == 1
        assert result[0]["id"] == "m1"

    def test_api_failure_returns_empty(self):
        from strategy.crypto_directional import fetch_raw_crypto_markets

        with patch("strategy.crypto_directional.requests.get", side_effect=Exception("timeout")):
            result = fetch_raw_crypto_markets()

        assert result == []


class TestValidateClaudeResult:
    """Tests for _validate_claude_result (internal helper)."""

    def test_valid_result(self):
        from strategy.crypto_directional import _validate_claude_result

        result = _validate_claude_result({
            "coin": "BTC", "coingecko_id": "bitcoin", "strike": 100000.0, "direction": "above"
        })
        assert result is not None
        assert result["coin"] == "BTC"
        assert result["strike"] == 100000.0

    def test_none_result(self):
        from strategy.crypto_directional import _validate_claude_result

        assert _validate_claude_result(None) is None

    def test_missing_coin(self):
        from strategy.crypto_directional import _validate_claude_result

        result = _validate_claude_result({
            "coingecko_id": "bitcoin", "strike": 100000.0, "direction": "above"
        })
        assert result is None

    def test_missing_strike(self):
        from strategy.crypto_directional import _validate_claude_result

        result = _validate_claude_result({
            "coin": "BTC", "coingecko_id": "bitcoin", "direction": "above"
        })
        assert result is None

    def test_invalid_strike_type(self):
        from strategy.crypto_directional import _validate_claude_result

        result = _validate_claude_result({
            "coin": "BTC", "coingecko_id": "bitcoin", "strike": "not-a-number", "direction": "above"
        })
        assert result is None

    def test_implausible_btc_strike(self):
        from strategy.crypto_directional import _validate_claude_result

        result = _validate_claude_result({
            "coin": "BTC", "coingecko_id": "bitcoin", "strike": 5.0, "direction": "above"
        })
        assert result is None

    def test_implausible_eth_strike(self):
        from strategy.crypto_directional import _validate_claude_result

        result = _validate_claude_result({
            "coin": "ETH", "coingecko_id": "ethereum", "strike": 10.0, "direction": "above"
        })
        assert result is None

    def test_string_type_not_dict(self):
        from strategy.crypto_directional import _validate_claude_result

        assert _validate_claude_result("not a dict") is None
