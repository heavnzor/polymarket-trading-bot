"""Test trade: place a $1 limit order on a liquid market via Polymarket CLOB."""
import os
import sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "worker"))

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY
from data.markets import fetch_active_markets

def main():
    host = "https://clob.polymarket.com"
    key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS")
    chain_id = 137

    print("Connecting to CLOB...")
    client = ClobClient(
        host,
        key=key,
        chain_id=chain_id,
        signature_type=1,
        funder=funder or None,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    print("Connected!\n")

    # Check balance
    print("Checking balance...")
    try:
        bal = client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=1,
        ))
        print(f"CLOB Balance: {bal}\n")
    except Exception as e:
        print(f"Balance check error: {e}\n")

    # Update balance/allowance
    print("Updating balance/allowance in CLOB system...")
    try:
        upd = client.update_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=1,
        ))
        print(f"Update result: {upd}\n")
    except Exception as e:
        print(f"Update error: {e}\n")

    # Re-check balance
    try:
        bal = client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=1,
        ))
        print(f"CLOB Balance after update: {bal}\n")
    except Exception as e:
        print(f"Balance re-check error: {e}\n")

    # Find a liquid market
    print("Fetching active markets...")
    markets = fetch_active_markets(limit=10, min_volume=10000)
    if not markets:
        print("No active markets found!")
        return

    market = markets[0]
    print(f"Market: {market.question}")
    print(f"Token IDs: {market.token_ids}\n")

    if not market.token_ids:
        print("No token IDs!")
        return

    # Get price for the first outcome (YES)
    token_id = market.token_ids[0]
    print(f"Getting price for token: {token_id[:20]}...")
    try:
        price_data = client.get_price(token_id, side="BUY")
        print(f"Price data: {price_data}")
        if isinstance(price_data, dict):
            price = float(price_data.get("price", 0))
        else:
            price = float(price_data)
        print(f"Current BUY price: {price}\n")
    except Exception as e:
        print(f"Price error: {e}")
        return

    if price <= 0 or price >= 1:
        print(f"Invalid price: {price}")
        return

    # Place a $1 limit order - buy at slightly below market
    # For a $1 trade: size = amount / price
    trade_price = round(price - 0.01, 2)  # 1 cent below market
    if trade_price <= 0:
        trade_price = 0.01
    size = round(1.0 / trade_price, 1)  # ~$1 worth

    print(f"Placing limit order: BUY {size} shares @ {trade_price} (~${size * trade_price:.2f})")
    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=trade_price,
            size=size,
            side=BUY,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, "GTC")
        print(f"\nOrder response: {resp}")
        print("\nTRADE TEST SUCCESSFUL!")
    except Exception as e:
        print(f"\nOrder error: {e}")


if __name__ == "__main__":
    main()
