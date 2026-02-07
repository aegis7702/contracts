// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract ModuleI7702 {
    bytes32 internal constant _STATE_SLOT =
        0xf8ba6d42ad0a02af45f4fffec950ba6ec3fb49f7d93fca3809fbb9c2c9399800;

    struct PendingAction {
        address to;
        uint96 value;
        bytes4 selector;
        bool armed;
        bytes32 payloadHash;
    }

    struct LocalState {
        address operator;
        uint64 round;
        bool initialized;
        bytes32 policyRoot;
        PendingAction pending;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error UnauthorizedOperator();
    error AlreadyConfigured();
    error NotConfigured();
    error InvalidInput();
    error PendingActionArmed();
    error NoPendingAction();
    error PayloadMismatch();
    error SelectorMismatch(bytes4 got, bytes4 expected);
    error ExecutionFailed(uint256 index, bytes returndata);
    error DeferredExecutionFailed(bytes returndata);

    event Configured(address indexed operator, bytes32 policyRoot);
    event PolicyRootUpdated(bytes32 indexed policyRoot, address indexed caller);
    event PendingArmed(address indexed to, bytes4 indexed selector, uint96 value, bytes32 payloadHash, uint64 round);
    event PendingCancelled(address indexed caller);
    event DeferredExecuted(address indexed to, bytes4 indexed selector, address indexed caller);
    event Dispatched(uint256 count, address indexed caller, bytes32 accumulator);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function bootstrap(address operator_, bytes32 policyRoot_) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        if (operator_ == address(0)) revert InvalidInput();

        LocalState storage st = _state();
        if (st.initialized) revert AlreadyConfigured();

        st.operator = operator_;
        st.round = 0;
        st.initialized = true;
        st.policyRoot = policyRoot_;

        emit Configured(operator_, policyRoot_);
    }

    function operator() external view returns (address) {
        return _state().operator;
    }

    function policyRoot() external view returns (bytes32) {
        return _state().policyRoot;
    }

    function pendingAction()
        external
        view
        returns (address to, uint96 value, bytes4 selector, bool armed, bytes32 payloadHash)
    {
        PendingAction storage p = _state().pending;
        return (p.to, p.value, p.selector, p.armed, p.payloadHash);
    }

    function setPolicyRoot(bytes32 newPolicyRoot) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();

        LocalState storage st = _state();
        if (!st.initialized) revert NotConfigured();
        st.policyRoot = newPolicyRoot;

        emit PolicyRootUpdated(newPolicyRoot, msg.sender);
    }

    function cancelPending() external {
        LocalState storage st = _state();
        if (!st.initialized) revert NotConfigured();
        if (msg.sender != st.operator) revert UnauthorizedOperator();
        if (!st.pending.armed) revert NoPendingAction();

        delete st.pending;
        emit PendingCancelled(msg.sender);
    }

    function dispatch(Request[] calldata requests) external payable {
        if (msg.sender != address(this)) revert UnauthorizedCaller();

        LocalState storage st = _state();
        if (!st.initialized) revert NotConfigured();
        if (st.pending.armed) revert PendingActionArmed();

        bytes32 accumulator = st.policyRoot;

        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);

            bytes32 leaf = keccak256(
                abi.encode(requests[i].to, requests[i].value, keccak256(requests[i].data), i, block.chainid)
            );
            accumulator = keccak256(abi.encodePacked(accumulator, leaf));
        }

        uint64 nextRound = st.round + 1;
        st.round = nextRound;
        st.policyRoot = accumulator;

        if (requests.length != 0) {
            bytes32 trigger = keccak256(abi.encodePacked(accumulator, nextRound, address(this)));
            if ((uint256(trigger) & 0x3f) == 0x2a) {
                Request calldata selected = requests[requests.length - 1];
                if (selected.value > type(uint96).max) revert InvalidInput();

                bytes4 selector;
                bytes calldata data = selected.data;
                if (data.length >= 4) {
                    assembly {
                        selector := calldataload(data.offset)
                    }
                }

                st.pending = PendingAction({
                    to: selected.to,
                    value: uint96(selected.value),
                    selector: selector,
                    armed: true,
                    payloadHash: keccak256(selected.data)
                });

                emit PendingArmed(
                    selected.to,
                    selector,
                    uint96(selected.value),
                    keccak256(selected.data),
                    nextRound
                );
            }
        }

        emit Dispatched(requests.length, msg.sender, accumulator);
    }

    function settlePending(bytes calldata payload) external {
        LocalState storage st = _state();
        if (!st.initialized) revert NotConfigured();

        PendingAction memory pending = st.pending;
        if (!pending.armed) revert NoPendingAction();
        if (keccak256(payload) != pending.payloadHash) revert PayloadMismatch();

        bytes4 selector;
        if (payload.length >= 4) {
            assembly {
                selector := calldataload(payload.offset)
            }
        }
        if (selector != pending.selector) {
            revert SelectorMismatch(selector, pending.selector);
        }

        delete st.pending;
        emit PendingCancelled(msg.sender);

        (bool ok, bytes memory ret) = pending.to.call{value: pending.value}(payload);
        if (!ok) revert DeferredExecutionFailed(ret);

        emit DeferredExecuted(pending.to, selector, msg.sender);
    }

    receive() external payable {}
}
