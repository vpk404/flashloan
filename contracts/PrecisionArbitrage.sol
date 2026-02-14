// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@aave/core-v3/contracts/flashloan/base/FlashLoanSimpleReceiverBase.sol";
import "@aave/core-v3/contracts/interfaces/IPoolAddressesProvider.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract PrecisionArbitrage is FlashLoanSimpleReceiverBase {
    
    address public immutable owner;
    bool public paused;
    
    event ArbitrageExecuted(
        address indexed asset,
        uint256 loanAmount,
        uint256 profit
    );
    
    event ArbitrageFailed(string reason);
    
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
    
    constructor(address _poolProvider) 
        FlashLoanSimpleReceiverBase(IPoolAddressesProvider(_poolProvider)) 
    {
        owner = msg.sender;
    }
    
    function executeArbitrage(
        address asset,
        uint256 amount,
        address dex1,
        address dex2,
        uint256 minProfit,
        bytes calldata swapData1,
        bytes calldata swapData2
    ) external onlyOwner {
        // Store parameters for the callback
        bytes memory params = abi.encode(dex1, dex2, minProfit, swapData1, swapData2);
        
        // Initiate flash loan
        POOL.flashLoanSimple(
            address(this),
            asset,
            amount,
            params,
            0
        );
    }
    
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == address(POOL), "Invalid caller");
        
        (address dex1, address dex2, uint256 minProfit, bytes memory swapData1, bytes memory swapData2) = 
            abi.decode(params, (address, address, uint256, bytes, bytes));
        
        uint256 startBalance = IERC20(asset).balanceOf(address(this));
        
        // Approve DEX1 to spend our tokens
        IERC20(asset).approve(dex1, amount);
        
        // Execute swap 1: Buy on cheap DEX
        (bool success1, ) = dex1.call(swapData1);
        require(success1, "Swap 1 failed");
        
        // Get intermediate token balance (e.g., WETH)
        address intermediateToken = getIntermediateToken(swapData1);
        uint256 intermediateBalance = IERC20(intermediateToken).balanceOf(address(this));
        
        // Approve DEX2 to spend intermediate tokens
        IERC20(intermediateToken).approve(dex2, intermediateBalance);
        
        // Execute swap 2: Sell on expensive DEX
        (bool success2, ) = dex2.call(swapData2);
        require(success2, "Swap 2 failed");
        
        // Check profit
        uint256 endBalance = IERC20(asset).balanceOf(address(this));
        uint256 requiredReturn = amount + premium + minProfit;
        
        require(endBalance >= requiredReturn, "Insufficient profit");
        
        // Approve repayment
        IERC20(asset).approve(address(POOL), amount + premium);
        
        emit ArbitrageExecuted(asset, amount, endBalance - startBalance);
        
        return true;
    }
    
    function getIntermediateToken(bytes memory swapData) internal pure returns (address) {
        // Extract token from swap data (simplified)
        // In production, pass this as parameter
        return 0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619; // WETH
    }
    
    function rescueTokens(address token) external onlyOwner {
        IERC20(token).transfer(
            owner,
            IERC20(token).balanceOf(address(this))
        );
    }
    
    function togglePause() external onlyOwner {
        paused = !paused;
    }
    
    receive() external payable {}
}