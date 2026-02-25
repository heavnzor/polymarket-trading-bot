"""Check USDC and USDC.e balances on EOA and proxy wallets."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# Polygon RPC
RPC_URLS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon.drpc.org",
]

w3 = None
for url in RPC_URLS:
    _w3 = Web3(Web3.HTTPProvider(url))
    if _w3.is_connected():
        w3 = _w3
        print(f"Connected via {url}")
        break
if not w3:
    print("ERROR: Cannot connect to any Polygon RPC")
    sys.exit(1)

# Addresses
PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
EOA = w3.eth.account.from_key(PRIVATE_KEY).address
PROXY = os.getenv("POLYMARKET_FUNDER_ADDRESS")

# Token contracts
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC (Polygon)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"       # USDC.e (Bridged, used by Polymarket)

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
     "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
]


def get_balance(token_addr: str, wallet: str) -> float:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    decimals = contract.functions.decimals().call()
    raw = contract.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
    return raw / (10 ** decimals)


def main():
    print(f"Connected to Polygon: {w3.is_connected()}")
    print(f"Block: {w3.eth.block_number}\n")

    print(f"EOA Wallet:   {EOA}")
    print(f"Proxy Wallet: {PROXY}\n")

    # POL (ex-MATIC) balance
    eoa_pol = w3.eth.get_balance(Web3.to_checksum_address(EOA)) / 1e18
    proxy_pol = w3.eth.get_balance(Web3.to_checksum_address(PROXY)) / 1e18

    # USDC Native
    eoa_usdc = get_balance(USDC_NATIVE, EOA)
    proxy_usdc = get_balance(USDC_NATIVE, PROXY)

    # USDC.e (Bridged)
    eoa_usdce = get_balance(USDC_E, EOA)
    proxy_usdce = get_balance(USDC_E, PROXY)

    print("=" * 55)
    print(f"{'Token':<15} {'EOA':>15} {'Proxy':>15}")
    print("=" * 55)
    print(f"{'POL':<15} {eoa_pol:>15.4f} {proxy_pol:>15.4f}")
    print(f"{'USDC (native)':<15} {eoa_usdc:>15.6f} {proxy_usdc:>15.6f}")
    print(f"{'USDC.e (bridg)':<15} {eoa_usdce:>15.6f} {proxy_usdce:>15.6f}")
    print("=" * 55)

    print()
    if eoa_usdc > 0 and eoa_usdce == 0:
        print("⚠ Tu as du USDC natif, mais Polymarket utilise USDC.e (bridged).")
        print("  → Il faut swapper USDC natif → USDC.e (via Uniswap/QuickSwap)")
        print("  → Puis transférer USDC.e vers le proxy wallet.")
    elif eoa_usdce > 0:
        print("✓ Tu as du USDC.e sur l'EOA.")
        print(f"  → Il faut transférer {eoa_usdce:.6f} USDC.e vers le proxy wallet.")
    elif proxy_usdce > 0:
        print("✓ USDC.e déjà sur le proxy wallet. Prêt à trader!")
    else:
        print("✗ Aucun USDC trouvé. Vérifie que les fonds sont bien arrivés.")


if __name__ == "__main__":
    main()
