const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("FlashLoanBot Contracts", function () {
  let FlashLoanArbitrage, flashLoanArbitrage;
  let LiquidationBot, liquidationBot;
  let owner;

  beforeEach(async function () {
    [owner] = await ethers.getSigners();

    FlashLoanArbitrage = await ethers.getContractFactory("FlashLoanArbitrage");
    flashLoanArbitrage = await FlashLoanArbitrage.deploy();
    await flashLoanArbitrage.waitForDeployment();

    LiquidationBot = await ethers.getContractFactory("LiquidationBot");
    liquidationBot = await LiquidationBot.deploy();
    await liquidationBot.waitForDeployment();
  });

  it("Should set the right owner for FlashLoanArbitrage", async function () {
    expect(await flashLoanArbitrage.owner()).to.equal(owner.address);
  });

  it("Should set the right owner for LiquidationBot", async function () {
    expect(await liquidationBot.owner()).to.equal(owner.address);
  });

  it("Should have correct POOL address constant in FlashLoanArbitrage", async function () {
    const POOL_ADDRESS = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
    expect(await flashLoanArbitrage.POOL_ADDRESS()).to.equal(POOL_ADDRESS);
  });

  it("Should have correct POOL address constant in LiquidationBot", async function () {
    const POOL_ADDRESS = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
    expect(await liquidationBot.POOL_ADDRESS()).to.equal(POOL_ADDRESS);
  });
});
