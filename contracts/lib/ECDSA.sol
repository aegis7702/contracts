// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal ECDSA helpers (PoC). Inspired by OpenZeppelin.
library ECDSA {
    error InvalidSignatureLength(uint256 len);
    error InvalidSignatureS();
    error InvalidSignatureV(uint8 v);

    function toEthSignedMessageHash(bytes32 hash) internal pure returns (bytes32) {
        // 32-byte message prefix
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", hash));
    }

    function recover(bytes32 hash, bytes memory signature) internal pure returns (address) {
        if (signature.length != 65) {
            revert InvalidSignatureLength(signature.length);
        }

        bytes32 r;
        bytes32 s;
        uint8 v;
        // solhint-disable-next-line no-inline-assembly
        assembly {
            r := mload(add(signature, 0x20))
            s := mload(add(signature, 0x40))
            v := byte(0, mload(add(signature, 0x60)))
        }

        // EIP-2: s must be in the lower half order
        // secp256k1n/2:
        bytes32 maxS = 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;
        if (uint256(s) > uint256(maxS)) {
            revert InvalidSignatureS();
        }
        if (v != 27 && v != 28) {
            revert InvalidSignatureV(v);
        }

        address signer = ecrecover(hash, v, r, s);
        return signer;
    }
}
