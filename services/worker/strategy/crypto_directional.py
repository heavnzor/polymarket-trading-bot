"""Crypto directional strategy: Student-t model on BTC/ETH price threshold markets."""

import json
import logging
import math
import re
import requests
from datetime import datetime, timezone

from config import CryptoDirectionalConfig

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# Regex patterns to extract strike and coin from market questions
PRICE_PATTERNS = [
    # "Will BTC be above $70,000 on June 30?"
    re.compile(
        r"(?:Will|will)\s+(BTC|Bitcoin|ETH|Ethereum)\s+(?:be\s+)?(?:above|below|over|under|reach|hit|exceed)\s+"
        r"\$?([\d,]+(?:\.\d+)?)\s*([kmb])?",
        re.IGNORECASE,
    ),
    # "BTC above $70000?"
    re.compile(
        r"(BTC|Bitcoin|ETH|Ethereum)\s+(?:above|below|over|under|price\s+above|price\s+below)\s+"
        r"\$?([\d,]+(?:\.\d+)?)\s*([kmb])?",
        re.IGNORECASE,
    ),
    # "Bitcoin price > $70,000 by..."
    re.compile(
        r"(BTC|Bitcoin|ETH|Ethereum)\s+(?:price\s+)?[><=]+\s*\$?([\d,]+(?:\.\d+)?)\s*([kmb])?",
        re.IGNORECASE,
    ),
]

SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

# Minimum plausible strike prices per coin (filters out parse artifacts)
MIN_STRIKE = {"bitcoin": 1_000, "ethereum": 50}

DATE_PATTERNS = [
    # "on June 30, 2025" or "by June 30"
    re.compile(
        r"(?:on|by|before)\s+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)",
        re.IGNORECASE,
    ),
    # "June 30" at end of string
    re.compile(
        r"(\w+\s+\d{1,2}(?:,?\s*\d{4}))\s*\??$",
        re.IGNORECASE,
    ),
]

COIN_MAP = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
}


def extract_market_params(question: str) -> dict | None:
    """Extract coin, strike price, and direction from market question.

    Returns {coin, strike, direction, coingecko_id} or None if not a price threshold market.
    """
    for pattern in PRICE_PATTERNS:
        match = pattern.search(question)
        if match:
            coin_raw = match.group(1).lower()
            strike_str = match.group(2).replace(",", "")
            try:
                strike = float(strike_str)
            except ValueError:
                continue

            # Apply suffix multiplier (k/m/b)
            suffix = (match.group(3) or "").lower()
            if suffix in SUFFIX_MULTIPLIERS:
                strike *= SUFFIX_MULTIPLIERS[suffix]

            coin_id = COIN_MAP.get(coin_raw)
            if not coin_id:
                continue

            # Sanity check: reject implausible strike prices
            min_strike = MIN_STRIKE.get(coin_id, 0)
            if strike < min_strike:
                logger.debug(
                    f"CD: skipping implausible strike ${strike:.0f} for {coin_id} "
                    f"(min=${min_strike}) in: {question[:60]}"
                )
                continue

            direction = "above"
            if any(w in question.lower() for w in ("below", "under")):
                direction = "below"

            return {
                "coin": coin_raw.upper() if coin_raw in ("btc", "eth") else coin_raw.title(),
                "coingecko_id": coin_id,
                "strike": strike,
                "direction": direction,
            }
    return None


def fetch_price_history(
    coingecko_id: str, days: int = 30, api_base: str = "https://api.coingecko.com/api/v3"
) -> list[float]:
    """Fetch daily close prices from CoinGecko.

    Returns list of prices (most recent last).
    """
    try:
        resp = requests.get(
            f"{api_base}/coins/{coingecko_id}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        prices = [p[1] for p in data.get("prices", [])]
        return prices
    except Exception as e:
        logger.error(f"Failed to fetch price history for {coingecko_id}: {e}")
        return []


def get_spot_price(
    coingecko_id: str, api_base: str = "https://api.coingecko.com/api/v3"
) -> float | None:
    """Get current spot price from CoinGecko."""
    try:
        resp = requests.get(
            f"{api_base}/simple/price",
            params={"ids": coingecko_id, "vs_currencies": "usd"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get(coingecko_id, {}).get("usd")
    except Exception as e:
        logger.error(f"Failed to get spot price for {coingecko_id}: {e}")
        return None


def compute_ewma_vol(prices: list[float], lambda_: float = 0.94) -> float:
    """Compute EWMA volatility from daily prices.

    Returns daily volatility as a decimal (e.g., 0.04 = 4%/day).
    """
    if len(prices) < 3:
        return 0.0

    # Log returns
    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            returns.append(math.log(prices[i] / prices[i - 1]))

    if not returns:
        return 0.0

    # EWMA variance
    variance = returns[0] ** 2
    for r in returns[1:]:
        variance = lambda_ * variance + (1 - lambda_) * r ** 2

    return math.sqrt(variance)


def student_t_prob(
    spot: float,
    strike: float,
    t_days: float,
    sigma: float,
    nu: float = 6.0,
    direction: str = "above",
) -> float:
    """Compute P(S_T >= K) using Student-t distribution on log-returns.

    Model: ln(S_T/S_0) ~ t_nu * sigma * sqrt(T)
    where t_nu is Student-t with nu degrees of freedom.

    Args:
        spot: Current price
        strike: Strike price
        t_days: Days to expiry
        sigma: Daily volatility (EWMA)
        nu: Degrees of freedom (5-8 for crypto)
        direction: 'above' (P(S>=K)) or 'below' (P(S<K))
    """
    try:
        from scipy.stats import t as t_dist
    except ImportError:
        logger.error("scipy not installed, falling back to normal approximation")
        return _normal_approx(spot, strike, t_days, sigma, direction)

    if spot <= 0 or strike <= 0 or t_days <= 0 or sigma <= 0:
        return 0.5

    t_years = t_days / 365.0
    sigma_t = sigma * math.sqrt(t_days)

    # d = ln(K/S0) / (sigma * sqrt(T))
    d = math.log(strike / spot) / sigma_t if sigma_t > 0 else 0

    # Scale for Student-t: d_scaled = d * sqrt((nu-2)/nu)
    # Student-t(nu) has variance nu/(nu-2), rescale from normal to t.
    scale = math.sqrt((nu - 2) / nu) if nu > 2 else 1.0
    d_scaled = d * scale

    p_below = t_dist.cdf(d_scaled, nu)
    p_above = 1.0 - p_below

    return p_above if direction == "above" else p_below


def _normal_approx(spot, strike, t_days, sigma, direction):
    """Fallback: normal distribution approximation."""
    from math import erf

    sigma_t = sigma * math.sqrt(t_days)
    if sigma_t <= 0:
        return 0.5
    d = math.log(strike / spot) / sigma_t
    p_below = 0.5 * (1 + erf(d / math.sqrt(2)))
    p_above = 1.0 - p_below
    return p_above if direction == "above" else p_below


def detect_edge(p_model: float, p_market: float) -> float:
    """Compute edge in points (0-100 scale).

    Positive edge = model says higher probability than market.
    """
    return (p_model - p_market) * 100


def kelly_size(
    edge_pts: float,
    p_model: float,
    capital: float,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.05,
) -> float:
    """Compute position size using fractional Kelly criterion.

    Capital comes from the on-chain USDC.e balance (API source of truth).
    No hardcoded max_per_trade — the only caps are Kelly fraction and
    max_position_pct (both percentage-based).

    Kelly: f* = (p*b - q) / b where b = odds, p = win prob, q = 1-p
    Then apply fraction (0.25 = quarter Kelly) for safety.
    """
    if p_model <= 0 or p_model >= 1 or edge_pts <= 0 or capital <= 0:
        return 0.0

    # Binary market: if we bet on YES at price p_market
    # b = (1/p_market) - 1 = (1 - p_market) / p_market
    p_market = p_model - edge_pts / 100
    if p_market <= 0 or p_market >= 1:
        return 0.0

    b = (1 - p_market) / p_market
    q = 1 - p_model
    kelly_full = (p_model * b - q) / b if b > 0 else 0

    if kelly_full <= 0:
        return 0.0

    size = kelly_full * kelly_fraction * capital
    size = min(size, capital * max_position_pct)
    return round(max(0, size), 2)


def fetch_crypto_threshold_markets() -> list[dict]:
    """Fetch active BTC/ETH price threshold markets from Gamma API."""
    markets = []
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": 100,
                "tag": "crypto",
            },
            timeout=15,
        )
        resp.raise_for_status()
        all_markets = resp.json()

        for market in all_markets:
            question = market.get("question", "")
            params = extract_market_params(question)
            if params:
                tokens = market.get("tokens", [])
                token_id = tokens[0].get("token_id", "") if tokens else ""

                # Get market price
                outcome_prices = market.get("outcomePrices", [])
                if isinstance(outcome_prices, str):
                    outcome_prices = json.loads(outcome_prices)
                p_market = float(outcome_prices[0]) if outcome_prices else 0.5

                # Extract end date for days to expiry
                end_date_str = market.get("endDate") or market.get("end_date_iso")
                days_to_expiry = 30  # default
                if end_date_str:
                    try:
                        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                        days_to_expiry = max(0.5, (end_date - datetime.now(timezone.utc)).total_seconds() / 86400)
                    except (ValueError, TypeError):
                        pass

                markets.append({
                    "market_id": market.get("id", ""),
                    "token_id": token_id,
                    "question": question,
                    "p_market": p_market,
                    "days_to_expiry": days_to_expiry,
                    **params,
                })

    except Exception as e:
        logger.error(f"Failed to fetch crypto threshold markets: {e}")

    return markets


def fetch_raw_crypto_markets() -> list[dict]:
    """Fetch raw crypto markets from Gamma API without parsing.

    Returns the raw market dicts (with question, tokens, outcomePrices, endDate).
    Parsing is deferred to parse_markets_batch() or extract_market_params().
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={"active": "true", "closed": "false", "limit": 100, "tag": "crypto"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch crypto markets: {e}")
        return []


def _build_market_entry(market: dict, params: dict) -> dict:
    """Build a standardized market entry from raw market data and parsed params.

    Shared by fetch_crypto_threshold_markets and parse_markets_batch to avoid
    duplicating the token/price/expiry extraction logic.
    """
    tokens = market.get("tokens", [])
    token_id = tokens[0].get("token_id", "") if tokens else ""

    outcome_prices = market.get("outcomePrices", [])
    if isinstance(outcome_prices, str):
        outcome_prices = json.loads(outcome_prices)
    p_market = float(outcome_prices[0]) if outcome_prices else 0.5

    end_date_str = market.get("endDate") or market.get("end_date_iso")
    days_to_expiry = 30
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            days_to_expiry = max(0.5, (end_date - datetime.now(timezone.utc)).total_seconds() / 86400)
        except (ValueError, TypeError):
            pass

    return {
        "market_id": market.get("id", ""),
        "token_id": token_id,
        "question": market.get("question", ""),
        "p_market": p_market,
        "days_to_expiry": days_to_expiry,
        **params,
    }


async def parse_markets_batch(raw_markets: list[dict], anthropic_config=None) -> list[dict]:
    """Parse market questions using Claude Sonnet NL parsing with regex fallback.

    Batches all questions into a single Sonnet call. For questions Claude could not
    parse (null), or if the API call fails entirely, falls back to the regex-based
    extract_market_params().

    Returns the same format as fetch_crypto_threshold_markets().
    """
    from ai.claude_caller import call_claude_json, ModelTier

    questions = [m.get("question", "") for m in raw_markets]

    # Try Claude NL parsing
    parsed_by_claude: dict = {}
    if anthropic_config and questions:
        system_prompt = (
            "You are a structured data extractor for crypto prediction markets. "
            "Given a list of market questions, extract the cryptocurrency, strike price, "
            "direction (above/below), and CoinGecko ID for each. "
            "Only process crypto price threshold markets (BTC/Bitcoin, ETH/Ethereum). "
            "Return null for non-crypto or non-threshold markets."
        )

        questions_text = "\n".join(f"{i}: {q}" for i, q in enumerate(questions))
        user_prompt = (
            f"Parse these market questions. Return a JSON object mapping index to parsed data:\n\n"
            f"{questions_text}\n\n"
            f"Return JSON:\n"
            f'{{\n'
            f'  "results": {{\n'
            f'    "0": {{"coin": "BTC", "coingecko_id": "bitcoin", "strike": 100000.0, "direction": "above"}},\n'
            f'    "3": null,\n'
            f'    ...\n'
            f'  }}\n'
            f'}}\n\n'
            f"Rules:\n"
            f"- coin: uppercase ticker (BTC, ETH)\n"
            f"- coingecko_id: \"bitcoin\" or \"ethereum\"\n"
            f"- strike: numeric price in USD (handle k/m/b suffixes, commas)\n"
            f"- direction: \"above\" or \"below\"\n"
            f"- Return null for non-crypto-threshold questions\n"
            f"- Only include BTC/Bitcoin and ETH/Ethereum markets"
        )

        try:
            result = await call_claude_json(
                anthropic_config, ModelTier.SONNET, user_prompt, system_prompt, max_tokens=2048
            )
            if result and "results" in result:
                parsed_by_claude = result["results"]
        except Exception as e:
            logger.warning(f"CD NL parsing failed, falling back to regex: {e}")

    # Build final markets list
    markets: list[dict] = []
    for i, market in enumerate(raw_markets):
        question = market.get("question", "")

        # Try Claude result first
        params = _validate_claude_result(parsed_by_claude.get(str(i)))

        # Fallback to regex
        if params is None:
            params = extract_market_params(question)

        if params is None:
            continue

        markets.append(_build_market_entry(market, params))

    return markets


def _validate_claude_result(claude_result) -> dict | None:
    """Validate and normalize a single Claude NL parsing result.

    Returns a params dict compatible with extract_market_params() output,
    or None if the result is invalid.
    """
    if not isinstance(claude_result, dict):
        return None

    coin = claude_result.get("coin", "")
    coingecko_id = claude_result.get("coingecko_id", "")
    strike = claude_result.get("strike")
    direction = claude_result.get("direction", "above")

    if not coin or not coingecko_id or strike is None:
        return None

    try:
        strike = float(strike)
    except (ValueError, TypeError):
        return None

    min_strike = MIN_STRIKE.get(coingecko_id, 0)
    if strike < min_strike:
        logger.debug(
            f"CD NL: rejecting implausible strike ${strike:.0f} for {coingecko_id} "
            f"(min=${min_strike})"
        )
        return None

    return {
        "coin": coin,
        "coingecko_id": coingecko_id,
        "strike": strike,
        "direction": direction,
    }
