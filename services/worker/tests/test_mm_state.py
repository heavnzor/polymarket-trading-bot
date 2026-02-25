"""Tests for mm/state.py — order state machine and QuotePair."""

import pytest
from mm.state import (
    OrderState,
    can_transition,
    parse_clob_status,
    QuotePair,
)


class TestOrderStateTransitions:
    def test_new_to_live(self):
        assert can_transition(OrderState.NEW, OrderState.LIVE) is True

    def test_new_to_filled(self):
        assert can_transition(OrderState.NEW, OrderState.FILLED) is True

    def test_live_to_partial(self):
        assert can_transition(OrderState.LIVE, OrderState.PARTIAL) is True

    def test_filled_is_terminal(self):
        assert can_transition(OrderState.FILLED, OrderState.LIVE) is False
        assert can_transition(OrderState.FILLED, OrderState.CANCELLED) is False

    def test_cancelled_can_receive_fill(self):
        assert can_transition(OrderState.CANCELLED, OrderState.FILLED) is True


class TestParseClobStatus:
    def test_live_variants(self):
        assert parse_clob_status("LIVE") == OrderState.LIVE
        assert parse_clob_status("ACTIVE") == OrderState.LIVE
        assert parse_clob_status("OPEN") == OrderState.LIVE

    def test_filled_variants(self):
        assert parse_clob_status("MATCHED") == OrderState.FILLED
        assert parse_clob_status("FILLED") == OrderState.FILLED

    def test_cancelled_variants(self):
        assert parse_clob_status("CANCELLED") == OrderState.CANCELLED
        assert parse_clob_status("CANCELED") == OrderState.CANCELLED
        assert parse_clob_status("EXPIRED") == OrderState.CANCELLED

    def test_unknown_returns_unknown(self):
        assert parse_clob_status("FOOBAR") == OrderState.UNKNOWN


class TestQuotePairBidAskSize:
    def test_default_bid_ask_size_from_size(self):
        """When bid_size/ask_size not set, __post_init__ copies from size."""
        qp = QuotePair(
            market_id="m1",
            token_id="t1",
            bid_price=0.40,
            ask_price=0.60,
            size=100.0,
        )
        assert qp.bid_size == 100.0
        assert qp.ask_size == 100.0

    def test_explicit_bid_ask_size_preserved(self):
        """When bid_size/ask_size are explicitly set, they are NOT overwritten."""
        qp = QuotePair(
            market_id="m1",
            token_id="t1",
            bid_price=0.40,
            ask_price=0.60,
            size=100.0,
            bid_size=50.0,
            ask_size=75.0,
        )
        assert qp.bid_size == 50.0
        assert qp.ask_size == 75.0

    def test_no_token_id_and_condition_id(self):
        """New fields are stored correctly."""
        qp = QuotePair(
            market_id="m1",
            token_id="t1",
            bid_price=0.40,
            ask_price=0.60,
            size=100.0,
            no_token_id="no-t1",
            condition_id="cond-abc",
        )
        assert qp.no_token_id == "no-t1"
        assert qp.condition_id == "cond-abc"


class TestQuotePairProperties:
    def test_spread(self):
        qp = QuotePair(market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10)
        assert qp.spread == pytest.approx(0.20)

    def test_mid(self):
        qp = QuotePair(market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10)
        assert qp.mid == pytest.approx(0.50)

    def test_is_active(self):
        qp = QuotePair(market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10)
        assert qp.is_active is True

    def test_is_terminal(self):
        qp = QuotePair(
            market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10,
            bid_state=OrderState.FILLED, ask_state=OrderState.CANCELLED,
        )
        assert qp.is_terminal is True

    def test_is_fully_filled(self):
        qp = QuotePair(
            market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10,
            bid_state=OrderState.FILLED, ask_state=OrderState.FILLED,
        )
        assert qp.is_fully_filled is True


class TestQuotePairStateTransitions:
    def test_update_bid_state(self):
        qp = QuotePair(market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10)
        assert qp.update_bid_state(OrderState.LIVE) is True
        assert qp.bid_state == OrderState.LIVE

    def test_invalid_bid_transition(self):
        qp = QuotePair(
            market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10,
            bid_state=OrderState.FILLED,
        )
        assert qp.update_bid_state(OrderState.LIVE) is False
        assert qp.bid_state == OrderState.FILLED

    def test_idempotent_transition(self):
        qp = QuotePair(
            market_id="m1", token_id="t1", bid_price=0.40, ask_price=0.60, size=10,
            bid_state=OrderState.LIVE,
        )
        assert qp.update_bid_state(OrderState.LIVE) is False
