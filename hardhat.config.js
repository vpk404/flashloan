require("@nomiclabs/hardhat-waffle");
require("dotenv").config();

module.exports = {
  solidity: {
    version: "0.8.10",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200
      }
    }
  },
  networks: {
    hardhat: {
      forking: {
        // Get a free key from alchemy.com for best performance
        url: process.env.POLYGON_RPC_URL || "https://polygon-rpc.com",
        blockNumber: 82980000 // Pinned block for consistent testing
      }
    },
    polygon: {
      url: process.env.POLYGON_RPC_URL || "https://polygon-rpc.com",
      accounts: process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : []
    }
  }
};
