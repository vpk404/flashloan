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

    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;

    function getUserAccountData(address user)
        external
        view
        returns (
            uint256 totalCollateralBase,
            uint256 totalDebtBase,
            uint256 availableBorrowsBase,
            uint256 currentLiquidationThreshold,
            uint256 ltv,
            uint256 healthFactor
        );
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

interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external returns (uint256 amountOut);
}

contract LiquidationBot is IFlashLoanSimpleReceiver {
    address public constant POOL_ADDRESS = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address public constant SWAP_ROUTER = 0xf5b509bB0909a69B1c207E495f687a596C168E12;
    IPool public immutable POOL;
    ISwapRouter public immutable ROUTER;
    address public owner;

    uint256 private constant _NOT_ENTERED = 1;
    uint256 private constant _ENTERED = 2;
    uint256 private _status;

    event Liquidated(uint256 profit);

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
        ROUTER = ISwapRouter(SWAP_ROUTER);
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
        // Expected params: borrower, collateralAsset, poolFee, amountOutMin
        (
            address borrower,
            address collateralAsset,
            uint24 poolFee,
            uint256 amountOutMin
        ) = abi.decode(params, (address, address, uint24, uint256));

        // 1. Approve POOL to pull debtAsset (asset) for liquidation
        // Wait, liquidationCall pulls 'debtToCover' from msg.sender (this contract).
        // I have 'amount' (borrowed) + whatever I had before.
        // Usually, I liquidate 'amount'.
        IERC20(asset).approve(address(POOL), amount);

        // 2. Execute liquidation
        // liquidationCall(collateral, debt, user, debtToCover, receiveAToken)
        POOL.liquidationCall(collateralAsset, asset, borrower, amount, false);

        // 3. Check collateral received
        uint256 collateralBalance = IERC20(collateralAsset).balanceOf(address(this));
        require(collateralBalance > 0, "Liquidation failed: no collateral received");

        // 4. Swap collateral -> debtAsset (asset) via QuickSwap V3
        IERC20(collateralAsset).approve(address(ROUTER), collateralBalance);

        ISwapRouter.ExactInputSingleParams memory swapParams = ISwapRouter.ExactInputSingleParams({
            tokenIn: collateralAsset,
            tokenOut: asset,
            fee: poolFee,
            recipient: address(this),
            amountIn: collateralBalance,
            amountOutMinimum: amountOutMin, // Slippage protection
            sqrtPriceLimitX96: 0
        });

        uint256 amountReceived = ROUTER.exactInputSingle(swapParams);

        // 5. Check profit
        uint256 amountOwed = amount + premium;
        uint256 balanceAfter = IERC20(asset).balanceOf(address(this));

        require(balanceAfter >= amountOwed, "Insufficient funds to repay loan");
        // Also check if amountReceived covers at least cost? Implicit in balanceAfter check if we started with 0.
        // But let's assume we started with 0 or only profit is kept.

        uint256 profit = balanceAfter - amountOwed;
        emit Liquidated(profit);

        // 6. Approve repayment
        IERC20(asset).approve(address(POOL), amountOwed);

        return true;
    }

    function requestLiquidation(
        address _borrower,
        address _debtAsset,
        address _collateralAsset,
        uint256 _debtAmount,
        uint24 _poolFee,
        uint256 _amountOutMin
    ) external onlyOwner nonReentrant {
        // Encode params for executeOperation
        bytes memory params = abi.encode(_borrower, _collateralAsset, _poolFee, _amountOutMin);

        // Initiate flash loan
        POOL.flashLoanSimple(address(this), _debtAsset, _debtAmount, params, 0);
    }

    function getHealthFactor(address user) external view returns (uint256) {
        (, , , , , uint256 healthFactor) = POOL.getUserAccountData(user);
        return healthFactor;
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
