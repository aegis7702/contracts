// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Public registry that stores:
/// - (implAddress + codehash) -> SAFE/UNSAFE
/// - (optional) a human-readable reason string for the verdict (PoC convenience)
/// - a fixed-size ring buffer of the most recent updates (PoC convenience)
contract ImplSafetyRegistry {
    enum Verdict {
        Unknown,
        Safe,
        Unsafe
    }

    address public owner;
    mapping(address => bool) public publisher;
    mapping(bytes32 => Verdict) private _verdictOf; // key = keccak256(impl, codehash)
    mapping(bytes32 => string) private _reasonOf; // key = keccak256(impl, codehash)

    uint256 public immutable recentCap;
    uint256 public recentCursor;
    uint256 public recentSize;

    struct RecentPair {
        address impl;
        bytes32 codehash;
    }

    mapping(uint256 => RecentPair) private _recent; // ring buffer

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PublisherSet(address indexed publisher, bool allowed);
    event VerdictUpdated(address indexed impl, bytes32 indexed codehash, Verdict verdict, string reason, address indexed by);

    error NotOwner();
    error NotPublisher();
    error ZeroAddress();
    error InvalidRecentCap(uint256 cap);
    error IndexOutOfBounds(uint256 index, uint256 size);

    constructor(uint256 recentCap_) {
        uint256 cap = recentCap_ == 0 ? 5 : recentCap_;
        if (cap > 64) revert InvalidRecentCap(cap);
        recentCap = cap;

        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
        publisher[msg.sender] = true;
        emit PublisherSet(msg.sender, true);
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyPublisher() {
        if (!publisher[msg.sender]) revert NotPublisher();
        _;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setPublisher(address who, bool allowed) external onlyOwner {
        if (who == address(0)) revert ZeroAddress();
        publisher[who] = allowed;
        emit PublisherSet(who, allowed);
    }

    function _key(address impl, bytes32 codehash) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(impl, codehash));
    }

    function setVerdict(address impl, bytes32 codehash, Verdict verdict, string calldata reason) external onlyPublisher {
        _setVerdict(impl, codehash, verdict, reason);
    }

    function _setVerdict(address impl, bytes32 codehash, Verdict verdict, string calldata reason) internal {
        bytes32 k = _key(impl, codehash);
        _verdictOf[k] = verdict;
        _reasonOf[k] = reason;

        uint256 idx = recentCursor;
        _recent[idx] = RecentPair({impl: impl, codehash: codehash});
        unchecked {
            idx += 1;
        }
        if (idx == recentCap) idx = 0;
        recentCursor = idx;
        if (recentSize < recentCap) {
            recentSize += 1;
        }

        emit VerdictUpdated(impl, codehash, verdict, reason, msg.sender);
    }

    /// @notice Convenience helper: record verdict for current extcodehash(impl)
    function setVerdictCurrent(address impl, Verdict verdict, string calldata reason) external onlyPublisher {
        bytes32 codehash = extcodehash(impl);
        _setVerdict(impl, codehash, verdict, reason);
    }

    function getVerdict(address impl, bytes32 codehash) external view returns (Verdict) {
        return _verdictOf[_key(impl, codehash)];
    }

    function getReason(address impl, bytes32 codehash) external view returns (string memory) {
        return _reasonOf[_key(impl, codehash)];
    }

    function getRecord(address impl, bytes32 codehash) external view returns (Verdict verdict, string memory reason) {
        bytes32 k = _key(impl, codehash);
        return (_verdictOf[k], _reasonOf[k]);
    }

    function getRecentPairs()
        external
        view
        returns (address[] memory impls, bytes32[] memory codehashes)
    {
        uint256 n = recentSize;
        impls = new address[](n);
        codehashes = new bytes32[](n);

        if (n == 0) return (impls, codehashes);

        if (n == recentCap) {
            for (uint256 i = 0; i < n; i++) {
                uint256 idx = recentCursor + recentCap - 1 - i;
                idx = idx % recentCap;
                RecentPair storage p = _recent[idx];
                impls[i] = p.impl;
                codehashes[i] = p.codehash;
            }
            return (impls, codehashes);
        }

        for (uint256 i = 0; i < n; i++) {
            uint256 idx = n - 1 - i;
            RecentPair storage p = _recent[idx];
            impls[i] = p.impl;
            codehashes[i] = p.codehash;
        }
        return (impls, codehashes);
    }

    function getRecentPairAt(uint256 index) external view returns (address impl, bytes32 codehash) {
        uint256 n = recentSize;
        if (index >= n) revert IndexOutOfBounds(index, n);

        uint256 idx;
        if (n == recentCap) {
            // newest-first; index 0 == most recent
            idx = recentCursor + recentCap - 1 - index;
            idx = idx % recentCap;
        } else {
            // not wrapped yet; newest sits at (n - 1)
            idx = n - 1 - index;
        }

        RecentPair storage p = _recent[idx];
        return (p.impl, p.codehash);
    }

    function isSafe(address impl, bytes32 codehash) external view returns (bool) {
        return _verdictOf[_key(impl, codehash)] == Verdict.Safe;
    }

    function isSafeCurrent(address impl) external view returns (bool) {
        bytes32 codehash = extcodehash(impl);
        return _verdictOf[_key(impl, codehash)] == Verdict.Safe;
    }

    function extcodehash(address a) public view returns (bytes32 h) {
        assembly {
            h := extcodehash(a)
        }
    }
}
