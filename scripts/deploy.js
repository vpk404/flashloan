const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying contracts with the account:", deployer.address);

  // Aave V3 PoolAddressesProvider on Polygon Mainnet
  const POOL_ADDRESSES_PROVIDER = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb";

  const SimpleFlashLoan = await hre.ethers.getContractFactory("SimpleFlashLoan");
  const flashLoan = await SimpleFlashLoan.deploy(POOL_ADDRESSES_PROVIDER);

  await flashLoan.deployed();

  console.log("SimpleFlashLoan deployed to:", flashLoan.address);
  console.log("Update CONTRACT_ADDRESS in bot.py with this address!");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
