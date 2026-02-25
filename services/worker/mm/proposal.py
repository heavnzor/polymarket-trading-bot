"""Quote proposal pipeline for market-making.

Inspired by Hummingbot's proposal architecture. Each stage transforms a
QuoteProposal, allowing composable adjustments (multi-level, vol widening,
event risk, budget constraints, post-only filtering).
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OrderProposal:
    """A single proposed order (bid or ask)."""
    market_id: str
    token_id: str
    side: str          # "BUY" or "SELL"
    price: float
    size: float
    level: int = 0     # 0=tight, 1=medium, 2=wide
    is_hanging: bool = False


@dataclass
class QuoteProposal:
    """A set of proposed orders for a single market."""
    market_id: str
    token_id: str
    bids: list[OrderProposal] = field(default_factory=list)
    asks: list[OrderProposal] = field(default_factory=list)
    mid: float = 0.0
    reservation_price: float = 0.0


def create_base_proposal(
    market_id: str,
    token_id: str,
    bid_price: float,
    ask_price: float,
    bid_size: float,
    ask_size: float,
    mid: float,
    reservation_price: float = 0.0,
) -> QuoteProposal:
    """Create a base proposal with level-0 bid and/or ask.

    If bid_size or ask_size is 0, that side is omitted.
    """
    proposal = QuoteProposal(
        market_id=market_id,
        token_id=token_id,
        mid=mid,
        reservation_price=reservation_price or mid,
    )
    if bid_size > 0 and bid_price > 0:
        proposal.bids.append(OrderProposal(
            market_id=market_id,
            token_id=token_id,
            side="BUY",
            price=bid_price,
            size=bid_size,
            level=0,
        ))
    if ask_size > 0 and ask_price > 0:
        proposal.asks.append(OrderProposal(
            market_id=market_id,
            token_id=token_id,
            side="SELL",
            price=ask_price,
            size=ask_size,
            level=0,
        ))
    return proposal


def apply_multi_level(
    proposal: QuoteProposal,
    levels: int = 1,
    spread_mult: float = 1.5,
    size_mult: float = 2.0,
) -> QuoteProposal:
    """Add multi-level quotes. Level 0 already exists; add levels 1..n.

    Each additional level is wider (spread_mult compounds) and larger
    (size_mult compounds).
    """
    if levels <= 1:
        return proposal
    if not proposal.bids and not proposal.asks:
        return proposal

    mid = proposal.mid
    new_bids = list(proposal.bids)
    new_asks = list(proposal.asks)

    for lvl in range(1, levels):
        mult = spread_mult ** lvl
        sz_mult = size_mult ** lvl

        if proposal.bids:
            base = proposal.bids[0]
            delta_from_mid = mid - base.price
            new_price = max(0.01, round(mid - delta_from_mid * mult, 2))
            new_bids.append(OrderProposal(
                market_id=base.market_id,
                token_id=base.token_id,
                side="BUY",
                price=new_price,
                size=round(base.size * sz_mult, 1),
                level=lvl,
            ))

        if proposal.asks:
            base = proposal.asks[0]
            delta_from_mid = base.price - mid
            new_price = min(0.99, round(mid + delta_from_mid * mult, 2))
            new_asks.append(OrderProposal(
                market_id=base.market_id,
                token_id=base.token_id,
                side="SELL",
                price=new_price,
                size=round(base.size * sz_mult, 1),
                level=lvl,
            ))

    proposal.bids = new_bids
    proposal.asks = new_asks
    return proposal


def _widen_spreads(proposal: QuoteProposal, multiplier: float) -> None:
    """Widen bid/ask prices around the mid by the given multiplier (in-place)."""
    mid = proposal.mid
    for order in proposal.bids:
        delta = mid - order.price
        order.price = max(0.01, round(mid - delta * multiplier, 2))
    for order in proposal.asks:
        delta = order.price - mid
        order.price = min(0.99, round(mid + delta * multiplier, 2))


def apply_vol_adjustment(
    proposal: QuoteProposal,
    vol_pts: float,
    threshold: float = 5.0,
) -> QuoteProposal:
    """Widen spreads when volatility exceeds threshold.

    Multiplier = 1.0 + (vol - threshold) / threshold, capped at 2x.
    """
    if vol_pts <= threshold:
        return proposal
    multiplier = min(2.0, 1.0 + (vol_pts - threshold) / threshold)
    _widen_spreads(proposal, multiplier)
    return proposal


def apply_event_risk(
    proposal: QuoteProposal,
    guard_warning: bool,
    widen_pct: float = 50.0,
) -> QuoteProposal:
    """Widen spreads when guard signals event risk."""
    if not guard_warning:
        return proposal
    multiplier = 1.0 + widen_pct / 100.0
    _widen_spreads(proposal, multiplier)
    return proposal


def apply_budget_constraint(
    proposal: QuoteProposal,
    available_capital: float,
    committed: float = 0.0,
) -> QuoteProposal:
    """Cap order sizes to fit within available capital budget.

    Removes orders that would exceed the budget entirely.
    """
    remaining = available_capital - committed
    if remaining <= 0:
        proposal.bids = []
        proposal.asks = []
        return proposal

    min_viable_size = 5.0
    new_bids = []
    used = 0.0

    for order in proposal.bids:
        cost = order.size * order.price
        if used + cost > remaining:
            max_size = (remaining - used) / order.price if order.price > 0 else 0
            if max_size >= min_viable_size:
                order.size = round(max_size, 1)
                new_bids.append(order)
                used += order.size * order.price
            break
        new_bids.append(order)
        used += cost

    new_asks = []
    for order in proposal.asks:
        cost = order.size * (1 - order.price)
        if used + cost > remaining:
            max_size = (remaining - used) / (1 - order.price) if order.price < 1 else 0
            if max_size >= min_viable_size:
                order.size = round(max_size, 1)
                new_asks.append(order)
                used += order.size * (1 - order.price)
            break
        new_asks.append(order)
        used += cost

    proposal.bids = new_bids
    proposal.asks = new_asks
    return proposal


def apply_post_only_filter(
    proposal: QuoteProposal,
    best_bid: float,
    best_ask: float,
) -> QuoteProposal:
    """Ensure no orders would cross the book (post-only enforcement).

    Bids must be below best_ask, asks must be above best_bid.
    """
    if best_ask > 0:
        for order in proposal.bids:
            if order.price >= best_ask:
                order.price = max(0.01, round(best_ask - 0.01, 2))
    if best_bid > 0:
        for order in proposal.asks:
            if order.price <= best_bid:
                order.price = min(0.99, round(best_bid + 0.01, 2))
    return proposal
