"""Market scanner: discover and filter MM-viable markets from Gamma API."""

import asyncio
import logging
import time
import requests
from datetime import datetime, timezone, timedelta
from config import MarketMakingConfig, PolymarketConfig

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


class MarketScanner:
    """Scans Polymarket for markets suitable for market-making."""

    def __init__(self, mm_config: MarketMakingConfig, poly_config: PolymarketConfig):
        self.mm_config = mm_config
        self.poly_config = poly_config
        self._cache: list[dict] = []
        self._cache_time: float = 0

    async def scan(self, client=None) -> list[dict]:
        """Fetch and filter markets. Uses cache if fresh enough.

        Returns list of {market_id, token_id, yes_token_id, no_token_id, condition_id,
        question, spread, depth, mid, end_date, is_early, ...}.
        """
        now = time.time()
        cache_ttl = self.mm_config.mm_scanner_refresh_minutes * 60
        if self._cache and (now - self._cache_time) < cache_ttl:
            return self._cache

        logger.info("Scanning markets for MM opportunities...")
        raw_markets = self._fetch_active_markets()
        candidate_markets = self._prefilter_candidates(raw_markets)

        # Run market evaluation in parallel with concurrency limit
        concurrency = getattr(self.mm_config, 'mm_scanner_concurrency', 10)
        sem = asyncio.Semaphore(concurrency)

        async def _evaluate_with_limit(market):
            async with sem:
                try:
                    return await asyncio.to_thread(self._evaluate_market, market, client)
                except Exception as e:
                    logger.debug(f"Error evaluating market {market.get('id', '?')}: {e}")
                    return None

        scan_tasks = [_evaluate_with_limit(m) for m in candidate_markets]
        results = await asyncio.gather(*scan_tasks)
        filtered = [r for r in results if r is not None]

        # Sort by effective spread: early markets get a virtual boost
        boost = self.mm_config.mm_early_market_boost
        filtered.sort(
            key=lambda m: m.get("spread", 0) + (boost if m.get("is_early") else 0),
            reverse=True,
        )

        # Cap early market slots
        max_early = self.mm_config.mm_early_market_max_slots
        early_count = 0
        capped = []
        for m in filtered:
            if m.get("is_early"):
                if early_count >= max_early:
                    continue
                early_count += 1
            capped.append(m)

        # Cap at max markets
        capped = capped[:self.mm_config.mm_max_markets]

        self._cache = capped
        self._cache_time = now
        early_in_final = sum(1 for m in capped if m.get("is_early"))
        logger.info(
            f"Scanner found {len(capped)} MM-viable markets ({early_in_final} early) "
            f"out of {len(raw_markets)} (candidates={len(candidate_markets)})"
        )
        return capped

    def invalidate_cache(self):
        self._cache = []
        self._cache_time = 0

    def _fetch_active_markets(self) -> list[dict]:
        """Fetch all active, non-closed markets from Gamma API."""
        all_markets = []
        offset = 0
        limit = 100

        while True:
            try:
                resp = requests.get(
                    f"{GAMMA_API}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": limit,
                        "offset": offset,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                all_markets.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
            except Exception as e:
                logger.error(f"Gamma API fetch failed at offset {offset}: {e}")
                break

        return all_markets

    def _is_early_market(self, market: dict) -> tuple[bool, float]:
        """Check if market was created within mm_early_market_hours.

        Returns (is_early, hours_old). hours_old is -1 if createdAt is missing.
        """
        created_at = market.get("createdAt") or market.get("created_at")
        if not created_at:
            return False, -1

        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_old = (now - created).total_seconds() / 3600
            is_early = hours_old <= self.mm_config.mm_early_market_hours
            return is_early, round(hours_old, 1)
        except (ValueError, TypeError):
            return False, -1

    def _prefilter_candidates(self, markets: list[dict]) -> list[dict]:
        """Cheap pre-filter to avoid querying CLOB on thousands of markets."""
        candidates = []

        for market in markets:
            try:
                if market.get("enableOrderBook") is False:
                    continue

                # Keep only "middle-probability" markets before expensive book checks.
                outcome_prices = market.get("outcomePrices", [])
                if isinstance(outcome_prices, str):
                    import json
                    outcome_prices = json.loads(outcome_prices)
                if not outcome_prices:
                    continue
                yes_price = float(outcome_prices[0])
                if yes_price < 0.02 or yes_price > 0.98:
                    continue

                # Tag early market status
                is_early, hours_old = self._is_early_market(market)
                market["_is_early"] = is_early
                market["_hours_old"] = hours_old

                # Basic activity/liquidity gate from Gamma metadata.
                volume_24h = float(market.get("volume24hr", 0) or 0)
                activity_per_min = volume_24h / 1440.0

                # Relaxed threshold for early markets
                if is_early:
                    min_activity = self.mm_config.mm_early_market_min_activity
                else:
                    min_activity = self.mm_config.mm_min_activity_per_min

                if activity_per_min < min_activity:
                    continue

                liquidity = float(market.get("liquidity", 0) or 0)
                candidates.append((volume_24h, liquidity, market))
            except Exception:
                continue

        # Highest activity/liquidity first
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)

        # Bound expensive CLOB checks.
        max_candidates = max(self.mm_config.mm_max_markets * 30, 150)
        return [m for _, _, m in candidates[:max_candidates]]

    def _evaluate_market(self, market: dict, client=None) -> dict | None:
        """Apply hard filters to determine if market is MM-viable.

        Returns market info dict or None if filtered out.
        """
        market_id = market.get("id", "")
        question = market.get("question", "")
        description = market.get("description", "") or ""
        resolution_source = market.get("resolutionSource", "") or ""
        is_early = market.get("_is_early", False)
        hours_old = market.get("_hours_old", -1)

        # Filter: must be tradable on order book
        if market.get("enableOrderBook") is False:
            return None

        # Token extraction: YES (index 0) and NO (index 1) from both legacy and modern formats.
        yes_token_id = None
        no_token_id = None
        tokens = market.get("tokens")
        if isinstance(tokens, list) and len(tokens) >= 2:
            first = tokens[0] or {}
            second = tokens[1] or {}
            yes_token_id = first.get("token_id") or first.get("tokenId") or first.get("asset_id")
            no_token_id = second.get("token_id") or second.get("tokenId") or second.get("asset_id")
        elif isinstance(tokens, list) and len(tokens) == 1:
            first = tokens[0] or {}
            yes_token_id = first.get("token_id") or first.get("tokenId") or first.get("asset_id")

        if not yes_token_id:
            clob_ids = market.get("clobTokenIds")
            if isinstance(clob_ids, str):
                try:
                    import json
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    clob_ids = []
            if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                yes_token_id = clob_ids[0]
                no_token_id = clob_ids[1]
            elif isinstance(clob_ids, list) and len(clob_ids) == 1:
                yes_token_id = clob_ids[0]

        if not yes_token_id:
            return None

        # Filter: resolution date must be > 24h and < 60 days
        end_date_str = market.get("endDate") or market.get("end_date_iso")
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                days_to_resolution = (end_date - now).total_seconds() / 86400
                if days_to_resolution < 1 or days_to_resolution > 60:
                    return None
            except (ValueError, TypeError):
                return None
        else:
            return None

        yes_token_id = str(yes_token_id)
        no_token_id = str(no_token_id) if no_token_id else None

        # Extract condition_id for split/merge operations
        condition_id = market.get("conditionId") or market.get("condition_id")

        # Get spread and depth from CLOB if client available
        spread = None
        mid = None
        depth = 0

        if client:
            book_summary = client.get_book_summary(yes_token_id)
            if book_summary:
                spread = book_summary.get("spread", 0) * 100  # convert to points
                mid = book_summary.get("mid")
                depth = (book_summary.get("bid_depth_5", 0) + book_summary.get("ask_depth_5", 0))

        # Fallback to Gamma metadata if CLOB summary is unavailable.
        if spread is None or mid is None:
            # Use Gamma API prices as fallback
            outcome_prices = market.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)
            if outcome_prices:
                try:
                    yes_price = float(outcome_prices[0])
                    # Validate Gamma price against extreme range before accepting
                    if yes_price < 0.02 or yes_price > 0.98:
                        return None
                    mid = yes_price
                    spread = 4.0  # assume 4pt spread when no CLOB data
                except (ValueError, IndexError):
                    return None

        if spread is None or mid is None:
            return None

        # Hard filter: spread
        if spread < self.mm_config.mm_min_spread_pts:
            return None

        # Hard filter: depth (relaxed for early markets)
        if client:
            min_depth = (
                self.mm_config.mm_early_market_min_depth_usd
                if is_early
                else self.mm_config.mm_min_depth_usd
            )
            if depth < min_depth:
                return None

        # Filter: avoid extreme prices (too close to 0 or 1)
        if mid < 0.02 or mid > 0.98:
            return None

        # Volume/activity filter based on Gamma data (relaxed for early markets)
        volume_24h = float(market.get("volume24hr", 0) or 0)
        activity_per_min = volume_24h / 1440.0
        if is_early:
            min_activity = self.mm_config.mm_early_market_min_activity
        else:
            min_activity = self.mm_config.mm_min_activity_per_min
        if activity_per_min < min_activity:
            return None

        return {
            "market_id": market_id,
            "token_id": yes_token_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "condition_id": condition_id,
            "question": question[:200],
            "spread": round(spread, 2),
            "mid": round(mid, 4),
            "depth": round(depth, 2),
            "volume_24h": volume_24h,
            "end_date": end_date_str,
            "days_to_resolution": round(days_to_resolution, 1),
            "is_early": is_early,
            "hours_old": hours_old,
            "description": description[:500],
            "resolution_source": resolution_source[:200],
        }
