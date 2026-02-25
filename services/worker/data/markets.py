import json
import logging
import re
import requests
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# Category detection keywords
CATEGORY_PATTERNS = {
    "politics": r"(?i)(president|election|trump|biden|congress|senate|governor|vote|democrat|republican|political|impeach|legislation|bill\s+pass)",
    "crypto": r"(?i)(bitcoin|btc|ethereum|eth|crypto|token|defi|nft|blockchain|solana|sol\b)",
    "sports": r"(?i)(nba|nfl|mlb|nhl|premier league|champions league|world cup|super bowl|playoff|championship|match|tournament|game\s+\d)",
    "finance": r"(?i)(fed\b|interest rate|inflation|gdp|stock|s&p|nasdaq|recession|unemployment|tariff|trade war)",
    "tech": r"(?i)(apple|google|meta|microsoft|openai|ai\b|artificial intelligence|launch|release|iphone|spacex)",
    "geopolitics": r"(?i)(war|ukraine|russia|china|taiwan|nato|sanction|military|conflict|ceasefire|peace)",
    "entertainment": r"(?i)(oscar|grammy|emmy|box office|movie|album|netflix|spotify|concert|award show)",
    "science": r"(?i)(nasa|climate|vaccine|fda|drug|approval|trial|pandemic|disease|earthquake|hurricane)",
}


def detect_category(question: str, description: str = "") -> str:
    """Detect market category from question and description text."""
    text = f"{question} {description}"
    scores = {}
    for category, pattern in CATEGORY_PATTERNS.items():
        matches = re.findall(pattern, text)
        if matches:
            scores[category] = len(matches)
    if scores:
        return max(scores, key=scores.get)
    return "other"


@dataclass
class Market:
    id: str
    question: str
    description: str
    outcomes: list[str]
    outcome_prices: list[float]
    token_ids: list[str]
    volume: float
    liquidity: float
    best_bid: float | None
    best_ask: float | None
    end_date: str | None
    active: bool
    accepting_orders: bool
    category: str = "other"


def fetch_active_markets(limit: int = 50, min_volume: float = 1000) -> list[Market]:
    """Fetch active, tradeable markets from Gamma API."""
    params = {
        "limit": limit,
        "closed": False,
        "order": "volumeNum",
        "ascending": False,
        "active": True,
    }
    if min_volume > 0:
        params["volume_num_min"] = min_volume

    try:
        resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=30)
        resp.raise_for_status()
        raw_markets = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return []

    markets = []
    for m in raw_markets:
        if not m.get("enableOrderBook") or not m.get("acceptingOrders"):
            continue

        try:
            outcomes = json.loads(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else (m.get("outcomes") or [])
            outcome_prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices"), str) else (m.get("outcomePrices") or [])
            token_ids = json.loads(m.get("clobTokenIds", "[]")) if isinstance(m.get("clobTokenIds"), str) else (m.get("clobTokenIds") or [])
            outcome_prices = [float(p) for p in outcome_prices]
        except (json.JSONDecodeError, ValueError):
            continue

        if not token_ids or not outcomes:
            continue

        question = m.get("question", "")
        description = m.get("description", "")[:500]
        category = detect_category(question, description)

        markets.append(Market(
            id=str(m.get("id", "")),
            question=question,
            description=description,
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            token_ids=token_ids,
            volume=float(m.get("volume", 0) or 0),
            liquidity=float(m.get("liquidity", 0) or 0),
            best_bid=m.get("bestBid"),
            best_ask=m.get("bestAsk"),
            end_date=m.get("endDate"),
            active=bool(m.get("active")),
            accepting_orders=bool(m.get("acceptingOrders")),
            category=category,
        ))

    logger.info(f"Fetched {len(markets)} active tradeable markets")
    return markets


def fetch_market_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> list[dict] | None:
    """Fetch price history for a market token from Gamma API.

    Returns list of {t: timestamp, p: price} or None on error.
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/prices",
            params={"token_id": token_id, "interval": interval, "fidelity": fidelity},
            timeout=15,
        )
        if resp.status_code == 404:
            # Some tokens don't have price history yet — not an error
            logger.debug(f"No price history for token {token_id[:20]}... (404)")
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("history", data) if isinstance(data, dict) else data
    except requests.exceptions.HTTPError as e:
        logger.warning(f"Price history HTTP error for {token_id[:20]}...: {e}")
        return None
    except Exception as e:
        logger.warning(f"Price history fetch failed for {token_id[:20]}...: {e}")
        return None


def format_markets_for_llm(markets: list[Market]) -> str:
    """Format markets into a concise string for Claude's triage."""
    lines = []
    for i, m in enumerate(markets, 1):
        prices_str = ", ".join(
            f"{o}: {p:.2f}" for o, p in zip(m.outcomes, m.outcome_prices)
        )
        lines.append(
            f"[{i}] ID: {m.id}\n"
            f"    Question: {m.question}\n"
            f"    Outcomes: {prices_str}\n"
            f"    Volume: ${m.volume:,.0f} | Liquidity: ${m.liquidity:,.0f}\n"
            f"    End: {m.end_date or 'N/A'} | Category: {m.category}\n"
            f"    Description: {m.description[:200]}"
        )
    return "\n\n".join(lines)


def format_market_detail(market: Market) -> str:
    """Format a single market with full details for deep analysis."""
    prices_str = "\n".join(
        f"  - {o}: price={p:.4f}, token_id={t}"
        for o, p, t in zip(market.outcomes, market.outcome_prices, market.token_ids)
    )
    return (
        f"Market ID: {market.id}\n"
        f"Question: {market.question}\n"
        f"Description: {market.description}\n"
        f"Category: {market.category}\n"
        f"Outcomes:\n{prices_str}\n"
        f"Volume: ${market.volume:,.0f}\n"
        f"Liquidity: ${market.liquidity:,.0f}\n"
        f"Best Bid: {market.best_bid} | Best Ask: {market.best_ask}\n"
        f"End Date: {market.end_date or 'N/A'}\n"
        f"Accepting Orders: {market.accepting_orders}"
    )
