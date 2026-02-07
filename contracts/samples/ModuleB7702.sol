pragma solidity ^0.8.24;

contract ModuleB7702 {
    bytes32 internal constant _STATE_SLOT =
        0xd685b998788cd167fd06a067711486d75f523bb6eeb8871bfc240b29e112a900;

    struct AccessRule {
        uint64 start;
        uint64 end;
        address target;
        bytes4 selector;
        uint256 maxValue;
        bool enabled;
    }

    struct LocalState {
        mapping(address => AccessRule) rules;
    }

    struct Request {
        address to;
        uint256 value;
        bytes data;
    }

    error UnauthorizedCaller();
    error RuleMissing();
    error RuleExpired();
    error RuleNotStarted();
    error TargetMismatch(address to);
    error SelectorMismatch(bytes4 got);
    error ValueExceeded(uint256 got, uint256 max);
    error ExecutionFailed(uint256 index, bytes returndata);

    event RuleUpdated(address indexed key, AccessRule rule);
    event RuleDisabled(address indexed key);
    event Dispatched(uint256 count, address indexed caller);

    function _state() internal pure returns (LocalState storage st) {
        bytes32 slot = _STATE_SLOT;
        assembly {
            st.slot := slot
        }
    }

    function setRule(address key, AccessRule calldata rule) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        LocalState storage st = _state();
        st.rules[key] = AccessRule({
            start: rule.start,
            end: rule.end,
            target: rule.target,
            selector: rule.selector,
            maxValue: rule.maxValue,
            enabled: true
        });
        emit RuleUpdated(key, st.rules[key]);
    }

    function disableRule(address key) external {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        LocalState storage st = _state();
        st.rules[key].enabled = false;
        emit RuleDisabled(key);
    }

    function ruleOf(address key) external view returns (AccessRule memory) {
        if (msg.sender != address(this)) revert UnauthorizedCaller();
        return _state().rules[key];
    }

    function dispatch(Request[] calldata requests) external payable {
        if (msg.sender == address(this)) {
            _dispatch(requests);
            return;
        }

        AccessRule memory rule = _state().rules[msg.sender];
        if (!rule.enabled) revert RuleMissing();
        if (rule.start != 0 && block.timestamp < rule.start) revert RuleNotStarted();
        if (rule.end != 0 && block.timestamp > rule.end) revert RuleExpired();

        for (uint256 i = 0; i < requests.length; i++) {
            if (rule.target != address(0) && requests[i].to != rule.target) {
                revert TargetMismatch(requests[i].to);
            }
            if (rule.maxValue != 0 && requests[i].value > rule.maxValue) {
                revert ValueExceeded(requests[i].value, rule.maxValue);
            }
            if (rule.selector != bytes4(0)) {
                bytes calldata d = requests[i].data;
                bytes4 sel;
                if (d.length >= 4) {
                    assembly {
                        sel := calldataload(d.offset)
                    }
                }
                if (sel != rule.selector) revert SelectorMismatch(sel);
            }
        }

        _dispatch(requests);
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
