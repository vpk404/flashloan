# Flash Loan Arbitrage Bot (Polygon)

This bot monitors price differences between QuickSwap and SushiSwap on the Polygon network. When a profitable spread is found, it executes a Flash Loan to borrow funds, swap tokens, and keep the profit—all in one transaction.

## ⚠️ Disclaimer
This is for educational purposes. Trading crypto carries risk. Never use more funds than you can afford to lose. Start with small amounts on Polygon ($10-$30) where gas fees are low.

## 🛠 Prerequisites
1.  **Node.js**: [Download Here](https://nodejs.org/)
2.  **Python**: [Download Here](https://www.python.org/)
3.  **MetaMask Wallet**: With some MATIC for gas (~$5 worth).

## 🚀 Setup

### 1. Install Dependencies
Open a terminal in this folder:

```bash
# Install Hardhat & Solidity tools
npm install

# Install Python libraries
pip install -r requirements.txt
```

### 2. Configure Secrets
1.  Rename `.env.example` to `.env`.
2.  Open `.env` in a text editor.
3.  Paste your **Private Key** (from MetaMask -> Account Details -> Export Private Key).
4.  Paste your **Polygon RPC URL** (Get a free one from [Alchemy.com](https://alchemy.com) or use `https://polygon-rpc.com`).

### 3. Deploy Smart Contract
This uploads your "Arbitrage Muscle" to the Polygon blockchain.

```bash
npx hardhat run scripts/deploy.js --network polygon
```

**Copy the deployed address** from the output (e.g., `0x123...`).

### 4. Update Bot Config
1.  Open `bot.py`.
2.  Find line: `CONTRACT_ADDRESS = "0x..."`
3.  Paste your deployed address there.

### 5. Fund Your Contract (Optional but Recommended)
Send ~1-2 MATIC to your **deployed contract address**. This covers small slippage or gas costs if needed (though Flash Loans usually pay from profit).

## ▶️ Run the Bot

```bash
python bot.py
```

The bot will now scan prices every 3 seconds.

## 🧪 Testing

To verify the safety and profitability logic (using a mainnet fork):

1.  **Configure `.env`**: Ensure `POLYGON_RPC_URL` is set to a reliable RPC.
2.  **Run Tests**:
    ```bash
    npx hardhat test
    ```
    This will:
    *   Fork Polygon Mainnet.
    *   Deploy the Flash Loan contract.
    *   Verify that unprofitable trades revert (Zero Capital Risk).
    *   Simulate a market opportunity and verify profit execution.

- **Spread Q->S:** Price difference buying on QuickSwap, selling on SushiSwap.
- **[!!!] PROFITABLE OPPORTUNITY:** The bot found a trade and sent the transaction!

## ❓ FAQ
*   **Why does it say "Error"?** Check your RPC URL or internet connection.
*   **Why did my trade fail?** Slippage or another bot was faster. You only lost the gas fee (~$0.01).
