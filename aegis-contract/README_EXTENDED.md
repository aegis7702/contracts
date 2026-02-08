# 7702-Aegis PoC - README (Extended)

> **Summary**: This project is a PoC for **EIP-7702 (Prague/Pectra)**. It reproduces a common real-world constraint ("I want to delegate my EOA to an arbitrary 7702 implementation, but wallets block it because it's risky") and demonstrates one practical approach: **Aegis Guard (Delegator) + Impl Safety Registry + AegisFeePolicy**.
>
> - **Registry**: a shared on-chain DB that stores a **SAFE / UNSAFE / UNKNOWN** verdict for each `(implAddress, extcodehash)` pair (plus human-readable notes)
> - **Guard (Delegator)**: a gatekeeper executed via 7702 delegation from an EOA.
>   - Minimal per-wallet state (impl, freeze, recovery, sentinel) is stored in **EOA storage**
>   - On every call, it checks the Registry and `delegatecall`s into the impl only if SAFE
>   - On every execution, it queries FeePolicy and automatically charges a token fee (top-up model)
> - **FeePolicy**: managed by the service operator to configure per-wallet fees
> - **Sample impls**: demonstrates benign/malicious patterns, storage-collision DoS, centralization risk, etc.

**WARNING**
- This repository is for learning/demo/hackathon PoC purposes.
- Some sample implementations intentionally contain dangerous patterns.
- Do not use on mainnet. Use only in local/test environments.

---

## 1) Why this project exists?

### 1.1 What EIP-7702 enables
EIP-7702 provides a delegation mechanism that lets a traditional EOA (regular wallet address) behave like a smart contract wallet.

- Users can keep their existing EOA address, while
- gaining smart-wallet UX such as batched execution, session keys, gas abstraction, etc.

### 1.2 Why wallets block "delegate to arbitrary implementations"
In EIP-7702, "which implementation you delegate to" is effectively an upgrade that changes the wallet logic itself.

- Delegating to a malicious implementation can keep the wallet in a compromised state, and
- even experienced engineers cannot instantly verify storage collisions, initialization paths, and hidden privilege routes across implementations.

So in practice, many wallets avoid exposing an "arbitrary 7702 implementation delegation" UI.

### 1.3 The Aegis approach
Aegis takes the approach of "verify, then allow" instead of "block everything".

- Enforce that only "safe implementations" can execute via an on-chain Registry verdict
- The Guard checks the Registry on every execution: run if SAFE, otherwise block
- Execution is charged via a top-up ERC20 fee token (infra cost)

This PoC implements the core skeleton with "contracts + scripts + tests".

---

## 2) Architecture at a glance

### 2.1 Components

- **EOA Wallet Address**: the user's existing wallet address
- **AegisGuardDelegator**: the "gatekeeper/proxy" that the EOA delegates to via 7702
- **ImplSafetyRegistry**: stores `(implAddress, codehash) -> SAFE/UNSAFE/UNKNOWN` (plus notes)
- **AegisFeePolicy**: contract that lets the operator manage per-wallet fee policy
- **Implementation (impl)**: contract providing actual wallet features (batch execution, etc.)
- **AegisToken (AGT)**: PoC top-up fee token (ERC20)

### 2.2 Call flow (conceptual)

```text
User (EOA) address = W

(1) Install 7702 delegation: W.code = 0xef0100 || Guard

(2) After that, whenever anyone sends a CALL/Tx to W,
    W executes the Guard code (in W's storage context)

W (delegated)
  └─ Guard.fallback()
      ├─ (A) wallet frozen? (W.storage)
      ├─ (B) active impl? (W.storage)
      ├─ (C) Check Registry: is (impl, extcodehash(impl)) SAFE?
      ├─ (D) Query FeePolicy and transfer the fee token
      └─ (E) delegatecall into the impl (forward msg.data as-is)

Because the impl executes in W's context:
- address(this) == W
- storage == W.storage
- token/ETH movements happen from W's assets
```

---

## 3) Key idea: enforcing safety with minimal Guard state

### 3.1 The Registry stores only the impl safety verdict (and notes)
`ImplSafetyRegistry.sol` keeps the Guard's enforcement input to one thing: a verdict keyed by `(implAddress, extcodehash(impl))`.

- What the Guard enforces:
  - `(implAddress, extcodehash(impl)) -> Verdict(Safe/Unsafe/Unknown)`
- PoC convenience metadata (not used by the Guard for enforcement):
  - `name`, `summary`, `description`, `reasons` (human-readable notes)
  - `updatedAt` timestamp for the last update
  - a fixed-size ring buffer of the most recent impl updates (default N=5; deployment scripts use 5)

It does not store per-wallet state (selected impl, freeze flag, recovery key).

Why:
- The Registry should remain a shared dataset (reusable by other wallets/projects), and
- per-wallet state (freeze/recovery key) should be independent per wallet.

The Registry has a `publisher` allowlist; in this PoC, the deployer records verdicts.

It also stores optional "swap compatibility" records keyed by `(fromImpl, fromCodehash, toImpl, toCodehash)` (useful for "swap" audits).

### 3.2 The Guard's per-wallet state is stored in "EOA storage"
In EIP-7702, when the Guard code executes, the storage context is not the Guard contract address; it's the delegating EOA `W`.

So if the Guard stores any state, it ends up in **`W.storage`**.

In this PoC, the Guard keeps state minimal:
- active implementation address
- frozen flag + reason
- recovery address
- sentinel address (optional)
- fee config is not stored in Guard storage; it is read from `AegisFeePolicy`

### 3.3 "Standard hashed slots" to reduce storage collisions
EOA storage is shared by impls as well (because the impl executes via `delegatecall`).

So the Guard:
- stores the implementation address in the **EIP-1967 implementation slot**
- stores the remaining config in **ERC-7201 namespaced storage hashed slots**

It does not use low slots like slot0.

> Important: This reduces the chance of collisions for "Guard-owned state", but it cannot "remap" arbitrary impl storage accesses.
> In the EVM, a proxy cannot intercept `SSTORE/SLOAD` and rewrite slots.
> If an impl writes to random slots (e.g., slot0), the correct approach is to treat it as risky and mark it UNSAFE.

### 3.4 How the Guard prevents impl "config tampering" (immutability checks)
Because the impl executes via `delegatecall`, a malicious impl could try to modify the Guard's config slots, e.g.:
- change the recovery address
- change the active impl

This PoC Guard mitigates that by:

1. Taking a snapshot of `GuardConfig` and the active impl before `delegatecall`
2. Comparing the state after `delegatecall`
3. Freezing the wallet immediately if anything changed

This way, attempts to mutate Guard config/impl during execution get detected and the wallet is frozen to prevent further damage.

---

## 4) Fee model (top-up token charging)

`AegisGuardDelegator` performs the following on every forwarding (impl execution):

- Query fee config via `AegisFeePolicy.getFeeConfig(wallet)`
- Execute `feeToken.transfer(feeRecipient, feePerCall)` via a low-level call
- If any of `feeToken` / `feeRecipient` / `feePerCall` is zero, fees are disabled

### Important properties
- The fee transfer is attempted before `delegatecall`, but
- in the `fallback` / `aegis_forceExecute` path, any `delegatecall` revert bubbles up, so in failure cases the fee transfer is rolled back as well.

However, if the Guard reverts before `delegatecall` (e.g., frozen state, unset impl, UNSAFE impl, fee transfer failure), the transaction fails.

---

## 5) Freeze / Recovery / Sentinel (per-wallet)

In this project, freeze/recovery operate per wallet `W` (not in the shared Registry).

### 5.1 Freeze
- `aegis_freeze(string reason)`
  - caller: **self-call** (the wallet sends a tx to itself) or **sentinel**
  - when frozen, the Guard blocks all forwarding

### 5.2 Unfreeze
- `aegis_unfreeze()`
  - caller: **recovery address only**

### 5.3 Why have a sentinel?
A sentinel lets an external component (e.g., a post-execution monitoring watcher) freeze the wallet immediately when it detects anomalies.

In this PoC, sentinel is just a single address.
In production, it could be an off-chain monitoring service/bot.

---

## 6) Included sample 7702 implementations (impls)

All implementations live under `samples/**`. Filenames/symbols use neutral naming (`ModuleA7702` ~ `ModuleI7702`) for model training convenience.

- `ModuleA7702.sol`
- `ModuleB7702.sol`
- `ModuleC7702.sol`
- `ModuleD7702.sol`
- `ModuleE7702.sol`
- `ModuleF7702.sol`
- `ModuleG7702.sol`
- `ModuleH7702.sol`
- `ModuleI7702.sol`

For the intent/scenario of each implementation, refer to the per-module docs under the same path (`samples/**/Module*.md`).

> Note: `samples/` is often a symlink pointing to `aegis-ai-core/ai/samples/` in the other repo.

### 6.1 SAFE/UNSAFE criteria (shared)

In this PoC, the `ImplSafetyRegistry` verdict follows these shared criteria:

- `SAFE`: an unauthorized third party cannot use this impl to cause **arbitrary execution** or a **persistent/permanent DoS** for the wallet
- `SAFE` also considers "fee griefing" where an unauthorized party can repeatedly trigger successful calls to drain the fee token (unauthorized calls should typically revert)
- `UNSAFE`: there exists a realistic path for the above risks (hidden side effects, delayed triggers, storage-collision-driven auth/state confusion, etc.)

Current sample classification (A~I):

- `SAFE`: `ModuleA7702`, `ModuleB7702`, `ModuleG7702`
- `UNSAFE`: `ModuleC7702`, `ModuleD7702`, `ModuleE7702`, `ModuleF7702`, `ModuleH7702`, `ModuleI7702`

> Note: `UNSAFE` samples are blocked by the Guard's normal forwarding (fallback) path.
> In demos/tests, they are only executed via `aegis_forceSetImplementation` + `aegis_forceExecute`.

---

## 7) Repo structure

```text
aegis-contract/
  contracts/                   # AegisGuard + registry + fee policy + token
  scripts/                     # demo/deploy scripts (Hardhat)
  test/                        # end-to-end tests (Hardhat + hardhat_setCode)
  samples/                     # (optional) symlink -> ../aegis-ai-core/ai/samples

aegis-ai-core/
  ai/                          # scanner core (python)
    samples/                   # sample corpus used for RAG/evaluation
```

Hardhat compiles both `contracts/` and (optionally) `samples/` via `paths.sources` in `hardhat.config.js`.

---

## 8) How to run

### 8.1 Install

```bash
npm install
```

### 8.2 Run a local fork chain (Hardhat)

```bash
FORK_URL=<YOUR_RPC_URL> npx hardhat node
# Or pin a specific block
FORK_URL=<YOUR_RPC_URL> FORK_BLOCK_NUMBER=22222222 npx hardhat node
```

`hardhat.config.js` is configured with `hardfork: "prague"`.

### 8.3 Deploy + seed the Registry

```bash
npx hardhat run scripts/deploy.js --network localhost
```

### 8.4 A real 7702 (type=4) single-tx demo

```bash
npx hardhat run scripts/demo7702.js --network localhost
```

This script:
1. Deploys Registry / FeePolicy / Guard / FeeToken / ModuleA
2. Marks ModuleA as SAFE
3. Sends a single type=4 transaction to:
   - install delegation and call `aegis_init`
4. Prints that a fee was charged after one execution

### 8.4-b No-fee deployment/demo

A separate script that disables fees by setting FeePolicy to `token=0x0, recipient=0x0, feePerCall=0`:

```bash
npx hardhat run scripts/deployNoFee.js --network localhost
npx hardhat run scripts/demo7702NoFee.js --network localhost
```

### 8.5 Tests

```bash
npx hardhat test
```

For convenience, tests use `hardhat_setCode` to directly write the delegation indicator to the wallet address and quickly reproduce the "delegated state".

### 8.6 Deploy to Sepolia

Deploy the full contract set to Sepolia using `PK` (and optionally `SEPOLIA_RPC_URL`) from `.env.testnet`:

```bash
npx hardhat run scripts/deploySepoliaAll.js --network sepolia
# Or
npm run deploy:sepolia
```

The 2026-02-07 deployment addresses are summarized in `README.md`. For detailed artifacts, see:
- `deployments/sepolia-latest.json`
- `deployments/sepolia-2026-02-07T04-57-31-823Z.json`

---

## 9) Common issues / troubleshooting

### 9.1 Delegation suddenly disappears or code gets overwritten
In Hardhat simulated environments, account code overwrites can happen.
If test/scripts unintentionally change the wallet address code, the delegation indicator may appear to have disappeared.

### 9.2 type=4 tx fails (authorization nonce / chainId)
- `demo7702.js` uses `auth.nonce = currentNonce + 1` for the "non-sponsored" case.
- If it fails in your environment due to nonce/chainId mismatch:
  - specify `chainId` in `wallet.authorize({ ..., nonce: ..., chainId: ... })`, or
  - adjust `nonce` accordingly.

### 9.3 Fee is not charged
- The wallet address `W` must have a feeToken balance.
- If any of `feeToken`, `feeRecipient`, `feePerCall` returned from `AegisFeePolicy` is zero, fees are disabled.

---

## 10) Limitations (honest)

This PoC demonstrates the "Aegis Guard + Registry" skeleton, not a complete product.

- If the Registry incorrectly marks an impl SAFE, the Guard will execute it.
  - In other words, the real security is the **AI/static analysis/review process**, and
  - Guard/Registry are the rails that enforce that decision on-chain.

- Freeze blocks "execution through the Guard".
  - If an attacker fully compromises the wallet key and installs a different delegation, Guard-level freeze alone has limits.

- If you want to charge fees even for failed executions, you need a separate charging rail (e.g., prepaid or off-chain settlement), because on-chain atomicity will roll back transfers.

---

## 11) Next extension ideas (toward a product)

- Store AI analysis reports on IPFS/Arweave and record a `reportHash` (CID hash) in the Registry
- Decentralize publishers (a verifier network) and strengthen the trust model via staking/slashing
- Connect "pre/post execution monitoring" to a sentinel bot to implement an operational flow that automatically freezes wallets
- Combine with ERC-5792 (batched execution standard), ERC-6900 (modular accounts), etc.

---

## License
MIT
