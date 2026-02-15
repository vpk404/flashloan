import os
import time
import json
import logging
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
import requests

# Setup logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Load env
load_dotenv()

# Configuration
RPC_URL = os.getenv("ALCHEMY_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LIQUIDATION_CONTRACT = os.getenv("LIQUIDATION_CONTRACT")
AAVE_POOL = os.getenv("AAVE_POOL", "0x794a61358D6845594F94dc1DB02A252b5b4814aD")
CHAIN_ID = int(os.getenv("CHAIN_ID", 137))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 30))
DRY_RUN = int(os.getenv("DRY_RUN", 1))
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", 2.0))

# Aave V3 Borrow Event
BORROW_TOPIC = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"

# Token Addresses (Polygon) & Decimals
TOKENS = {
    "USDC":   "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "USDT":   "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    "DAI":    "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    "WMATIC": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    "WETH":   "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "WBTC":   "0x1BFD67037B42CfDJ6271c0A97e9006F1051B3C56" # Added WBTC as per prompt
}
DECIMALS = {
    "USDC": 6, "USDT": 6, "DAI": 18, "WMATIC": 18, "WETH": 18, "WBTC": 8
}
REVERSE_TOKENS = {v.lower(): k for k, v in TOKENS.items()}

# Collateral Pairs to try for liquidation
COLLATERAL_PAIRS = {
    "USDC":   ["WETH", "WMATIC", "WBTC"],
    "USDT":   ["WETH", "WMATIC", "WBTC"],
    "WETH":   ["USDC", "USDT"],
    "WMATIC": ["USDC", "USDT"],
    "DAI":    ["USDC", "USDT", "WETH", "WMATIC"], # inferred
}

# Web3 Setup
if not RPC_URL:
    logger.error("ALCHEMY_RPC_URL not set")
    exit(1)

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    logger.error("Failed to connect to RPC")
    exit(1)

account = Account.from_key(PRIVATE_KEY) if PRIVATE_KEY else None

# Minimal ABI for Aave Pool
POOL_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
            {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
            {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

pool_contract = w3.eth.contract(address=AAVE_POOL, abi=POOL_ABI)

# Minimal ABI for Liquidation Contract
LIQ_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "_borrower", "type": "address"},
            {"internalType": "address", "name": "_debtAsset", "type": "address"},
            {"internalType": "address", "name": "_collateralAsset", "type": "address"},
            {"internalType": "uint256", "name": "_debtAmount", "type": "uint256"},
            {"internalType": "uint24", "name": "_poolFee", "type": "uint24"},
            {"internalType": "uint256", "name": "_amountOutMin", "type": "uint256"}
        ],
        "name": "requestLiquidation",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]
liq_contract = w3.eth.contract(address=LIQUIDATION_CONTRACT, abi=LIQ_ABI) if LIQUIDATION_CONTRACT else None

def get_token_price(symbol):
    # Same as orchestrator.py, simplified
    if symbol in ["USDC", "USDT", "DAI"]: return 1.0
    if symbol == "WMATIC": return 0.50 # Placeholder
    if symbol == "WETH": return 3000.0 # Placeholder
    if symbol == "WBTC": return 40000.0 # Placeholder
    return 0.0

def scan_borrowers():
    checked_borrowers = set()
    scan_count = 0

    while True:
        scan_count += 1
        logger.info(f"[Scan #{scan_count}] Fetching borrowers on-chain...")

        try:
            latest_block = w3.eth.block_number
            from_block = max(0, latest_block - 2000)
            logger.info(f"  Scanning blocks {from_block} to {latest_block}...")

            logs = w3.eth.get_logs({
                "address": AAVE_POOL,
                "topics": [BORROW_TOPIC],
                "fromBlock": from_block
            })

            logger.info(f"  Found {len(logs)} borrow events")

            borrowers_to_check = set()
            for log in logs:
                # Decode log
                # topic[1] = reserve (address)
                # topic[2] = onBehalfOf (address)
                if len(log["topics"]) < 3: continue

                reserve = w3.to_checksum_address("0x" + log["topics"][1].hex()[-40:])
                on_behalf_of = w3.to_checksum_address("0x" + log["topics"][2].hex()[-40:])

                # We also need the amount to check minimal size
                # data is amount (uint256) + interestRateMode + referralCode
                # amount is first 32 bytes
                # Use bytes directly to avoid 0x prefix issues with .hex()
                amount_bytes = log["data"][:32]
                amount = int.from_bytes(amount_bytes, byteorder="big")

                # Check value > $50
                # Resolve symbol
                symbol = REVERSE_TOKENS.get(reserve.lower())
                if not symbol: continue

                decimals = DECIMALS.get(symbol, 18)
                price = get_token_price(symbol)
                value_usd = (amount / (10**decimals)) * price

                if value_usd < 50: continue

                borrowers_to_check.add((on_behalf_of, reserve, amount)) # Store tuple

            logger.info(f"[Scan #{scan_count}] {len(borrowers_to_check)} positions to check")

            # Check Health Factors
            for borrower, debt_asset, debt_amount in borrowers_to_check:
                if borrower in checked_borrowers: continue # Simple cache

                try:
                    data = pool_contract.functions.getUserAccountData(borrower).call()
                    health_factor = data[5] / 1e18

                    if health_factor < 1.0:
                        logger.info(f"🚨 LIQUIDATABLE FOUND!")
                        logger.info(f"  Address:       {borrower}")
                        logger.info(f"  Health Factor: {health_factor:.4f}")

                        debt_symbol = REVERSE_TOKENS.get(debt_asset.lower(), "UNKNOWN")
                        price_debt = get_token_price(debt_symbol)
                        debt_decimals = DECIMALS.get(debt_symbol, 18)
                        debt_usd = (debt_amount / (10**debt_decimals)) * price_debt

                        logger.info(f"  Debt:          ${debt_usd:.2f} in {debt_symbol}")

                        # Try collaterals
                        collaterals = COLLATERAL_PAIRS.get(debt_symbol, [])
                        best_profit = -9999
                        best_collateral = None

                        for coll_symbol in collaterals:
                            coll_asset = TOKENS[coll_symbol]
                            # Calculate profit
                            # profit = (debt_usd * 0.05) - (debt_usd * 0.0009) - (debt_usd * 0.003) - 0.50
                            profit = (debt_usd * 0.05) - (debt_usd * 0.0009) - (debt_usd * 0.003) - 0.50

                            logger.info(f"  Collateral try: {coll_symbol} → profit=${profit:.2f}")

                            if profit > best_profit:
                                best_profit = profit
                                best_collateral = coll_asset

                        if best_profit > MIN_PROFIT_USD and best_collateral:
                            if not LIQUIDATION_CONTRACT:
                                logger.info("  [SCAN ONLY] Set LIQUIDATION_CONTRACT to execute")
                            else:
                                execute_liquidation(borrower, debt_asset, best_collateral, debt_amount, best_profit)

                    checked_borrowers.add(borrower)

                except Exception as e:
                    logger.error(f"Error checking {borrower}: {e}")

            if scan_count % 10 == 0:
                checked_borrowers.clear()

        except Exception as e:
            logger.error(f"Scan failed: {e}")

        logger.info(f"[Scan #{scan_count}] Waiting {SCAN_INTERVAL}s...")
        time.sleep(SCAN_INTERVAL)

def execute_liquidation(borrower, debt_asset, collateral_asset, debt_amount, profit):
    if not account:
        logger.warning("No private key, cannot execute.")
        return

    logger.info(f"  [EXECUTING] Expected Profit: ${profit:.2f}")

    # 500 = 0.05% fee tier for QuickSwap V3 usually? Or 3000 (0.3%)?
    # Prompt says "QuickSwap V3". Fees vary. 500, 3000, 10000.
    # Usually stable-stable is 500 or 100.
    # We should probably pick based on pair.
    # For now hardcode 3000 (0.3%) as conservative estimate?
    # Prompt profit formula used 0.003 (0.3%), so I'll use 3000.
    pool_fee = 3000

    # amountOutMin calculation
    # We want at least debt_amount + premium back.
    # Plus some profit?
    # Let's set amountOutMin = debt_amount + premium
    premium = int(debt_amount * 0.0009)
    amount_out_min = debt_amount + premium

    try:
        tx = liq_contract.functions.requestLiquidation(
            borrower,
            debt_asset,
            collateral_asset,
            debt_amount,
            pool_fee,
            amount_out_min
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 1000000, # Manual gas limit or estimate
            'maxFeePerGas': w3.eth.max_priority_fee + (2 * w3.eth.get_block("latest")["baseFeePerGas"]),
            'maxPriorityFeePerGas': w3.eth.max_priority_fee,
            'chainId': CHAIN_ID
        })

        # Simulate
        w3.eth.call(tx)
        logger.info("  [SIMULATION] Success!")

        if DRY_RUN == 0:
            signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"  [SENT] Tx Hash: {w3.to_hex(tx_hash)}")
        else:
            logger.info("  [DRY RUN] Set DRY_RUN=0 to send for real")

    except Exception as e:
        logger.error(f"  [EXECUTION FAILED] {e}")

if __name__ == "__main__":
    scan_borrowers()
