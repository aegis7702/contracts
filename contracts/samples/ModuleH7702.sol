pragma solidity ^0.8.24;

contract ModuleH7702 {
    bytes32 internal constant _STATE_SLOT =
        0x6f1093f99737e68df4ff9fd35f6e2ef0c5cf0cd23708ce3f6bb77eb8a9d6d300;

    struct LocalState {
        address guardian;
        bool blocked;
        uint64 epoch;
        uint64 score;
        bytes32 rolling;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error UnauthorizedGuardian();
    error AlreadyConfigured();
    error NotConfigured();
    error InvalidInput();
    error Blocked();
    error ExecutionFailed(uint256 index, bytes returndata);

    event Configured(address indexed guardian, bytes32 seed);
    event BlockedSet(bool value, address indexed caller);
    event Pattern(bytes32 digest, uint64 value);
    event RollingUpdated(bytes32 rolling, uint64 epoch, uint64 score);
    event Dispatched(uint256 count, address indexed caller);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function bootstrap(address guardian_, bytes32 seed) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        if (guardian_ == address(0)) revert InvalidInput();

        LocalState storage st = _state();
        if (st.guardian != address(0)) revert AlreadyConfigured();

        st.guardian = guardian_;
        st.blocked = false;
        st.epoch = 0;
        st.score = 0;
        st.rolling = seed;

        emit Configured(guardian_, seed);
    }

    function guardian() external view returns (address) {
        return _state().guardian;
    }

    function blocked() external view returns (bool) {
        return _state().blocked;
    }

    function score() external view returns (uint64) {
        return _state().score;
    }

    function rolling() external view returns (bytes32) {
        return _state().rolling;
    }

    function setBlocked(bool value) external {
        LocalState storage st = _state();
        if (st.guardian == address(0)) revert NotConfigured();
        if (msg.sender != st.guardian) revert UnauthorizedGuardian();
        st.blocked = value;
        emit BlockedSet(value, msg.sender);
    }

    function dispatch(Request[] calldata requests) external payable {
        if (msg.sender != address(this)) revert UnauthorizedCaller();

        LocalState storage st = _state();
        if (st.guardian == address(0)) revert NotConfigured();
        if (st.blocked) revert Blocked();

        bytes32 rollingValue = st.rolling;
        uint64 s = st.score;

        for (uint256 i = 0; i < requests.length; i++) {
            bytes32 entry = keccak256(
                abi.encode(requests[i].to, requests[i].value, keccak256(requests[i].data), i, rollingValue)
            );
            rollingValue = keccak256(abi.encodePacked(rollingValue, entry));

            if ((uint256(rollingValue) & 0x0fff) == 0x0a11) {
                unchecked {
                    s += 1;
                }
                emit Pattern(rollingValue, s);
            }

            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }

        uint64 nextEpoch = st.epoch + 1;
        st.epoch = nextEpoch;
        st.score = s;
        st.rolling = rollingValue;

        if (s >= 3) {
            bytes32 gate = keccak256(abi.encodePacked(rollingValue, nextEpoch, block.chainid));
            if ((uint256(gate) & 0x1f) == 0x1f) {
                st.blocked = true;
                emit BlockedSet(true, msg.sender);
            }
        }

        emit RollingUpdated(rollingValue, nextEpoch, s);
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
