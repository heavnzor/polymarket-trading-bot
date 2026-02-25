"""
Swap USDC native → USDC.e on Polygon via Uniswap V3, then transfer to Polymarket proxy wallet.

Steps:
1. Approve Uniswap V3 Router to spend USDC native
2. Swap USDC native → USDC.e
3. Transfer USDC.e from EOA → Proxy wallet
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()

# --- Config ---
RPC_URLS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon.drpc.org",
]

USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
UNISWAP_V3_ROUTER = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
PROXY_WALLET = Web3.to_checksum_address(os.getenv("POLYMARKET_FUNDER_ADDRESS"))

# --- ABIs ---
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
     "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": False,
     "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "type": "function"},
    {"constant": True,
     "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "type": "function"},
    {"constant": False,
     "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}],
     "type": "function"},
]

SWAP_ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn", "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "fee", "type": "uint24"},
                {"name": "recipient", "type": "address"},
                {"name": "deadline", "type": "uint256"},
                {"name": "amountIn", "type": "uint256"},
                {"name": "amountOutMinimum", "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params",
            "type": "tuple",
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]


def connect():
    for url in RPC_URLS:
        w3 = Web3(Web3.HTTPProvider(url))
        if w3.is_connected():
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            print(f"Connected via {url}")
            return w3
    print("ERROR: Cannot connect to Polygon RPC")
    sys.exit(1)


def get_balance(w3, token_addr, wallet):
    contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
    raw = contract.functions.balanceOf(wallet).call()
    return raw  # raw units (6 decimals for USDC)


def send_tx(w3, account, tx):
    """Sign and send a transaction, wait for receipt."""
    tx["nonce"] = w3.eth.get_transaction_count(account.address)
    tx["from"] = account.address
    tx["chainId"] = 137

    # Estimate gas
    if "gas" not in tx:
        try:
            tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.3)
        except Exception as e:
            print(f"  Gas estimation failed: {e}")
            tx["gas"] = 300_000

    # EIP-1559 gas pricing
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", w3.to_wei(30, "gwei"))
    tx["maxPriorityFeePerGas"] = w3.to_wei(30, "gwei")
    tx["maxFeePerGas"] = base_fee * 2 + tx["maxPriorityFeePerGas"]

    # Remove legacy gasPrice if present
    tx.pop("gasPrice", None)

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"  Status: {'SUCCESS' if receipt['status'] == 1 else 'FAILED'} (gas used: {receipt['gasUsed']})")
    return receipt


def main():
    w3 = connect()
    account = w3.eth.account.from_key(PRIVATE_KEY)
    eoa = account.address
    print(f"EOA: {eoa}")
    print(f"Proxy: {PROXY_WALLET}\n")

    usdc_contract = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
    usdce_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)
    router = w3.eth.contract(address=UNISWAP_V3_ROUTER, abi=SWAP_ROUTER_ABI)

    # Check balances
    usdc_bal = get_balance(w3, USDC_NATIVE, eoa)
    usdce_bal = get_balance(w3, USDC_E, eoa)
    pol_bal = w3.eth.get_balance(eoa)

    print(f"USDC native: {usdc_bal / 1e6:.6f}")
    print(f"USDC.e:      {usdce_bal / 1e6:.6f}")
    print(f"POL:         {pol_bal / 1e18:.4f}\n")

    if pol_bal < Web3.to_wei(0.01, "ether"):
        print("ERROR: Not enough POL for gas fees!")
        sys.exit(1)

    # Amount to swap (leave 0.5 USDC as buffer, swap the rest)
    swap_amount = usdc_bal  # swap all native USDC
    if swap_amount <= 0:
        print("No USDC native to swap. Checking USDC.e balance...")
        if usdce_bal > 0:
            print(f"Already have {usdce_bal / 1e6:.6f} USDC.e, proceeding to transfer.")
            swap_amount = 0
        else:
            print("No USDC found on EOA. Nothing to do.")
            sys.exit(0)

    if swap_amount > 0:
        print(f"=== Step 1: Approve Uniswap V3 Router to spend {swap_amount / 1e6:.6f} USDC native ===")
        # Check current allowance
        current_allowance = usdc_contract.functions.allowance(eoa, UNISWAP_V3_ROUTER).call()
        if current_allowance < swap_amount:
            approve_tx = usdc_contract.functions.approve(
                UNISWAP_V3_ROUTER, swap_amount
            ).build_transaction({
                "from": eoa,
                "chainId": 137,
            })
            receipt = send_tx(w3, account, approve_tx)
            if receipt["status"] != 1:
                print("ERROR: Approve failed!")
                sys.exit(1)
            print()
        else:
            print("  Already approved.\n")

        print(f"=== Step 2: Swap {swap_amount / 1e6:.6f} USDC native → USDC.e ===")
        # Try fee tiers: 100 (0.01%), 500 (0.05%), 3000 (0.3%)
        for fee_tier in [100, 500, 3000]:
            try:
                deadline = int(time.time()) + 600  # 10 min
                # Minimum output: 99% of input (1% slippage max for stablecoins)
                min_out = int(swap_amount * 0.99)

                swap_params = (
                    USDC_NATIVE,      # tokenIn
                    USDC_E,           # tokenOut
                    fee_tier,         # fee
                    eoa,              # recipient
                    deadline,         # deadline
                    swap_amount,      # amountIn
                    min_out,          # amountOutMinimum
                    0,                # sqrtPriceLimitX96 (no limit)
                )

                swap_tx = router.functions.exactInputSingle(swap_params).build_transaction({
                    "from": eoa,
                    "chainId": 137,
                    "value": 0,
                })

                print(f"  Trying fee tier {fee_tier} ({fee_tier/10000:.2%})...")
                receipt = send_tx(w3, account, swap_tx)
                if receipt["status"] == 1:
                    print(f"  Swap successful with fee tier {fee_tier}!")
                    break
                else:
                    print(f"  Swap failed with fee tier {fee_tier}, trying next...")
            except Exception as e:
                print(f"  Fee tier {fee_tier} failed: {e}")
                continue
        else:
            print("ERROR: All fee tiers failed! Check pool liquidity.")
            sys.exit(1)

        print()

    # Refresh USDC.e balance
    usdce_bal = get_balance(w3, USDC_E, eoa)
    print(f"USDC.e balance after swap: {usdce_bal / 1e6:.6f}\n")

    if usdce_bal <= 0:
        print("ERROR: No USDC.e to transfer!")
        sys.exit(1)

    print(f"=== Step 3: Transfer {usdce_bal / 1e6:.6f} USDC.e → Proxy wallet ===")
    transfer_tx = usdce_contract.functions.transfer(
        PROXY_WALLET, usdce_bal
    ).build_transaction({
        "from": eoa,
        "chainId": 137,
    })
    receipt = send_tx(w3, account, transfer_tx)
    if receipt["status"] != 1:
        print("ERROR: Transfer failed!")
        sys.exit(1)

    print()
    # Final check
    proxy_usdce = get_balance(w3, USDC_E, PROXY_WALLET)
    eoa_usdce = get_balance(w3, USDC_E, eoa)
    print("=== Final Balances ===")
    print(f"EOA   USDC.e: {eoa_usdce / 1e6:.6f}")
    print(f"Proxy USDC.e: {proxy_usdce / 1e6:.6f}")
    print("\nDone! Le proxy wallet est maintenant financé.")


if __name__ == "__main__":
    main()
