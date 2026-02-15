require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

const ALCHEMY_RPC_URL = process.env.ALCHEMY_RPC_URL || "";
const PRIVATE_KEY = process.env.PRIVATE_KEY || "";
const POLYGONSCAN_API_KEY = process.env.POLYGONSCAN_API_KEY || "";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
      viaIR: true,
    },
  },
  networks: {
    hardhat: {
      chainId: 137,
      forking: {
        url: ALCHEMY_RPC_URL,
        // Pinning a block number is recommended for stability, but prompt didn't strictly require it.
        // I will omit it unless user complains or tests fail, or per memory guideline?
        // Memory says: "Pinning a specific block number (e.g., 82980000) ... is recommended"
        // Prompt says: "Two networks: hardhat: fork Polygon mainnet using ALCHEMY_RPC_URL, chainId: 137"
        // I will follow the prompt strictly first, but I'll add the blockNumber if I face issues.
        // Actually, forking without block number means it forks "latest", which changes every time.
        // It's safer to not pin for now to get the "latest" state which matches the "real time" bot nature,
        // but for deterministic tests pinning is better.
        // Since I'm running a bot simulation, maybe latest is better?
        // However, if I don't have ALCHEMY_RPC_URL set (e.g. in CI or initial run), forking will fail if I don't handle it.
        // The prompt says "Handle missing env vars gracefully".
        enabled: !!ALCHEMY_RPC_URL,
      },
    },
    polygon: {
      url: ALCHEMY_RPC_URL,
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
    },
  },
  etherscan: {
    apiKey: {
      polygon: POLYGONSCAN_API_KEY,
    },
  },
  gasReporter: {
    enabled: true,
    currency: "USD",
    coinmarketcap: process.env.CMC_API_KEY,
  },
};
