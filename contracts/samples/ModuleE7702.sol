pragma solidity ^0.8.24;

contract ModuleE7702 {
    address public slotA;

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error AlreadyConfigured();
    error AccessDenied();
    error ExecutionFailed(uint256 index, bytes returndata);

    event Configured(address indexed value);
    event Dispatched(uint256 count, address indexed caller);

    function bootstrap() external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        if (slotA != address(0)) revert AlreadyConfigured();
        slotA = address(this);
        emit Configured(slotA);
    }

    function dispatch(Request[] calldata requests) external payable {
        if (msg.sender != slotA) revert AccessDenied();
        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
