"""Order state machine and QuotePair tracking for market-making."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class OrderState(Enum):
    NEW = "NEW"
    LIVE = "LIVE"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


# Valid transitions: {current_state: {allowed_next_states}}
TRANSITIONS = {
    OrderState.NEW: {OrderState.LIVE, OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN},
    OrderState.LIVE: {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN},
    OrderState.PARTIAL: {OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN},
    OrderState.FILLED: set(),  # terminal
    OrderState.CANCELLED: {OrderState.FILLED},  # fill can arrive after cancel sent
    OrderState.UNKNOWN: {OrderState.LIVE, OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELLED},
}


def can_transition(current: OrderState, target: OrderState) -> bool:
    return target in TRANSITIONS.get(current, set())


def parse_clob_status(status_str: str) -> OrderState:
    """Map CLOB API status string to OrderState."""
    s = status_str.upper().strip()
    mapping = {
        "LIVE": OrderState.LIVE,
        "ACTIVE": OrderState.LIVE,
        "OPEN": OrderState.LIVE,
        "MATCHED": OrderState.FILLED,
        "FILLED": OrderState.FILLED,
        "CANCELLED": OrderState.CANCELLED,
        "CANCELED": OrderState.CANCELLED,
        "EXPIRED": OrderState.CANCELLED,
    }
    return mapping.get(s, OrderState.UNKNOWN)


@dataclass
class QuotePair:
    """Represents a bid+ask quote pair on a single market."""
    market_id: str
    token_id: str
    bid_price: float
    ask_price: float
    size: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    no_token_id: str | None = None
    condition_id: str | None = None
    bid_order_id: str | None = None
    ask_order_id: str | None = None
    bid_state: OrderState = OrderState.NEW
    ask_state: OrderState = OrderState.NEW
    db_id: int | None = None
    quoted_mid: float = 0.0  # actual market mid at time of quoting (for requote comparison)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.bid_size == 0.0:
            self.bid_size = self.size
        if self.ask_size == 0.0:
            self.ask_size = self.size

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    @property
    def mid(self) -> float:
        return (self.bid_price + self.ask_price) / 2

    @property
    def is_active(self) -> bool:
        return (
            self.bid_state in (OrderState.NEW, OrderState.LIVE, OrderState.PARTIAL)
            or self.ask_state in (OrderState.NEW, OrderState.LIVE, OrderState.PARTIAL)
        )

    @property
    def is_fully_filled(self) -> bool:
        return self.bid_state == OrderState.FILLED and self.ask_state == OrderState.FILLED

    @property
    def is_terminal(self) -> bool:
        bid_done = self.bid_state in (OrderState.FILLED, OrderState.CANCELLED)
        ask_done = self.ask_state in (OrderState.FILLED, OrderState.CANCELLED)
        return bid_done and ask_done

    def update_bid_state(self, new_state: OrderState) -> bool:
        """Idempotent state transition for bid side. Returns True if state changed."""
        if new_state == self.bid_state:
            return False
        if can_transition(self.bid_state, new_state):
            self.bid_state = new_state
            self.updated_at = datetime.now(timezone.utc)
            return True
        logger.warning(
            f"Invalid bid transition {self.bid_state.value} -> {new_state.value} "
            f"for {self.market_id[:16]}"
        )
        return False

    def update_ask_state(self, new_state: OrderState) -> bool:
        """Idempotent state transition for ask side. Returns True if state changed."""
        if new_state == self.ask_state:
            return False
        if can_transition(self.ask_state, new_state):
            self.ask_state = new_state
            self.updated_at = datetime.now(timezone.utc)
            return True
        logger.warning(
            f"Invalid ask transition {self.ask_state.value} -> {new_state.value} "
            f"for {self.market_id[:16]}"
        )
        return False

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()
