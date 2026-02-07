// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Public registry that stores (implAddress + extcodehash(impl)) keyed records:
/// - verdict: SAFE / UNSAFE / UNKNOWN
/// - name / summary / description / reasons: human-readable notes
/// - updatedAt: timestamp for last update
/// - a fixed-size ring buffer of the most recent impl updates (PoC convenience)
///
/// Also stores optional "swap compatibility" records keyed by (fromImpl, fromCodehash, toImpl, toCodehash).
contract ImplSafetyRegistry {
    enum Verdict {
        Unknown,
        Safe,
        Unsafe
    }

    struct Record {
        Verdict verdict;
        uint64 updatedAt;
        string name;
        string summary;
        string description;
        string reasons;
    }

    address public owner;
    mapping(address => bool) public publisher;
    mapping(bytes32 => Record) private _recordOf; // key = keccak256(impl, codehash)
    mapping(bytes32 => Record) private _swapRecordOf; // key = keccak256(fromImpl, fromCodehash, toImpl, toCodehash)

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
    event RecordUpdated(address indexed impl, bytes32 indexed codehash, Verdict verdict, uint64 updatedAt, address indexed by);
    event SwapRecordUpdated(address indexed fromImpl, address indexed toImpl, Verdict verdict, uint64 updatedAt, address indexed by);

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

    function setRecord(
        address impl,
        bytes32 codehash,
        Verdict verdict,
        string calldata name,
        string calldata summary,
        string calldata description,
        string calldata reasons
    ) external onlyPublisher {
        _setRecord(impl, codehash, verdict, name, summary, description, reasons);
    }

    function _setRecord(
        address impl,
        bytes32 codehash,
        Verdict verdict,
        string calldata name,
        string calldata summary,
        string calldata description,
        string calldata reasons
    ) internal {
        if (impl == address(0)) revert ZeroAddress();
        bytes32 k = _key(impl, codehash);
        Record storage r = _recordOf[k];
        r.verdict = verdict;
        r.updatedAt = uint64(block.timestamp);
        r.name = name;
        r.summary = summary;
        r.description = description;
        r.reasons = reasons;

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

        emit RecordUpdated(impl, codehash, verdict, r.updatedAt, msg.sender);
    }

    /// @notice Convenience helper: record for current extcodehash(impl)
    function setRecordCurrent(
        address impl,
        Verdict verdict,
        string calldata name,
        string calldata summary,
        string calldata description,
        string calldata reasons
    ) external onlyPublisher {
        bytes32 codehash = extcodehash(impl);
        _setRecord(impl, codehash, verdict, name, summary, description, reasons);
    }

    function getVerdict(address impl, bytes32 codehash) external view returns (Verdict) {
        return _recordOf[_key(impl, codehash)].verdict;
    }

    function getRecord(address impl, bytes32 codehash)
        external
        view
        returns (
            Verdict verdict,
            string memory name,
            string memory summary,
            string memory description,
            string memory reasons,
            uint64 updatedAt
        )
    {
        Record storage r = _recordOf[_key(impl, codehash)];
        return (r.verdict, r.name, r.summary, r.description, r.reasons, r.updatedAt);
    }

    function getRecordCurrent(address impl)
        external
        view
        returns (
            Verdict verdict,
            string memory name,
            string memory summary,
            string memory description,
            string memory reasons,
            uint64 updatedAt,
            bytes32 codehash
        )
    {
        codehash = extcodehash(impl);
        Record storage r = _recordOf[_key(impl, codehash)];
        return (r.verdict, r.name, r.summary, r.description, r.reasons, r.updatedAt, codehash);
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
        return _recordOf[_key(impl, codehash)].verdict == Verdict.Safe;
    }

    function isSafeCurrent(address impl) external view returns (bool) {
        bytes32 codehash = extcodehash(impl);
        return _recordOf[_key(impl, codehash)].verdict == Verdict.Safe;
    }

    function getRecentRecords()
        external
        view
        returns (
            address[] memory impls,
            bytes32[] memory codehashes,
            Verdict[] memory verdicts,
            uint64[] memory updatedAts,
            string[] memory names,
            string[] memory summaries,
            string[] memory descriptions,
            string[] memory reasonsList
        )
    {
        uint256 n = recentSize;
        impls = new address[](n);
        codehashes = new bytes32[](n);
        verdicts = new Verdict[](n);
        updatedAts = new uint64[](n);
        names = new string[](n);
        summaries = new string[](n);
        descriptions = new string[](n);
        reasonsList = new string[](n);

        if (n == 0) {
            return (impls, codehashes, verdicts, updatedAts, names, summaries, descriptions, reasonsList);
        }

        for (uint256 i = 0; i < n; i++) {
            uint256 idx;
            if (n == recentCap) {
                idx = recentCursor + recentCap - 1 - i;
                idx = idx % recentCap;
            } else {
                idx = n - 1 - i;
            }

            RecentPair storage p = _recent[idx];
            impls[i] = p.impl;
            codehashes[i] = p.codehash;

            Record storage r = _recordOf[_key(p.impl, p.codehash)];
            verdicts[i] = r.verdict;
            updatedAts[i] = r.updatedAt;
            names[i] = r.name;
            summaries[i] = r.summary;
            descriptions[i] = r.description;
            reasonsList[i] = r.reasons;
        }

        return (impls, codehashes, verdicts, updatedAts, names, summaries, descriptions, reasonsList);
    }

    // -------------------------
    // Swap compatibility records
    // -------------------------

    function _swapKey(address fromImpl, bytes32 fromCodehash, address toImpl, bytes32 toCodehash)
        internal
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(fromImpl, fromCodehash, toImpl, toCodehash));
    }

    function setSwapRecord(
        address fromImpl,
        bytes32 fromCodehash,
        address toImpl,
        bytes32 toCodehash,
        Verdict verdict,
        string calldata name,
        string calldata summary,
        string calldata description,
        string calldata reasons
    ) external onlyPublisher {
        _setSwapRecord(fromImpl, fromCodehash, toImpl, toCodehash, verdict, name, summary, description, reasons);
    }

    function setSwapRecordCurrent(
        address fromImpl,
        address toImpl,
        Verdict verdict,
        string calldata name,
        string calldata summary,
        string calldata description,
        string calldata reasons
    ) external onlyPublisher {
        bytes32 fromCodehash = extcodehash(fromImpl);
        bytes32 toCodehash = extcodehash(toImpl);
        _setSwapRecord(fromImpl, fromCodehash, toImpl, toCodehash, verdict, name, summary, description, reasons);
    }

    function _setSwapRecord(
        address fromImpl,
        bytes32 fromCodehash,
        address toImpl,
        bytes32 toCodehash,
        Verdict verdict,
        string calldata name,
        string calldata summary,
        string calldata description,
        string calldata reasons
    ) internal {
        if (fromImpl == address(0) || toImpl == address(0)) revert ZeroAddress();
        bytes32 k = _swapKey(fromImpl, fromCodehash, toImpl, toCodehash);
        Record storage r = _swapRecordOf[k];
        r.verdict = verdict;
        r.updatedAt = uint64(block.timestamp);
        r.name = name;
        r.summary = summary;
        r.description = description;
        r.reasons = reasons;
        emit SwapRecordUpdated(fromImpl, toImpl, verdict, r.updatedAt, msg.sender);
    }

    function getSwapRecord(address fromImpl, bytes32 fromCodehash, address toImpl, bytes32 toCodehash)
        external
        view
        returns (
            Verdict verdict,
            string memory name,
            string memory summary,
            string memory description,
            string memory reasons,
            uint64 updatedAt
        )
    {
        Record storage r = _swapRecordOf[_swapKey(fromImpl, fromCodehash, toImpl, toCodehash)];
        return (r.verdict, r.name, r.summary, r.description, r.reasons, r.updatedAt);
    }

    function getSwapRecordCurrent(address fromImpl, address toImpl)
        external
        view
        returns (
            Verdict verdict,
            string memory name,
            string memory summary,
            string memory description,
            string memory reasons,
            uint64 updatedAt,
            bytes32 fromCodehash,
            bytes32 toCodehash
        )
    {
        fromCodehash = extcodehash(fromImpl);
        toCodehash = extcodehash(toImpl);
        Record storage r = _swapRecordOf[_swapKey(fromImpl, fromCodehash, toImpl, toCodehash)];
        return (r.verdict, r.name, r.summary, r.description, r.reasons, r.updatedAt, fromCodehash, toCodehash);
    }

    function extcodehash(address a) public view returns (bytes32 h) {
        assembly {
            h := extcodehash(a)
        }
    }
}
