"""Performance metrics for market-making: spread capture, fill quality, adverse selection."""

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def spread_capture_rate(fills: list[dict], quotes: list[dict]) -> float:
    """Compute % of theoretical spread captured.

    For each completed round-trip (buy + sell), compute:
    actual_spread = sell_price - buy_price
    theoretical_spread = ask_price - bid_price (at time of quote)
    capture = actual / theoretical
    """
    if not fills:
        return 0.0

    total_capture = 0.0
    count = 0

    # Group fills by quote_id for round-trip matching
    quote_fills: dict[int, list] = {}
    for fill in fills:
        qid = fill.get("quote_id")
        if qid is not None:
            quote_fills.setdefault(qid, []).append(fill)

    for qid, qfills in quote_fills.items():
        buys = [f for f in qfills if f["side"] == "BUY"]
        sells = [f for f in qfills if f["side"] == "SELL"]
        if buys and sells:
            buy_price = sum(f["price"] * f["size"] for f in buys) / sum(f["size"] for f in buys)
            sell_price = sum(f["price"] * f["size"] for f in sells) / sum(f["size"] for f in sells)
            actual_spread = sell_price - buy_price

            # Find matching quote for theoretical spread
            matching_quote = next((q for q in quotes if q.get("id") == qid), None)
            if matching_quote:
                theoretical = matching_quote["ask_price"] - matching_quote["bid_price"]
                if theoretical > 0:
                    total_capture += actual_spread / theoretical
                    count += 1

    return total_capture / count if count > 0 else 0.0


def fill_quality(fill_price: float, mid_at_fill: float, side: str) -> float:
    """Compute fill quality: how favorable was the fill vs mid.

    Returns value in basis points. Positive = good (bought below mid or sold above).
    """
    if mid_at_fill <= 0:
        return 0.0

    if side == "BUY":
        improvement = (mid_at_fill - fill_price) / mid_at_fill
    else:
        improvement = (fill_price - mid_at_fill) / mid_at_fill

    return improvement * 10000  # to bps


def adverse_selection(
    fill_price: float,
    mid_at_fill: float,
    mid_at_later: float,
    side: str,
) -> float:
    """Compute adverse selection: how much did mid move against us after fill.

    Returns value in basis points. Positive = adverse (mid moved against us).
    """
    if mid_at_fill <= 0:
        return 0.0

    if side == "BUY":
        # If we bought and mid went down, that's adverse
        movement = (mid_at_fill - mid_at_later) / mid_at_fill
    else:
        # If we sold and mid went up, that's adverse
        movement = (mid_at_later - mid_at_fill) / mid_at_fill

    return movement * 10000  # to bps


def compute_pnl(fills: list[dict]) -> dict:
    """Compute gross and net PnL from fills.

    Returns {gross_pnl, net_pnl, total_fees, num_round_trips}.
    """
    total_buy_cost = 0.0
    total_sell_revenue = 0.0
    total_buy_size = 0.0
    total_sell_size = 0.0
    total_fees = 0.0

    for fill in fills:
        price = fill["price"]
        size = fill["size"]
        fee = fill.get("fee", 0)
        total_fees += fee

        if fill["side"] == "BUY":
            total_buy_cost += price * size
            total_buy_size += size
        else:
            total_sell_revenue += price * size
            total_sell_size += size

    matched_size = min(total_buy_size, total_sell_size)
    gross_pnl = total_sell_revenue - total_buy_cost if matched_size > 0 else 0.0
    net_pnl = gross_pnl - total_fees

    return {
        "gross_pnl": round(gross_pnl, 6),
        "net_pnl": round(net_pnl, 6),
        "total_fees": round(total_fees, 6),
        "num_round_trips": int(matched_size) if matched_size > 0 else 0,
        "total_buy_size": round(total_buy_size, 4),
        "total_sell_size": round(total_sell_size, 4),
    }


def sharpe_ratio(daily_returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Compute annualized Sharpe ratio from daily returns."""
    if len(daily_returns) < 2:
        return 0.0
    mean_ret = sum(daily_returns) / len(daily_returns)
    excess = [r - risk_free_rate / 365 for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    variance = sum((r - mean_excess) ** 2 for r in excess) / (len(excess) - 1)
    std = math.sqrt(variance) if variance > 0 else 0.001
    return (mean_excess / std) * math.sqrt(365)


def profit_factor(fills: list[dict]) -> float:
    """Gross profits / gross losses. > 1.0 means profitable."""
    gross_profit = 0.0
    gross_loss = 0.0

    # Group by round-trips
    buy_prices = []
    sell_prices = []

    for fill in fills:
        if fill["side"] == "BUY":
            buy_prices.append(fill["price"])
        else:
            sell_prices.append(fill["price"])

    for bp, sp in zip(sorted(buy_prices), sorted(sell_prices)):
        pnl = sp - bp
        if pnl > 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def profit_factor_from_round_trips(round_trips: list[dict]) -> float:
    """Compute profit factor from completed round-trips.

    Uses actual entry/exit prices from round-trip records instead of
    sorted-price matching. More accurate for inventory-based MM.

    Args:
        round_trips: List of round-trip dicts with 'net_pnl' field.

    Returns:
        Gross gains / gross losses. Returns inf if no losses.
    """
    gross_gains = 0.0
    gross_losses = 0.0
    for rt in round_trips:
        pnl = float(rt.get("net_pnl", 0))
        if pnl > 0:
            gross_gains += pnl
        elif pnl < 0:
            gross_losses += abs(pnl)
    if gross_losses == 0:
        return float("inf") if gross_gains > 0 else 0.0
    return gross_gains / gross_losses


def inventory_turn_rate(fills_count: int, avg_inventory: float, period_hours: float) -> float:
    """How many times inventory turns over per day."""
    if avg_inventory <= 0 or period_hours <= 0:
        return 0.0
    daily_fills = fills_count * (24 / period_hours)
    return daily_fills / (2 * avg_inventory)  # 2 fills per round-trip
