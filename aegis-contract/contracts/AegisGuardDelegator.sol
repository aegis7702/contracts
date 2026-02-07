// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ImplSafetyRegistry} from "./ImplSafetyRegistry.sol";
import {AegisFeePolicy} from "./AegisFeePolicy.sol";

/// @notice 7702 delegator/guard that:
/// 1) stores minimal per-wallet state in the EOA's storage using hash slots (EIP-1967 + ERC-7201)
/// 2) checks (impl + codehash) against ImplSafetyRegistry
/// 3) charges fee (ERC20) per forwarded execution via external fee policy
/// 4) delegates all unknown calls to active implementation
///
/// Design constraints (per request):
/// - Guard enforcement uses ONLY (implAddress + codehash) -> safe/unsafe from the registry
/// - Freeze/recovery are per-wallet (stored in the delegated account's storage)
/// - Guard keeps state minimal; uses standardized hash slots to avoid collisions
contract AegisGuardDelegator {
    // -------------------------
    // Errors
    // -------------------------
    error Frozen(string reason);
    error NotSelf();
    error NotRecovery();
    error NotSentinel();
    error UnsafeImplementation(address impl, bytes32 codehash);
    error ImplementationNotSet();
    error FeeTransferFailed();
    error IllegalConfigMutation();
    error IllegalImplementationMutation();
    error ZeroAddress();

    // -------------------------
    // Events
    // -------------------------
    event AegisInitialized(address indexed wallet, address indexed impl, address recovery, address sentinel);
    event ImplementationSet(address indexed wallet, address indexed impl, bytes32 codehash);
    event FrozenSet(address indexed wallet, string reason, address indexed by);
    event Unfrozen(address indexed wallet, address indexed by);
    event RecoverySet(address indexed wallet, address indexed recovery);
    event SentinelSet(address indexed wallet, address indexed sentinel);
    event FeePaid(address indexed wallet, address indexed token, address indexed recipient, uint256 amount);
    event Forwarded(address indexed wallet, address indexed impl, bytes4 indexed selector, bool success);
    event ImplementationForceSet(address indexed wallet, address indexed impl, bytes32 codehash);
    event ForcedExecution(address indexed wallet, address indexed impl, bytes4 indexed selector, bool success);

    // -------------------------
    // Registry (global)
    // -------------------------
    ImplSafetyRegistry public immutable registry;
    AegisFeePolicy public immutable feePolicy;

    constructor(address registryAddress, address feePolicyAddress) {
        if (registryAddress == address(0)) revert ZeroAddress();
        if (feePolicyAddress == address(0)) revert ZeroAddress();
        registry = ImplSafetyRegistry(registryAddress);
        feePolicy = AegisFeePolicy(feePolicyAddress);
    }

    // -------------------------
    // Storage slots
    // -------------------------

    /// @dev EIP-1967 implementation slot = bytes32(uint256(keccak256('eip1967.proxy.implementation')) - 1)
    bytes32 internal constant _IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    /// @dev ERC-7201 namespaced storage location for guard config.
    /// Namespace: "aegis7702.guard.storage"
    /// Computed as: keccak256(abi.encode(uint256(keccak256(namespace)) - 1)) & ~bytes32(uint256(0xff))
    bytes32 internal constant _CONFIG_SLOT =
        0x4b23459f0a84a2f955d2d9b2345fb64bea4d124b563876511bd09b5967836b00;

    struct GuardConfig {
        bool frozen;
        address recovery;
        address sentinel;
        string freezeReason;
        uint256 configNonce; // reserved for future (e.g., signed meta-tx), PoC uses for replay protection if needed
    }

    function _config() internal pure returns (GuardConfig storage cfg) {
        bytes32 slot = _CONFIG_SLOT;
        assembly {
            cfg.slot := slot
        }
    }

    function _getImplementation() internal view returns (address impl) {
        bytes32 slot = _IMPLEMENTATION_SLOT;
        assembly {
            impl := sload(slot)
        }
    }

    function _setImplementation(address impl) internal {
        bytes32 slot = _IMPLEMENTATION_SLOT;
        assembly {
            sstore(slot, impl)
        }
    }

    // -------------------------
    // Auth helpers
    // -------------------------

    /// @dev In the common 7702 UX, the wallet sends a tx to itself. Then msg.sender == address(this).
    function _requireSelf() internal view {
        if (msg.sender != address(this)) revert NotSelf();
    }

    function _requireRecovery() internal view {
        GuardConfig storage cfg = _config();
        if (msg.sender != cfg.recovery) revert NotRecovery();
    }

    function _requireSentinelOrSelf() internal view {
        GuardConfig storage cfg = _config();
        if (msg.sender != address(this) && msg.sender != cfg.sentinel) revert NotSentinel();
    }

    // -------------------------
    // Public view helpers
    // -------------------------

    function aegis_getImplementation() external view returns (address) {
        return _getImplementation();
    }

    function aegis_isFrozen() external view returns (bool) {
        return _config().frozen;
    }

    function aegis_getFreezeReason() external view returns (string memory) {
        return _config().freezeReason;
    }

    function aegis_getRecovery() external view returns (address) {
        return _config().recovery;
    }

    function aegis_getSentinel() external view returns (address) {
        return _config().sentinel;
    }

    function aegis_getFeeConfig() external view returns (address token, address recipient, uint256 feePerCall) {
        return feePolicy.getFeeConfig(address(this));
    }

    function aegis_version() external pure returns (string memory) {
        return "AegisGuardDelegator/0.1";
    }

    // -------------------------
    // Wallet setup / controls
    // -------------------------

    /// @notice Initialize per-wallet configuration.
    /// @dev Must be called by the wallet itself (self-call). Front-run resistant in PoC because attacker can't make msg.sender == address(this).
    function aegis_init(
        address impl,
        address recovery,
        address sentinel
    ) external {
        _requireSelf();

        // only allow first-time init
        GuardConfig storage cfg = _config();
        if (cfg.recovery != address(0) || _getImplementation() != address(0)) {
            // already initialized; keep this strict for PoC
            revert IllegalConfigMutation();
        }

        if (recovery == address(0)) revert ZeroAddress();

        cfg.recovery = recovery;
        cfg.sentinel = sentinel;
        cfg.frozen = false;
        cfg.freezeReason = "";

        _setImplementationChecked(impl);

        emit AegisInitialized(address(this), impl, recovery, sentinel);
    }

    /// @notice Change active implementation. Only self-call, and only when not frozen.
    function aegis_setImplementation(address impl) external {
        GuardConfig storage cfg = _config();
        if (cfg.frozen) revert Frozen(cfg.freezeReason);
        _requireSelf();
        _setImplementationChecked(impl);
    }

    /// @notice Force set active implementation without registry SAFE check.
    /// @dev Self-call only; intended for controlled exceptional flows.
    function aegis_forceSetImplementation(address impl) external {
        GuardConfig storage cfg = _config();
        if (cfg.frozen) revert Frozen(cfg.freezeReason);
        _requireSelf();
        _setImplementationUnchecked(impl);
    }

    function _setImplementationChecked(address impl) internal {
        if (impl == address(0)) revert ZeroAddress();
        bytes32 codehash = registry.extcodehash(impl);
        bool ok = registry.isSafe(impl, codehash);
        if (!ok) revert UnsafeImplementation(impl, codehash);
        _setImplementation(impl);
        emit ImplementationSet(address(this), impl, codehash);
    }

    function _setImplementationUnchecked(address impl) internal {
        if (impl == address(0)) revert ZeroAddress();
        bytes32 codehash = registry.extcodehash(impl);
        _setImplementation(impl);
        emit ImplementationForceSet(address(this), impl, codehash);
    }

    /// @notice Set/rotate recovery.
    /// - If NOT frozen: only self-call
    /// - If frozen: only current recovery (so compromised main key can't rotate recovery)
    function aegis_setRecovery(address newRecovery) external {
        if (newRecovery == address(0)) revert ZeroAddress();
        GuardConfig storage cfg = _config();
        if (cfg.frozen) {
            _requireRecovery();
        } else {
            _requireSelf();
        }
        cfg.recovery = newRecovery;
        emit RecoverySet(address(this), newRecovery);
    }

    /// @notice Set sentinel address (optional). Only self-call and only when not frozen.
    function aegis_setSentinel(address newSentinel) external {
        GuardConfig storage cfg = _config();
        if (cfg.frozen) revert Frozen(cfg.freezeReason);
        _requireSelf();
        cfg.sentinel = newSentinel;
        emit SentinelSet(address(this), newSentinel);
    }

    /// @notice Freeze wallet (blocks forwarding). Callable by self-call OR configured sentinel.
    function aegis_freeze(string calldata reason) external {
        _requireSentinelOrSelf();
        GuardConfig storage cfg = _config();
        cfg.frozen = true;
        cfg.freezeReason = reason;
        emit FrozenSet(address(this), reason, msg.sender);
    }

    /// @notice Unfreeze wallet. Only recovery can unfreeze.
    function aegis_unfreeze() external {
        _requireRecovery();
        GuardConfig storage cfg = _config();
        cfg.frozen = false;
        cfg.freezeReason = "";
        emit Unfrozen(address(this), msg.sender);
    }

    /// @notice Force execute current implementation calldata without SAFE check.
    /// @dev Reverts on delegatecall failure (same revert behavior as fallback path).
    function aegis_forceExecute(bytes calldata implCalldata) external payable returns (bool success, bytes memory returndata) {
        (address implBefore, bool ok, bytes memory ret) = _executeEntry(implCalldata, false);
        emit ForcedExecution(address(this), implBefore, _selectorFromPayload(implCalldata), ok);
        return (ok, ret);
    }

    function _executeEntry(
        bytes calldata payload,
        bool enforceSafeCheck
    ) internal returns (address implBefore, bool success, bytes memory returndata) {
        GuardConfig storage cfg = _config();
        if (cfg.frozen) revert Frozen(cfg.freezeReason);

        address impl = _getImplementation();
        if (impl == address(0)) revert ImplementationNotSet();
        implBefore = impl;
        (success, returndata) = _executeWithPolicy(impl, payload, enforceSafeCheck);
    }

    function _selectorFromPayload(bytes calldata payload) internal pure returns (bytes4 selector) {
        if (payload.length < 4) return bytes4(0);
        assembly {
            selector := calldataload(payload.offset)
        }
    }

    // -------------------------
    // Forwarding
    // -------------------------

    receive() external payable {
        // accept ETH
    }

    fallback() external payable {
        (address implBefore, bool success, bytes memory returndata) = _executeEntry(msg.data, true);
        emit Forwarded(address(this), implBefore, _selectorFromPayload(msg.data), success);

        // bubble return data
        assembly {
            return(add(returndata, 0x20), mload(returndata))
        }
    }

    function _chargeFee() internal {
        (address token, address recipient, uint256 feePerCall) = feePolicy.getFeeConfig(address(this));
        if (token == address(0) || recipient == address(0) || feePerCall == 0) {
            return;
        }
        (bool ok, bytes memory ret) = token.call(
            abi.encodeWithSignature("transfer(address,uint256)", recipient, feePerCall)
        );
        if (!ok) revert FeeTransferFailed();
        if (ret.length > 0) {
            // ERC20 transfer should return bool; if provided, require true
            if (!abi.decode(ret, (bool))) revert FeeTransferFailed();
        }

        emit FeePaid(address(this), token, recipient, feePerCall);
    }

    function _executeWithPolicy(
        address impl,
        bytes calldata payload,
        bool enforceSafeCheck
    ) internal returns (bool success, bytes memory returndata) {
        GuardConfig storage cfg = _config();

        if (enforceSafeCheck) {
            bytes32 codehash = registry.extcodehash(impl);
            if (!registry.isSafe(impl, codehash)) {
                revert UnsafeImplementation(impl, codehash);
            }
        }

        bool frozenBefore = cfg.frozen;
        address recoveryBefore = cfg.recovery;
        address sentinelBefore = cfg.sentinel;
        uint256 nonceBefore = cfg.configNonce;
        bytes32 freezeReasonHashBefore = keccak256(bytes(cfg.freezeReason));
        address implBefore = impl;

        _chargeFee();
        (success, returndata) = impl.delegatecall(payload);

        if (!success) {
            assembly {
                revert(add(returndata, 0x20), mload(returndata))
            }
        }

        if (
            _getImplementation() != implBefore ||
            cfg.frozen != frozenBefore ||
            cfg.recovery != recoveryBefore ||
            cfg.sentinel != sentinelBefore ||
            cfg.configNonce != nonceBefore ||
            keccak256(bytes(cfg.freezeReason)) != freezeReasonHashBefore
        ) {
            cfg.frozen = true;
            cfg.freezeReason = "Aegis:mutation-detected";
            emit FrozenSet(address(this), cfg.freezeReason, msg.sender);
        }
    }

}
