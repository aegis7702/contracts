// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "./ECDSA.sol";

contract ModuleA7702 {
    using ECDSA for bytes32;

    bytes32 internal constant _STATE_SLOT =
        0x4781d7498700f46e94e0d978250c77d9822dbc8ff3fc80905a50b8d84e8ca600;

    struct LocalState {
        uint256 counterValue;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error SignatureCheckFailed();
    error InvalidCounter(uint256 expected, uint256 got);
    error ExecutionFailed(uint256 index, bytes returndata);

    event Dispatched(uint256 count, address indexed caller);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function dispatch(Request[] calldata requests) external payable {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        _dispatch(requests);
    }

    function dispatchByAuth(Request[] calldata requests, uint256 counterInput, bytes calldata auth) external payable {
        LocalState storage st = _state();
        if (counterInput != st.counterValue) revert InvalidCounter(st.counterValue, counterInput);

        bytes32 requestHash = _hashRequests(requests);
        bytes32 digest = keccak256(
            abi.encodePacked("MODULE_A_7702", block.chainid, address(this), counterInput, requestHash)
        );
        address signer = digest.toEthSignedMessageHash().recover(auth);
        if (signer != address(this)) revert SignatureCheckFailed();

        st.counterValue = counterInput + 1;
        _dispatch(requests);
    }

    function counter() external view returns (uint256) {
        // Prevent unauthorized "fee griefing" calls via the guard forwarding path.
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        return _state().counterValue;
    }

    function _hashRequests(Request[] calldata requests) internal pure returns (bytes32) {
        bytes32 acc;
        for (uint256 i = 0; i < requests.length; i++) {
            bytes32 h = keccak256(abi.encode(requests[i].to, requests[i].value, keccak256(requests[i].data)));
            acc = keccak256(abi.encodePacked(acc, h));
        }
        return acc;
    }

    function _dispatch(Request[] calldata requests) internal {
        for (uint256 i = 0; i < requests.length; i++) {
            (bool ok, bytes memory ret) = requests[i].to.call{value: requests[i].value}(requests[i].data);
            if (!ok) revert ExecutionFailed(i, ret);
        }
        emit Dispatched(requests.length, msg.sender);
    }

    receive() external payable {}
}
