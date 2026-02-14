// SPDX-License-Identifier: MIT
pragma solidity ^0.8.10;

import {FlashLoanSimpleReceiverBase} from "@aave/core-v3/contracts/flashloan/base/FlashLoanSimpleReceiverBase.sol";
import {IPoolAddressesProvider} from "@aave/core-v3/contracts/interfaces/IPoolAddressesProvider.sol";
import {IERC20} from "@aave/core-v3/contracts/dependencies/openzeppelin/contracts/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
}

contract SimpleFlashLoan is FlashLoanSimpleReceiverBase {
    address payable owner;

    constructor(address _addressProvider)
        FlashLoanSimpleReceiverBase(IPoolAddressesProvider(_addressProvider))
    {
        owner = payable(msg.sender);
    }

    /**
        This function is called after your contract has received the flash loaned amount
     */
    struct FlashParams {
        address routerA;
        address routerB;
        address tokenA;
        address tokenB;
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        
        // 1. Decode params
        FlashParams memory decoded = abi.decode(params, (FlashParams));
        
        // 2. Approve Router A to spend our loan (Token A)
        IERC20(asset).approve(decoded.routerA, amount);

        // 3. Swap on Router A (Buy Token B with Token A)
        address[] memory path = new address[](2);
        path[0] = decoded.tokenA;
        path[1] = decoded.tokenB;
        
        uint[] memory amounts = IUniswapV2Router(decoded.routerA).swapExactTokensForTokens(
            amount, 
            0,
            path,
            address(this), 
            block.timestamp
        );
        
        // Reuse path for second swap
        path[0] = decoded.tokenB;
        path[1] = decoded.tokenA;
        
        // 4. Approve Router B to spend Token B
        IERC20(decoded.tokenB).approve(decoded.routerB, amounts[1]);
        
        // 5. Swap on Router B (Sell Token B back to Token A)
        // Try to swap back. We need AT LEAST amount + premium to repay loan.
        try IUniswapV2Router(decoded.routerB).swapExactTokensForTokens(
            amounts[1],
            amount + premium, // Fail if we don't get enough to repay loan!
            path,
            address(this), 
            block.timestamp
        ) returns (uint[] memory amounts2) {
            
            // 6. Approve Aave to take back the loan + fee
            IERC20(asset).approve(address(POOL), amount + premium);
            
            // 7. Profit Check
            if (amounts2[1] > amount + premium) {
                IERC20(asset).transfer(owner, amounts2[1] - (amount + premium));
            }
            
            return true;
        } catch {
            // Swap failed (profit < 0), revert entire transaction so we lose only gas
            revert("Trade not profitable, reverting to save funds.");
        }
    }

    function requestFlashLoan(address _token, uint256 _amount, address _routerA, address _routerB, address _tokenB) public {
        address receiverAddress = address(this);
        address asset = _token;
        uint256 amount = _amount;
        bytes memory params = abi.encode(_routerA, _routerB, _token, _tokenB);
        uint16 referralCode = 0;

        POOL.flashLoanSimple(
            receiverAddress,
            asset,
            amount,
            params,
            referralCode
        );
    }

    function withdraw(address _token) external {
        require(msg.sender == owner, "Only owner");
        IERC20(_token).transfer(owner, IERC20(_token).balanceOf(address(this)));
    }
    
    receive() external payable {}
}
