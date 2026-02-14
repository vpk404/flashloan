"""
Fail-resistant execution helpers for arbitrage/flashloan orchestrator.

Requirements (env):
- ALCHEMY_RPC_URL (or ALCHEMY_API_KEY to build one)
- ONEINCH_API_KEY
- PRIVATE_KEY
- CONTRACT_ADDRESS (your arbitrage/flashloan contract or router)
Optional:
- MATIC_USD (to avoid fetching price every run)
"""
import os
import sys
import time
import json
import requests
from decimal import Decimal
from web3 import Web3
from eth_account import Account

# ---------- Configuration / constants ----------
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))   # Polygon default
MIN_PROFIT_USD = Decimal(os.getenv("MIN_PROFIT_USD", "10"))  # strict guard for $30 budget
SLIPPAGE = Decimal(os.getenv("SLIPPAGE", "0.003"))  # 0.3% default
GAS_BUFFER_MULTIPLIER = Decimal("1.20")  # 20% gas buffer
TOKEN_DECIMALS = {
    # add tokens you will trade. Fill with token addresses in uppercase keys you use in code.
    "USDC": 6,
    "USDT": 6,
    "WETH": 18,
    "WMATIC": 18,
}

# ---------- Env validation ----------
def require_env(name):
    v = os.getenv(name)
    if not v:
        print(f"[ERROR] missing env var: {name}")
        sys.exit(1)
    return v

ALCHEMY_RPC = os.getenv("ALCHEMY_RPC_URL") or os.getenv("ALCHEMY_API_URL") or None
if not ALCHEMY_RPC:
    # allow using ALCHEMY_API_KEY and construct a public RPC endpoint for Polygon:
    ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY")
    if ALCHEMY_KEY:
        ALCHEMY_RPC = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    else:
        require_env("ALCHEMY_RPC_URL")  # will exit

ONEINCH_API_KEY = require_env("ONEINCH_API_KEY")
PRIVATE_KEY = require_env("PRIVATE_KEY")
CONTRACT_ADDRESS = require_env("CONTRACT_ADDRESS")  # contract or router address you will call

# ---------- Web3 setup ----------
w3 = Web3(Web3.HTTPProvider(ALCHEMY_RPC))
acct = Account.from_key(PRIVATE_KEY)
MY_ADDRESS = acct.address

# ---------- Utilities ----------
def to_raw(amount: Decimal, token_symbol: str) -> int:
    d = TOKEN_DECIMALS.get(token_symbol)
    if d is None:
        raise ValueError(f"Unknown token decimals: {token_symbol}")
    return int((amount * (10 ** d)).to_integral_value())

def from_raw(amount_int: int, token_symbol: str) -> Decimal:
    d = TOKEN_DECIMALS.get(token_symbol)
    if d is None:
        raise ValueError(f"Unknown token decimals: {token_symbol}")
    return Decimal(amount_int) / (10 ** d)

def http_get(url, params=None, headers=None, timeout=8):
    headers = headers or {}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

# ---------- Price helpers ----------
def get_matic_price_usd() -> Decimal:
    """
    Try to fetch MATIC price in USD via 1inch quote (sell 1 MATIC -> USDC).
    Fallback: env var MATIC_USD if provided.
    """
    env_price = os.getenv("MATIC_USD")
    if env_price:
        return Decimal(env_price)

    try:
        # 1inch quote: fromToken = WMATIC, toToken = USDC. Addresses for Polygon:
        WMATIC_ADDR = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"  # polygon wmatic
        USDC_ADDR = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"   # polygon usdc
        amount = 10**18  # 1 WMATIC
        url = "https://api.1inch.io/v5.0/137/quote"
        params = {"fromTokenAddress": WMATIC_ADDR, "toTokenAddress": USDC_ADDR, "amount": str(amount)}
        headers = {"Authorization": ONEINCH_API_KEY}
        j = http_get(url, params=params, headers=headers)
        to_amount = int(j["toTokenAmount"])
        # to_amount is in USDC raw (6 decimals)
        return Decimal(to_amount) / Decimal(10**TOKEN_DECIMALS["USDC"])
    except Exception as e:
        print("[WARN] failed to fetch MATIC price via 1inch:", e)
        # very conservative fallback
        return Decimal("0.7")  # extremely conservative placeholder (replace via env!)

# ---------- 1inch helpers ----------
def get_1inch_quote(from_token_addr, to_token_addr, amount_raw):
    """
    Returns JSON quote from 1inch '/quote' endpoint.
    amount_raw = integer raw amount in token decimals.
    """
    url = f"https://api.1inch.io/v5.0/{CHAIN_ID}/quote"
    headers = {"Authorization": ONEINCH_API_KEY}
    params = {"fromTokenAddress": from_token_addr, "toTokenAddress": to_token_addr, "amount": str(amount_raw)}
    return http_get(url, params=params, headers=headers)

def get_1inch_swap_calldata(from_token_addr, to_token_addr, amount_raw, slippage_pct, dest_address):
    """
    Use 1inch '/swap' endpoint to build calldata for the swap to embed in your tx.
    Returns object with 'tx' that contains to, data, value, gasEstimate (if provided).
    """
    url = f"https://api.1inch.io/v5.0/{CHAIN_ID}/swap"
    headers = {"Authorization": ONEINCH_API_KEY}
    params = {
        "fromTokenAddress": from_token_addr,
        "toTokenAddress": to_token_addr,
        "amount": str(amount_raw),
        "fromAddress": dest_address,
        "slippage": str(float(slippage_pct * 100)),  # 1inch expects percent like '0.3' for 0.3%
        "disableEstimate": "true",  # we'll estimate gas ourselves
        "allowPartialFill": "false"
    }
    return http_get(url, params=params, headers=headers)

# ---------- Gas / simulation / profit checks ----------
def estimate_and_simulate(tx):
    """
    Estimate gas, add a buffer, simulate call. Returns gas_limit (int) and simulation success bool.
    """
    try:
        gas_est = w3.eth.estimate_gas(tx)
    except Exception as e:
        print("[WARN] gas estimation failed:", e)
        return None, False

    gas_limit = int(Decimal(gas_est) * GAS_BUFFER_MULTIPLIER)
    tx_for_call = dict(tx)  # copy
    tx_for_call["gas"] = gas_limit

    try:
        # simulate the exact tx against a node
        w3.eth.call(tx_for_call)
        return gas_limit, True
    except Exception as e:
        print("[WARN] simulation (eth_call) failed:", e)
        return gas_limit, False

def gas_cost_usd(gas_limit, gas_price_wei, matic_price_usd):
    # gas_price_wei is integer
    total_matic = Decimal(gas_limit) * Decimal(gas_price_wei) / Decimal(10**18)
    return total_matic * matic_price_usd

# ---------- Net profit calculation ----------
def compute_net_profit_usd(input_amount_raw, expected_output_raw, input_token_symbol, output_token_symbol, matic_price_usd, loan_fee_usd=Decimal("0")):
    """
    Convert raw token amounts to USD and compute net profit minus loan fee (if any).
    For simplicity assumes stablecoins are quoted in USD (USDC/USDT).
    """
    # Convert input -> USD
    if input_token_symbol in ("USDC", "USDT"):
        input_usd = from_raw(input_amount_raw, input_token_symbol)
        output_usd = from_raw(expected_output_raw, output_token_symbol) if output_token_symbol in ("USDC", "USDT") else None
    else:
        # fallback: compute via MATIC price if token is WMATIC or WETH rough via MATIC price (approx)
        # For precise conversion, integrate a token price oracle.
        input_usd = None
        output_usd = None

    # If either is None, attempt to estimate by converting output_token (if WMATIC) via matic price
    # This is conservative; extend for WETH etc.
    if input_usd is None and input_token_symbol == "WMATIC":
        input_usd = from_raw(input_amount_raw, "WMATIC") * matic_price_usd
    if output_usd is None and output_token_symbol == "WMATIC":
        output_usd = from_raw(expected_output_raw, "WMATIC") * matic_price_usd

    if input_usd is None or output_usd is None:
        raise RuntimeError("Unable to compute USD values for tokens; extend compute_net_profit_usd for other tokens.")

    net = Decimal(output_usd) - Decimal(input_usd) - Decimal(loan_fee_usd)
    return net

# ---------- Core safe execution flow ----------
def safe_execute_swap_pair(from_token_addr, to_token_addr, from_token_sym, to_token_sym, human_amount, dest_address=MY_ADDRESS, dry_run=True):
    """
    High-level flow:
    1) Get 1inch quote for amount
    2) Calculate amountOutMin using SLIPPAGE
    3) Get swap calldata from 1inch
    4) Build transaction to call the contract/router (or to call your arbitrage contract with that calldata)
    5) Estimate + simulate
    6) Compute net profit (USD) after gas & optional loan fee
    7) If passes MIN_PROFIT_USD -> sign & send (or return signed tx for private submission)
    """

    # 1) Build raw input amount
    amount_raw = to_raw(Decimal(human_amount), from_token_sym)

    # 2) Get quote (expected output)
    try:
        q = get_1inch_quote(from_token_addr, to_token_addr, amount_raw)
        expected_out_raw = int(q["toTokenAmount"])
    except Exception as e:
        print("[WARN] 1inch quote failed:", e)
        return False

    # 3) Compute amountOutMin with slippage buffer
    amount_out_min = int(Decimal(expected_out_raw) * (Decimal(1) - SLIPPAGE))

    # 4) Request swap calldata
    try:
        swap = get_1inch_swap_calldata(from_token_addr, to_token_addr, amount_raw, SLIPPAGE, dest_address)
        tx_obj = swap["tx"]
        # tx_obj: {to, data, value, gas} - value is hex or int
    except Exception as e:
        print("[WARN] 1inch swap construction failed:", e)
        return False

    # Build our transaction dictionary; ensure chain id and nonce
    tx = {
        "to": Web3.to_checksum_address(tx_obj["to"]),
        "from": MY_ADDRESS,
        "data": tx_obj["data"],
        "value": int(tx_obj.get("value", 0)),
        "chainId": CHAIN_ID,
        "nonce": w3.eth.get_transaction_count(MY_ADDRESS),
    }

    # 5) Estimate gas and simulate
    gas_limit, sim_ok = estimate_and_simulate(tx)
    if not sim_ok:
        print("[INFO] Simulation failed — skipping execution.")
        return False

    # 6) Gas price and USD cost
    gas_price = w3.eth.gas_price  # wei
    matic_price = get_matic_price_usd()
    gas_usd = gas_cost_usd(gas_limit, gas_price, matic_price)

    # 7) Compute net profit in USD
    loan_fee_usd = Decimal("0")  # set if using flashloan (e.g., loan_fee_rate * borrowed_amount_usd)
    try:
        net_profit = compute_net_profit_usd(amount_raw, expected_out_raw, from_token_sym, to_token_sym, matic_price, loan_fee_usd)
    except Exception as e:
        print("[WARN] cannot compute net profit USD:", e)
        return False

    net_minus_gas = net_profit - Decimal(gas_usd)

    print(f"[DEBUG] expected_output_raw={expected_out_raw}, amount_out_min={amount_out_min}")
    print(f"[DEBUG] gas_limit={gas_limit}, gas_price_wei={gas_price}, gas_usd={gas_usd:.6f}")
    print(f"[DEBUG] raw_net_profit_usd={net_profit:.6f}, net_after_gas={net_minus_gas:.6f}")

    # 8) Strict profit guard
    if net_minus_gas < MIN_PROFIT_USD:
        print(f"[SKIP] net profit after gas {net_minus_gas:.6f} < MIN_PROFIT_USD {MIN_PROFIT_USD}")
        return False

    # 9) Prepare final tx with gas and fee params
    tx_final = dict(tx)
    tx_final["gas"] = gas_limit
    # Use EIP-1559 style where supported; fallback to legacy if node rejects
    try:
        max_fee = w3.eth.max_priority_fee  # may not exist; fallback:
        tx_final["maxPriorityFeePerGas"] = w3.to_wei(2, "gwei")
        tx_final["maxFeePerGas"] = int(gas_price * 2)  # simple heuristic; you may tune this
    except Exception:
        tx_final["gasPrice"] = gas_price

    # 10) Final on-chain re-check: ensure pool state didn't move significantly since quote
    # Simple conservative check: re-quote and require expected_out_raw close to original (within tolerance)
    try:
        q2 = get_1inch_quote(from_token_addr, to_token_addr, amount_raw)
        expected_out_raw_now = int(q2["toTokenAmount"])
        delta_pct = (Decimal(expected_out_raw_now) - Decimal(expected_out_raw)) / Decimal(expected_out_raw)
        if delta_pct < Decimal("-0.01") or delta_pct > Decimal("0.01"):
            # >1% change: abort
            print(f"[ABORT] pool quote changed by {delta_pct:.4f} — aborting to avoid revert/MEV")
            return False
    except Exception as e:
        print("[WARN] re-quote failed; continuing cautiously:", e)

    # 11) Sign and optionally send
    signed = acct.sign_transaction(tx_final)

    if dry_run or os.getenv("DRY_RUN", "1") == "1":
        print("[DRY RUN] Signed tx prepared. Not broadcasting.")
        return signed.rawTransaction.hex()

    # 12) Send raw transaction (consider replacing with private submission)
    try:
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        print("[SENT] tx hash:", tx_hash.hex())
        return True
    except Exception as e:
        print("[ERROR] send_raw_transaction failed:", e)
        return False

# ---------------- Example usage ----------------
if __name__ == "__main__":
    # Example placeholders -- replace token addresses with correct ones for Polygon
    WMATIC = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"
    USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

    # Try a safe dry-run swap: 10 WMATIC -> USDC
    safe_execute_swap_pair(WMATIC, USDC, "WMATIC", "USDC", human_amount=Decimal("10"), dry_run=True)
