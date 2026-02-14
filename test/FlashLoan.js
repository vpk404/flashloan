const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Flash Loan Arbitrage", function () {
  let flashLoan;
  let owner;
  let whale;

  // Addresses
  const USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";
  const WETH = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619";
  const QUICKSWAP_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff";
  const SUSHISWAP_ROUTER = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506";
  const AAVE_POOL_PROVIDER = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb";

  // Amount to borrow
  const BORROW_AMOUNT = ethers.utils.parseUnits("1000", 6); // 1000 USDC

  before(async function () {
    // Deploy Contract
    const FlashLoan = await ethers.getContractFactory("SimpleFlashLoan");
    flashLoan = await FlashLoan.deploy(AAVE_POOL_PROVIDER);
    await flashLoan.deployed();

    [owner] = await ethers.getSigners();
  });

  it("Should revert on unprofitable trade (Zero Capital Risk)", async function () {
    await expect(
      flashLoan.requestFlashLoan(
        USDC,
        BORROW_AMOUNT,
        SUSHISWAP_ROUTER,
        QUICKSWAP_ROUTER,
        WETH
      )
    ).to.be.revertedWith("Trade not profitable, reverting to save funds.");
  });

  it("Should execute profitable trade when opportunity exists", async function () {
    const [_, manipulator] = await ethers.getSigners();

    // Manipulate market
    // We need a fuller ABI for the router to use swapExactETHForTokens
    const routerAbi = [
      "function swapExactETHForTokens(uint amountOutMin, address[] calldata path, address to, uint deadline) external payable returns (uint[] memory amounts)",
      "function swapExactTokensForTokens(uint amountIn, uint amountOutMin, address[] calldata path, address to, uint deadline) external returns (uint[] memory amounts)",
      "function getAmountsOut(uint amountIn, address[] calldata path) external view returns (uint[] memory amounts)"
    ];
    const quickRouter = await ethers.getContractAt(routerAbi, QUICKSWAP_ROUTER);
    const usdcContract = await ethers.getContractAt("IERC20", USDC);
    const WMATIC = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270";

    await ethers.provider.send("hardhat_setBalance", [
      manipulator.address,
      "0x" + (ethers.utils.parseEther("10000000").toHexString().slice(2)), // 10 Million MATIC
    ]);

    // 1. Swap MATIC -> USDC on QuickSwap to get USDC and pump USDC reserves (making USDC cheap? No, affecting WMATIC/USDC pair)
    // Actually we just want USDC to pump the USDC/WETH pair.

    const hugePump = ethers.utils.parseEther("5000000"); // 5 Million MATIC

    await quickRouter.connect(manipulator).swapExactETHForTokens(
        0,
        [WMATIC, USDC],
        manipulator.address,
        Math.floor(Date.now() / 1000) + 60,
        { value: hugePump }
    );

    const usdcBal = await usdcContract.balanceOf(manipulator.address);
    await usdcContract.connect(manipulator).approve(QUICKSWAP_ROUTER, usdcBal);

    // 2. Swap USDC -> WETH on QuickSwap
    // This increases USDC reserves and decreases WETH reserves in the USDC/WETH pool.
    // Price of WETH in USDC = USDC_Reserves / WETH_Reserves.
    // Numerator goes up, Denominator goes down -> Price goes UP.
    // WETH becomes expensive on QuickSwap.
    // So we Buy Low on Sushi, Sell High on Quick.

    await quickRouter.connect(manipulator).swapExactTokensForTokens(
        usdcBal,
        0,
        [USDC, WETH],
        manipulator.address,
        Math.floor(Date.now() / 1000) + 60
    );

    // Now try the flash loan again
    await expect(
      flashLoan.requestFlashLoan(
        USDC,
        BORROW_AMOUNT,
        SUSHISWAP_ROUTER,
        QUICKSWAP_ROUTER,
        WETH
      )
    ).to.not.be.reverted;

    const ownerBalance = await usdcContract.balanceOf(owner.address);
    expect(ownerBalance).to.be.gt(0);
  });
});
