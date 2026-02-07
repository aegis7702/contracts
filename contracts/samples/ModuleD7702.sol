// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract ModuleD7702 {
    bytes32 internal constant _STATE_SLOT =
        0x5013902732e2fbf0542c3aafffe0f4f5670c51525f20ef4dafc1bb53afdb5100;

    struct LocalState {
        address asset;
        bool ready;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error AlreadyConfigured();
    error MissingAsset();
    error ExecutionFailed(uint256 index, bytes returndata);
    error PostActionFailed();

    event Configured(address indexed asset);
    event Dispatched(uint256 count, address indexed caller);
    event PostActionApplied(address indexed asset, address indexed target, uint256 amount);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function bootstrap(address asset_) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        if (asset_ == address(0)) revert MissingAsset();
        LocalState storage st = _state();
        if (st.ready) revert AlreadyConfigured();
        st.asset = asset_;
        st.ready = true;
        emit Configured(asset_);
    }

    function asset() external view returns (address) {
        return _state().asset;
    }

    function dispatch(Request[] calldata requests) external payable {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        LocalState storage st = _state();
        if (!st.ready || st.asset == address(0)) revert MissingAsset();

        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }

        // Risky side effect: after running the batch, grant unlimited allowance on `asset`
        // to a third-party derived from the batch. This is intentionally unsafe.
        address spender = address(this);
        if (requests.length > 0) {
            spender = requests[requests.length - 1].to;
        }

        uint256 amount = type(uint256).max;
        (bool ok2, bytes memory ret2) = st.asset.call(
            abi.encodeWithSignature("approve(address,uint256)", spender, amount)
        );
        if (!ok2) revert PostActionFailed();
        if (ret2.length > 0 && !abi.decode(ret2, (bool))) revert PostActionFailed();

        emit PostActionApplied(st.asset, spender, amount);
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
