// scripts/deploy.js
const hre = require("hardhat");

async function main() {
    const Arbitrage = await hre.ethers.getContractFactory("PrecisionArbitrage");
    
    // Polygon Aave Pool Provider
    const poolProvider = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb";
    
    const arbitrage = await Arbitrage.deploy(poolProvider);
    await arbitrage.waitForDeployment();
    
    console.log("Deployed to:", await arbitrage.getAddress());
}

main().catch(console.error);