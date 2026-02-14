import time
import os
import json
from web3 import Web3
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CONTRACT_ADDRESS = "0x0000000000000000000000000000000000000000" # UPDATE THIS AFTER DEPLOY

# Token Addresses (Polygon)
USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
WETH = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"

# Router Addresses
QUICKSWAP_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
SUSHISWAP_ROUTER = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506"

# Connect to Polygon
w3 = Web3(Web3.HTTPProvider(RPC_URL))

if not PRIVATE_KEY:
    print("Error: PRIVATE_KEY not found in .env file.")
    exit(1)

account = w3.eth.account.from_key(PRIVATE_KEY)

# ABIs (Simplified for Interaction)
PAIR_ABI = '[{"constant":true,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"payable":false,"stateMutability":"view","type":"function"}]'
ROUTER_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
FLASHLOAN_ABI = '[{"inputs":[{"internalType":"address","name":"_token","type":"address"},{"internalType":"uint256","name":"_amount","type":"uint256"},{"internalType":"address","name":"_routerA","type":"address"},{"internalType":"address","name":"_routerB","type":"address"},{"internalType":"address","name":"_tokenB","type":"address"}],"name":"requestFlashLoan","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

# Setup Contracts
flash_loan = w3.eth.contract(address=CONTRACT_ADDRESS, abi=FLASHLOAN_ABI)
quick_router = w3.eth.contract(address=QUICKSWAP_ROUTER, abi=ROUTER_ABI)
sushi_router = w3.eth.contract(address=SUSHISWAP_ROUTER, abi=ROUTER_ABI)

def get_amount_out(router, amount_in, path):
    try:
        amounts = router.functions.getAmountsOut(amount_in, path).call()
        return amounts[-1]
    except Exception as e:
        # print(f"Error fetching price: {e}")
        return 0

def execute_trade(amount, routerA, routerB):
    try:
        print(f"[*] Constructing Flash Loan Transaction...")
        
        # Build Transaction
        nonce = w3.eth.get_transaction_count(account.address)
        
        # Estimate Gas (Optional but recommended)
        # gas_estimate = flash_loan.functions.requestFlashLoan(...).estimate_gas({...})
        
        tx = flash_loan.functions.requestFlashLoan(
            USDC,
            amount,
            routerA,
            routerB,
            WETH
        ).build_transaction({
            'chainId': 137, # Polygon Mainnet
            'gas': 500000,  # High limit to be safe
            'gasPrice': w3.eth.gas_price,
            'nonce': nonce
        })
        
        # Sign & Send
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"[*] Trade Sent! TX Hash: {w3.to_hex(tx_hash)}")
        print("Waiting for confirmation...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        
        if receipt.status == 1:
            print("[+] Trade SUCCESS! Profit secured.")
        else:
            print("[-] Trade FAILED (Reverted). Cost: Gas fee only.")
            
    except Exception as e:
        print(f"Trade Execution Error: {e}")

def run_bot():
    print(f"[*] Starting Arbitrage Bot on account {account.address}")
    print(f"[*] Monitoring Spread between QuickSwap & SushiSwap (USDC/WETH)...")
    
    amount_in = 100 * (10**6) # Start with 100 USDC loan (6 decimals)
    path = [USDC, WETH]
    
    while True:
        try:
            # 1. Check Price on QuickSwap (Buy WETH with USDC)
            amount_weth_quick = get_amount_out(quick_router, amount_in, path)
            
            # 2. Check Price on SushiSwap (Sell WETH for USDC)
            amount_usdc_sushi = get_amount_out(sushi_router, amount_weth_quick, [WETH, USDC])
            
            # Calculate Profit (Swap 1: Q->S)
            profit_qs = amount_usdc_sushi - amount_in
            
            # Calculate Reverse (Swap 2: S->Q)
            amount_weth_sushi = get_amount_out(sushi_router, amount_in, path)
            amount_usdc_quick = get_amount_out(quick_router, amount_weth_sushi, [WETH, USDC])
            profit_sq = amount_usdc_quick - amount_in

            print(f"Spread Q->S: {profit_qs/1e6:.4f} USDC | S->Q: {profit_sq/1e6:.4f} USDC")

            # Threshold: > 0.50 USDC profit (to cover gas + fee)
            THRESHOLD = 0.5 * (10**6) 

            if profit_qs > THRESHOLD:
                print(f"[!!!] PROFITABLE OPPORTUNITY FOUND: Q->S (+{profit_qs/1e6} USDC)")
                execute_trade(amount_in, QUICKSWAP_ROUTER, SUSHISWAP_ROUTER)
                time.sleep(10) # Cooldown
            
            elif profit_sq > THRESHOLD:
                print(f"[!!!] PROFITABLE OPPORTUNITY FOUND: S->Q (+{profit_sq/1e6} USDC)")
                execute_trade(amount_in, SUSHISWAP_ROUTER, QUICKSWAP_ROUTER)
                time.sleep(10) # Cooldown

            time.sleep(3)

        except Exception as e:
            print(f"Error loop: {e}")
            time.sleep(3)

if __name__ == "__main__":
    run_bot()
