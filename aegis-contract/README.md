# 7702-Aegis PoC (Hardhat + ethers)

This repo is a Hardhat project scaffold for a hackathon PoC that runs in an **EIP-7702 (Prague/Pectra)** environment.
It provides an "Aegis Guard (Delegator)" + "Impl Safety Registry" + a sample set of 7702 implementations.

> WARNING
>
> - This code is a PoC for learning/demo/hackathon purposes.
> - Do not use on mainnet. Use only on testnets/local networks.

---

## Components

### On-chain
- `ImplSafetyRegistry.sol`
  - Public registry that stores a verdict for `(implAddress, extcodehash(impl)) -> SAFE/UNSAFE`.
  - Stored fields:
    - `name: string`
    - `summary: string`
    - `description: string` (2-3 lines, use `\n` for line breaks)
    - `reasons: string` (the backend joins `reasons[]` and stores it)
    - `updatedAt: uint64`
  - PoC convenience features:
    - Ring buffer for the most recent N updates (default N=5; deploy scripts also set N=5)
    - Separate record for swap-compatibility risk (current impl -> new impl)

- `AegisGuardDelegator.sol`
  - Guard/delegator that an EOA delegates to via EIP-7702.
  - Responsibilities:
    1) Store minimal per-wallet state (`activeImplementation`, `frozen`, `recovery`, `sentinel`, etc.) in hashed slots
       in the delegated EOA storage (EIP-1967 + ERC-7201)
    2) Check whether the current impl is SAFE via `ImplSafetyRegistry`
    3) Charge a token fee per forwarded execution via `AegisFeePolicy` (prepaid model)
    4) If SAFE, `delegatecall` into the impl
  - TxNote (post-audit record):
    - Store `txHash -> {name, summary, description, reasons, updatedAt}` in wallet (EOA) storage
    - Sentinel-only write functions
    - Ring buffer of the most recent 20 tx hashes

- `AegisFeePolicy.sol`
  - Contract where the service operator configures per-wallet/default fee policy.

- Sample 7702 implementations (`samples/**`)
  - `ModuleA7702.sol`
  - `ModuleB7702.sol`
  - `ModuleC7702.sol`
  - `ModuleD7702.sol`
  - `ModuleE7702.sol`
  - `ModuleF7702.sol`
  - `ModuleG7702.sol`
  - `ModuleH7702.sol`
  - `ModuleI7702.sol`
  - For details, see the `*.md` docs next to each file.

- `AegisToken.sol`
  - Simple PoC ERC-20 token (for prepaid balance + fee collection testing).

> Note: the sample implementation corpus lives in the separate repo `aegis-ai-core`.
> This repo assumes `samples/` is a symlink to `../aegis-ai-core/ai/samples` for developer convenience.

---

## Run (Example)

### 1) Install

```bash
npm install
```

### 2) Run a local fork chain

Hardhat runs with fork + the Prague hardfork enabled.

- After setting `forking.url` in `hardhat.config.js` to your RPC:

```bash
npx hardhat node
```

> Note: per Hardhat docs, simulated networks can perform **account code overwrite**, which may remove EIP-7702
> delegation indicators. If delegation seems to "randomly" disappear during experiments, suspect this first.

### 3) Deploy

```bash
npx hardhat run scripts/deploy.js --network localhost
```

To deploy without fees (no-charge):

```bash
npx hardhat run scripts/deployNoFee.js --network localhost
```

Or, a simple demo that performs "delegation install (type=4) + aegis_init" in a single tx using
**ethers.js `authorize()` + tx override `authorizationList`**:

```bash
npx hardhat run scripts/demo7702.js --network localhost
```

No-fee single-transaction demo:

```bash
npx hardhat run scripts/demo7702NoFee.js --network localhost
```

### 4) (Important) Apply EIP-7702 delegation (set-code)

This PoC requires an "EOA -> Guard" delegation so that calls/txs to the EOA address execute the Guard code.

- Use a **type 0x04 (txType=4)** transaction to set the EOA code to `0xef0100 || <GuardAddress>`.

---

## Recommended Demo Scenarios

1) Set a SAFE impl and call `dispatch` -> success + fee charged
2) Try to set an UNSAFE impl -> the Guard blocks it via the Registry
3) (UNSAFE demo) Run `ModuleE7702`, then swap to `ModuleF7702` -> observe storage-collision DoS
4) Flip `ModuleG7702` state flags and observe behavior changes

---

## SAFE/UNSAFE Classification Criteria (Common)

The verdicts written to `ImplSafetyRegistry` follow these criteria:

- `SAFE`: an unauthorized third party cannot use the impl to trigger **arbitrary execution** or create **persistent/permanent DoS** for the wallet
- `SAFE` may include "fee griefing" cases (repeated successful calls that burn fee tokens), but unauthorized calls should typically revert
- `UNSAFE`: a practical path exists for the above risks (hidden side effects, delayed triggers, auth/state confusion from storage collisions, etc.)

Sample classification (A~I):

- `SAFE`: `ModuleA7702`, `ModuleB7702`, `ModuleG7702`
- `UNSAFE`: `ModuleC7702`, `ModuleD7702`, `ModuleE7702`, `ModuleF7702`, `ModuleH7702`, `ModuleI7702`

> Note: UNSAFE samples are blocked on the Guard's normal forwarding (fallback) path.
> In demos/tests, they are executed only via `aegis_forceSetImplementation` + `aegis_forceExecute`.

---

## Sepolia Deployment Addresses (2026-02-07)

> Note: this PoC changes ABI frequently (e.g., the recent-N ring buffer, note schema changes, etc.).
> The addresses below may not match the ABI in the current working tree. If needed, redeploy via
> `scripts/deploySepoliaAll.js` and refresh `deployments/sepolia-latest.json`.

- Full deployment details (addresses/tx hashes/verdict seeding txs): `deployments/chain-11155111-latest.json`

- deployedAt: `2026-02-07T15:43:22.249Z`
- deployer: `0xfFf6679e75B926DA54f53FAF9Cf2594F86BB1Aa8`
- `ImplSafetyRegistry`: `0x01F95904ecd0848A28bDEEBB78FbFbac5aD7F2e6`
- `AegisToken`: `0xa9F884D633aeC8f3893631E29Cf0264c11899f55`
- `AegisFeePolicy`: `0x411D01aDe2eCDFb878239A233601bAf136Cfc61E`
- `AegisGuardDelegator`: `0x2f7bB54E59DadC1f593FAea2c84092825Fd0533B`
- `ModuleA7702`: `0x803Bc23698933e062210683357Ec20c394131e56`
- `ModuleB7702`: `0xa453dbeb0f8f04b8fcA341260760cfDb78B40CcC`
- `ModuleC7702`: `0xD506b539cb8d3a99C5308E2d8d4A49384F0207E1`
- `ModuleD7702`: `0x8f5b5f03a92382b4c49e9e446061486A9de09A0B`
- `ModuleE7702`: `0x416ebc59c5223F988651d3A3D3AC74E1B9ff6a9B`
- `ModuleF7702`: `0x2a72c0E2f7eB22eb1CB7fe54764290555D122978`
- `ModuleG7702`: `0x529C8029401EE3443a1B44d33173ff510f3EbB7d`
- `ModuleH7702`: `0xCeE24d93AfcFa95b07Ed25bBaa679ed5DeC3743B`
- `ModuleI7702`: `0xDe2E0c61251d901FD65dFf4e3E175fbBA04D3734`

Deployment detail artifacts (tx hashes, SAFE/UNSAFE seeding txs):
- `deployments/sepolia-latest.json`
- `deployments/sepolia-2026-02-07T15-43-22-249Z.json`

Apply 7702 delegation only (nonce auto-calculated):

```bash
npm run delegate:sepolia
# optional: WITH_INIT=1 IMPL=<implAddr> RECOVERY=<addr> SENTINEL=<addr> npm run delegate:sepolia
```

---

## License
MIT
