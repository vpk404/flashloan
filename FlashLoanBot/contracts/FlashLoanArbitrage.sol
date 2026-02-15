// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address recipient, uint256 amount) external returns (bool);
}

contract FlashLoanArbitrage is IFlashLoanSimpleReceiver {
    address public constant POOL_ADDRESS = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    IPool public immutable POOL;
    address public owner;

    uint256 private constant _NOT_ENTERED = 1;
    uint256 private constant _ENTERED = 2;
    uint256 private _status;

    event ArbitrageExecuted(uint256 profit);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    modifier nonReentrant() {
        require(_status != _ENTERED, "ReentrancyGuard: reentrant call");
        _status = _ENTERED;
        _;
        _status = _NOT_ENTERED;
    }

    constructor() {
        POOL = IPool(POOL_ADDRESS);
        owner = msg.sender;
        _status = _NOT_ENTERED;
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == address(POOL), "Caller must be pool");
        require(initiator == address(this), "Initiator must be this");

        // Decode params
        // Expected params: targetA, dataA, intermediateAsset, targetB, dataB, amountOutMin
        (
            address targetA,
            bytes memory dataA,
            address intermediateAsset,
            address targetB,
            bytes memory dataB,
            uint256 amountOutMin
        ) = abi.decode(params, (address, bytes, address, address, bytes, uint256));

        // 1. Swap A -> B
        IERC20(asset).approve(targetA, amount);
        (bool successA, ) = targetA.call(dataA);
        require(successA, "Swap A failed");

        // 2. Swap B -> A
        uint256 intermediateBalance = IERC20(intermediateAsset).balanceOf(address(this));
        IERC20(intermediateAsset).approve(targetB, intermediateBalance);
        (bool successB, ) = targetB.call(dataB);
        require(successB, "Swap B failed");

        // 3. Check profit
        uint256 amountOwed = amount + premium;
        uint256 balanceAfter = IERC20(asset).balanceOf(address(this));

        require(balanceAfter >= amountOwed, "Insufficient to repay");
        require(balanceAfter >= amountOutMin, "Slippage / Profit check failed");

        uint256 profit = balanceAfter - amountOwed;
        emit ArbitrageExecuted(profit);

        // 4. Approve repayment
        IERC20(asset).approve(address(POOL), amountOwed);

        return true;
    }

    function requestFlashLoan(
        address _token,
        uint256 _amount,
        bytes calldata _params
    ) external onlyOwner nonReentrant {
        POOL.flashLoanSimple(address(this), _token, _amount, _params, 0);
    }

    function withdraw(address _token) external onlyOwner {
        uint256 balance = IERC20(_token).balanceOf(address(this));
        IERC20(_token).transfer(owner, balance);
    }

    function withdrawMATIC() external onlyOwner {
        payable(owner).transfer(address(this).balance);
    }

    receive() external payable {}
}
