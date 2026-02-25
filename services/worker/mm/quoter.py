"""Quote management: place, cancel, requote, reconcile quote pairs."""

import logging

from mm.state import QuotePair, OrderState, parse_clob_status
from config import MarketMakingConfig

logger = logging.getLogger(__name__)


class Quoter:
    """Manages quote lifecycle on the Polymarket CLOB."""

    def __init__(self, client, mm_config: MarketMakingConfig):
        self.client = client
        self.config = mm_config
        self._last_quote_failure: dict | None = None

    def get_last_quote_failure(self) -> dict | None:
        if not self._last_quote_failure:
            return None
        return dict(self._last_quote_failure)

    def place_quote_pair(
        self,
        token_id: str,
        market_id: str,
        bid_price: float,
        ask_price: float,
        size: float,
        place_ask: bool = True,
        place_bid: bool = True,
    ) -> QuotePair | None:
        """Place a bid+ask quote pair. Returns QuotePair or None on failure."""
        self._last_quote_failure = None
        get_last_order_error = getattr(self.client, "get_last_order_error", lambda: None)

        pair = QuotePair(
            market_id=market_id,
            token_id=token_id,
            bid_price=bid_price,
            ask_price=ask_price,
            size=size,
        )
        bid_error = None
        ask_error = None

        if place_bid:
            # Place bid (BUY)
            bid_resp = self.client.place_limit_order(
                token_id=token_id,
                price=bid_price,
                size=size,
                side="BUY",
                order_type="GTC",
                post_only=self.config.mm_post_only,
            )
            if bid_resp:
                pair.bid_order_id = self._extract_order_id(bid_resp)
                pair.bid_state = OrderState.LIVE if pair.bid_order_id else OrderState.UNKNOWN
            else:
                pair.bid_state = OrderState.CANCELLED
                bid_error = get_last_order_error()
        else:
            pair.bid_state = OrderState.CANCELLED

        if place_ask:
            # Place ask (SELL)
            ask_resp = self.client.place_limit_order(
                token_id=token_id,
                price=ask_price,
                size=size,
                side="SELL",
                order_type="GTC",
                post_only=self.config.mm_post_only,
            )
            if ask_resp:
                pair.ask_order_id = self._extract_order_id(ask_resp)
                pair.ask_state = OrderState.LIVE if pair.ask_order_id else OrderState.UNKNOWN
            else:
                pair.ask_state = OrderState.CANCELLED
                ask_error = get_last_order_error()
        else:
            pair.ask_state = OrderState.CANCELLED

        if not pair.bid_order_id and not pair.ask_order_id:
            self._last_quote_failure = {
                "market_id": market_id,
                "token_id": token_id,
                "bid_error": bid_error,
                "ask_error": ask_error,
                "place_bid": place_bid,
                "place_ask": place_ask,
                "size": size,
                "bid_price": bid_price,
                "ask_price": ask_price,
            }
            sides = []
            if place_bid:
                sides.append("bid")
            if place_ask:
                sides.append("ask")
            logger.warning(
                f"Quote failed for {market_id} "
                f"(sides={'+'.join(sides)}, bid_error={bid_error}, ask_error={ask_error})"
            )
            return None

        if place_bid and place_ask:
            mode = "BID+ASK"
        elif place_bid:
            mode = "BID-only"
        else:
            mode = "ASK-only"
        logger.info(
            f"Quote placed ({mode}): BID {bid_price:.2f} / ASK {ask_price:.2f} "
            f"x{size} on {market_id}"
        )
        return pair

    def cancel_quote_pair(self, pair: QuotePair) -> bool:
        """Cancel both sides of a quote pair."""
        success = True
        if pair.bid_order_id and pair.bid_state in (OrderState.LIVE, OrderState.PARTIAL):
            if not self.client.cancel_order(pair.bid_order_id):
                success = False
            else:
                pair.update_bid_state(OrderState.CANCELLED)

        if pair.ask_order_id and pair.ask_state in (OrderState.LIVE, OrderState.PARTIAL):
            if not self.client.cancel_order(pair.ask_order_id):
                success = False
            else:
                pair.update_ask_state(OrderState.CANCELLED)

        return success

    def requote(
        self,
        pair: QuotePair,
        new_bid: float,
        new_ask: float,
        new_size: float | None = None,
        place_ask: bool = True,
        place_bid: bool = True,
    ) -> QuotePair | None:
        """Cancel old pair and place new one. Returns new QuotePair."""
        self.cancel_quote_pair(pair)
        size = new_size if new_size is not None else pair.size
        return self.place_quote_pair(
            token_id=pair.token_id,
            market_id=pair.market_id,
            bid_price=new_bid,
            ask_price=new_ask,
            size=size,
            place_ask=place_ask,
            place_bid=place_bid,
        )

    def requote_preserving_hanging(
        self,
        pair: QuotePair,
        new_bid: float,
        new_ask: float,
        new_size: float,
        place_ask: bool = True,
        place_bid: bool = True,
    ) -> QuotePair | None:
        """Requote while preserving hanging (partially-filled) orders.

        Only cancels LIVE orders whose price changed significantly.
        PARTIAL orders are left alone (hanging orders).
        """
        cancel_bid = (
            pair.bid_order_id
            and pair.bid_state == OrderState.LIVE
            and abs(pair.bid_price - new_bid) >= 0.005
        )
        cancel_ask = (
            pair.ask_order_id
            and pair.ask_state == OrderState.LIVE
            and abs(pair.ask_price - new_ask) >= 0.005
        )

        if cancel_bid:
            try:
                self.client.cancel_order(pair.bid_order_id)
                pair.update_bid_state(OrderState.CANCELLED)
            except Exception as e:
                logger.debug(f"Failed to cancel hanging bid: {e}")
                cancel_bid = False

        if cancel_ask:
            try:
                self.client.cancel_order(pair.ask_order_id)
                pair.update_ask_state(OrderState.CANCELLED)
            except Exception as e:
                logger.debug(f"Failed to cancel hanging ask: {e}")
                cancel_ask = False

        new_pair = QuotePair(
            market_id=pair.market_id,
            token_id=pair.token_id,
            bid_price=new_bid,
            ask_price=new_ask,
            size=new_size,
        )

        # Keep hanging (partial) order IDs
        if not cancel_bid and pair.bid_order_id and pair.bid_state == OrderState.PARTIAL:
            new_pair.bid_order_id = pair.bid_order_id
            new_pair.bid_state = OrderState.PARTIAL
            new_pair.bid_price = pair.bid_price
        elif place_bid and cancel_bid:
            bid_result = self._place_single_order(pair.token_id, new_bid, new_size, "BUY")
            if bid_result:
                new_pair.bid_order_id = bid_result
                new_pair.bid_state = OrderState.NEW

        if not cancel_ask and pair.ask_order_id and pair.ask_state == OrderState.PARTIAL:
            new_pair.ask_order_id = pair.ask_order_id
            new_pair.ask_state = OrderState.PARTIAL
            new_pair.ask_price = pair.ask_price
        elif place_ask and cancel_ask:
            ask_result = self._place_single_order(pair.token_id, new_ask, new_size, "SELL")
            if ask_result:
                new_pair.ask_order_id = ask_result
                new_pair.ask_state = OrderState.NEW

        if new_pair.bid_order_id or new_pair.ask_order_id:
            return new_pair
        return None

    def reconcile_quote(self, pair: QuotePair) -> list[dict]:
        """Check order status for both sides of a quote pair.

        Returns list of detected fills: [{side, order_id, price, size_matched, meta}]
        Handles race conditions: cancel sent but fill arrives.
        """
        fills = []

        # Check bid
        if pair.bid_order_id and pair.bid_state in (
            OrderState.LIVE, OrderState.PARTIAL, OrderState.NEW
        ):
            is_filled, status, size_matched, meta = self.client.is_order_filled(
                pair.bid_order_id
            )
            new_state = parse_clob_status(status)

            if is_filled or new_state == OrderState.FILLED:
                pair.update_bid_state(OrderState.FILLED)
                fills.append({
                    "side": "BUY",
                    "order_id": pair.bid_order_id,
                    "price": meta.get("avg_fill_price") or pair.bid_price,
                    "size_matched": size_matched or pair.size,
                    "meta": meta,
                })
            elif size_matched > 0 and not is_filled:
                pair.update_bid_state(OrderState.PARTIAL)
            else:
                pair.update_bid_state(new_state)

        # Check ask
        if pair.ask_order_id and pair.ask_state in (
            OrderState.LIVE, OrderState.PARTIAL, OrderState.NEW
        ):
            is_filled, status, size_matched, meta = self.client.is_order_filled(
                pair.ask_order_id
            )
            new_state = parse_clob_status(status)

            if is_filled or new_state == OrderState.FILLED:
                pair.update_ask_state(OrderState.FILLED)
                fills.append({
                    "side": "SELL",
                    "order_id": pair.ask_order_id,
                    "price": meta.get("avg_fill_price") or pair.ask_price,
                    "size_matched": size_matched or pair.size,
                    "meta": meta,
                })
            elif size_matched > 0 and not is_filled:
                pair.update_ask_state(OrderState.PARTIAL)
            else:
                pair.update_ask_state(new_state)

        return fills

    def _place_single_order(
        self, token_id: str, price: float, size: float, side: str
    ) -> str | None:
        """Place a single limit order and return the order ID, or None."""
        resp = self.client.place_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            order_type="GTC",
            post_only=self.config.mm_post_only,
        )
        if resp:
            return self._extract_order_id(resp)
        return None

    @staticmethod
    def _extract_order_id(response: dict) -> str | None:
        """Extract order ID from CLOB post_order response."""
        if isinstance(response, dict):
            return response.get("orderID") or response.get("order_id") or response.get("id")
        return None
