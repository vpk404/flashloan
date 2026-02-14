#!/usr/bin/env python3
"""
Arbitrage Bot with ONLY Spot Price API + Alchemy
No 1inch Swap API needed - builds transactions manually
"""

import os
import asyncio
import json
from decimal import Decimal
from web3 import Web3
from eth_account import Account
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ============ CONFIGURATION ============
ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY")
ONEINCH_KEY = os.getenv("ONEINCH_API_KEY")  # Spot Price only
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Polygon connection
POLYGON_HTTP = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
w3 = Web3(Web3.HTTPProvider(POLYGON_HTTP))

# Your deployed contract address (after deployment)
CONTRACT_ADDRESS = "0x..."  # <-- PASTE HERE

# Account
account = Account.from_key(PRIVATE_KEY)

# Token Addresses (Polygon)
TOKENS = {
    'USDC': '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
    'WETH': '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619',
    'WMATIC': '0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270',
    'USDT': '0xc2132D05D31c914a87C6611C10748AEb04B58e8F'
}

# DEX Routers
QUICKSWAP_ROUTER = '0xf5b509bB0909a69B1c207E495f687a596C168E12'
SUSHISWAP_ROUTER = '0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506'

# Minimal Router ABI (just what we need)
ROUTER_ABI = [
    # Get amounts out (for price checking)
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function"
    },
    # Swap exact tokens for tokens (the actual swap)
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# Initialize routers
qs_router = w3.eth.contract(address=QUICKSWAP_ROUTER, abi=ROUTER_ABI)
ss_router = w3.eth.contract(address=SUSHISWAP_ROUTER, abi=ROUTER_ABI)

# Budget tracking
BUDGET_USD = Decimal('30')
gas_spent = Decimal('0')


# ============ PRICE DISCOVERY ============

async def get_spot_prices_1inch(session, tokens):
    """Use your Spot Price API to get USD prices"""
    url = "https://api.1inch.dev/price/v1.1/137"
    headers = {"Authorization": f"Bearer {ONEINCH_KEY}"}
    
    payload = {
        "tokens": tokens,
        "currency": "USD"
    }
    
    try:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                print(f"Spot Price Error: {resp.status}")
                return None
    except Exception as e:
        print(f"API Error: {e}")
        return None


async def get_dex_prices_direct(amount_in, token_in, token_out):
    """Get prices directly from DEX routers (FREE, no API needed)"""
    path = [token_in, token_out]
    
    try:
        # QuickSwap
        qs_amounts = qs_router.functions.getAmountsOut(amount_in, path).call()
        qs_out = Decimal(qs_amounts[1])
        
        # SushiSwap  
        ss_amounts = ss_router.functions.getAmountsOut(amount_in, path).call()
        ss_out = Decimal(ss_amounts[1])
        
        return {
            'quickswap': qs_out,
            'sushiswap': ss_out
        }
    except Exception as e:
        print(f"DEX price error: {e}")
        return None


# ============ SWAP DATA BUILDER ============

def build_swap_calldata(router_address, amount_in, amount_out_min, path, to_address):
    """
    Build swap transaction data manually (no 1inch Swap API needed)
    This creates the 'data' field for the transaction
    """
    router = w3.eth.contract(address=router_address, abi=ROUTER_ABI)
    
    # Build swapExactTokensForTokens call
    deadline = w3.eth.get_block('latest')['timestamp'] + 300  # 5 min deadline
    
    swap_data = router.encodeABI(
        fn_name='swapExactTokensForTokens',
        args=[
            amount_in,           # amountIn
            amount_out_min,      # amountOutMin (slippage protected)
            path,                # [tokenIn, tokenOut]
            to_address,          # recipient (your contract)
            deadline             # deadline
        ]
    )
    
    return swap_data


# ============ ARBITRAGE LOGIC ============

async def find_arbitrage_opportunity(session):
    """Find profitable arbitrage opportunities"""
    global gas_spent
    
    if gas_spent >= BUDGET_USD:
        print("💸 BUDGET EXHAUSTED!")
        return None
    
    # Amount to check (1000 USDC, 6 decimals)
    amount_in = 1000 * (10 ** 6)
    
    print(f"\n🔍 Scanning... (Budget: ${BUDGET_USD - gas_spent:.2f} left)")
    
    # Get prices from both DEXs (FREE direct calls)
    prices = await get_dex_prices_direct(amount_in, TOKENS['USDC'], TOKENS['WETH'])
    
    if not prices:
        return None
    
    qs_price = prices['quickswap']
    ss_price = prices['sushiswap']
    
    print(f"QuickSwap: 1000 USDC → {qs_price / 10**18:.6f} WETH")
    print(f"SushiSwap: 1000 USDC → {ss_price / 10**18:.6f} WETH")
    
    # Determine direction
    if qs_price > ss_price:
        # Buy on SushiSwap (cheaper), sell on QuickSwap (expensive)
        buy_dex = SUSHISWAP_ROUTER
        sell_dex = QUICKSWAP_ROUTER
        buy_price = ss_price
        sell_price = qs_price
        direction = "SushiSwap → QuickSwap"
    elif ss_price > qs_price:
        # Buy on QuickSwap, sell on SushiSwap
        buy_dex = QUICKSWAP_ROUTER
        sell_dex = SUSHISWAP_ROUTER
        buy_price = qs_price
        sell_price = ss_price
        direction = "QuickSwap → SushiSwap"
    else:
        print("No spread")
        return None
    
    # Calculate spread
    spread = (sell_price - buy_price) / buy_price
    spread_pct = spread * 100
    
    print(f"Direction: {direction}")
    print(f"Spread: {spread_pct:.3f}%")
    
    # Check if profitable (need >1% for safety with $30 budget)
    if spread > Decimal('0.01'):
        # Calculate profit for $2000 flash loan
        loan_amount = 2000 * (10 ** 6)  # 2000 USDC
        expected_weth_profit = (loan_amount * sell_price / amount_in) - (loan_amount * buy_price / amount_in)
        
        # Convert WETH profit to USD (approx $1800/WETH)
        profit_usd = (expected_weth_profit / 10**18) * Decimal('1800')
        gas_cost = Decimal('0.15')  # ~$0.15 on Polygon
        
        print(f"💰 Expected profit: ${profit_usd:.2f}")
        print(f"⛽ Gas cost: ~${gas_cost:.2f}")
        
        # Safety: profit must cover gas * 2.5 + $5 minimum
        if profit_usd > (5 + gas_cost * Decimal('2.5')):
            print("🎯 PROFITABLE! Preparing execution...")
            
            # Build swap calldata (no 1inch needed!)
            path_buy = [TOKENS['USDC'], TOKENS['WETH']]
            path_sell = [TOKENS['WETH'], TOKENS['USDC']]
            
            # Buy swap: USDC → WETH (min amount with 0.5% slippage)
            amount_out_min_buy = int(buy_price * Decimal('0.995'))
            swap_data_buy = build_swap_calldata(
                buy_dex, 
                loan_amount, 
                amount_out_min_buy,
                path_buy,
                CONTRACT_ADDRESS
            )
            
            # Sell swap: WETH → USDC (min amount with 0.5% slippage)
            weth_received = int(loan_amount * buy_price / (10**6))
            amount_out_min_sell = int(loan_amount * Decimal('1.005'))  # Should get back ~2000+ USDC
            swap_data_sell = build_swap_calldata(
                sell_dex,
                weth_received,
                amount_out_min_sell,
                path_sell,
                CONTRACT_ADDRESS
            )
            
            return {
                'loan_token': TOKENS['USDC'],
                'loan_amount': loan_amount,
                'buy_dex': buy_dex,
                'sell_dex': sell_dex,
                'expected_profit': profit_usd,
                'swap_data_buy': swap_data_buy,
                'swap_data_sell': swap_data_sell
            }
    
    return None


# ============ EXECUTION ============

async def execute_arbitrage(opportunity):
    """Execute the flash loan arbitrage"""
    global gas_spent
    
    print("\n🚀 EXECUTING ARBITRAGE!")
    print(f"Expected profit: ${opportunity['expected_profit']:.2f}")
    
    # Load contract
    arbitrage_abi = [
        {
            "inputs": [
                {"name": "asset", "type": "address"},
                {"name": "amount", "type": "uint256"},
                {"name": "dex1", "type": "address"},
                {"name": "dex2", "type": "address"},
                {"name": "minProfit", "type": "uint256"},
                {"name": "swapData1", "type": "bytes"},
                {"name": "swapData2", "type": "bytes"}
            ],
            "name": "executeArbitrage",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        }
    ]
    
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=arbitrage_abi)
    
    # Calculate min profit (80% of expected for safety)
    min_profit = int(opportunity['expected_profit'] * Decimal('0.8') * 10**6)
    
    # Build transaction
    gas_price = w3.eth.gas_price
    
    tx = contract.functions.executeArbitrage(
        opportunity['loan_token'],
        opportunity['loan_amount'],
        opportunity['buy_dex'],
        opportunity['sell_dex'],
        min_profit,
        opportunity['swap_data_buy'],
        opportunity['swap_data_sell']
    ).build_transaction({
        'from': account.address,
        'gas': 300000,
        'gasPrice': int(gas_price * 1.1),  # +10% to compete
        'nonce': w3.eth.get_transaction_count(account.address),
        'chainId': 137
    })
    
    # Simulate first (CRITICAL - saves gas!)
    try:
        w3.eth.call(tx)
        print("✅ Simulation passed!")
    except Exception as e:
        print(f"❌ Simulation failed: {e}")
        print("Skipping to save gas...")
        return False
    
    # Execute for real
    try:
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"📤 Transaction sent: {tx_hash.hex()}")
        
        # Wait for receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        
        gas_used = receipt.gasUsed
        gas_cost_matic = w3.from_wei(gas_used * gas_price, 'ether')
        gas_cost_usd = Decimal(gas_cost_matic) * Decimal('0.5')  # $0.50/MATIC
        
        gas_spent += gas_cost_usd
        
        if receipt.status == 1:
            print(f"✅ SUCCESS! Gas used: ${gas_cost_usd:.2f}")
            return True
        else:
            print(f"❌ FAILED! Gas lost: ${gas_cost_usd:.2f}")
            return False
            
    except Exception as e:
        print(f"💥 Execution error: {e}")
        return False


# ============ MAIN LOOP ============

async def main():
    """Main scanning loop"""
    print("=" * 50)
    print("🤖 ARBITRAGE BOT STARTED")
    print(f"💼 Wallet: {account.address}")
    print(f"💰 Budget: ${BUDGET_USD} gas limit")
    print("=" * 50)
    
    # Check balance
    balance = w3.eth.get_balance(account.address)
    matic_balance = Decimal(w3.from_wei(balance, 'ether'))
    print(f"💎 MATIC Balance: {matic_balance:.4f}")
    
    if matic_balance < 10:
        print("⚠️  WARNING: Low MATIC! Add 15+ MATIC for gas.")
    
    async with aiohttp.ClientSession() as session:
        while gas_spent < BUDGET_USD:
            try:
                opp = await find_arbitrage_opportunity(session)
                
                if opp:
                    success = await execute_arbitrage(opp)
                    if success:
                        print("🎉 Trade complete!")
                        # Wait a bit after successful trade
                        await asyncio.sleep(10)
                else:
                    await asyncio.sleep(3)  # Scan every 3 seconds
                    
            except Exception as e:
                print(f"Error in main loop: {e}")
                await asyncio.sleep(5)
    
    print("💸 Budget exhausted. Stopping bot.")


if __name__ == "__main__":
    asyncio.run(main())