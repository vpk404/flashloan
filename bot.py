import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# Core network/router constants (Polygon)
USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
WETH = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
QUICKSWAP_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
SUSHISWAP_ROUTER = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506"
CHAIN_ID_POLYGON = 137

PAIR_ABI = '[{"constant":true,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"payable":false,"stateMutability":"view","type":"function"}]'
ROUTER_ABI = '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"}]'
FLASHLOAN_ABI = '[{"inputs":[{"internalType":"address","name":"_token","type":"address"},{"internalType":"uint256","name":"_amount","type":"uint256"},{"internalType":"address","name":"_routerA","type":"address"},{"internalType":"address","name":"_routerB","type":"address"},{"internalType":"address","name":"_tokenB","type":"address"}],"name":"requestFlashLoan","outputs":[],"stateMutability":"nonpayable","type":"function"}]'


@dataclass
class BotConfig:
    rpc_url: str
    private_key: str
    contract_address: str
    scan_interval_seconds: int
    cooldown_seconds: int
    dry_run: bool
    loan_amount_usdc: float
    min_profit_usdc: float
    max_gas_gwei: float
    max_daily_attempts: int


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def load_config() -> BotConfig:
    return BotConfig(
        rpc_url=os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"),
        private_key=os.getenv("PRIVATE_KEY", ""),
        contract_address=os.getenv("CONTRACT_ADDRESS", "0x0000000000000000000000000000000000000000"),
        scan_interval_seconds=env_int("SCAN_INTERVAL_SECONDS", 3),
        cooldown_seconds=env_int("COOLDOWN_SECONDS", 10),
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        loan_amount_usdc=env_float("LOAN_AMOUNT_USDC", 10.0),
        min_profit_usdc=env_float("MIN_PROFIT_USDC", 1.2),
        max_gas_gwei=env_float("MAX_GAS_GWEI", 80.0),
        max_daily_attempts=env_int("MAX_DAILY_ATTEMPTS", 3),
    )


class FlashLoanBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.w3 = Web3(Web3.HTTPProvider(config.rpc_url))

        if not config.private_key:
            raise ValueError("PRIVATE_KEY missing. Add it to your environment or .env.")

        self.account = self.w3.eth.account.from_key(config.private_key)
        self.flash_loan = self.w3.eth.contract(address=config.contract_address, abi=FLASHLOAN_ABI)
        self.quick_router = self.w3.eth.contract(address=QUICKSWAP_ROUTER, abi=ROUTER_ABI)
        self.sushi_router = self.w3.eth.contract(address=SUSHISWAP_ROUTER, abi=ROUTER_ABI)

        self.attempts_today = 0
        self.attempt_day = time.strftime("%Y-%m-%d")

    @staticmethod
    def usdc_to_base(amount_usdc: float) -> int:
        return int(amount_usdc * 10**6)

    @staticmethod
    def base_to_usdc(amount_base: int) -> float:
        return amount_base / 10**6

    def _reset_daily_counter_if_needed(self):
        current_day = time.strftime("%Y-%m-%d")
        if current_day != self.attempt_day:
            self.attempt_day = current_day
            self.attempts_today = 0

    def get_amount_out(self, router, amount_in, path):
        try:
            amounts = router.functions.getAmountsOut(amount_in, path).call()
            return amounts[-1]
        except Exception:
            return 0

    def estimate_roundtrip_profit(self, amount_in_base: int):
        path_buy = [USDC, WETH]
        path_sell = [WETH, USDC]

        amount_weth_quick = self.get_amount_out(self.quick_router, amount_in_base, path_buy)
        amount_usdc_sushi = self.get_amount_out(self.sushi_router, amount_weth_quick, path_sell)
        profit_qs = amount_usdc_sushi - amount_in_base

        amount_weth_sushi = self.get_amount_out(self.sushi_router, amount_in_base, path_buy)
        amount_usdc_quick = self.get_amount_out(self.quick_router, amount_weth_sushi, path_sell)
        profit_sq = amount_usdc_quick - amount_in_base

        return profit_qs, profit_sq

    def should_trade(self, expected_profit_base: int) -> bool:
        if expected_profit_base < self.usdc_to_base(self.config.min_profit_usdc):
            return False

        gas_price_gwei = self.w3.from_wei(self.w3.eth.gas_price, "gwei")
        if gas_price_gwei > self.config.max_gas_gwei:
            print(f"[SAFEGUARD] Skip trade: gas {gas_price_gwei:.2f} gwei > limit {self.config.max_gas_gwei} gwei")
            return False

        self._reset_daily_counter_if_needed()
        if self.attempts_today >= self.config.max_daily_attempts:
            print(f"[SAFEGUARD] Daily attempt limit hit ({self.config.max_daily_attempts})")
            return False

        return True

    def execute_trade(self, amount_in_base: int, router_a: str, router_b: str):
        if self.config.dry_run:
            print("[DRY-RUN] Trade qualified but DRY_RUN=true, skipping live transaction.")
            return

        nonce = self.w3.eth.get_transaction_count(self.account.address)
        gas_price = self.w3.eth.gas_price

        tx = self.flash_loan.functions.requestFlashLoan(
            USDC,
            amount_in_base,
            router_a,
            router_b,
            WETH,
        ).build_transaction(
            {
                "chainId": CHAIN_ID_POLYGON,
                "gas": 500000,
                "gasPrice": gas_price,
                "nonce": nonce,
            }
        )

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.config.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        self.attempts_today += 1

        print(f"[*] Trade sent: {self.w3.to_hex(tx_hash)}")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status == 1:
            print("[+] Trade SUCCESS")
        else:
            print("[-] Trade reverted (gas spent only)")

    def run(self):
        amount_in_base = self.usdc_to_base(self.config.loan_amount_usdc)
        print(f"[*] Account: {self.account.address}")
        print(f"[*] Mode: {'DRY-RUN' if self.config.dry_run else 'LIVE'}")
        print(f"[*] Loan size: {self.config.loan_amount_usdc:.2f} USDC")
        print(f"[*] Min expected profit: {self.config.min_profit_usdc:.2f} USDC")
        print(f"[*] Max gas limit: {self.config.max_gas_gwei:.2f} gwei")

        while True:
            try:
                profit_qs, profit_sq = self.estimate_roundtrip_profit(amount_in_base)
                print(
                    f"Spread Q->S: {self.base_to_usdc(profit_qs):.4f} USDC | "
                    f"S->Q: {self.base_to_usdc(profit_sq):.4f} USDC"
                )

                if profit_qs >= profit_sq and self.should_trade(profit_qs):
                    print(f"[SIGNAL] Q->S qualifies (+{self.base_to_usdc(profit_qs):.4f} USDC est.)")
                    self.execute_trade(amount_in_base, QUICKSWAP_ROUTER, SUSHISWAP_ROUTER)
                    time.sleep(self.config.cooldown_seconds)
                elif profit_sq > profit_qs and self.should_trade(profit_sq):
                    print(f"[SIGNAL] S->Q qualifies (+{self.base_to_usdc(profit_sq):.4f} USDC est.)")
                    self.execute_trade(amount_in_base, SUSHISWAP_ROUTER, QUICKSWAP_ROUTER)
                    time.sleep(self.config.cooldown_seconds)

                time.sleep(self.config.scan_interval_seconds)
            except Exception as exc:
                print(f"[ERROR] Loop failed: {exc}")
                time.sleep(self.config.scan_interval_seconds)


if __name__ == "__main__":
    cfg = load_config()
    bot = FlashLoanBot(cfg)
    bot.run()
