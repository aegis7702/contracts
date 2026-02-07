// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract ModuleF7702 {
    bool public slotFlag;

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error Blocked();
    error UnauthorizedCaller();
    error ExecutionFailed(uint256 index, bytes returndata);

    event Dispatched(uint256 count, address indexed caller);

    function dispatch(Request[] calldata requests) external payable {
        // In the 7702 "wallet self-call" UX, msg.sender == address(this).
        // External callers must not be able to execute arbitrary calls from the wallet.
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        if (slotFlag) revert Blocked();
        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
