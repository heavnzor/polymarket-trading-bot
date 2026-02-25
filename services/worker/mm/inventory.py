"""Inventory tracking and management for market-making."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import MarketMakingConfig

logger = logging.getLogger(__name__)


@dataclass
class MarketInventory:
    """In-memory inventory state for a single market (YES + NO tokens)."""
    market_id: str
    token_id: str
    no_token_id: str = ""
    net_position: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    # NO-side inventory
    no_position: float = 0.0
    no_avg_entry_price: float = 0.0
    no_realized_pnl: float = 0.0
    opened_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def position_age_hours(self) -> float:
        """Hours since position was opened."""
        if self.opened_at is None or (self.net_position == 0 and self.no_position == 0):
            return 0.0
        now = datetime.now(timezone.utc)
        return (now - self.opened_at).total_seconds() / 3600

    @property
    def mergeable_pairs(self) -> float:
        """Number of YES+NO pairs that can be merged back to USDC."""
        return min(self.net_position, self.no_position) if self.net_position > 0 and self.no_position > 0 else 0.0


class InventoryManager:
    """Track and manage inventory across all MM markets."""

    def __init__(self, mm_config: MarketMakingConfig):
        self.config = mm_config
        self._inventory: dict[str, MarketInventory] = {}

    def get(self, market_id: str) -> MarketInventory:
        """Get or create inventory for a market."""
        if market_id not in self._inventory:
            self._inventory[market_id] = MarketInventory(
                market_id=market_id, token_id=""
            )
        return self._inventory[market_id]

    def process_fill(self, market_id: str, token_id: str,
                     side: str, price: float, size: float,
                     is_no_token: bool = False):
        """Update inventory after a fill.

        side: 'BUY' or 'SELL'
        is_no_token: True if this fill is on the NO token side.
        """
        inv = self.get(market_id)
        inv.updated_at = datetime.now(timezone.utc)

        if is_no_token:
            inv.no_token_id = token_id
            self._apply_fill(inv, side, price, size, no_side=True)
        else:
            inv.token_id = token_id
            self._apply_fill(inv, side, price, size, no_side=False)

        # Track position open time
        if side == "BUY":
            if is_no_token and inv.no_position > 0 and inv.opened_at is None:
                inv.opened_at = datetime.now(timezone.utc)
            elif not is_no_token and inv.net_position > 0 and inv.opened_at is None:
                inv.opened_at = datetime.now(timezone.utc)

        # Reset open time when position fully closed
        if inv.net_position == 0 and inv.no_position == 0:
            inv.opened_at = None

    @staticmethod
    def _apply_fill(inv: MarketInventory, side: str, price: float, size: float,
                    no_side: bool = False):
        """Apply a fill to YES or NO position with FIFO P&L."""
        delta = size if side == "BUY" else -size

        if no_side:
            old_pos = inv.no_position
            avg = inv.no_avg_entry_price
        else:
            old_pos = inv.net_position
            avg = inv.avg_entry_price

        new_pos = old_pos + delta

        if delta > 0 and old_pos >= 0:
            if new_pos > 0:
                total_cost = avg * old_pos + price * delta
                avg = total_cost / new_pos
        elif delta < 0 and old_pos > 0:
            shares_sold = min(abs(delta), old_pos)
            pnl = shares_sold * (price - avg)
            if no_side:
                inv.no_realized_pnl += pnl
            else:
                inv.realized_pnl += pnl
        elif delta < 0 and old_pos <= 0:
            if new_pos < 0:
                total_cost = avg * abs(old_pos) + price * abs(delta)
                avg = total_cost / abs(new_pos)
        elif delta > 0 and old_pos < 0:
            shares_covered = min(delta, abs(old_pos))
            pnl = shares_covered * (avg - price)
            if no_side:
                inv.no_realized_pnl += pnl
            else:
                inv.realized_pnl += pnl

        if no_side:
            inv.no_position = new_pos
            inv.no_avg_entry_price = avg
        else:
            inv.net_position = new_pos
            inv.avg_entry_price = avg

        token_label = "NO" if no_side else "YES"
        logger.debug(
            f"Inventory {inv.market_id[:16]} ({token_label}): {old_pos:.1f} -> {new_pos:.1f} "
            f"(fill: {side} {size}@{price:.2f})"
        )

    def process_merge(self, market_id: str, amount: float):
        """Record a merge operation: reduces both YES and NO positions equally."""
        inv = self.get(market_id)
        if inv.net_position >= amount and inv.no_position >= amount:
            inv.net_position -= amount
            inv.no_position -= amount
            inv.updated_at = datetime.now(timezone.utc)
            logger.info(
                f"Merge {market_id[:16]}: {amount:.1f} pairs merged. "
                f"YES={inv.net_position:.1f}, NO={inv.no_position:.1f}"
            )
        else:
            logger.warning(
                f"Merge failed {market_id[:16]}: requested {amount:.1f} but "
                f"YES={inv.net_position:.1f}, NO={inv.no_position:.1f}"
            )

    def process_split(self, market_id: str, amount: float, yes_token_id: str, no_token_id: str):
        """Record a split operation: adds equal amounts to YES and NO positions."""
        inv = self.get(market_id)
        inv.token_id = yes_token_id
        inv.no_token_id = no_token_id
        inv.net_position += amount
        inv.no_position += amount
        inv.updated_at = datetime.now(timezone.utc)
        # Split is at $1 per pair, so cost per token is $0.50 initially
        # But we track avg_entry at 0.50 for both sides (symmetric)
        if inv.avg_entry_price == 0:
            inv.avg_entry_price = 0.50
        if inv.no_avg_entry_price == 0:
            inv.no_avg_entry_price = 0.50
        logger.info(
            f"Split {market_id[:16]}: {amount:.1f} USDC → YES={inv.net_position:.1f}, NO={inv.no_position:.1f}"
        )

    def get_total_exposure(self) -> float:
        """Get total absolute exposure across all markets in USDC (YES + NO)."""
        total = 0.0
        for inv in self._inventory.values():
            total += abs(inv.net_position) * inv.avg_entry_price if inv.avg_entry_price > 0 else 0.0
            total += abs(inv.no_position) * inv.no_avg_entry_price if inv.no_avg_entry_price > 0 else 0.0
        return total

    def get_total_realized_pnl(self) -> float:
        return sum(inv.realized_pnl + inv.no_realized_pnl for inv in self._inventory.values())

    def get_unwind_urgency(self, market_id: str, max_hours: float = 24.0) -> float:
        """Get unwind urgency factor based on position age.

        Returns 0.0 for fresh positions, scaling to 1.0 at max_hours.
        Used to increase skew for aging positions.
        """
        inv = self._inventory.get(market_id)
        if inv is None:
            return 0.0
        age = inv.position_age_hours()
        return min(age / max_hours, 1.0)

    def needs_unwind(self, market_id: str, max_per_market: float) -> bool:
        """Check if a market's inventory exceeds unwind threshold."""
        inv = self.get(market_id)
        threshold = max_per_market * self.config.mm_unwind_threshold
        return abs(inv.net_position) > threshold

    def is_at_capacity(self, market_id: str, max_per_market: float, mid: float = 0.0) -> bool:
        """Check if inventory is at max for a market."""
        inv = self.get(market_id)
        # Use avg_entry if available, else fall back to mid
        yes_price = inv.avg_entry_price if inv.avg_entry_price > 0 else mid
        no_price = inv.no_avg_entry_price if inv.no_avg_entry_price > 0 else (1 - mid if mid > 0 else 0)
        total_usdc = abs(inv.net_position) * yes_price + abs(inv.no_position) * no_price
        return total_usdc >= max_per_market

    def get_skew_direction(self, market_id: str, max_per_market: float) -> float:
        """Get inventory skew direction. Positive = long YES, negative = long NO."""
        inv = self.get(market_id)
        if max_per_market <= 0:
            return 0.0
        # Net skew: YES - NO positions (in value terms)
        yes_value = inv.net_position * (inv.avg_entry_price if inv.avg_entry_price > 0 else 0.5)
        no_value = inv.no_position * (inv.no_avg_entry_price if inv.no_avg_entry_price > 0 else 0.5)
        return (yes_value - no_value) / max_per_market

    def get_merge_amount(self, market_id: str) -> float:
        """Get amount that can be merged (min of YES and NO positions)."""
        inv = self.get(market_id)
        return inv.mergeable_pairs

    def get_all_positions(self) -> list[dict]:
        """Get all non-zero positions for reporting (YES + NO)."""
        positions = []
        for inv in self._inventory.values():
            if abs(inv.net_position) > 0.001 or abs(inv.no_position) > 0.001:
                positions.append({
                    "market_id": inv.market_id,
                    "token_id": inv.token_id,
                    "no_token_id": inv.no_token_id,
                    "yes_position": round(inv.net_position, 4),
                    "no_position": round(inv.no_position, 4),
                    "yes_avg_entry": round(inv.avg_entry_price, 4),
                    "no_avg_entry": round(inv.no_avg_entry_price, 4),
                    "realized_pnl": round(inv.realized_pnl + inv.no_realized_pnl, 4),
                    "mergeable_pairs": round(inv.mergeable_pairs, 4),
                    # Backward compat
                    "net_position": round(inv.net_position, 4),
                    "avg_entry": round(inv.avg_entry_price, 4),
                })
        return positions

    def load_from_db(self, db_inventory: list[dict]):
        """Load inventory state from database records.

        Groups records by market_id to restore both YES and NO positions.
        DB records have: market_id, token_id, net_position, avg_entry_price, realized_pnl.
        """
        by_market: dict[str, list[dict]] = {}
        for record in db_inventory:
            mid = record["market_id"]
            by_market.setdefault(mid, []).append(record)

        for market_id, records in by_market.items():
            inv = MarketInventory(market_id=market_id, token_id="")
            for rec in records:
                token_id = rec.get("token_id", "")
                pos = float(rec.get("net_position", 0))
                avg = float(rec.get("avg_entry_price", 0))
                pnl = float(rec.get("realized_pnl", 0))
                # Heuristic: if there's already a YES token set and this is a different token,
                # treat it as NO. Otherwise, first token is YES.
                if inv.token_id and token_id != inv.token_id:
                    inv.no_token_id = token_id
                    inv.no_position = pos
                    inv.no_avg_entry_price = avg
                    inv.no_realized_pnl = pnl
                else:
                    inv.token_id = token_id
                    inv.net_position = pos
                    inv.avg_entry_price = avg
                    inv.realized_pnl = pnl
            self._inventory[market_id] = inv
        logger.info(f"Loaded inventory for {len(by_market)} markets from DB ({len(db_inventory)} records)")

    def reconcile_with_clob(self, db_inventory: list[dict]) -> list[dict]:
        """Compare in-memory inventory vs DB, return divergences, auto-correct.

        DB is the source of truth (persists across restarts).
        Groups DB records by market_id to handle both YES and NO tokens.
        Returns list of divergence dicts for logging.
        """
        divergences = []
        # Group DB records by market_id
        db_by_market: dict[str, list[dict]] = {}
        for r in db_inventory:
            db_by_market.setdefault(r["market_id"], []).append(r)

        for market_id, records in db_by_market.items():
            mem_inv = self._inventory.get(market_id)

            for db_rec in records:
                db_pos = float(db_rec.get("net_position", 0))
                db_token = db_rec.get("token_id", "")

                # Determine if this record is YES or NO
                is_no = (mem_inv and mem_inv.no_token_id and db_token == mem_inv.no_token_id)
                if not is_no and mem_inv and mem_inv.token_id and db_token != mem_inv.token_id:
                    # Token doesn't match YES — assume NO
                    is_no = True

                if is_no:
                    mem_pos = mem_inv.no_position if mem_inv else 0.0
                else:
                    mem_pos = mem_inv.net_position if mem_inv else 0.0

                if abs(mem_pos - db_pos) > 0.1:
                    divergences.append({
                        "market_id": market_id,
                        "token_id": db_token,
                        "side": "NO" if is_no else "YES",
                        "mem_pos": mem_pos,
                        "db_pos": db_pos,
                    })
                    if mem_inv:
                        if is_no:
                            mem_inv.no_position = db_pos
                        else:
                            mem_inv.net_position = db_pos
                    else:
                        inv = MarketInventory(
                            market_id=market_id,
                            token_id=db_token if not is_no else "",
                            net_position=db_pos if not is_no else 0.0,
                            avg_entry_price=float(db_rec.get("avg_entry_price", 0)),
                            realized_pnl=float(db_rec.get("realized_pnl", 0)),
                        )
                        if is_no:
                            inv.no_token_id = db_token
                            inv.no_position = db_pos
                        self._inventory[market_id] = inv
                        mem_inv = inv  # For subsequent records

        return divergences
