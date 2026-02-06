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
    error ExecutionFailed(uint256 index, bytes returndata);

    event Dispatched(uint256 count, address indexed caller);

    function dispatch(Request[] calldata requests) external payable {
        if (slotFlag) revert Blocked();
        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
