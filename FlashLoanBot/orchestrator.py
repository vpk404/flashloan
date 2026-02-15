import os
import time
import json
import logging
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Load env
load_dotenv()

# Configuration
RPC_URL = os.getenv("ALCHEMY_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
ONEINCH_API_KEY = os.getenv("ONEINCH_API_KEY")
ONEINCH_API_URL = "https://api.1inch.dev/swap/v6.0/137/swap"
CHAIN_ID = int(os.getenv("CHAIN_ID", 137))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 15))
DRY_RUN = int(os.getenv("DRY_RUN", 1))
SLIPPAGE = float(os.getenv("SLIPPAGE", 0.005)) # 0.5%
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", 2.0))

# Token Addresses (Polygon)
TOKENS = {
    "USDC":   "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "USDT":   "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    "DAI":    "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    "WMATIC": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    "WETH":   "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
}

# Decimals
DECIMALS = {
    "USDC": 6,
    "USDT": 6,
    "DAI": 18,
    "WMATIC": 18,
    "WETH": 18,
}

# Pairs to scan: (TokenA, TokenB, LoanAmount)
PAIRS = [
    ("USDC", "USDT", 500),
    ("USDC", "DAI",  500),
    ("WMATIC", "USDC", 100),
    ("WETH", "USDC", 0.1),
]

# Web3 Setup
if not RPC_URL:
    logger.error("ALCHEMY_RPC_URL not set")
    exit(1)

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    logger.error("Failed to connect to RPC")
    exit(1)

account = Account.from_key(PRIVATE_KEY) if PRIVATE_KEY else None
if account:
    logger.info(f"Connected to Polygon. Wallet: {account.address}")
else:
    logger.warning("PRIVATE_KEY not set. Read-only mode.")

def load_contract():
    path = "artifacts/contracts/FlashLoanArbitrage.sol/FlashLoanArbitrage.json"
    if not os.path.exists(path):
        logger.error(f"Artifact not found at {path}. Compile contracts first.")
        return None
    with open(path) as f:
        data = json.load(f)
    return w3.eth.contract(address=CONTRACT_ADDRESS, abi=data["abi"])

contract = load_contract() if CONTRACT_ADDRESS else None

def get_token_price(symbol):
    # Simplified price fetcher
    # Use CoinGecko for real implementation, fallback to env or 1 for stables
    if symbol in ["USDC", "USDT", "DAI"]:
        return 1.0

    # Check env override
    if symbol == "WMATIC" and os.getenv("MATIC_USD"):
        return float(os.getenv("MATIC_USD"))

    # Fallback to simple hardcoded values for simulation if needed
    # Or fetch from CoinGecko public API
    try:
        cg_id = {
            "WMATIC": "matic-network",
            "WETH": "ethereum",
        }.get(symbol)

        if cg_id:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return resp.json()[cg_id]["usd"]
    except Exception as e:
        logger.warning(f"Failed to fetch price for {symbol}: {e}")

    # Last resort fallback
    if symbol == "WMATIC": return 0.50
    if symbol == "WETH": return 3000.0
    return 0.0

def get_1inch_swap_data(src_token, dst_token, amount, from_address):
    if not ONEINCH_API_KEY:
        logger.error("ONEINCH_API_KEY not set")
        return None

    headers = {"Authorization": f"Bearer {ONEINCH_API_KEY}"}
    params = {
        "src": src_token,
        "dst": dst_token,
        "amount": str(int(amount)),
        "from": from_address,
        "slippage": SLIPPAGE * 100, # 1inch expects percentage (e.g. 1 for 1%)? Or maybe 0.5?
        # Prompt says: "amountOutMin = expected_output * (1 - slippage)" locally.
        # But 1inch API takes slippage param too.
        # Docs says 'slippage' is min 0, max 50.
        "disableEstimate": "true", # We estimate locally via eth_call
        "includeTokensInfo": "true"
    }

    try:
        resp = requests.get(ONEINCH_API_URL, headers=headers, params=params)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            logger.warning("1inch Rate Limit")
            time.sleep(1)
        else:
            logger.error(f"1inch API Error: {resp.text}")
    except Exception as e:
        logger.error(f"1inch Request Failed: {e}")
    return None

def scan_pair(token_a_sym, token_b_sym, loan_amount_units):
    token_a = TOKENS[token_a_sym]
    token_b = TOKENS[token_b_sym]
    decimals_a = DECIMALS[token_a_sym]

    loan_amount_wei = int(loan_amount_units * (10**decimals_a))

    # 1. Get Swap A -> B
    swap_a_b = get_1inch_swap_data(token_a, token_b, loan_amount_wei, CONTRACT_ADDRESS)
    if not swap_a_b: return

    dst_amount_b = int(swap_a_b.get("dstAmount", "0"))
    if dst_amount_b == 0: return

    # 2. Get Swap B -> A
    swap_b_a = get_1inch_swap_data(token_b, token_a, dst_amount_b, CONTRACT_ADDRESS)
    if not swap_b_a: return

    dst_amount_a = int(swap_b_a.get("dstAmount", "0"))

    # 3. Calculate Stats
    balance_after = dst_amount_a
    flash_loan_fee = int(loan_amount_wei * 0.0009) # 0.09%
    amount_owed = loan_amount_wei + flash_loan_fee

    net_profit_wei = balance_after - amount_owed

    price_a = get_token_price(token_a_sym)
    gross_profit_usd = (balance_after - loan_amount_wei) / (10**decimals_a) * price_a
    net_profit_usd = net_profit_wei / (10**decimals_a) * price_a

    # Gas cost estimation
    gas_price = w3.eth.gas_price
    gas_limit = 600000
    gas_cost_wei = gas_limit * gas_price
    price_matic = get_token_price("WMATIC")
    gas_cost_usd = (gas_cost_wei / 1e18) * price_matic

    final_profit_usd = net_profit_usd - gas_cost_usd

    logger.info(f"[{token_a_sym}→{token_b_sym}→{token_a_sym}] loan={loan_amount_units} | gross=${gross_profit_usd:.2f} | aave_fee=${(flash_loan_fee/(10**decimals_a)*price_a):.2f} | gas=${gas_cost_usd:.2f} | NET=${final_profit_usd:.2f}")

    if final_profit_usd < MIN_PROFIT_USD:
        logger.info(f"  [SKIP] Net profit < MIN ${MIN_PROFIT_USD}")
        return

    # 4. Execute
    logger.info("  [PROFITABLE] Executing...")

    # Construct params for contract
    # decode params in solidity: targetA, dataA, intermediateAsset, targetB, dataB, amountOutMin

    target_a = Web3.to_checksum_address(swap_a_b["tx"]["to"])
    data_a = bytes.fromhex(swap_a_b["tx"]["data"][2:])
    intermediate_asset = Web3.to_checksum_address(token_b)
    target_b = Web3.to_checksum_address(swap_b_a["tx"]["to"])
    data_b = bytes.fromhex(swap_b_a["tx"]["data"][2:])

    # amountOutMin: Slippage protection for the WHOLE flow?
    # Contract checks: balanceAfter >= amountOutMin
    # If we want to ensure we at least break even + gas, or just amountOwed?
    # Prompt says: amountOutMin = expected_output * (1 - slippage)
    amount_out_min = int(dst_amount_a * (1 - SLIPPAGE))

    # Encode params
    params_encoded = w3.eth.abi.encode(
        ['address', 'bytes', 'address', 'address', 'bytes', 'uint256'],
        [target_a, data_a, intermediate_asset, target_b, data_b, amount_out_min]
    )

    # Simulate via eth_call
    # Using requestFlashLoan(address _token, uint256 _amount, bytes calldata _params)
    try:
        # Build transaction object
        tx_data = contract.functions.requestFlashLoan(
            Web3.to_checksum_address(token_a),
            loan_amount_wei,
            params_encoded
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': gas_limit,
            'maxFeePerGas': w3.eth.max_priority_fee + (2 * w3.eth.get_block("latest")["baseFeePerGas"]),
            'maxPriorityFeePerGas': w3.eth.max_priority_fee,
            'chainId': CHAIN_ID
        })

        # Simulate
        w3.eth.call(tx_data)
        logger.info("  [SIMULATION] Success!")

        if DRY_RUN == 0:
            signed_tx = w3.eth.account.sign_transaction(tx_data, private_key=PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            logger.info(f"  [SENT] Tx Hash: {w3.to_hex(tx_hash)}")
        else:
            logger.info("  [DRY RUN] Set DRY_RUN=0 to send for real")

    except Exception as e:
        logger.error(f"  [SIMULATION FAILED] {e}")

def main():
    if not CONTRACT_ADDRESS:
        logger.warning("CONTRACT_ADDRESS not set. Set it in .env after deployment.")

    while True:
        logger.info(f"[Scan] MATIC price: ${get_token_price('WMATIC'):.2f}")

        if CONTRACT_ADDRESS and contract:
            for p in PAIRS:
                scan_pair(p[0], p[1], p[2])

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
