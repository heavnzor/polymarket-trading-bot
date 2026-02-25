"""Claude guard-fou: minimal AI usage for resolution clause checks and catalyst detection."""

import asyncio
import json
import logging
import re
import requests
from datetime import datetime, timezone

from config import AppConfig
from executor.client import PolymarketClient
from db import store
from mm.news_context import NewsContextFetcher

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


try:
    from ai.claude_caller import _extract_json
except ImportError:
    def _extract_json(text: str) -> dict | None:
        """Fallback: extract JSON from Claude response."""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except (json.JSONDecodeError, IndexError):
                pass
        return None


def _build_market_contexts(
    market_ids: list[str],
    news_fetcher: NewsContextFetcher | None,
    early_market_hours: int = 48,
) -> list[dict]:
    """Fetch market details from Gamma API and enrich with news for early markets.

    Returns list of dicts with question, description, is_early, hours_old, news_headlines.
    """
    contexts = []

    for market_id in market_ids:
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=10,
            )
            if resp.status_code != 200:
                contexts.append({
                    "market_id": market_id,
                    "question": "",
                    "description": "",
                    "is_early": False,
                    "hours_old": -1,
                    "news_headlines": [],
                })
                continue

            data = resp.json()
            question = data.get("question", "")
            description = (data.get("description", "") or "")[:500]
            resolution_source = (data.get("resolutionSource", "") or "")[:200]

            # Detect early market
            is_early = False
            hours_old = -1
            created_at = data.get("createdAt") or data.get("created_at")
            if created_at:
                try:
                    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    hours_old = round((now - created).total_seconds() / 3600, 1)
                    is_early = hours_old <= early_market_hours
                except (ValueError, TypeError):
                    pass

            # Fetch news for early markets
            news_headlines = []
            if is_early and news_fetcher:
                try:
                    news_ctx = news_fetcher.fetch_context(question, description)
                    news_headlines = news_ctx.get("headlines", [])
                except Exception as e:
                    logger.debug(f"News fetch failed for {market_id}: {e}")

            contexts.append({
                "market_id": market_id,
                "question": question,
                "description": description,
                "resolution_source": resolution_source,
                "is_early": is_early,
                "hours_old": hours_old,
                "news_headlines": news_headlines,
            })

        except Exception as e:
            logger.debug(f"Failed to fetch market context for {market_id}: {e}")
            contexts.append({
                "market_id": market_id,
                "question": "",
                "description": "",
                "is_early": False,
                "hours_old": -1,
                "news_headlines": [],
            })

    return contexts


async def claude_guard_loop(config: AppConfig, client: PolymarketClient | None = None):
    """Claude guard loop: periodic check for resolution traps and catalysts.

    Runs every guard_interval_minutes. Makes 1 Claude API call per cycle.
    Checks:
    1. Resolution clause traps on active MM positions
    2. Upcoming catalysts that could cause sudden price moves
    3. Market anomalies (volume spikes, dislocations)

    Returns warnings to pause/kill specific markets.
    """
    guard_cfg = config.guard
    if not guard_cfg.guard_enabled:
        logger.info("Claude guard disabled")
        return

    # Initialize news fetcher if enabled
    news_fetcher = None
    if guard_cfg.guard_news_enabled:
        news_fetcher = NewsContextFetcher(
            max_headlines=guard_cfg.guard_news_max_headlines,
            cache_ttl_minutes=guard_cfg.guard_news_cache_minutes,
        )
        logger.info("Claude guard news context enabled")

    logger.info(f"Claude guard started (interval={guard_cfg.guard_interval_minutes}min)")

    cycle = 0
    while True:
        try:
            cycle += 1

            # Clean stale kills (> 24h) from kill list on each cycle
            try:
                bot_status_raw = await store.get_bot_status_field("guard_kill_list")
                if bot_status_raw:
                    old_kills = json.loads(bot_status_raw)
                    if old_kills:
                        fresh_kills = []
                        for mk_id in old_kills:
                            kill_ts = await store.get_bot_status_field(f"guard_killed_{mk_id}")
                            if kill_ts:
                                try:
                                    killed_at = datetime.fromisoformat(kill_ts.replace("Z", "+00:00"))
                                    age_hours = (datetime.now(timezone.utc) - killed_at).total_seconds() / 3600
                                    if age_hours < 24:
                                        fresh_kills.append(mk_id)
                                    else:
                                        logger.info(f"Guard: removing stale kill {mk_id[:16]} ({age_hours:.0f}h old)")
                                except (ValueError, TypeError):
                                    fresh_kills.append(mk_id)
                            else:
                                fresh_kills.append(mk_id)
                        if len(fresh_kills) != len(old_kills):
                            await store.update_bot_status({
                                "guard_kill_list": json.dumps(fresh_kills),
                            })
            except Exception as e:
                logger.debug(f"Guard kill list cleanup error: {e}")

            # Get current active positions/markets
            mm_quotes = await store.get_active_mm_quotes()
            inventory = await store.get_mm_inventory()

            active_markets = set()
            for q in mm_quotes:
                active_markets.add(q["market_id"])
            for inv in inventory:
                if abs(float(inv.get("net_position", 0))) > 0.001:
                    active_markets.add(inv["market_id"])

            if not active_markets:
                logger.debug("Claude guard: no active markets to check")
                await asyncio.sleep(guard_cfg.guard_interval_minutes * 60)
                continue

            # Build enriched market contexts (sync, run in thread)
            market_ids = list(active_markets)[:20]
            market_contexts = await asyncio.to_thread(
                _build_market_contexts,
                market_ids,
                news_fetcher,
                config.mm.mm_early_market_hours,
            )

            # Call Claude with enriched context
            result = await _call_claude_guard(config, market_contexts)

            if result:
                warnings = result.get("warnings", [])
                kill_markets = result.get("kill_markets", [])

                for warning in warnings:
                    logger.warning(f"Claude guard warning: {warning}")

                if kill_markets:
                    logger.critical(
                        f"Claude guard: KILL recommended for markets: {kill_markets}"
                    )
                    for market_id in kill_markets:
                        # 1. Cancel live orders on CLOB for this market
                        if client:
                            try:
                                cancelled_count = 0
                                # Primary: use DB quotes to find order IDs
                                db_quotes = await store.get_mm_quotes_by_market(market_id)
                                order_ids = set()
                                for q in db_quotes:
                                    if q.get("bid_order_id"):
                                        order_ids.add(q["bid_order_id"])
                                    if q.get("ask_order_id"):
                                        order_ids.add(q["ask_order_id"])
                                for oid in order_ids:
                                    try:
                                        await asyncio.to_thread(client.cancel_order, oid)
                                        cancelled_count += 1
                                    except Exception:
                                        pass
                                # Backup: check inventory for token_ids, match against open orders
                                if cancelled_count == 0:
                                    inv_records = await store.get_mm_inventory(market_id)
                                    token_ids = {r.get("token_id") for r in inv_records if r.get("token_id")}
                                    if token_ids:
                                        open_orders = await asyncio.to_thread(client.get_open_orders)
                                        for order in (open_orders or []):
                                            if order.get("asset_id") in token_ids:
                                                oid = order.get("id") or order.get("order_id")
                                                if oid:
                                                    await asyncio.to_thread(client.cancel_order, oid)
                                                    cancelled_count += 1
                                logger.info(f"Guard: cancelled {cancelled_count} CLOB orders for {market_id}")
                            except Exception as e:
                                logger.error(f"Guard: failed to cancel CLOB orders for {market_id}: {e}")
                        # 2. Mark quotes cancelled in DB
                        await store.cancel_mm_quotes_for_market(market_id)
                        # 3. Flag market as killed so MM loop skips it
                        await store.update_bot_status({
                            f"guard_killed_{market_id}": datetime.now(timezone.utc).isoformat(),
                        })

                # Merge new kills with existing fresh kills (< 24h)
                existing_kill_json = await store.get_bot_status_field("guard_kill_list")
                existing_kills = set(json.loads(existing_kill_json)) if existing_kill_json else set()
                merged_kills = existing_kills | set(kill_markets)
                # Only keep kills that have a fresh timestamp (< 24h)
                fresh_kills = set()
                for mk_id in merged_kills:
                    kill_ts = await store.get_bot_status_field(f"guard_killed_{mk_id}")
                    if kill_ts:
                        try:
                            killed_at = datetime.fromisoformat(kill_ts.replace("Z", "+00:00"))
                            age_hours = (datetime.now(timezone.utc) - killed_at).total_seconds() / 3600
                            if age_hours < 24:
                                fresh_kills.add(mk_id)
                        except (ValueError, TypeError):
                            fresh_kills.add(mk_id)
                    elif mk_id in kill_markets:
                        # Newly killed this cycle (timestamp just set above)
                        fresh_kills.add(mk_id)

                await store.update_bot_status({
                    "guard_cycle": cycle,
                    "guard_warnings": len(warnings),
                    "guard_kills": len(kill_markets),
                    "guard_kill_list": json.dumps(list(fresh_kills)),
                    "guard_last_check": datetime.now(timezone.utc).isoformat(),
                })

        except Exception as e:
            logger.error(f"Claude guard error in cycle {cycle}: {e}", exc_info=True)

        await asyncio.sleep(guard_cfg.guard_interval_minutes * 60)


async def _call_claude_guard(config: AppConfig, market_contexts: list[dict]) -> dict | None:
    """Make a single Claude API call to check for resolution traps and catalysts."""
    try:
        import anthropic

        api_key = config.anthropic.api_key
        if not api_key:
            logger.warning("No Anthropic API key configured for Claude guard")
            return None

        client_kwargs = {"api_key": api_key}
        if config.anthropic.base_url:
            client_kwargs["base_url"] = config.anthropic.base_url

        aclient = anthropic.Anthropic(**client_kwargs)

        # Build per-market context blocks
        market_blocks = []
        for ctx in market_contexts:
            market_id = ctx["market_id"]
            block = f"Market: {market_id}\n"
            if ctx.get("question"):
                block += f"Question: {ctx['question']}\n"
            if ctx.get("description"):
                block += f"Description: {ctx['description']}\n"
            if ctx.get("resolution_source"):
                block += f"Resolution source: {ctx['resolution_source']}\n"
            if ctx.get("is_early"):
                block += f"*** EARLY MARKET (created {ctx.get('hours_old', '?')}h ago) ***\n"
            if ctx.get("news_headlines"):
                block += "Recent news:\n"
                for h in ctx["news_headlines"]:
                    source = f" ({h['source']})" if h.get("source") else ""
                    block += f"  - {h['title']}{source}\n"
            market_blocks.append(block)

        markets_text = "\n---\n".join(market_blocks)

        prompt = f"""You are a market risk guard for a Polymarket trading bot.
Check these active markets for risks. For each market, consider:
1. Resolution clause traps (ambiguous wording, multiple interpretations)
2. Upcoming catalysts that could cause >10% price swing in <1 hour
3. Known resolution controversies or disputed outcomes

For EARLY MARKETS: apply extra scrutiny on resolution criteria clarity, as these markets may have poorly defined or untested resolution mechanisms.

Markets being monitored:

{markets_text}

Respond in JSON:
{{
  "warnings": ["string description of each warning"],
  "kill_markets": ["market_id strings that should be immediately exited"],
  "safe_markets": ["market_id strings that are fine to continue"]
}}

If no issues found, return empty warnings and kill_markets arrays.
Be conservative: only flag genuine risks, not speculative concerns."""

        response = await asyncio.to_thread(
            aclient.messages.create,
            model=config.anthropic.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text if response.content else ""
        result = _extract_json(text)
        return result

    except Exception as e:
        logger.error(f"Claude guard API call failed: {e}")
        return None
