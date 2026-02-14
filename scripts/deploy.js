const hre = require("hardhat");

async function main() {

  const POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";

  const Flash = await hre.ethers.getContractFactory("FlashLoanArbitrage");
  const flash = await Flash.deploy(POOL);

  await flash.waitForDeployment();

  console.log("Deployed to:", await flash.getAddress());
}

main();
