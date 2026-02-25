"""Targeted tests for MM quoter side-placement behavior."""

from unittest.mock import MagicMock

from config import MarketMakingConfig
from mm.quoter import Quoter
from mm.state import OrderState


def test_place_quote_pair_bid_only_skips_sell_when_no_inventory():
    client = MagicMock()
    client.place_limit_order.return_value = {"orderID": "bid-1"}
    cfg = MarketMakingConfig()
    quoter = Quoter(client, cfg)

    pair = quoter.place_quote_pair(
        token_id="tok-1",
        market_id="mkt-1",
        bid_price=0.42,
        ask_price=0.58,
        size=6.0,
        place_ask=False,
    )

    assert pair is not None
    assert pair.bid_order_id == "bid-1"
    assert pair.ask_order_id is None
    assert pair.bid_state == OrderState.LIVE
    assert pair.ask_state == OrderState.CANCELLED
    client.place_limit_order.assert_called_once()


def test_place_quote_pair_bid_only_returns_none_if_bid_fails():
    client = MagicMock()
    client.place_limit_order.return_value = None
    client.get_last_order_error.return_value = {"code": "post_only_cross"}
    cfg = MarketMakingConfig()
    quoter = Quoter(client, cfg)

    pair = quoter.place_quote_pair(
        token_id="tok-1",
        market_id="mkt-1",
        bid_price=0.42,
        ask_price=0.58,
        size=6.0,
        place_ask=False,
    )

    assert pair is None
    client.place_limit_order.assert_called_once()
    failure = quoter.get_last_quote_failure()
    assert failure is not None
    assert failure["market_id"] == "mkt-1"
    assert failure["bid_error"]["code"] == "post_only_cross"
