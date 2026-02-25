import logging
import requests

logger = logging.getLogger(__name__)

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


def fetch_order_book(token_id: str) -> dict | None:
    """Fetch order book for a token directly from the CLOB API (no auth needed)."""
    try:
        resp = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch order book for {token_id}: {e}")
        return None


def parse_order_book(raw_book: dict) -> dict:
    """Parse a raw CLOB order book into structured metrics.

    Returns dict with bids, asks, spread, depth, imbalance, etc.
    """
    bids = raw_book.get("bids", [])
    asks = raw_book.get("asks", [])

    if not bids or not asks:
        return {
            "has_data": False,
            "spread_pct": None,
            "bid_depth_usdc": 0,
            "ask_depth_usdc": 0,
            "imbalance": 0,
            "best_bid": None,
            "best_ask": None,
            "levels": 0,
        }

    # Best bid/ask
    best_bid = float(bids[0].get("price", 0))
    best_ask = float(asks[0].get("price", 0))

    # Spread
    spread = best_ask - best_bid if best_ask > best_bid else 0
    midpoint = (best_bid + best_ask) / 2 if (best_bid + best_ask) > 0 else 1
    spread_pct = (spread / midpoint) * 100

    # Depth (total USDC available within 5% of midpoint)
    bid_depth = 0.0
    for level in bids:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        if price >= midpoint * 0.95:
            bid_depth += price * size

    ask_depth = 0.0
    for level in asks:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        if price <= midpoint * 1.05:
            ask_depth += price * size

    # Order imbalance: positive = more buy pressure, negative = more sell pressure
    total = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total if total > 0 else 0

    return {
        "has_data": True,
        "spread_pct": round(spread_pct, 2),
        "bid_depth_usdc": round(bid_depth, 2),
        "ask_depth_usdc": round(ask_depth, 2),
        "imbalance": round(imbalance, 3),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "levels": len(bids) + len(asks),
    }


def format_orderbook_for_llm(metrics: dict) -> str:
    """Format order book metrics into a string for Claude."""
    if not metrics.get("has_data"):
        return "Order book: No data available."

    imb_label = "BUY pressure" if metrics["imbalance"] > 0.1 else (
        "SELL pressure" if metrics["imbalance"] < -0.1 else "balanced"
    )

    return (
        f"Order Book Analysis:\n"
        f"  Best Bid: {metrics['best_bid']:.4f} | Best Ask: {metrics['best_ask']:.4f}\n"
        f"  Spread: {metrics['spread_pct']:.2f}%\n"
        f"  Bid Depth (5%): ${metrics['bid_depth_usdc']:.2f} | Ask Depth (5%): ${metrics['ask_depth_usdc']:.2f}\n"
        f"  Order Imbalance: {metrics['imbalance']:+.3f} ({imb_label})\n"
        f"  Total Levels: {metrics['levels']}"
    )


def fetch_market_activity(condition_id: str, limit: int = 20) -> dict:
    """Fetch recent trade activity for a market from Gamma API.

    Returns dict with trade_count, buy_volume, sell_volume, avg_trade_size,
    recent_direction.
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/activity",
            params={"market": condition_id, "limit": limit},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"has_data": False}

        trades = resp.json()
        if not trades or not isinstance(trades, list):
            return {"has_data": False}

        buy_volume = 0.0
        sell_volume = 0.0
        trade_count = len(trades)

        for t in trades:
            side = t.get("side", "").upper()
            size = float(t.get("size", 0) or 0)
            price = float(t.get("price", 0) or 0)
            usd_value = size * price

            if side == "BUY":
                buy_volume += usd_value
            else:
                sell_volume += usd_value

        total_volume = buy_volume + sell_volume
        avg_trade_size = total_volume / trade_count if trade_count > 0 else 0

        # Buy/sell ratio
        buy_ratio = buy_volume / total_volume if total_volume > 0 else 0.5

        # Recent direction: last 5 trades
        recent = trades[:5] if len(trades) >= 5 else trades
        recent_buys = sum(1 for t in recent if t.get("side", "").upper() == "BUY")
        recent_direction = "bullish" if recent_buys > len(recent) / 2 else "bearish"

        return {
            "has_data": True,
            "trade_count": trade_count,
            "buy_volume_usd": round(buy_volume, 2),
            "sell_volume_usd": round(sell_volume, 2),
            "buy_ratio": round(buy_ratio, 3),
            "avg_trade_size": round(avg_trade_size, 2),
            "recent_direction": recent_direction,
        }
    except Exception as e:
        logger.error(f"Failed to fetch market activity for {condition_id}: {e}")
        return {"has_data": False}


def format_activity_for_llm(activity: dict) -> str:
    """Format market activity data for Claude."""
    if not activity.get("has_data"):
        return "Market Activity: No recent trade data available."

    ratio_label = "strong BUY" if activity["buy_ratio"] > 0.65 else (
        "strong SELL" if activity["buy_ratio"] < 0.35 else "mixed"
    )

    return (
        f"Recent Market Activity:\n"
        f"  Trades: {activity['trade_count']} recent\n"
        f"  Buy Volume: ${activity['buy_volume_usd']:.2f} | Sell Volume: ${activity['sell_volume_usd']:.2f}\n"
        f"  Buy Ratio: {activity['buy_ratio']:.1%} ({ratio_label})\n"
        f"  Avg Trade Size: ${activity['avg_trade_size']:.2f}\n"
        f"  Recent Direction: {activity['recent_direction']}"
    )
