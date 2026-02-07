# ModuleB7702

## Summary
- Access-rule based execution implementation.
- Supports per-caller rule constraints (time, target, selector, value cap).

## Public Functions
- `setRule(address key, AccessRule rule)`
- `disableRule(address key)`
- `ruleOf(address key)`
- `dispatch(Request[] requests)`

## Evaluation Note
- Baseline implementation with explicit, on-chain scoped authorization:
  - Only self-call can configure rules (`setRule`/`disableRule`).
  - External callers can execute only within their rule constraints.
- View helper(s) are self-call only to avoid unauthorized fee griefing through the guard forwarding path.
- Registry classification target (common criterion: unauthorized 3rd-party DoS/arbitrary-exec): `SAFE`.
