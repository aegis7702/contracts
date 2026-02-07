// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Service-side fee policy registry.
/// @dev Guard queries this contract at runtime to get fee settings for each wallet.
contract AegisFeePolicy {
    struct FeeConfig {
        address token;
        address recipient;
        uint256 feePerCall;
        bool hasCustomConfig;
    }

    address public owner;
    mapping(address => bool) public operator;

    FeeConfig private _defaultConfig;
    mapping(address => FeeConfig) private _walletConfig;

    error NotOwner();
    error NotOperator();
    error ZeroAddress();

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event OperatorSet(address indexed operator, bool allowed);
    event DefaultFeeConfigSet(address indexed token, address indexed recipient, uint256 feePerCall);
    event WalletFeeConfigSet(address indexed wallet, address indexed token, address indexed recipient, uint256 feePerCall);
    event WalletFeeConfigCleared(address indexed wallet);

    constructor(address token, address recipient, uint256 feePerCall) {
        owner = msg.sender;
        operator[msg.sender] = true;
        _defaultConfig = FeeConfig({
            token: token,
            recipient: recipient,
            feePerCall: feePerCall,
            hasCustomConfig: false
        });

        emit OwnershipTransferred(address(0), msg.sender);
        emit OperatorSet(msg.sender, true);
        emit DefaultFeeConfigSet(token, recipient, feePerCall);
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyOperator() {
        if (!operator[msg.sender]) revert NotOperator();
        _;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setOperator(address who, bool allowed) external onlyOwner {
        if (who == address(0)) revert ZeroAddress();
        operator[who] = allowed;
        emit OperatorSet(who, allowed);
    }

    function setDefaultFeeConfig(address token, address recipient, uint256 feePerCall) external onlyOperator {
        _defaultConfig.token = token;
        _defaultConfig.recipient = recipient;
        _defaultConfig.feePerCall = feePerCall;
        emit DefaultFeeConfigSet(token, recipient, feePerCall);
    }

    function setWalletFeeConfig(address wallet, address token, address recipient, uint256 feePerCall) external onlyOperator {
        if (wallet == address(0)) revert ZeroAddress();
        _walletConfig[wallet] = FeeConfig({
            token: token,
            recipient: recipient,
            feePerCall: feePerCall,
            hasCustomConfig: true
        });
        emit WalletFeeConfigSet(wallet, token, recipient, feePerCall);
    }

    function clearWalletFeeConfig(address wallet) external onlyOperator {
        if (wallet == address(0)) revert ZeroAddress();
        delete _walletConfig[wallet];
        emit WalletFeeConfigCleared(wallet);
    }

    function getFeeConfig(address wallet) external view returns (address token, address recipient, uint256 feePerCall) {
        FeeConfig storage cfg = _walletConfig[wallet];
        if (cfg.hasCustomConfig) {
            return (cfg.token, cfg.recipient, cfg.feePerCall);
        }
        return (_defaultConfig.token, _defaultConfig.recipient, _defaultConfig.feePerCall);
    }
}
