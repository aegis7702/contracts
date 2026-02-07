// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Public registry that stores ONLY: (implAddress + codehash) -> SAFE/UNSAFE
/// @dev This is intentionally minimal per the design requirement.
contract ImplSafetyRegistry {
    enum Verdict {
        Unknown,
        Safe,
        Unsafe
    }

    address public owner;
    mapping(address => bool) public publisher;
    mapping(bytes32 => Verdict) private _verdictOf; // key = keccak256(impl, codehash)

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PublisherSet(address indexed publisher, bool allowed);
    event VerdictUpdated(address indexed impl, bytes32 indexed codehash, Verdict verdict, address indexed by);

    error NotOwner();
    error NotPublisher();
    error ZeroAddress();

    constructor() {
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

    function setVerdict(address impl, bytes32 codehash, Verdict verdict) external onlyPublisher {
        bytes32 k = _key(impl, codehash);
        _verdictOf[k] = verdict;
        emit VerdictUpdated(impl, codehash, verdict, msg.sender);
    }

    /// @notice Convenience helper: record verdict for current extcodehash(impl)
    function setVerdictCurrent(address impl, Verdict verdict) external onlyPublisher {
        bytes32 codehash = extcodehash(impl);
        bytes32 k = _key(impl, codehash);
        _verdictOf[k] = verdict;
        emit VerdictUpdated(impl, codehash, verdict, msg.sender);
    }

    function getVerdict(address impl, bytes32 codehash) external view returns (Verdict) {
        return _verdictOf[_key(impl, codehash)];
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
