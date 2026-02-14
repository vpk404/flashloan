// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address recipient, uint256 amount) external returns (bool);
}

contract FlashLoanArbitrage {

    address public owner;
    IPool public immutable POOL;

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    constructor(address _pool) {
        owner = msg.sender;
        POOL = IPool(_pool);
    }

    function executeArbitrage(
        address asset,
        uint256 amount,
        address swapTarget,
        bytes calldata swapData
    ) external onlyOwner {
        bytes memory data = abi.encode(
            asset,
            amount,
            swapTarget,
            swapData
        );

        POOL.flashLoanSimple(
            address(this),
            asset,
            amount,
            data,
            0
        );
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address,
        bytes calldata params
    ) external returns (bool) {

        require(msg.sender == address(POOL), "Not pool");

        (
            address token,
            uint256 loanAmount,
            address swapTarget,
            bytes memory swapData
        ) = abi.decode(params, (address, uint256, address, bytes));

        uint256 balanceBefore = IERC20(token).balanceOf(address(this));

        IERC20(token).approve(swapTarget, loanAmount);

        (bool success,) = swapTarget.call(swapData);
        require(success, "Swap failed");

        uint256 balanceAfter = IERC20(token).balanceOf(address(this));

        uint256 amountOwed = amount + premium;

        require(balanceAfter >= amountOwed, "Not profitable");

        IERC20(token).approve(address(POOL), amountOwed);

        uint256 profit = balanceAfter - amountOwed;

        if (profit > 0) {
            IERC20(token).transfer(owner, profit);
        }

        return true;
    }

    function withdraw(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        IERC20(token).transfer(owner, bal);
    }
}
