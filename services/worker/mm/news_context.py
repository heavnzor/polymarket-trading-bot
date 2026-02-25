"""News context fetcher: Google News RSS headlines for market enrichment."""

import logging
import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

# Prefixes to strip from market questions for keyword extraction
_QUESTION_PREFIXES = re.compile(
    r"^(Will|Is|Are|Has|Have|Does|Do|Did|Can|Could|Should|Would|Shall)\s+",
    re.IGNORECASE,
)

# Trailing date patterns like "by December 2026?" or "in 2025?"
_TRAILING_DATE = re.compile(
    r"\s+(by|before|after|in|on)\s+\w+\s*\d{4}\s*\??\s*$",
    re.IGNORECASE,
)

# Filler words to remove from search queries
_FILLER_WORDS = {
    "the", "a", "an", "be", "been", "being", "to", "of", "and", "or",
    "that", "this", "it", "its", "for", "with", "at", "by", "from",
    "than", "more", "less", "before", "after", "above", "below",
}


class NewsContextFetcher:
    """Fetches recent news headlines from Google News RSS for market context."""

    def __init__(self, max_headlines: int = 5, cache_ttl_minutes: int = 30):
        self.max_headlines = max_headlines
        self.cache_ttl = cache_ttl_minutes * 60
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def fetch_context(self, question: str, description: str = "") -> dict:
        """Fetch news context for a market question.

        Returns {description, headlines, query_used}.
        """
        query = self._extract_keywords(question)
        headlines = self._get_cached_or_fetch(query)

        return {
            "description": description[:500] if description else "",
            "headlines": headlines[:self.max_headlines],
            "query_used": query,
        }

    def _extract_keywords(self, question: str) -> str:
        """Extract search keywords from a market question."""
        text = question.strip()

        # Remove trailing question mark
        text = text.rstrip("?").strip()

        # Strip question prefixes
        text = _QUESTION_PREFIXES.sub("", text).strip()

        # Strip trailing date patterns
        text = _TRAILING_DATE.sub("", text).strip()

        # Remove filler words
        words = text.split()
        filtered = [w for w in words if w.lower() not in _FILLER_WORDS]

        # Keep reasonable length for search query
        result = " ".join(filtered[:10])
        return result if result else question[:50]

    def _get_cached_or_fetch(self, query: str) -> list[dict]:
        """Return cached headlines or fetch fresh ones."""
        cache_key = query.lower().strip()
        now = time.time()

        if cache_key in self._cache:
            cached_time, cached_headlines = self._cache[cache_key]
            if (now - cached_time) < self.cache_ttl:
                return cached_headlines

        headlines = self._search_google_news(query)
        self._cache[cache_key] = (now, headlines)

        # Evict old cache entries
        expired = [k for k, (t, _) in self._cache.items() if (now - t) > self.cache_ttl * 2]
        for k in expired:
            del self._cache[k]

        return headlines

    def _search_google_news(self, query: str) -> list[dict]:
        """Search Google News RSS and return parsed headlines."""
        try:
            url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PolyBot/1.0)",
            })
            resp.raise_for_status()
            return self._parse_rss(resp.text)
        except Exception as e:
            logger.debug(f"Google News RSS fetch failed for '{query}': {e}")
            return []

    def _parse_rss(self, xml_text: str) -> list[dict]:
        """Parse Google News RSS XML into headline dicts."""
        headlines = []
        try:
            root = ET.fromstring(xml_text)
            channel = root.find("channel")
            if channel is None:
                return []

            for item in channel.findall("item"):
                title_el = item.find("title")
                source_el = item.find("source")
                pub_date_el = item.find("pubDate")

                title = unescape(title_el.text) if title_el is not None and title_el.text else ""
                source = source_el.text if source_el is not None and source_el.text else ""
                pub_date = pub_date_el.text if pub_date_el is not None and pub_date_el.text else ""

                if title:
                    headlines.append({
                        "title": title,
                        "source": source,
                        "pub_date": pub_date,
                    })

                if len(headlines) >= self.max_headlines:
                    break

        except ET.ParseError as e:
            logger.debug(f"RSS XML parse error: {e}")

        return headlines
