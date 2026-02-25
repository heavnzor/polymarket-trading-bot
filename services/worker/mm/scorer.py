"""Market scorer: qualitative evaluation of MM candidates via Claude Sonnet.

Scores markets on 3 axes (resolution_clarity, market_quality, profitability)
before they are quoted. Filters out low-score markets and re-sorts by score.
"""

import json
import logging
import time
from dataclasses import dataclass

from ai.claude_caller import ModelTier, call_claude_json
from config import AnthropicConfig, MarketMakingConfig
from db import store

logger = logging.getLogger(__name__)

SCORER_SYSTEM_PROMPT = """You are a quantitative analyst evaluating prediction markets for automated market-making.

For each market, score it on 3 axes (1-10 scale):

1. **resolution_clarity** (weight 0.4): Is the resolution clause clear and unambiguous? Are there edge cases that could cause disputes? A clear, objective resolution source (e.g. official data feed) scores high.

2. **market_quality** (weight 0.3): Is this market healthy for market-making? Consider depth, volume, spread. Low liquidity or extreme prices score low.

3. **profitability** (weight 0.3): Is the spread exploitable? Is there enough volatility and flow to capture spread? Very tight spreads or dead markets score low.

Also provide:
- **flag**: null if no issue, or one of: "ambiguous_resolution", "low_liquidity", "manipulation_risk", "near_resolution_trap", "extreme_price"
- **note**: 1-sentence explanation

Respond ONLY with valid JSON, no markdown fences."""

SCORER_USER_TEMPLATE = """Score these {count} markets for market-making viability.

Markets:
{markets_json}

Respond with this exact JSON structure:
{{"scores": {{"<market_id>": {{"resolution_clarity": <1-10>, "market_quality": <1-10>, "profitability": <1-10>, "overall": <weighted_avg>, "flag": <null_or_string>, "note": "<1 sentence>"}}, ...}}}}"""

# Weights for the 3 scoring axes
WEIGHT_RESOLUTION = 0.4
WEIGHT_QUALITY = 0.3
WEIGHT_PROFITABILITY = 0.3

DEFAULT_SCORE = 6.0


@dataclass
class MarketScore:
    resolution_clarity: float
    market_quality: float
    profitability: float
    overall: float
    flag: str | None
    note: str
    timestamp: float


class MarketScorer:
    """Scores MM candidate markets via Claude Sonnet (batch).

    - Cache per market_id with configurable TTL
    - Only uncached/expired markets are sent to Sonnet
    - Fail-safe: returns original list on any error
    """

    def __init__(self, mm_config: MarketMakingConfig, anthropic_config: AnthropicConfig):
        self._cache: dict[str, MarketScore] = {}
        self._cache_ttl: float = mm_config.mm_scorer_cache_minutes * 60
        self._enabled = mm_config.mm_scorer_enabled
        self._min_score = mm_config.mm_scorer_min_score
        self._anthropic_config = anthropic_config

    async def score_and_filter(self, markets: list[dict]) -> list[dict]:
        """Score markets via Sonnet (batch) and filter/re-sort by score.

        - Cached markets are not re-sent to Sonnet
        - Markets below mm_scorer_min_score are filtered out
        - Final sort by score descending
        - Fail-safe: returns original list if Sonnet fails
        """
        if not self._enabled or not markets:
            return markets

        now = time.time()

        # Separate cached vs uncached markets
        uncached = []
        for m in markets:
            mid = m["market_id"]
            cached = self._cache.get(mid)
            if cached and (now - cached.timestamp) < self._cache_ttl:
                continue
            uncached.append(m)

        # Call Sonnet only for uncached markets
        if uncached:
            try:
                new_scores = await self._score_batch(uncached)
                for mid, score in new_scores.items():
                    self._cache[mid] = score
                logger.info(
                    f"MM scorer: scored {len(markets)} markets "
                    f"({len(markets) - len(uncached)} cached, {len(uncached)} new)"
                )
            except Exception as e:
                logger.error(f"MM scorer: Sonnet call failed, using original list: {e}")
                return markets
        else:
            logger.debug(
                f"MM scorer: all {len(markets)} markets cached, no Sonnet call"
            )

        # Persist last scores to bot_status for dashboard/debug
        try:
            scores_snapshot = {
                mid: {
                    "overall": s.overall,
                    "flag": s.flag,
                    "note": s.note,
                }
                for mid, s in self._cache.items()
                if any(m["market_id"] == mid for m in markets)
            }
            await store.update_bot_status({
                "mm_scorer_last_scores": json.dumps(scores_snapshot),
            })
        except Exception:
            pass  # Non-critical

        # Filter and sort
        filtered = []
        filtered_count = 0
        for m in markets:
            mid = m["market_id"]
            cached = self._cache.get(mid)
            score = cached.overall if cached else DEFAULT_SCORE
            if score < self._min_score:
                filtered_count += 1
                continue
            m["_scorer_score"] = score
            m["_scorer_flag"] = cached.flag if cached else None
            filtered.append(m)

        if filtered_count:
            logger.info(
                f"MM scorer: filtered {filtered_count} markets below score {self._min_score}"
            )

        # Sort by score descending
        filtered.sort(key=lambda m: m.get("_scorer_score", 0), reverse=True)

        return filtered

    async def _score_batch(self, markets: list[dict]) -> dict[str, MarketScore]:
        """Call Sonnet with a batch of markets and parse scores."""
        now = time.time()

        # Build compact market summaries for the prompt
        market_summaries = []
        for m in markets:
            market_summaries.append({
                "market_id": m["market_id"],
                "question": m.get("question", ""),
                "description": m.get("description", "")[:300],
                "resolution_source": m.get("resolution_source", ""),
                "spread_pts": m.get("spread", 0),
                "mid": m.get("mid", 0),
                "depth_usd": m.get("depth", 0),
                "volume_24h": m.get("volume_24h", 0),
                "days_to_resolution": m.get("days_to_resolution", 0),
                "is_early": m.get("is_early", False),
                "hours_old": m.get("hours_old", -1),
            })

        user_prompt = SCORER_USER_TEMPLATE.format(
            count=len(market_summaries),
            markets_json=json.dumps(market_summaries, indent=2),
        )

        result = await call_claude_json(
            self._anthropic_config,
            ModelTier.SONNET,
            user_prompt,
            system_prompt=SCORER_SYSTEM_PROMPT,
            max_tokens=2048,
        )

        if not result or "scores" not in result:
            logger.warning("MM scorer: invalid Sonnet response (no 'scores' key)")
            raise ValueError("Invalid Sonnet response")

        scores = {}
        for mid, data in result["scores"].items():
            try:
                rc = float(data.get("resolution_clarity", DEFAULT_SCORE))
                mq = float(data.get("market_quality", DEFAULT_SCORE))
                pf = float(data.get("profitability", DEFAULT_SCORE))
                overall = (
                    rc * WEIGHT_RESOLUTION
                    + mq * WEIGHT_QUALITY
                    + pf * WEIGHT_PROFITABILITY
                )
                scores[mid] = MarketScore(
                    resolution_clarity=rc,
                    market_quality=mq,
                    profitability=pf,
                    overall=round(overall, 1),
                    flag=data.get("flag"),
                    note=data.get("note", ""),
                    timestamp=now,
                )
            except (TypeError, ValueError) as e:
                logger.debug(f"MM scorer: parse error for {mid}: {e}")
                scores[mid] = MarketScore(
                    resolution_clarity=DEFAULT_SCORE,
                    market_quality=DEFAULT_SCORE,
                    profitability=DEFAULT_SCORE,
                    overall=DEFAULT_SCORE,
                    flag=None,
                    note="parse_error",
                    timestamp=now,
                )

        return scores
