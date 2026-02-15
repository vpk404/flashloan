const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const network = hre.network.name;

  console.log(`Deploying FlashLoanArbitrage with the account: ${deployer.address}`);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log(`Account balance: ${hre.ethers.formatEther(balance)} MATIC`);

  if (network === "polygon") {
    const minBalance = hre.ethers.parseEther("0.5");
    if (balance < minBalance) {
      console.error("Insufficient funds! You need at least 0.5 MATIC.");
      process.exit(1);
    }
  } else if (network === "hardhat") {
    console.log("DRY RUN: Deploying to local Hardhat network (fork or local)");
  }

  const FlashLoanArbitrage = await hre.ethers.getContractFactory("FlashLoanArbitrage");
  const contract = await FlashLoanArbitrage.deploy();

  await contract.waitForDeployment();

  const address = await contract.getAddress();
  console.log(`FlashLoanArbitrage deployed to: ${address}`);

  if (network === "polygon") {
    console.log(`View on PolygonScan: https://polygonscan.com/address/${address}`);
    console.log(`Verify with: npx hardhat verify --network polygon ${address}`);
  } else {
    console.log("This address is NOT real (simulation only).");
  }

  console.log(`Add CONTRACT_ADDRESS=${address} to your .env`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
