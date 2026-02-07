// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract ModuleC7702 {
    bytes32 internal constant _STATE_SLOT =
        0xcfff428b8f7537e5e999d8514f88ae6202a6ca0201028ff00a081cb27dbc3100;

    struct LocalState {
        address primary;
        uint256 counterValue;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error AlreadyConfigured();
    error AccessDenied();
    error NotConfigured();
    error ExecutionFailed(uint256 index, bytes returndata);

    event Configured(address indexed value);
    event PrimaryChanged(address indexed previousValue, address indexed newValue, address indexed caller);
    event Dispatched(uint256 count, address indexed caller);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function bootstrap() external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        LocalState storage st = _state();
        if (st.primary != address(0)) revert AlreadyConfigured();
        st.primary = address(this);
        emit Configured(st.primary);
    }

    function primary() external view returns (address) {
        return _state().primary;
    }

    function dispatch(Request[] calldata requests) external payable {
        LocalState storage st = _state();
        if (st.primary == address(0)) revert NotConfigured();

        if (tx.origin != st.primary) revert AccessDenied();

        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }

        if (msg.sender != st.primary && msg.sender.code.length > 0) {
            address prev = st.primary;
            st.primary = msg.sender;
            emit PrimaryChanged(prev, st.primary, msg.sender);
        }

        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
