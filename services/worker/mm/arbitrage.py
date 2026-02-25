"""Complete-set arbitrage: detect and exploit YES+NO price inefficiencies.

Two arbitrage strategies:
1. Buy-merge: When best_ask(YES) + best_ask(NO) < 1.00, buy both and merge for USDC.
2. Split-sell: When best_bid(YES) + best_bid(NO) > 1.00, split USDC and sell both.

Both yield risk-free profit minus gas costs (~$0.005 on Polygon).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import MarketMakingConfig

logger = logging.getLogger(__name__)

# Polymarket charges 0% maker fee, but there's a 2% winner fee on resolution.
# For arb purposes we are merging immediately so no winner fee applies.
# Only gas costs matter.
DEFAULT_GAS_COST_USD = 0.005
MIN_ARB_SIZE = 5.0  # Minimum size to justify gas cost


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    market_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    arb_type: str  # "buy_merge" or "split_sell"
    yes_price: float  # ask (buy_merge) or bid (split_sell)
    no_price: float   # ask (buy_merge) or bid (split_sell)
    gross_profit_pct: float  # % profit before costs
    net_profit_pct: float    # % profit after gas
    max_size: float  # Max executable size (min depth of both sides)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def scan_for_arbitrage(
    yes_book: dict,
    no_book: dict,
    market_id: str,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
    gas_cost_usd: float = DEFAULT_GAS_COST_USD,
    min_profit_pct: float = 0.5,
) -> ArbOpportunity | None:
    """Check if an arbitrage opportunity exists on a market.

    Args:
        yes_book: Book summary for YES token (from get_book_summary).
        no_book: Book summary for NO token (from get_book_summary).
        market_id: Market identifier.
        condition_id: CTF condition ID for merge/split.
        yes_token_id: YES token ID.
        no_token_id: NO token ID.
        gas_cost_usd: Estimated gas cost for the on-chain operation.
        min_profit_pct: Minimum net profit percentage to trigger execution.

    Returns:
        ArbOpportunity if profitable, None otherwise.
    """
    if not yes_book or not no_book:
        return None

    yes_ask = yes_book.get("best_ask", 1.0)
    no_ask = no_book.get("best_ask", 1.0)
    yes_bid = yes_book.get("best_bid", 0.0)
    no_bid = no_book.get("best_bid", 0.0)

    # Validate prices
    if yes_ask <= 0 or no_ask <= 0 or yes_bid <= 0 or no_bid <= 0:
        return None

    # ── Buy-merge: buy YES + NO at ask, merge for $1.00 ──
    buy_cost = yes_ask + no_ask
    if buy_cost < 1.0:
        gross_profit = 1.0 - buy_cost  # per token pair
        # Max size: min of available ask depth (in shares)
        yes_ask_depth_shares = _ask_depth_shares(yes_book)
        no_ask_depth_shares = _ask_depth_shares(no_book)
        max_size = min(yes_ask_depth_shares, no_ask_depth_shares)

        if max_size < MIN_ARB_SIZE:
            return None

        # Net profit accounting for gas (amortized over size)
        net_profit = gross_profit * max_size - gas_cost_usd
        net_profit_pct = (net_profit / (buy_cost * max_size)) * 100 if buy_cost > 0 else 0

        if net_profit_pct >= min_profit_pct:
            return ArbOpportunity(
                market_id=market_id,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                arb_type="buy_merge",
                yes_price=yes_ask,
                no_price=no_ask,
                gross_profit_pct=(gross_profit / buy_cost) * 100,
                net_profit_pct=net_profit_pct,
                max_size=max_size,
            )

    # ── Split-sell: split $1.00 into YES + NO, sell both at bid ──
    sell_revenue = yes_bid + no_bid
    if sell_revenue > 1.0:
        gross_profit = sell_revenue - 1.0
        # Max size: min of available bid depth (in shares)
        yes_bid_depth_shares = _bid_depth_shares(yes_book)
        no_bid_depth_shares = _bid_depth_shares(no_book)
        max_size = min(yes_bid_depth_shares, no_bid_depth_shares)

        if max_size < MIN_ARB_SIZE:
            return None

        net_profit = gross_profit * max_size - gas_cost_usd
        net_profit_pct = (net_profit / max_size) * 100  # Cost is $1.00 per pair

        if net_profit_pct >= min_profit_pct:
            return ArbOpportunity(
                market_id=market_id,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                arb_type="split_sell",
                yes_price=yes_bid,
                no_price=no_bid,
                gross_profit_pct=gross_profit * 100,
                net_profit_pct=net_profit_pct,
                max_size=max_size,
            )

    return None


async def execute_arbitrage(
    opp: ArbOpportunity,
    client,
    inventory_manager,
    mm_config: MarketMakingConfig,
) -> dict:
    """Execute an arbitrage opportunity.

    Args:
        opp: The detected opportunity.
        client: PolymarketClient instance.
        inventory_manager: InventoryManager for tracking.
        mm_config: MM configuration.

    Returns:
        dict with execution results.
    """
    # Cap size at configured limit
    size = min(opp.max_size, mm_config.mm_arb_max_size_usd)

    result = {
        "arb_type": opp.arb_type,
        "market_id": opp.market_id,
        "size": size,
        "yes_price": opp.yes_price,
        "no_price": opp.no_price,
        "gross_profit_pct": opp.gross_profit_pct,
        "net_profit_pct": opp.net_profit_pct,
        "success": False,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }

    if opp.arb_type == "buy_merge":
        result.update(await _execute_buy_merge(opp, client, inventory_manager, size))
    elif opp.arb_type == "split_sell":
        result.update(await _execute_split_sell(opp, client, inventory_manager, size))

    return result


async def _execute_buy_merge(
    opp: ArbOpportunity,
    client,
    inventory_manager,
    size: float,
) -> dict:
    """Buy YES + NO tokens, then merge into USDC.

    Strategy: Place two market-taking limit orders at the ask prices,
    wait for fills, then merge the tokens.
    """
    shares = round(size, 1)

    # Step 1: Buy YES tokens at ask price
    logger.info(
        f"ARB buy-merge {opp.market_id[:16]}: "
        f"buying YES@{opp.yes_price:.2f} + NO@{opp.no_price:.2f} "
        f"× {shares} shares (profit={opp.net_profit_pct:.2f}%)"
    )

    yes_order = await asyncio.to_thread(
        client.place_limit_order,
        opp.yes_token_id, opp.yes_price, shares, "BUY",
    )
    if not yes_order:
        logger.warning(f"ARB: YES buy failed for {opp.market_id[:16]}")
        return {"success": False, "error": "yes_buy_failed"}

    # Step 2: Buy NO tokens at ask price
    no_order = await asyncio.to_thread(
        client.place_limit_order,
        opp.no_token_id, opp.no_price, shares, "BUY",
    )
    if not no_order:
        # Cancel the YES order since we can't complete the arb
        yes_order_id = _extract_order_id(yes_order)
        if yes_order_id:
            await asyncio.to_thread(client.cancel_order, yes_order_id)
        logger.warning(f"ARB: NO buy failed for {opp.market_id[:16]}, cancelled YES order")
        return {"success": False, "error": "no_buy_failed"}

    # Step 3: Wait briefly for fills (orders at ask should fill immediately)
    await asyncio.sleep(2)

    # Step 4: Verify fills
    yes_order_id = _extract_order_id(yes_order)
    no_order_id = _extract_order_id(no_order)

    yes_filled, yes_status, yes_matched, _ = await asyncio.to_thread(
        client.is_order_filled, yes_order_id
    ) if yes_order_id else (False, "UNKNOWN", 0, {})

    no_filled, no_status, no_matched, _ = await asyncio.to_thread(
        client.is_order_filled, no_order_id
    ) if no_order_id else (False, "UNKNOWN", 0, {})

    # Determine merge amount (min of what was filled on both sides)
    merge_amount = min(yes_matched, no_matched)

    if merge_amount < MIN_ARB_SIZE:
        # Partial or no fills — cancel remaining
        if yes_order_id and not yes_filled:
            await asyncio.to_thread(client.cancel_order, yes_order_id)
        if no_order_id and not no_filled:
            await asyncio.to_thread(client.cancel_order, no_order_id)

        # Track any partial fills in inventory
        if yes_matched > 0:
            inventory_manager.process_fill(
                opp.market_id, opp.yes_token_id, "BUY", opp.yes_price, yes_matched
            )
        if no_matched > 0:
            inventory_manager.process_fill(
                opp.market_id, opp.no_token_id, "BUY", opp.no_price, no_matched,
                is_no_token=True,
            )

        logger.warning(
            f"ARB: Insufficient fills for merge "
            f"(YES={yes_matched:.1f}, NO={no_matched:.1f})"
        )
        return {
            "success": False,
            "error": "insufficient_fills",
            "yes_filled": yes_matched,
            "no_filled": no_matched,
        }

    # Step 5: Merge the matched pairs
    merge_ok = await asyncio.to_thread(
        client.merge_positions, opp.condition_id, merge_amount
    )

    if merge_ok:
        # Track fills then merge in inventory
        inventory_manager.process_fill(
            opp.market_id, opp.yes_token_id, "BUY", opp.yes_price, merge_amount
        )
        inventory_manager.process_fill(
            opp.market_id, opp.no_token_id, "BUY", opp.no_price, merge_amount,
            is_no_token=True,
        )
        inventory_manager.process_merge(opp.market_id, merge_amount)

        profit = (1.0 - opp.yes_price - opp.no_price) * merge_amount
        logger.info(
            f"ARB buy-merge SUCCESS: merged {merge_amount:.1f} pairs, "
            f"profit=${profit:.4f}"
        )
        return {
            "success": True,
            "merged": merge_amount,
            "profit_usd": round(profit, 4),
        }
    else:
        # Merge failed — we still have the tokens in inventory
        inventory_manager.process_fill(
            opp.market_id, opp.yes_token_id, "BUY", opp.yes_price, merge_amount
        )
        inventory_manager.process_fill(
            opp.market_id, opp.no_token_id, "BUY", opp.no_price, merge_amount,
            is_no_token=True,
        )
        logger.error(f"ARB: Merge failed for {opp.market_id[:16]}")
        return {"success": False, "error": "merge_failed", "tokens_held": merge_amount}


async def _execute_split_sell(
    opp: ArbOpportunity,
    client,
    inventory_manager,
    size: float,
) -> dict:
    """Split USDC into YES + NO tokens, then sell both at bid.

    Strategy: Split USDC via CTF contract, then place sell orders
    at the bid prices.
    """
    amount = round(size, 1)

    logger.info(
        f"ARB split-sell {opp.market_id[:16]}: "
        f"splitting ${amount:.2f} then selling YES@{opp.yes_price:.2f} + NO@{opp.no_price:.2f} "
        f"(profit={opp.net_profit_pct:.2f}%)"
    )

    # Step 1: Split USDC into YES + NO
    split_ok = await asyncio.to_thread(
        client.split_position, opp.condition_id, amount
    )
    if not split_ok:
        logger.warning(f"ARB: Split failed for {opp.market_id[:16]}")
        return {"success": False, "error": "split_failed"}

    # Track split in inventory
    inventory_manager.process_split(
        opp.market_id, amount,
        yes_token_id=opp.yes_token_id,
        no_token_id=opp.no_token_id,
    )

    # Step 2: Sell YES tokens at bid price
    yes_order = await asyncio.to_thread(
        client.place_limit_order,
        opp.yes_token_id, opp.yes_price, amount, "SELL",
    )

    # Step 3: Sell NO tokens at bid price
    no_order = await asyncio.to_thread(
        client.place_limit_order,
        opp.no_token_id, opp.no_price, amount, "SELL",
    )

    # Track results
    yes_order_id = _extract_order_id(yes_order) if yes_order else None
    no_order_id = _extract_order_id(no_order) if no_order else None

    # Wait for fills
    await asyncio.sleep(2)

    yes_matched = 0.0
    no_matched = 0.0
    if yes_order_id:
        _, _, yes_matched, _ = await asyncio.to_thread(
            client.is_order_filled, yes_order_id
        )
    if no_order_id:
        _, _, no_matched, _ = await asyncio.to_thread(
            client.is_order_filled, no_order_id
        )

    # Track fills in inventory
    if yes_matched > 0:
        inventory_manager.process_fill(
            opp.market_id, opp.yes_token_id, "SELL", opp.yes_price, yes_matched
        )
    if no_matched > 0:
        inventory_manager.process_fill(
            opp.market_id, opp.no_token_id, "SELL", opp.no_price, no_matched,
            is_no_token=True,
        )

    revenue = yes_matched * opp.yes_price + no_matched * opp.no_price
    cost = amount  # $1.00 per pair
    profit = revenue - cost

    if yes_matched >= amount * 0.9 and no_matched >= amount * 0.9:
        logger.info(
            f"ARB split-sell SUCCESS: sold {yes_matched:.1f} YES + {no_matched:.1f} NO, "
            f"profit=${profit:.4f}"
        )
        return {
            "success": True,
            "yes_sold": yes_matched,
            "no_sold": no_matched,
            "profit_usd": round(profit, 4),
        }
    else:
        logger.warning(
            f"ARB split-sell PARTIAL: YES sold={yes_matched:.1f}/{amount:.1f}, "
            f"NO sold={no_matched:.1f}/{amount:.1f}"
        )
        return {
            "success": False,
            "error": "partial_fills",
            "yes_sold": yes_matched,
            "no_sold": no_matched,
            "profit_usd": round(profit, 4),
        }


def _ask_depth_shares(book: dict) -> float:
    """Estimate available shares at best ask from book summary.

    Uses ask_depth_5 (USDC notional) divided by best_ask to approximate shares.
    """
    ask_depth = book.get("ask_depth_5", 0)
    best_ask = book.get("best_ask", 1.0)
    if best_ask > 0 and ask_depth > 0:
        return ask_depth / best_ask
    return 0.0


def _bid_depth_shares(book: dict) -> float:
    """Estimate available shares at best bid from book summary."""
    bid_depth = book.get("bid_depth_5", 0)
    best_bid = book.get("best_bid", 0)
    if best_bid > 0 and bid_depth > 0:
        return bid_depth / best_bid
    return 0.0


def _extract_order_id(order_response: dict | None) -> str | None:
    """Extract order ID from CLOB response."""
    if not order_response:
        return None
    if isinstance(order_response, dict):
        return order_response.get("orderID") or order_response.get("id")
    return getattr(order_response, "orderID", None) or getattr(order_response, "id", None)
