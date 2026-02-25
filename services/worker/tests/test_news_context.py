"""Tests for the NewsContextFetcher."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from mm.news_context import NewsContextFetcher

# Sample Google News RSS response
SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bitcoin - Google News</title>
    <item>
      <title>Bitcoin surges past $100k for first time</title>
      <source url="https://example.com">Reuters</source>
      <pubDate>Sat, 22 Feb 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Crypto markets rally on institutional demand</title>
      <source url="https://example2.com">Bloomberg</source>
      <pubDate>Sat, 22 Feb 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>BTC ETF inflows hit record high</title>
      <source url="https://example3.com">CoinDesk</source>
      <pubDate>Fri, 21 Feb 2026 18:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


class TestExtractKeywords:
    """Tests for keyword extraction from market questions."""

    def test_strips_will_prefix(self):
        fetcher = NewsContextFetcher()
        result = fetcher._extract_keywords("Will BTC reach $100k by December 2026?")
        assert not result.startswith("Will")
        assert "BTC" in result

    def test_strips_is_prefix(self):
        fetcher = NewsContextFetcher()
        result = fetcher._extract_keywords("Is Bitcoin going to crash?")
        assert not result.startswith("Is")
        assert "Bitcoin" in result

    def test_strips_trailing_question_mark(self):
        fetcher = NewsContextFetcher()
        result = fetcher._extract_keywords("Will ETH flip BTC?")
        assert "?" not in result

    def test_removes_filler_words(self):
        fetcher = NewsContextFetcher()
        result = fetcher._extract_keywords("Will the price of Bitcoin be above $50000?")
        assert "the" not in result.lower().split()
        assert "of" not in result.lower().split()
        assert "be" not in result.lower().split()

    def test_empty_question_fallback(self):
        fetcher = NewsContextFetcher()
        result = fetcher._extract_keywords("")
        assert isinstance(result, str)

    def test_preserves_key_terms(self):
        fetcher = NewsContextFetcher()
        result = fetcher._extract_keywords("Will Donald Trump win the 2028 election?")
        assert "Donald" in result
        assert "Trump" in result


class TestCacheHit:
    """Tests for caching behavior."""

    def test_cache_hit_returns_same_result(self):
        """Identical query should return cached result without refetching."""
        fetcher = NewsContextFetcher(max_headlines=3, cache_ttl_minutes=30)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()

        with patch("mm.news_context.requests.get", return_value=mock_response) as mock_get:
            result1 = fetcher._get_cached_or_fetch("bitcoin price")
            result2 = fetcher._get_cached_or_fetch("bitcoin price")

        assert result1 == result2
        # Should only call requests.get once (second call hits cache)
        assert mock_get.call_count == 1

    def test_different_queries_not_cached(self):
        """Different queries should each make their own request."""
        fetcher = NewsContextFetcher(max_headlines=3, cache_ttl_minutes=30)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()

        with patch("mm.news_context.requests.get", return_value=mock_response) as mock_get:
            fetcher._get_cached_or_fetch("bitcoin price")
            fetcher._get_cached_or_fetch("ethereum price")

        assert mock_get.call_count == 2


class TestGoogleNewsRssParse:
    """Tests for RSS XML parsing."""

    def test_parse_valid_rss(self):
        """Valid RSS should return parsed headlines with title, source, pub_date."""
        fetcher = NewsContextFetcher(max_headlines=5)
        headlines = fetcher._parse_rss(SAMPLE_RSS)

        assert len(headlines) == 3
        assert headlines[0]["title"] == "Bitcoin surges past $100k for first time"
        assert headlines[0]["source"] == "Reuters"
        assert "2026" in headlines[0]["pub_date"]
        assert headlines[1]["title"] == "Crypto markets rally on institutional demand"
        assert headlines[1]["source"] == "Bloomberg"

    def test_parse_respects_max_headlines(self):
        """Parser should stop at max_headlines."""
        fetcher = NewsContextFetcher(max_headlines=2)
        headlines = fetcher._parse_rss(SAMPLE_RSS)
        assert len(headlines) == 2

    def test_parse_empty_rss(self):
        """Empty RSS should return empty list."""
        fetcher = NewsContextFetcher()
        empty_rss = '<?xml version="1.0"?><rss><channel></channel></rss>'
        headlines = fetcher._parse_rss(empty_rss)
        assert headlines == []

    def test_parse_invalid_xml(self):
        """Invalid XML should return empty list without raising."""
        fetcher = NewsContextFetcher()
        headlines = fetcher._parse_rss("not xml at all")
        assert headlines == []

    def test_google_news_rss_integration(self):
        """Mock the full HTTP fetch + parse flow."""
        fetcher = NewsContextFetcher(max_headlines=3)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()

        with patch("mm.news_context.requests.get", return_value=mock_response):
            headlines = fetcher._search_google_news("bitcoin")

        assert len(headlines) == 3
        assert headlines[0]["title"] == "Bitcoin surges past $100k for first time"


class TestNetworkFailure:
    """Tests for graceful degradation on network errors."""

    def test_network_failure_returns_empty(self):
        """Network error should return empty list, not raise."""
        fetcher = NewsContextFetcher()

        with patch("mm.news_context.requests.get", side_effect=Exception("Connection timeout")):
            headlines = fetcher._search_google_news("bitcoin crash")

        assert headlines == []

    def test_http_error_returns_empty(self):
        """HTTP error status should return empty list."""
        fetcher = NewsContextFetcher()

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("503 Service Unavailable")

        with patch("mm.news_context.requests.get", return_value=mock_response):
            headlines = fetcher._search_google_news("test query")

        assert headlines == []


class TestFetchContext:
    """Tests for the high-level fetch_context() method."""

    def test_fetch_context_combines_fields(self):
        """fetch_context should return description + headlines + query_used."""
        fetcher = NewsContextFetcher(max_headlines=3, cache_ttl_minutes=30)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()

        with patch("mm.news_context.requests.get", return_value=mock_response):
            result = fetcher.fetch_context(
                question="Will Bitcoin reach $100k?",
                description="Bitcoin price prediction market for 2026",
            )

        assert "description" in result
        assert result["description"] == "Bitcoin price prediction market for 2026"
        assert "headlines" in result
        assert len(result["headlines"]) > 0
        assert "query_used" in result
        assert isinstance(result["query_used"], str)

    def test_fetch_context_empty_description(self):
        """fetch_context with empty description should still work."""
        fetcher = NewsContextFetcher(max_headlines=3)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()

        with patch("mm.news_context.requests.get", return_value=mock_response):
            result = fetcher.fetch_context(question="Will ETH flip BTC?", description="")

        assert result["description"] == ""
        assert len(result["headlines"]) > 0
