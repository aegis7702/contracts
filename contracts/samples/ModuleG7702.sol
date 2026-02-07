pragma solidity ^0.8.24;

contract ModuleG7702 {
    bytes32 internal constant _STATE_SLOT =
        0x52f8f5821b5cdddf373d04caaa93065865380012632568f3bfdc05eec3212100;

    struct LocalState {
        address operator;
        bool flag;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error UnauthorizedOperator();
    error AlreadyConfigured();
    error InvalidInput();
    error Blocked();
    error ExecutionFailed(uint256 index, bytes returndata);

    event Configured(address indexed operator);
    event FlagUpdated(bool value, address indexed caller);
    event Dispatched(uint256 count, address indexed caller);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function bootstrap(address operator_) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        if (operator_ == address(0)) revert InvalidInput();
        LocalState storage st = _state();
        if (st.operator != address(0)) revert AlreadyConfigured();
        st.operator = operator_;
        st.flag = false;
        emit Configured(operator_);
    }

    function operator() external view returns (address) {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        return _state().operator;
    }

    function flag() external view returns (bool) {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        return _state().flag;
    }

    function setFlag(bool value) external {
        LocalState storage st = _state();
        if (msg.sender != st.operator) revert UnauthorizedOperator();
        st.flag = value;
        emit FlagUpdated(value, msg.sender);
    }

    function dispatch(Request[] calldata requests) external payable {
        LocalState storage st = _state();
        if (st.flag) revert Blocked();
        if (msg.sender != address(this)) revert UnauthorizedCaller();

        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
