"""Pricing engine for market-making: delta computation, skew, bid/ask."""

import logging
import math
import time

logger = logging.getLogger(__name__)

# Polymarket tick size
TICK_SIZE = 0.01


def round_to_tick(price: float) -> float:
    """Round price to Polymarket tick size (0.01)."""
    return round(round(price / TICK_SIZE) * TICK_SIZE, 2)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_weighted_mid(book_summary: dict) -> float | None:
    """Compute VWAP-weighted mid from book summary.

    Uses bid/ask depth to weight the mid towards the heavier side,
    reflecting where the "true" price likely is.
    """
    best_bid = book_summary.get("best_bid", 0)
    best_ask = book_summary.get("best_ask", 0)
    bid_depth = book_summary.get("bid_depth_5", 0)
    ask_depth = book_summary.get("ask_depth_5", 0)

    if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
        return None

    total_depth = bid_depth + ask_depth
    if total_depth <= 0:
        return (best_bid + best_ask) / 2

    # Weight mid towards the side with more depth
    w_bid = ask_depth / total_depth  # more ask depth → mid closer to bid
    w_ask = bid_depth / total_depth  # more bid depth → mid closer to ask
    return w_bid * best_bid + w_ask * best_ask


def compute_dynamic_delta(
    vol_short: float,
    book_imbalance: float,
    stale_risk: float,
    delta_min: float = 1.5,
    delta_max: float = 8.0,
    a: float = 0.3,
    b: float = 0.2,
    c: float = 0.3,
    d: float = 0.2,
    tracked_vol: float = 0.0,
) -> float:
    """Compute dynamic half-spread delta.

    Formula: delta = max(delta_min, a*vol + b*|imbalance| + c*stale + d*fee_buffer)
    All components in points (0-100 scale).

    Args:
        vol_short: Short-term volatility proxy (spread changes over recent cycles), in points.
        book_imbalance: Absolute book imbalance (0 to 1).
        stale_risk: Staleness risk factor (0 = fresh, 1 = very stale).
        delta_min: Minimum half-spread in points.
        delta_max: Maximum half-spread in points.
        a, b, c, d: Component weights.
        tracked_vol: EWMA tracked volatility in points. When > 0, overrides vol_short.
    """
    # Prefer tracked EWMA vol when available
    if tracked_vol > 0:
        vol_short = tracked_vol

    raw_delta = (
        a * vol_short
        + b * abs(book_imbalance) * 10  # scale imbalance to points
        + c * stale_risk * 5  # scale stale risk to points
        + d * 1.0  # fee buffer (maker fees = 0, but keep small buffer)
    )
    return clamp(raw_delta, delta_min, delta_max)


def compute_skew(
    net_inventory: float,
    max_inventory: float,
    skew_factor: float = 0.5,
    quadratic_factor: float = 0.3,
) -> float:
    """Compute inventory-driven quote shift with non-linear urgency.

    Uses quadratic term for extreme inventory levels:
    skew = -inv_ratio * skew_factor - sign(inv_ratio) * inv_ratio^2 * quadratic_factor

    This makes the skew grow faster as inventory becomes more extreme,
    encouraging faster unwind of large positions.
    """
    if max_inventory <= 0:
        return 0.0
    inv_ratio = max(-1.0, min(1.0, net_inventory / max_inventory))

    # Linear component
    linear = -inv_ratio * skew_factor
    # Quadratic component: grows faster at extremes
    sign = -1.0 if inv_ratio > 0 else 1.0
    quadratic = sign * (inv_ratio ** 2) * quadratic_factor

    return linear + quadratic


def compute_bid_ask(
    mid: float,
    delta: float,
    skew: float = 0.0,
) -> tuple[float, float]:
    """Compute bid and ask prices from mid, delta, and skew.

    All in Polymarket price format (0-1 scale, where delta is in points = 0.01 units).
    Returns (bid, ask) rounded to tick.
    """
    delta_price = delta / 100.0  # convert points to price units
    skew_price = skew / 100.0

    bid = mid - delta_price + skew_price
    ask = mid + delta_price + skew_price

    # Clamp to valid range
    bid = clamp(bid, 0.01, 0.99)
    ask = clamp(ask, 0.01, 0.99)

    # Ensure bid < ask with minimum spread of 1 tick
    bid = round_to_tick(bid)
    ask = round_to_tick(ask)

    if bid >= ask:
        mid_tick = round_to_tick(mid)
        bid = round_to_tick(mid_tick - TICK_SIZE)
        ask = round_to_tick(mid_tick + TICK_SIZE)

    return bid, ask


def compute_quote_size(
    capital: float,
    max_per_market: float,
    current_inventory_usdc: float,
    max_inventory: float,
    base_size_usd: float = 5.0,
) -> float:
    """Compute quote size considering inventory and capital constraints.

    All values are in USDC. current_inventory_usdc should be pre-converted
    from shares using avg_entry_price.

    Returns size in USDC.
    """
    # Don't quote if already at max inventory
    remaining_capacity = max_inventory - abs(current_inventory_usdc)
    if remaining_capacity <= 0:
        return 0.0

    size = min(base_size_usd, max_per_market, capital * 0.1, remaining_capacity)
    return max(0, round(size, 2))


def should_requote(
    current_pair,
    new_mid: float,
    threshold_pts: float = 0.5,
) -> bool:
    """Check if mid has moved enough to warrant requoting."""
    if current_pair is None:
        return True
    # Use quoted_mid (actual market mid at quote time) if available,
    # otherwise fall back to (bid+ask)/2 which can be wrong for BID-only quotes
    current_mid = current_pair.quoted_mid if current_pair.quoted_mid > 0 else current_pair.mid
    diff_pts = abs(new_mid - current_mid) * 100
    return diff_pts >= threshold_pts


class VolTracker:
    """Track realized volatility via EWMA of mid-price changes."""

    def __init__(self, halflife: int = 20):
        """
        Args:
            halflife: Number of observations for EWMA half-life.
        """
        self._alpha = 1 - 0.5 ** (1.0 / max(halflife, 1))
        self._ewma_var: dict[str, float] = {}  # market_id -> EWMA variance
        self._last_mid: dict[str, float] = {}   # market_id -> last mid

    def update(self, market_id: str, mid: float) -> float:
        """Record a new mid observation and return current vol estimate (in pts).

        Returns the EWMA standard deviation in price points.
        """
        last = self._last_mid.get(market_id)
        self._last_mid[market_id] = mid

        if last is None or last <= 0 or mid <= 0:
            return 0.0

        # Price change in points (x 100)
        change = (mid - last) * 100
        sq_change = change ** 2

        prev_var = self._ewma_var.get(market_id, sq_change)
        new_var = self._alpha * sq_change + (1 - self._alpha) * prev_var
        self._ewma_var[market_id] = new_var

        return new_var ** 0.5  # Standard deviation in pts

    def get_vol(self, market_id: str) -> float:
        """Get current vol estimate for a market (pts)."""
        var = self._ewma_var.get(market_id, 0.0)
        return var ** 0.5

    def reset(self, market_id: str):
        """Reset tracking for a market (e.g., when it's removed)."""
        self._ewma_var.pop(market_id, None)
        self._last_mid.pop(market_id, None)


class StaleTracker:
    """Track how long a market's mid-price has been unchanged (stale)."""

    def __init__(self, threshold_seconds: float = 60.0):
        self._threshold = threshold_seconds
        self._last_mid: dict[str, float] = {}
        self._last_change: dict[str, float] = {}  # monotonic time of last change

    def update_if_changed(self, market_id: str, mid: float) -> None:
        """Record a new mid observation. Updates last_change if mid moved."""
        now = time.monotonic()
        prev = self._last_mid.get(market_id)
        if prev is None or abs(mid - prev) > 1e-6:
            self._last_change[market_id] = now
        self._last_mid[market_id] = mid
        if market_id not in self._last_change:
            self._last_change[market_id] = now

    def get_staleness(self, market_id: str) -> float:
        """Return staleness factor: 0.0 = fresh, 1.0 = stale (at/beyond threshold)."""
        last = self._last_change.get(market_id)
        if last is None:
            return 0.0
        elapsed = time.monotonic() - last
        return min(elapsed / self._threshold, 1.0) if self._threshold > 0 else 0.0

    def reset(self, market_id: str) -> None:
        """Reset tracking for a market."""
        self._last_mid.pop(market_id, None)
        self._last_change.pop(market_id, None)
