"""Tests for executor/client.py (PolymarketClient)."""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from py_clob_client.exceptions import PolyApiException


# ═══════════════════════════════════════════════════════════════════════════════
# PolymarketClient tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPolymarketClientInit:
    """Constructor and connection lifecycle."""

    def test_init_sets_config_and_no_client(self, polymarket_config):
        from executor.client import PolymarketClient

        pm = PolymarketClient(polymarket_config)
        assert pm.config is polymarket_config
        assert pm._client is None

    def test_client_property_raises_when_not_connected(self, polymarket_config):
        from executor.client import PolymarketClient

        pm = PolymarketClient(polymarket_config)
        with pytest.raises(RuntimeError, match="Client not connected"):
            _ = pm.client

    @patch("executor.client.ClobClient")
    def test_connect_initializes_clob_client(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {"api_key": "k", "secret": "s"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        MockClob.assert_called_once_with(
            polymarket_config.host,
            key=polymarket_config.private_key,
            chain_id=polymarket_config.chain_id,
            signature_type=polymarket_config.signature_type,
            funder=polymarket_config.funder_address or None,
        )
        mock_instance.create_or_derive_api_creds.assert_called_once()
        mock_instance.set_api_creds.assert_called_once_with({"api_key": "k", "secret": "s"})
        assert pm._client is mock_instance

    @patch("executor.client.ClobClient")
    def test_client_property_returns_client_after_connect(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.client is mock_instance


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------


class TestGetMidpoint:
    @patch("executor.client.ClobClient")
    def test_midpoint_float_response(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_midpoint.return_value = 0.65
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        result = pm.get_midpoint("tok-123")
        assert result == 0.65

    @patch("executor.client.ClobClient")
    def test_midpoint_dict_response(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_midpoint.return_value = {"mid": "0.72"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        result = pm.get_midpoint("tok-123")
        assert result == 0.72

    @patch("executor.client.ClobClient")
    def test_midpoint_exception_returns_none(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_midpoint.side_effect = Exception("network error")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_midpoint("tok-123") is None


class TestGetPrice:
    @patch("executor.client.ClobClient")
    def test_price_float_response(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_price.return_value = 0.55
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_price("tok-123", side="SELL") == 0.55
        mock_instance.get_price.assert_called_once_with("tok-123", side="SELL")

    @patch("executor.client.ClobClient")
    def test_price_dict_response(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_price.return_value = {"price": "0.48"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_price("tok-123") == 0.48

    @patch("executor.client.ClobClient")
    def test_price_exception_returns_none(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_price.side_effect = Exception("timeout")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_price("tok-123") is None


class TestGetOrderBook:
    @patch("executor.client.ClobClient")
    def test_order_book_success(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        book = {"bids": [], "asks": []}
        mock_instance.get_order_book.return_value = book
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_order_book("tok-123") == book

    @patch("executor.client.ClobClient")
    def test_order_book_exception_returns_none(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order_book.side_effect = Exception("err")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_order_book("tok-123") is None


# ---------------------------------------------------------------------------
# Order management
# ---------------------------------------------------------------------------


class TestPlaceLimitOrder:
    @patch("executor.client.ClobClient")
    def test_buy_checks_balance_and_places(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed-order"
        mock_instance.post_order.return_value = {"orderID": "abc"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance", return_value=100.0):
            result = pm.place_limit_order("tok-123", price=0.50, size=10.0, side="BUY")

        assert result == {"orderID": "abc"}

    @patch("executor.client.ClobClient")
    def test_buy_insufficient_balance_returns_none(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance", return_value=1.0):
            result = pm.place_limit_order("tok-123", price=0.50, size=10.0, side="BUY")

        assert result is None
        mock_instance.create_order.assert_not_called()

    @patch("executor.client.ClobClient")
    def test_sell_preflight_sufficient_token_balance(self, MockClob, polymarket_config):
        """SELL order proceeds when token balance covers size."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed"
        mock_instance.post_order.return_value = {"orderID": "sell-1"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        # Token balance = 10, need 5 -> OK
        with patch.object(pm, "get_token_balance", return_value=10.0):
            result = pm.place_limit_order("tok-123", price=0.60, size=5.0, side="SELL")

        assert result == {"orderID": "sell-1"}

    @patch("executor.client.ClobClient")
    def test_sell_preflight_insufficient_token_balance(self, MockClob, polymarket_config):
        """SELL order rejected when token balance is below required size."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        # Token balance = 1, need 5 -> rejected
        with patch.object(pm, "get_token_balance", return_value=1.0):
            result = pm.place_limit_order("tok-123", price=0.60, size=5.0, side="SELL")

        assert result is None
        mock_instance.create_order.assert_not_called()

    @patch("executor.client.ClobClient")
    def test_buy_balance_none_proceeds(self, MockClob, polymarket_config):
        """When balance check returns None, order should still proceed."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed"
        mock_instance.post_order.return_value = {"orderID": "x"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance", return_value=None):
            result = pm.place_limit_order("tok-123", price=0.50, size=10.0, side="BUY")

        assert result == {"orderID": "x"}

    @patch("executor.client.ClobClient")
    def test_place_order_exception_returns_none(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.side_effect = Exception("order fail")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance", return_value=100.0):
            result = pm.place_limit_order("tok-123", price=0.50, size=10.0, side="BUY")

        assert result is None

    @patch("executor.client.ClobClient")
    def test_post_only_cross_retries_with_safer_price(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed"
        mock_instance.post_order.side_effect = [
            PolyApiException(error_msg={"error": "invalid post-only order: order crosses book"}),
            {"orderID": "retry-ok"},
        ]
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance", return_value=100.0):
            result = pm.place_limit_order(
                "tok-123",
                price=0.50,
                size=10.0,
                side="BUY",
                post_only=True,
            )

        assert result == {"orderID": "retry-ok"}
        assert mock_instance.create_order.call_count == 2
        first_price = mock_instance.create_order.call_args_list[0].args[0].price
        second_price = mock_instance.create_order.call_args_list[1].args[0].price
        assert first_price == 0.50
        assert second_price == 0.49

    @patch("executor.client.ClobClient")
    def test_post_only_cross_retries_multiple_ticks_until_success(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed"
        mock_instance.post_order.side_effect = [
            PolyApiException(error_msg={"error": "invalid post-only order: order crosses book"}),
            PolyApiException(error_msg={"error": "invalid post-only order: order crosses book"}),
            {"orderID": "retry-ok-2"},
        ]
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance", return_value=100.0):
            result = pm.place_limit_order(
                "tok-123",
                price=0.50,
                size=10.0,
                side="BUY",
                post_only=True,
            )

        assert result == {"orderID": "retry-ok-2"}
        assert mock_instance.create_order.call_count == 3
        prices = [c.args[0].price for c in mock_instance.create_order.call_args_list]
        assert prices == [0.50, 0.49, 0.48]


class TestGetOrder:
    @patch("executor.client.ClobClient")
    def test_get_order_success(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = {"status": "LIVE"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_order("order-1") == {"status": "LIVE"}

    @patch("executor.client.ClobClient")
    def test_get_order_exception(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.side_effect = Exception("not found")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_order("order-1") is None


# ---------------------------------------------------------------------------
# _as_float
# ---------------------------------------------------------------------------


class TestAsFloat:
    def test_none_returns_default(self):
        from executor.client import PolymarketClient

        assert PolymarketClient._as_float(None) == 0.0
        assert PolymarketClient._as_float(None, 5.0) == 5.0

    def test_valid_float_string(self):
        from executor.client import PolymarketClient

        assert PolymarketClient._as_float("3.14") == 3.14

    def test_valid_int(self):
        from executor.client import PolymarketClient

        assert PolymarketClient._as_float(42) == 42.0

    def test_invalid_returns_default(self):
        from executor.client import PolymarketClient

        assert PolymarketClient._as_float("not-a-number") == 0.0
        assert PolymarketClient._as_float("not-a-number", 99.0) == 99.0

    def test_empty_string_returns_default(self):
        from executor.client import PolymarketClient

        assert PolymarketClient._as_float("") == 0.0

    def test_zero(self):
        from executor.client import PolymarketClient

        assert PolymarketClient._as_float(0) == 0.0
        assert PolymarketClient._as_float("0") == 0.0


# ---------------------------------------------------------------------------
# _extract_order_execution_meta
# ---------------------------------------------------------------------------


class TestExtractOrderExecutionMeta:
    def _make_client(self, polymarket_config):
        from executor.client import PolymarketClient

        pm = PolymarketClient(polymarket_config)
        return pm

    def test_size_matched_first_key(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "size_matched": "10.5",
            "avg_fill_price": "0.55",
        })
        assert meta["status"] == "MATCHED"
        assert meta["size_matched"] == 10.5
        assert meta["avg_fill_price"] == 0.55

    def test_matched_size_key(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "live",
            "matched_size": 7.0,
            "avg_price": 0.60,
        })
        assert meta["status"] == "LIVE"
        assert meta["size_matched"] == 7.0
        assert meta["avg_fill_price"] == 0.60

    def test_filled_size_key(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "filled_size": "5.0",
            "fill_price": 0.70,
        })
        assert meta["size_matched"] == 5.0
        assert meta["avg_fill_price"] == 0.70

    def test_notional_from_exchange_field(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "size_matched": 10.0,
            "avg_fill_price": 0.55,
            "matched_notional": "5.50",
        })
        assert meta["notional_matched"] == 5.50

    def test_notional_computed_when_missing(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "size_matched": 10.0,
            "avg_fill_price": 0.55,
        })
        assert meta["notional_matched"] == pytest.approx(5.50, abs=0.01)

    def test_fees_paid_key(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "fees_paid": "0.12",
        })
        assert meta["fees_paid"] == 0.12

    def test_fees_alternative_key(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "fee": "0.05",
        })
        assert meta["fees_paid"] == 0.05

    def test_empty_order(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({})
        assert meta["status"] == "UNKNOWN"
        assert meta["size_matched"] == 0.0
        assert meta["avg_fill_price"] is None
        assert meta["notional_matched"] is None
        assert meta["fees_paid"] == 0.0

    def test_raw_preserved(self, polymarket_config):
        pm = self._make_client(polymarket_config)
        order = {"status": "MATCHED", "extra": "data"}
        meta = pm._extract_order_execution_meta(order)
        assert meta["raw"] is order

    def test_avg_fill_price_skips_zero_values(self, polymarket_config):
        """avg_fill_price should only be set if the value is > 0."""
        pm = self._make_client(polymarket_config)
        meta = pm._extract_order_execution_meta({
            "status": "MATCHED",
            "avg_fill_price": "0",
            "price": "0.55",
        })
        # avg_fill_price=0 is skipped, falls through to price=0.55
        assert meta["avg_fill_price"] == 0.55


# ---------------------------------------------------------------------------
# is_order_filled
# ---------------------------------------------------------------------------


class TestIsOrderFilled:
    @patch("executor.client.ClobClient")
    def test_matched_status(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = {
            "status": "MATCHED",
            "size_matched": "10.0",
            "original_size": "10.0",
            "avg_fill_price": "0.55",
        }
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        is_filled, status, size_matched, meta = pm.is_order_filled("order-1")
        assert is_filled is True
        assert status == "MATCHED"
        assert size_matched == 10.0

    @patch("executor.client.ClobClient")
    def test_live_status_not_filled(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = {
            "status": "LIVE",
            "size_matched": "0",
            "original_size": "10.0",
        }
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        is_filled, status, size_matched, meta = pm.is_order_filled("order-2")
        assert is_filled is False
        assert status == "LIVE"
        assert size_matched == 0.0

    @patch("executor.client.ClobClient")
    def test_cancelled_status(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = {
            "status": "CANCELLED",
            "size_matched": "3.0",
            "original_size": "10.0",
        }
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        is_filled, status, size_matched, meta = pm.is_order_filled("order-3")
        assert is_filled is False
        assert status == "CANCELLED"
        assert size_matched == 3.0

    @patch("executor.client.ClobClient")
    def test_get_order_returns_none(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = None
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        is_filled, status, size_matched, meta = pm.is_order_filled("order-nil")
        assert is_filled is False
        assert status == "UNKNOWN"
        assert meta == {}

    @patch("executor.client.ClobClient")
    def test_filled_by_size_comparison(self, MockClob, polymarket_config):
        """Even if status is not MATCHED, filled when size_matched >= original_size."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = {
            "status": "LIVE",
            "size_matched": "10.0",
            "original_size": "10.0",
        }
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        is_filled, status, size_matched, meta = pm.is_order_filled("order-full")
        assert is_filled is True

    @patch("executor.client.ClobClient")
    def test_exception_in_get_order_returns_unknown(self, MockClob, polymarket_config):
        """When get_order raises, it returns None, so is_order_filled sees UNKNOWN."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.side_effect = Exception("boom")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        is_filled, status, size_matched, meta = pm.is_order_filled("order-err")
        assert is_filled is False
        assert status == "UNKNOWN"
        assert meta == {}

    @patch("executor.client.ClobClient")
    def test_exception_in_meta_extraction_returns_error(self, MockClob, polymarket_config):
        """When _extract_order_execution_meta raises, the outer except catches it."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_order.return_value = {"status": "MATCHED"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        # Force an exception after get_order succeeds
        with patch.object(pm, "_extract_order_execution_meta", side_effect=Exception("parse error")):
            is_filled, status, size_matched, meta = pm.is_order_filled("order-err")

        assert is_filled is False
        assert status == "ERROR"
        assert meta == {}


# ---------------------------------------------------------------------------
# get_open_orders, cancel_order, cancel_all_orders
# ---------------------------------------------------------------------------


class TestOpenOrdersAndCancellation:
    @patch("executor.client.ClobClient")
    def test_get_open_orders_success(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_orders.return_value = [{"id": "o1"}, {"id": "o2"}]
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        result = pm.get_open_orders()
        assert len(result) == 2

    @patch("executor.client.ClobClient")
    def test_get_open_orders_exception_returns_empty(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.get_orders.side_effect = Exception("err")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.get_open_orders() == []

    @patch("executor.client.ClobClient")
    def test_cancel_order_success(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.cancel_order("order-1") is True
        mock_instance.cancel.assert_called_once_with("order-1")

    @patch("executor.client.ClobClient")
    def test_cancel_order_failure(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.cancel.side_effect = Exception("fail")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.cancel_order("order-1") is False

    @patch("executor.client.ClobClient")
    def test_cancel_all_orders_success(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.cancel_all_orders() is True
        mock_instance.cancel_all.assert_called_once()

    @patch("executor.client.ClobClient")
    def test_cancel_all_orders_failure(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.cancel_all.side_effect = Exception("fail")
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        assert pm.cancel_all_orders() is False


# ---------------------------------------------------------------------------
# get_onchain_balance
# ---------------------------------------------------------------------------


class TestGetOnchainBalance:
    def test_no_funder_address_returns_none(self, polymarket_config):
        from executor.client import PolymarketClient

        polymarket_config.funder_address = ""
        pm = PolymarketClient(polymarket_config)
        assert pm.get_onchain_balance() is None

    @patch("executor.client.requests.post")
    def test_parses_hex_result(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        # 50 USDC.e = 50_000_000 = 0x2FAF080
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "0x0000000000000000000000000000000000000000000000000000000002FAF080"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        balance = pm.get_onchain_balance()
        assert balance == pytest.approx(50.0)

    @patch("executor.client.requests.post")
    def test_zero_balance(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0x0"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        balance = pm.get_onchain_balance()
        assert balance == 0.0

    @patch("executor.client.requests.post")
    def test_rpc_error_returns_none(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_post.side_effect = Exception("connection refused")

        pm = PolymarketClient(polymarket_config)
        assert pm.get_onchain_balance() is None

    @patch("executor.client.requests.post")
    def test_uses_correct_rpc_url(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0x0"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        polymarket_config.rpc_url = "https://custom-rpc.example.com"
        pm = PolymarketClient(polymarket_config)
        pm.get_onchain_balance()

        call_args = mock_post.call_args
        assert call_args[0][0] == "https://custom-rpc.example.com"


# ---------------------------------------------------------------------------
# check_market_resolved
# ---------------------------------------------------------------------------


class TestCheckMarketResolved:
    @patch("executor.client.requests.get")
    def test_resolved_market_yes(self, mock_get, polymarket_config):
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "closed": True,
            "resolutionSource": "uma",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["1.0", "0.0"]',
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        result = pm.check_market_resolved("market-1")
        assert result["resolved"] is True
        assert result["outcome"] == "Yes"
        assert result["resolution_source"] == "uma"

    @patch("executor.client.requests.get")
    def test_not_closed_market(self, mock_get, polymarket_config):
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "closed": False,
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.55", "0.45"],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        result = pm.check_market_resolved("market-2")
        assert result["resolved"] is False
        assert result["outcome"] is None

    @patch("executor.client.requests.get")
    def test_404_returns_none(self, mock_get, polymarket_config):
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        assert pm.check_market_resolved("market-deleted") is None

    @patch("executor.client.requests.get")
    def test_exception_returns_none(self, mock_get, polymarket_config):
        from executor.client import PolymarketClient

        mock_get.side_effect = Exception("network")
        pm = PolymarketClient(polymarket_config)
        assert pm.check_market_resolved("market-err") is None

    @patch("executor.client.requests.get")
    def test_resolved_no_winner(self, mock_get, polymarket_config):
        """Closed but no outcome reaches 0.99 => resolved=False."""
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "closed": True,
            "resolutionSource": "uma",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.50", "0.50"],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        result = pm.check_market_resolved("market-3")
        assert result["resolved"] is False
        assert result["outcome"] is None

    @patch("executor.client.requests.get")
    def test_outcomes_as_list(self, mock_get, polymarket_config):
        """Handles outcomes as native lists (not JSON strings)."""
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "closed": True,
            "resolutionSource": "uma",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.01", "0.99"],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        result = pm.check_market_resolved("market-4")
        assert result["resolved"] is True
        assert result["outcome"] == "No"


# ---------------------------------------------------------------------------
# Split / Merge operations
# ---------------------------------------------------------------------------


class TestSplitPosition:
    @patch("executor.client.requests.post")
    @patch("executor.client.ClobClient")
    def test_split_success(self, MockClob, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        # RPC responses: allowance check, nonce, gas price, send tx, receipt
        allowance_resp = MagicMock()
        allowance_resp.json.return_value = {"result": "0x" + "f" * 64}  # large allowance
        nonce_resp = MagicMock()
        nonce_resp.json.return_value = {"result": "0x5"}
        gas_resp = MagicMock()
        gas_resp.json.return_value = {"result": "0x3B9ACA00"}  # 1 gwei
        send_resp = MagicMock()
        send_resp.json.return_value = {"result": "0x" + "ab" * 32}
        receipt_resp = MagicMock()
        receipt_resp.json.return_value = {"result": {"status": "0x1"}}

        mock_post.side_effect = [allowance_resp, nonce_resp, gas_resp, send_resp, receipt_resp]

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        condition_id = "ab" * 32
        result = pm.split_position(condition_id, 10.0)
        assert result is True

    @patch("executor.client.ClobClient")
    def test_split_failure_returns_false(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "_ensure_usdc_approval", side_effect=Exception("approval failed")):
            result = pm.split_position("ab" * 32, 10.0)
        assert result is False


class TestMergePositions:
    @patch("executor.client.requests.post")
    @patch("executor.client.ClobClient")
    def test_merge_success(self, MockClob, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        # RPC responses: nonce, gas price, send tx, receipt
        nonce_resp = MagicMock()
        nonce_resp.json.return_value = {"result": "0x5"}
        gas_resp = MagicMock()
        gas_resp.json.return_value = {"result": "0x3B9ACA00"}
        send_resp = MagicMock()
        send_resp.json.return_value = {"result": "0x" + "cd" * 32}
        receipt_resp = MagicMock()
        receipt_resp.json.return_value = {"result": {"status": "0x1"}}

        mock_post.side_effect = [nonce_resp, gas_resp, send_resp, receipt_resp]

        pm = PolymarketClient(polymarket_config)
        pm.connect()
        result = pm.merge_positions("ab" * 32, 5.0)
        assert result is True

    @patch("executor.client.ClobClient")
    def test_merge_failure_returns_false(self, MockClob, polymarket_config):
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "_send_transaction", return_value=None):
            result = pm.merge_positions("ab" * 32, 5.0)
        assert result is False


# ---------------------------------------------------------------------------
# get_token_balance
# ---------------------------------------------------------------------------


class TestGetTokenBalance:
    @patch("executor.client.requests.post")
    def test_token_balance_success(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        # 25 tokens = 25_000_000 = 0x17D7840
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": "0x00000000000000000000000000000000000000000000000000000000017D7840"
        }
        mock_post.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        balance = pm.get_token_balance("12345")
        assert balance == pytest.approx(25.0)

    @patch("executor.client.requests.post")
    def test_token_balance_zero(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0x0"}
        mock_post.return_value = mock_resp

        pm = PolymarketClient(polymarket_config)
        balance = pm.get_token_balance("12345")
        assert balance == 0.0

    def test_token_balance_no_funder_returns_none(self, polymarket_config):
        from executor.client import PolymarketClient

        polymarket_config.funder_address = ""
        pm = PolymarketClient(polymarket_config)
        assert pm.get_token_balance("12345") is None

    @patch("executor.client.requests.post")
    def test_token_balance_error_returns_none(self, mock_post, polymarket_config):
        from executor.client import PolymarketClient

        mock_post.side_effect = Exception("rpc error")
        pm = PolymarketClient(polymarket_config)
        assert pm.get_token_balance("12345") is None


# ---------------------------------------------------------------------------
# known_balance parameter on place_limit_order
# ---------------------------------------------------------------------------


class TestKnownBalance:
    @patch("executor.client.ClobClient")
    def test_known_balance_skips_rpc_call(self, MockClob, polymarket_config):
        """When known_balance is provided, get_onchain_balance should not be called."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed"
        mock_instance.post_order.return_value = {"orderID": "abc"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        with patch.object(pm, "get_onchain_balance") as mock_balance:
            result = pm.place_limit_order(
                "tok-123", price=0.50, size=10.0, side="BUY", known_balance=100.0
            )

        assert result == {"orderID": "abc"}
        mock_balance.assert_not_called()

    @patch("executor.client.ClobClient")
    def test_known_balance_insufficient_rejects(self, MockClob, polymarket_config):
        """When known_balance is below required, order should be rejected."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        result = pm.place_limit_order(
            "tok-123", price=0.50, size=10.0, side="BUY", known_balance=1.0
        )
        assert result is None
        mock_instance.create_order.assert_not_called()

    @patch("executor.client.ClobClient")
    def test_sell_uses_token_balance_not_known_balance(self, MockClob, polymarket_config):
        """SELL pre-flight uses get_token_balance, ignores known_balance."""
        from executor.client import PolymarketClient

        mock_instance = MagicMock()
        mock_instance.create_or_derive_api_creds.return_value = {}
        mock_instance.create_order.return_value = "signed"
        mock_instance.post_order.return_value = {"orderID": "sell-1"}
        MockClob.return_value = mock_instance

        pm = PolymarketClient(polymarket_config)
        pm.connect()

        # SELL checks token balance, not USDC known_balance
        with patch.object(pm, "get_token_balance", return_value=10.0) as mock_token:
            result = pm.place_limit_order(
                "tok-123", price=0.60, size=5.0, side="SELL", known_balance=0.0
            )

        assert result == {"orderID": "sell-1"}
        mock_token.assert_called_once_with("tok-123")
