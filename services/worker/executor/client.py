import logging
import time
from typing import Any, Callable
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OpenOrderParams
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.exceptions import PolyApiException
from config import PolymarketConfig

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


class PolymarketClient:
    def __init__(self, config: PolymarketConfig):
        self.config = config
        self._client: ClobClient | None = None
        self._heartbeat_id: str | None = None
        self._last_order_error: dict[str, Any] | None = None

    @staticmethod
    def _is_retryable_status(status_code: int | None) -> bool:
        return status_code in (425, 429)

    @staticmethod
    def _is_post_only_cross_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "post-only" in msg and "crosses book" in msg

    @staticmethod
    def _iter_post_only_retry_prices(
        price: float,
        side: str,
        max_steps: int = 5,
    ):
        tick = 0.01
        base = round(price, 2)
        direction = -1 if side.upper() == "BUY" else 1
        for step in range(1, max_steps + 1):
            candidate = round(base + direction * tick * step, 2)
            if candidate < 0.01 or candidate > 0.99 or candidate == base:
                continue
            yield candidate

    def _set_last_order_error(
        self,
        *,
        code: str,
        side: str,
        token_id: str,
        price: float,
        size: float,
        details: str,
    ) -> None:
        self._last_order_error = {
            "code": code,
            "side": side,
            "token_id": token_id,
            "price": round(price, 4),
            "size": size,
            "details": details,
            "timestamp": time.time(),
        }

    def get_last_order_error(self) -> dict[str, Any] | None:
        if not self._last_order_error:
            return None
        return dict(self._last_order_error)

    def _call_with_backoff(
        self,
        operation: str,
        fn: Callable[[], Any],
        max_attempts: int = 5,
        initial_delay: float = 0.5,
    ) -> Any:
        """Retry order-path endpoints during transient CLOB unavailability (425/429)."""
        delay = initial_delay
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except PolyApiException as e:
                status_code = getattr(e, "status_code", None)
                if self._is_retryable_status(status_code) and attempt < max_attempts:
                    logger.warning(
                        f"{operation} got HTTP {status_code}, retrying in {delay:.1f}s "
                        f"({attempt}/{max_attempts})"
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue
                raise

    def connect(self):
        """Initialize and authenticate the CLOB client."""
        self._client = ClobClient(
            self.config.host,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            signature_type=self.config.signature_type,
            funder=self.config.funder_address or None,
        )
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)
        self._heartbeat_id = None  # Reset stale heartbeat on reconnect
        logger.info("Connected to Polymarket CLOB")

        # Ensure CTF ERC-1155 approval for both exchange contracts
        for exchange_addr in (
            self.config.ctf_exchange_address,
            self.config.neg_risk_ctf_exchange_address,
        ):
            if exchange_addr:
                self._ensure_ctf_approval(exchange_addr)

    @property
    def client(self) -> ClobClient:
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self._client

    # ═══════════════════════════════════════════════════════════════════
    # PRICE DATA
    # ═══════════════════════════════════════════════════════════════════

    def get_midpoint(self, token_id: str) -> float | None:
        try:
            result = self.client.get_midpoint(token_id)
            if isinstance(result, dict):
                return float(result.get("mid", 0))
            return float(result)
        except Exception as e:
            err_str = str(e)
            if "404" in err_str or "No orderbook" in err_str:
                logger.debug(f"No orderbook for token {token_id[:20]}...")
            else:
                logger.warning(f"Failed to get midpoint for {token_id[:20]}...: {e}")
            return None

    def get_price(self, token_id: str, side: str = "BUY") -> float | None:
        try:
            result = self.client.get_price(token_id, side=side)
            if isinstance(result, dict):
                return float(result.get("price", 0))
            return float(result)
        except Exception as e:
            logger.error(f"Failed to get price for {token_id}: {e}")
            return None

    def get_order_book(self, token_id: str) -> dict | None:
        try:
            return self._call_with_backoff(
                "get_order_book",
                lambda: self.client.get_order_book(token_id),
            )
        except Exception as e:
            logger.error(f"Failed to get order book for {token_id}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # ORDER MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = "BUY",
        order_type: str = "GTC",
        post_only: bool = False,
        known_balance: float | None = None,
    ) -> dict | None:
        """Place a limit order with pre-flight balance check for BUY orders.

        Args:
            known_balance: If provided, skip on-chain balance RPC call and use this value.
        """
        self._last_order_error = None
        side_up = side.upper()
        side_const = BUY if side_up == "BUY" else SELL

        def _submit(order_price: float) -> dict:
            order_args = OrderArgs(
                token_id=token_id,
                price=order_price,
                size=size,
                side=side_const,
            )
            signed = self.client.create_order(order_args)
            return self._call_with_backoff(
                "post_order",
                lambda: self.client.post_order(signed, order_type, post_only=post_only),
            )

        try:
            # Pre-flight: check balance before BUY to avoid "not enough balance" errors
            if side_up == "BUY":
                required = round(price * size, 2)
                balance = known_balance if known_balance is not None else self.get_onchain_balance()
                if balance is not None and balance < required:
                    logger.warning(
                        f"Insufficient balance for {side_up} order: "
                        f"need ${required:.2f} but only ${balance:.2f} on-chain"
                    )
                    self._set_last_order_error(
                        code="insufficient_balance",
                        side=side_up,
                        token_id=token_id,
                        price=price,
                        size=size,
                        details=f"required={required:.2f}, onchain={balance:.2f}",
                    )
                    return None

            # Pre-flight: check CTF token balance before SELL
            if side_up == "SELL":
                token_balance = self.get_token_balance(token_id)
                if token_balance is not None and token_balance < size:
                    logger.warning(
                        f"Insufficient token balance for SELL: "
                        f"need {size} but only {token_balance:.2f} of token {token_id[:20]}..."
                    )
                    self._set_last_order_error(
                        code="insufficient_token_balance",
                        side=side_up,
                        token_id=token_id,
                        price=price,
                        size=size,
                        details=f"token_balance={token_balance:.2f}, required={size}",
                    )
                    return None

            resp = _submit(price)
            logger.info(
                f"Order placed: {side_up} {size} @ {price} for {token_id[:20]}... "
                f"(type={order_type}, post_only={post_only})"
            )
            return resp
        except PolyApiException as e:
            if post_only and self._is_post_only_cross_error(e):
                attempts = 0
                for retry_price in self._iter_post_only_retry_prices(price, side_up):
                    attempts += 1
                    try:
                        resp = _submit(retry_price)
                        logger.info(
                            f"Order placed after post-only reprice({attempts}): "
                            f"{side_up} {size} @ {retry_price} for {token_id[:20]}... "
                            f"(type={order_type}, post_only={post_only})"
                        )
                        return resp
                    except PolyApiException as retry_e:
                        if self._is_post_only_cross_error(retry_e):
                            continue
                        self._set_last_order_error(
                            code="api_error",
                            side=side_up,
                            token_id=token_id,
                            price=retry_price,
                            size=size,
                            details=str(retry_e),
                        )
                        logger.error(
                            f"Post-only retry failed with non-cross error: side={side_up} "
                            f"token={token_id[:20]} price={retry_price:.2f} size={size} error={retry_e}"
                        )
                        return None
                    except Exception as retry_e:
                        self._set_last_order_error(
                            code="exception",
                            side=side_up,
                            token_id=token_id,
                            price=retry_price,
                            size=size,
                            details=str(retry_e),
                        )
                        logger.error(
                            f"Post-only retry failed: side={side_up} token={token_id[:20]} "
                            f"price={retry_price:.2f} size={size} error={retry_e}"
                        )
                        return None
                self._set_last_order_error(
                    code="post_only_cross",
                    side=side_up,
                    token_id=token_id,
                    price=price,
                    size=size,
                    details=f"exhausted retries max_steps=5",
                )
                logger.error(
                    f"Post-only retry exhausted: side={side_up} token={token_id[:20]} "
                    f"base_price={price:.2f} size={size} attempts={attempts + 1}"
                )
                return None
            self._set_last_order_error(
                code="api_error",
                side=side_up,
                token_id=token_id,
                price=price,
                size=size,
                details=str(e),
            )
            logger.error(
                f"Failed to place order: side={side_up} token={token_id[:20]} price={price:.2f} "
                f"size={size} post_only={post_only} error={e}"
            )
            return None
        except Exception as e:
            self._set_last_order_error(
                code="exception",
                side=side_up,
                token_id=token_id,
                price=price,
                size=size,
                details=str(e),
            )
            logger.error(
                f"Failed to place order: side={side_up} token={token_id[:20]} price={price:.2f} "
                f"size={size} post_only={post_only} error={e}"
            )
            return None

    def get_order(self, order_id: str) -> dict | None:
        """Get a single order by ID."""
        try:
            return self._call_with_backoff(
                "get_order",
                lambda: self.client.get_order(order_id),
            )
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            return None

    @staticmethod
    def _as_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _extract_order_execution_meta(self, order: dict) -> dict:
        """Normalize order payload fields from CLOB into execution metadata."""
        size_matched = 0.0
        for key in ("size_matched", "matched_size", "filled_size"):
            if key in order:
                size_matched = self._as_float(order.get(key), 0.0)
                break

        avg_fill_price = None
        for key in ("avg_fill_price", "avg_price", "average_price", "fill_price", "price"):
            if key in order:
                val = self._as_float(order.get(key), 0.0)
                if val > 0:
                    avg_fill_price = val
                    break

        notional_matched = None
        for key in ("matched_notional", "matched_amount", "filled_value", "size_matched_usdc", "notional"):
            if key in order:
                val = self._as_float(order.get(key), 0.0)
                if val > 0:
                    notional_matched = val
                    break
        if notional_matched is None and avg_fill_price and size_matched > 0:
            notional_matched = avg_fill_price * size_matched

        fees_paid = 0.0
        for key in ("fees_paid", "fees", "fee"):
            if key in order:
                fees_paid = self._as_float(order.get(key), 0.0)
                break

        return {
            "status": str(order.get("status", "UNKNOWN")).upper(),
            "size_matched": size_matched,
            "avg_fill_price": avg_fill_price,
            "notional_matched": notional_matched,
            "fees_paid": fees_paid,
            "raw": order,
        }

    def is_order_filled(self, order_id: str) -> tuple[bool, str, float, dict]:
        """Check if an order is filled.

        Returns (is_filled, status, size_matched, execution_meta).
        Status: LIVE, MATCHED, CANCELLED, EXPIRED.
        """
        try:
            order = self.get_order(order_id)
            if not order:
                return False, "UNKNOWN", 0.0, {}

            meta = self._extract_order_execution_meta(order)
            status = meta["status"]
            size_matched = meta["size_matched"]
            original_size = self._as_float(order.get("original_size", order.get("size", 0)), 0.0)

            is_filled = (
                status == "MATCHED"
                or (original_size > 0 and size_matched >= original_size)
            )
            return is_filled, status, size_matched, meta
        except Exception as e:
            logger.error(f"Failed to check order fill {order_id}: {e}")
            return False, "ERROR", 0.0, {}

    def get_open_orders(self) -> list:
        try:
            return self._call_with_backoff(
                "get_open_orders",
                lambda: self.client.get_orders(OpenOrderParams()),
            )
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._call_with_backoff(
                "cancel_order",
                lambda: self.client.cancel(order_id),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        try:
            self._call_with_backoff("cancel_all_orders", self.client.cancel_all)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False

    def post_heartbeat(self, heartbeat_id: str | None = None) -> bool:
        """Keep open orders alive on the matching engine."""
        candidate_id = heartbeat_id if heartbeat_id is not None else self._heartbeat_id

        try:
            response = self._call_with_backoff(
                "post_heartbeat",
                lambda: self.client.post_heartbeat(candidate_id),
            )
            if isinstance(response, dict):
                self._heartbeat_id = response.get("heartbeat_id", self._heartbeat_id)
            return True
        except PolyApiException as e:
            payload = getattr(e, "error_msg", None)
            error_msg = payload.get("error", "") if isinstance(payload, dict) else ""

            if "Invalid Heartbeat ID" in str(error_msg):
                # Stale heartbeat ID — try hinted ID from error, then None
                hinted = payload.get("heartbeat_id") if isinstance(payload, dict) else None
                self._heartbeat_id = None
                # Try hinted ID first if available
                if hinted:
                    try:
                        response = self._call_with_backoff(
                            "post_heartbeat",
                            lambda: self.client.post_heartbeat(hinted),
                        )
                        if isinstance(response, dict):
                            self._heartbeat_id = response.get("heartbeat_id", hinted)
                        else:
                            self._heartbeat_id = hinted
                        return True
                    except Exception:
                        pass
                # Fallback: request fresh heartbeat
                try:
                    response = self._call_with_backoff(
                        "post_heartbeat",
                        lambda: self.client.post_heartbeat(None),
                    )
                    if isinstance(response, dict):
                        self._heartbeat_id = response.get("heartbeat_id")
                    return True
                except Exception as retry_err:
                    logger.warning(f"Fresh heartbeat request failed: {retry_err}")
                    return False

            # For other errors, try hint from payload if different
            hinted_id = payload.get("heartbeat_id") if isinstance(payload, dict) else None
            if hinted_id and hinted_id != candidate_id:
                try:
                    response = self._call_with_backoff(
                        "post_heartbeat",
                        lambda: self.client.post_heartbeat(hinted_id),
                    )
                    if isinstance(response, dict):
                        self._heartbeat_id = response.get("heartbeat_id", hinted_id)
                    else:
                        self._heartbeat_id = hinted_id
                    return True
                except Exception as retry_err:
                    logger.warning(f"Heartbeat recovery failed: {retry_err}")
                    return False
            logger.warning(f"Heartbeat failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════════
    # SPLIT / MERGE OPERATIONS (CTF contract)
    # ═══════════════════════════════════════════════════════════════════

    def split_position(
        self,
        condition_id: str,
        amount: float,
    ) -> bool:
        """Split USDC.e into YES + NO tokens via CTF contract.

        Calls splitPosition(collateralToken, parentCollectionId, conditionId, partition, amount)
        on the CTF contract.

        Args:
            condition_id: The conditionId of the market.
            amount: Amount of USDC.e to split (will be converted to 6-decimal raw units).

        Returns True on success, False on failure.
        """
        try:
            from eth_abi import encode

            amount_raw = int(amount * 1e6)  # USDC.e has 6 decimals

            # First: approve CTF contract to spend our USDC.e (if not already approved)
            self._ensure_usdc_approval(self.config.ctf_address, amount_raw)

            # splitPosition(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] partition, uint256 amount)
            # Function selector: splitPosition(address,bytes32,bytes32,uint256[],uint256)
            # Selector: 0x72ce4275 (from Polymarket CTF ABI)
            parent_collection_id = b'\x00' * 32
            condition_bytes = bytes.fromhex(condition_id.replace("0x", ""))
            partition = [1, 2]  # YES=1, NO=2

            # Encode parameters
            params = encode(
                ['address', 'bytes32', 'bytes32', 'uint256[]', 'uint256'],
                [
                    self.config.usdc_e_address,
                    parent_collection_id,
                    condition_bytes,
                    partition,
                    amount_raw,
                ]
            )
            data = "0x72ce4275" + params.hex()

            tx_hash = self._send_transaction(self.config.ctf_address, data)
            if tx_hash:
                logger.info(
                    f"Split {amount:.2f} USDC → YES+NO tokens "
                    f"(condition={condition_id[:16]}..., tx={tx_hash[:16]}...)"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Split position failed: {e}")
            return False

    def merge_positions(
        self,
        condition_id: str,
        amount: float,
    ) -> bool:
        """Merge YES + NO tokens back into USDC.e via CTF contract.

        Args:
            condition_id: The conditionId of the market.
            amount: Number of token pairs to merge (raw token units, not USDC).

        Returns True on success, False on failure.
        """
        try:
            from eth_abi import encode

            # CTF tokens use same decimals as USDC.e (6)
            amount_raw = int(amount * 1e6)

            parent_collection_id = b'\x00' * 32
            condition_bytes = bytes.fromhex(condition_id.replace("0x", ""))
            partition = [1, 2]

            # mergePositions(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] partition, uint256 amount)
            # Selector: 0x5d03a660
            params = encode(
                ['address', 'bytes32', 'bytes32', 'uint256[]', 'uint256'],
                [
                    self.config.usdc_e_address,
                    parent_collection_id,
                    condition_bytes,
                    partition,
                    amount_raw,
                ]
            )
            data = "0x5d03a660" + params.hex()

            tx_hash = self._send_transaction(self.config.ctf_address, data)
            if tx_hash:
                logger.info(
                    f"Merged {amount:.2f} YES+NO → USDC "
                    f"(condition={condition_id[:16]}..., tx={tx_hash[:16]}...)"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Merge positions failed: {e}")
            return False

    def _ensure_usdc_approval(self, spender: str, amount_raw: int) -> bool:
        """Approve spender to use USDC.e if current allowance is insufficient."""
        try:
            from eth_account import Account as _Acct
            # Check current allowance for the EOA (signer), not the proxy wallet
            eoa_addr = _Acct.from_key(self.config.private_key).address
            owner = eoa_addr.lower().replace("0x", "")
            spender_clean = spender.lower().replace("0x", "")
            # allowance(address,address) selector = 0xdd62ed3e
            data = "0xdd62ed3e" + owner.zfill(64) + spender_clean.zfill(64)

            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": self.config.usdc_e_address, "data": data}, "latest"],
                "id": 1,
            }
            resp = requests.post(self.config.rpc_url, json=payload, timeout=10)
            result = resp.json().get("result", "0x0")
            current_allowance = int(result, 16)

            if current_allowance >= amount_raw:
                return True

            # Approve max uint256
            max_uint = 2**256 - 1
            from eth_abi import encode
            approve_params = encode(['address', 'uint256'], [spender, max_uint])
            approve_data = "0x095ea7b3" + approve_params.hex()

            tx_hash = self._send_transaction(self.config.usdc_e_address, approve_data)
            if tx_hash:
                logger.info(f"USDC.e approval granted to {spender[:16]}... (tx={tx_hash[:16]}...)")
                return True
            return False
        except Exception as e:
            logger.error(f"USDC approval failed: {e}")
            return False

    def _ensure_ctf_approval(self, exchange_address: str) -> bool:
        """Approve an exchange to transfer CTF ERC-1155 tokens on our behalf.

        Uses isApprovedForAll / setApprovalForAll on the CTF contract.
        Checks both proxy wallet and EOA (signer) for approval.
        """
        try:
            from eth_account import Account as _Acct
            eoa_addr = _Acct.from_key(self.config.private_key).address
            owner = eoa_addr.lower().replace("0x", "")
            operator = exchange_address.lower().replace("0x", "")
            # isApprovedForAll(address,address) selector = 0xe985e9c5
            data = "0xe985e9c5" + owner.zfill(64) + operator.zfill(64)

            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": self.config.ctf_address, "data": data}, "latest"],
                "id": 1,
            }
            resp = requests.post(self.config.rpc_url, json=payload, timeout=10)
            result = resp.json().get("result", "0x0")
            is_approved = int(result, 16) != 0

            if is_approved:
                logger.debug(f"CTF already approved for {exchange_address[:16]}...")
                return True

            # setApprovalForAll(address operator, bool approved) selector = 0xa22cb465
            from eth_abi import encode
            approve_params = encode(['address', 'bool'], [exchange_address, True])
            approve_data = "0xa22cb465" + approve_params.hex()

            tx_hash = self._send_transaction(self.config.ctf_address, approve_data)
            if tx_hash:
                logger.info(
                    f"CTF setApprovalForAll granted to {exchange_address[:16]}... "
                    f"(tx={tx_hash[:16]}...)"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"CTF approval failed for {exchange_address[:16]}...: {e}")
            return False

    def _send_transaction(self, to_address: str, data: str) -> str | None:
        """Send a signed transaction to Polygon via RPC.

        Uses the proxy wallet's private key for signing.
        Retries once on 'nonce too low' with corrected nonce.
        Returns tx hash on success, None on failure.
        """
        try:
            from eth_account import Account
            import re as _re

            # Use EOA address (derived from private key) for nonce, not proxy wallet
            sender = Account.from_key(self.config.private_key).address

            # Get nonce
            nonce_payload = {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionCount",
                "params": [sender, "pending"],
                "id": 1,
            }
            resp = requests.post(self.config.rpc_url, json=nonce_payload, timeout=10)
            nonce = int(resp.json().get("result", "0x0"), 16)

            # Get gas price
            gas_price_payload = {
                "jsonrpc": "2.0",
                "method": "eth_gasPrice",
                "params": [],
                "id": 1,
            }
            resp = requests.post(self.config.rpc_url, json=gas_price_payload, timeout=10)
            gas_price = int(resp.json().get("result", "0x0"), 16)
            # Add 20% buffer to gas price for faster inclusion
            gas_price = int(gas_price * 1.2)

            for attempt in range(2):
                # Build transaction
                tx = {
                    "to": to_address,
                    "data": data,
                    "gas": 300000,  # generous gas limit for CTF operations
                    "gasPrice": gas_price,
                    "nonce": nonce,
                    "chainId": self.config.chain_id,
                    "value": 0,
                }

                # Sign transaction
                signed = Account.sign_transaction(tx, self.config.private_key)

                # Send raw transaction
                send_payload = {
                    "jsonrpc": "2.0",
                    "method": "eth_sendRawTransaction",
                    "params": ["0x" + signed.raw_transaction.hex()],
                    "id": 1,
                }
                resp = requests.post(self.config.rpc_url, json=send_payload, timeout=15)
                result = resp.json()

                if "error" not in result:
                    break

                err_msg = str(result["error"].get("message", ""))
                # Retry with corrected nonce on 'nonce too low'
                if "nonce too low" in err_msg and attempt == 0:
                    match = _re.search(r"next nonce (\d+)", err_msg)
                    if match:
                        nonce = int(match.group(1))
                        logger.info(f"Nonce too low, retrying with nonce={nonce}")
                        continue
                logger.error(f"Transaction failed: {result['error']}")
                return None

            tx_hash = result.get("result")
            if tx_hash:
                # Wait for confirmation and check revert status
                receipt = self._wait_for_receipt(tx_hash, timeout=30)
                if not receipt:
                    return None
            return tx_hash
        except Exception as e:
            logger.error(f"Send transaction failed: {e}")
            return None

    def _wait_for_receipt(self, tx_hash: str, timeout: int = 30):
        """Poll for transaction receipt."""
        import time as _time
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "eth_getTransactionReceipt",
                    "params": [tx_hash],
                    "id": 1,
                }
                resp = requests.post(self.config.rpc_url, json=payload, timeout=10)
                receipt = resp.json().get("result")
                if receipt:
                    status = int(receipt.get("status", "0x0"), 16)
                    if status == 1:
                        return receipt
                    else:
                        logger.error(f"Transaction reverted: {tx_hash}")
                        return None
            except Exception:
                pass
            _time.sleep(2)
        logger.warning(f"Transaction receipt timeout: {tx_hash}")
        return None

    # ═══════════════════════════════════════════════════════════════════
    # ON-CHAIN BALANCE
    # ═══════════════════════════════════════════════════════════════════

    def get_token_balance(self, token_id: str) -> float | None:
        """Get CTF token balance (YES or NO) for the proxy wallet, with EOA fallback.

        Uses the ERC1155 balanceOf(address, uint256) on the CTF contract.
        Checks funder_address (proxy) first; if 0, also checks EOA (signer)
        since split creates tokens on the EOA.
        """
        if not self.config.funder_address:
            return None
        try:
            from eth_abi import encode
            token_id_int = int(token_id) if not token_id.startswith("0x") else int(token_id, 16)

            def _balance_of(addr: str) -> float:
                params = encode(['address', 'uint256'], [addr, token_id_int])
                data = "0x00fdd58e" + params.hex()
                payload = {
                    "jsonrpc": "2.0",
                    "method": "eth_call",
                    "params": [{"to": self.config.ctf_address, "data": data}, "latest"],
                    "id": 1,
                }
                resp = requests.post(self.config.rpc_url, json=payload, timeout=10)
                result = resp.json().get("result", "0x0")
                return int(result, 16) / 1e6

            # Check proxy wallet first
            balance = _balance_of(self.config.funder_address)
            if balance > 0:
                return balance

            # Fallback: check EOA (split creates tokens on signer address)
            try:
                from eth_account import Account as _Acct
                eoa_addr = _Acct.from_key(self.config.private_key).address
                if eoa_addr.lower() != self.config.funder_address.lower():
                    eoa_balance = _balance_of(eoa_addr)
                    if eoa_balance > 0:
                        return eoa_balance
            except Exception:
                pass

            return balance
        except Exception as e:
            logger.error(f"Failed to get token balance for {token_id[:20]}...: {e}")
            return None

    def get_onchain_balance(self) -> float | None:
        """Get real USDC.e balance of the proxy wallet on Polygon."""
        if not self.config.funder_address:
            logger.warning("No funder_address configured for balance check")
            return None

        try:
            # balanceOf(address) selector = 0x70a08231
            address = self.config.funder_address.lower().replace("0x", "")
            data = "0x70a08231" + address.zfill(64)

            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{
                    "to": self.config.usdc_e_address,
                    "data": data,
                }, "latest"],
                "id": 1,
            }

            resp = requests.post(self.config.rpc_url, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json().get("result", "0x0")
            balance_raw = int(result, 16)
            balance = balance_raw / 1e6  # USDC.e has 6 decimals
            return balance
        except Exception as e:
            logger.error(f"Failed to get on-chain balance: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # MARKET RESOLUTION
    # ═══════════════════════════════════════════════════════════════════

    def check_market_resolved(self, market_id: str) -> dict | None:
        """Check if a Polymarket market has been resolved via Gamma API."""
        try:
            resp = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=15)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()

            is_closed = data.get("closed", False)
            resolution_source = data.get("resolutionSource")

            if not is_closed:
                return {"resolved": False, "outcome": None, "resolution_source": None}

            # Determine winning outcome from outcomePrices (resolved = 1.0 for winner)
            outcomes = data.get("outcomes", [])
            if isinstance(outcomes, str):
                import json
                outcomes = json.loads(outcomes)
            outcome_prices = data.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)

            winning_outcome = None
            for outcome, price in zip(outcomes, outcome_prices):
                try:
                    if float(price) >= 0.99:
                        winning_outcome = outcome
                        break
                except (ValueError, TypeError):
                    continue

            return {
                "resolved": is_closed and winning_outcome is not None,
                "outcome": winning_outcome,
                "resolution_source": resolution_source,
            }
        except Exception as e:
            logger.error(f"Failed to check market resolution {market_id}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # MM-SPECIFIC ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════

    def get_spread(self, token_id: str) -> float | None:
        """Get the real cross-matched spread for a token using the /spread endpoint."""
        try:
            result = self._call_with_backoff(
                "get_spread",
                lambda: self.client.get_spread(token_id),
            )
            if isinstance(result, dict):
                return float(result.get("spread", 0))
            return float(result)
        except Exception as e:
            logger.error(f"Failed to get spread for {token_id}: {e}")
            return None

    def get_last_trade_price(self, token_id: str) -> float | None:
        """Get the last trade price for a token."""
        try:
            result = self._call_with_backoff(
                "get_last_trade_price",
                lambda: self.client.get_last_trade_price(token_id),
            )
            if isinstance(result, dict):
                return float(result.get("price", 0))
            return float(result)
        except Exception as e:
            logger.error(f"Failed to get last trade price for {token_id}: {e}")
            return None

    def get_midpoints_batch(self, token_ids: list[str]) -> dict[str, float | None]:
        """Get midpoints for multiple tokens. Returns {token_id: midpoint}."""
        results = {}
        for token_id in token_ids:
            results[token_id] = self.get_midpoint(token_id)
        return results

    def place_limit_orders_batch(self, orders: list[dict]) -> list[dict | None]:
        """Place multiple limit orders. Each order: {token_id, price, size, side}.
        Returns list of responses (None for failures)."""
        results = []
        for order in orders:
            resp = self.place_limit_order(
                token_id=order["token_id"],
                price=order["price"],
                size=order["size"],
                side=order.get("side", "BUY"),
            )
            results.append(resp)
        return results

    def cancel_orders_batch(self, order_ids: list[str]) -> list[bool]:
        """Cancel multiple orders. Returns list of success booleans."""
        results = []
        for order_id in order_ids:
            results.append(self.cancel_order(order_id))
        return results

    def get_book_summary(self, token_id: str) -> dict | None:
        """Get order book summary with best bid/ask and depth in USDC notional."""
        try:
            book = self.get_order_book(token_id)
            if not book:
                return None

            if isinstance(book, dict):
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                min_order_size_raw = book.get("min_order_size", 5)
            else:
                bids = getattr(book, "bids", []) or []
                asks = getattr(book, "asks", []) or []
                min_order_size_raw = getattr(book, "min_order_size", 5)

            def _level_value(level, key: str, default=0.0):
                if isinstance(level, dict):
                    return level.get(key, default)
                return getattr(level, key, default)

            best_bid = self._as_float(_level_value(bids[0], "price"), 0.0) if bids else 0.0
            best_ask = self._as_float(_level_value(asks[0], "price"), 1.0) if asks else 1.0

            def _notional(level: dict) -> float:
                price = self._as_float(_level_value(level, "price"), 0.0)
                size = self._as_float(_level_value(level, "size"), 0.0)
                return max(0.0, price * size)

            # Keep depth in USDC-equivalent notional for downstream filters.
            bid_depth = sum(_notional(b) for b in bids[:5])
            ask_depth = sum(_notional(a) for a in asks[:5])
            min_order_size = self._as_float(min_order_size_raw, 5.0)
            if min_order_size <= 0:
                min_order_size = 5.0

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid,
                "mid": (best_bid + best_ask) / 2 if best_bid and best_ask else None,
                "bid_depth_5": bid_depth,
                "ask_depth_5": ask_depth,
                "min_order_size": min_order_size,
                "imbalance": (bid_depth - ask_depth) / (bid_depth + ask_depth)
                             if (bid_depth + ask_depth) > 0 else 0,
            }
        except Exception as e:
            logger.error(f"Failed to get book summary for {token_id}: {e}")
            return None
