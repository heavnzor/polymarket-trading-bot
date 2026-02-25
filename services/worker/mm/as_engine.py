"""Avellaneda-Stoikov pricing engine for market-making.

Pure math layer — no I/O, no external dependencies. Computes optimal
bid/ask quotes based on inventory, volatility, and time-to-resolution.

Reference: Avellaneda & Stoikov (2008), "High-frequency trading in a
limit order book", Quantitative Finance, 8(3), 217-224.

Core equations:
  reservation_price: r = mid - q * gamma * sigma^2 * T
  optimal_spread:    s = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/kappa)
  dynamic_gamma:     gamma = gamma_base * (1 + alpha * |q/q_max|)
"""

import logging
import math
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ASParams:
    """Parameters for the Avellaneda-Stoikov pricing model."""
    gamma_base: float = 0.1      # Base risk aversion
    gamma_alpha: float = 0.5     # Inventory-dependent gamma scaling
    kappa: float = 1.5           # Order arrival intensity (default, no data)
    T: float = 1.0               # Normalized time remaining
    min_spread_pts: float = 1.0  # Minimum spread in points
    max_spread_pts: float = 15.0 # Maximum spread in points


def compute_reservation_price(
    mid: float,
    inventory: float,
    max_inventory: float,
    gamma: float,
    vol: float,
    T: float,
) -> float:
    """Compute reservation price (indifference price).

    r = mid - q * gamma * sigma^2 * T

    where q = inventory / max_inventory (normalized), sigma = vol in price units.
    A long position (q > 0) lowers the reservation price, incentivizing sells.
    """
    if max_inventory <= 0:
        return mid
    q = inventory / max_inventory
    sigma = vol / 100.0
    return mid - q * gamma * (sigma ** 2) * T


def compute_optimal_spread(
    gamma: float,
    vol: float,
    T: float,
    kappa: float,
) -> float:
    """Compute optimal spread.

    s = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/kappa)

    Returns spread in price units (0-1 scale).
    """
    if gamma <= 0 or kappa <= 0:
        return 0.02
    sigma = vol / 100.0
    inventory_component = gamma * (sigma ** 2) * T
    arrival_component = (2.0 / gamma) * math.log(1.0 + gamma / kappa)
    return inventory_component + arrival_component


def compute_dynamic_gamma(
    gamma_base: float,
    alpha: float,
    inventory_ratio: float,
) -> float:
    """Compute inventory-adaptive gamma.

    gamma = gamma_base * (1 + alpha * |q/q_max|)

    Higher gamma when inventory is large -> wider spreads, more aggressive unwind.
    """
    return gamma_base * (1.0 + alpha * abs(inventory_ratio))


def compute_as_quotes(
    mid: float,
    inventory: float,
    max_inventory: float,
    vol_pts: float,
    T: float,
    params: ASParams,
    avg_entry_price: float = 0.0,
) -> tuple[float, float]:
    """Full AS quoting pipeline: gamma -> reservation -> spread -> bid/ask.

    Args:
        mid: Current mid price (0-1 scale).
        inventory: Net inventory in shares (positive = long).
        max_inventory: Maximum inventory capacity in shares.
        vol_pts: Volatility in points (0-100 scale).
        T: Normalized time remaining (0 = expiry, 1 = far).
        params: AS model parameters.
        avg_entry_price: Average entry price for "never sell below entry" protection.

    Returns:
        (bid, ask) tuple, clamped to [0.01, 0.99].
    """
    inv_ratio = inventory / max_inventory if max_inventory > 0 else 0.0
    gamma = compute_dynamic_gamma(params.gamma_base, params.gamma_alpha, inv_ratio)

    kappa = params.kappa

    r = compute_reservation_price(mid, inventory, max_inventory, gamma, vol_pts, T)

    s = compute_optimal_spread(gamma, vol_pts, T, kappa)

    # Clamp spread to configured bounds
    spread_pts = s * 100.0
    spread_pts = max(params.min_spread_pts, min(params.max_spread_pts, spread_pts))
    s = spread_pts / 100.0

    bid = r - s / 2.0
    ask = r + s / 2.0

    # Protection: never sell below avg entry (if we have inventory)
    if avg_entry_price > 0 and inventory > 0:
        ask = max(ask, avg_entry_price + 0.01)

    # Clamp to valid Polymarket range
    bid = max(0.01, min(0.99, round(bid, 2)))
    ask = max(0.01, min(0.99, round(ask, 2)))

    # Ensure bid < ask
    if bid >= ask:
        mid_point = (bid + ask) / 2
        bid = max(0.01, round(mid_point - 0.01, 2))
        ask = min(0.99, round(mid_point + 0.01, 2))

    return bid, ask


def estimate_time_remaining(days_to_resolution: float, max_T: float = 30.0) -> float:
    """Normalize days to resolution into T in [0, 1].

    At max_T days (default 30), T = 1.0. At 0 days, T -> 0.
    Near-resolution markets get tighter spreads (lower T).
    """
    if days_to_resolution <= 0:
        return 0.01
    return min(days_to_resolution / max_T, 1.0)


class KappaEstimator:
    """Estimate order arrival intensity (kappa) from observed fill rates.

    Tracks fills per market over a rolling time window. Higher fill rate
    implies higher kappa -> tighter spreads are viable.
    """

    def __init__(self, window_minutes: int = 60, default_kappa: float = 1.5):
        self._window_seconds = window_minutes * 60
        self._default = default_kappa
        self._fills: dict[str, list[float]] = {}

    def record_fill(self, market_id: str) -> None:
        """Record a fill event for a market."""
        now = time.monotonic()
        self._fills.setdefault(market_id, []).append(now)
        cutoff = now - self._window_seconds
        self._fills[market_id] = [t for t in self._fills[market_id] if t >= cutoff]

    def get_kappa(self, market_id: str, default: float | None = None) -> float:
        """Get estimated kappa for a market.

        Kappa is proportional to fill rate (fills per minute).
        Returns default if no fill data available.
        """
        if default is None:
            default = self._default
        fills = self._fills.get(market_id, [])
        if len(fills) < 2:
            return default
        now = time.monotonic()
        cutoff = now - self._window_seconds
        recent = [t for t in fills if t >= cutoff]
        if len(recent) < 2:
            return default
        span = recent[-1] - recent[0]
        if span <= 0:
            return default
        rate_per_min = (len(recent) - 1) / (span / 60.0)
        # Scale: 1 fill/min ~ kappa 1.5, 5 fills/min ~ kappa 5.0
        return max(0.5, min(10.0, rate_per_min * 1.0))

    def reset(self, market_id: str) -> None:
        """Clear fill history for a market."""
        self._fills.pop(market_id, None)
