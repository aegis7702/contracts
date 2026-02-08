# Aegis AI Core Server (PoC)

Goal: for EIP-7702 wallets (local/fork), provide:

- tx **precheck**: the wallet calls the API before sending a tx for context-based analysis
- tx **postaudit**: monitor new txs for watched wallets; if risky, freeze + write TxNote on-chain
- impl **scan**: analyze an impl address via `eth_getCode` (bytecode) and write the result to `ImplSafetyRegistry`
- impl **audit-apply**: audit before registering/applying a new impl; in swap mode also record a separate compatibility/migration-risk note

This PoC keeps state without a DB using `watchlist.json`/`cursor.json`.

## Policy (LLM-only blocking, minimize false positives)

- The backend does not block txs/impls via hard-coded rules. Allow/deny/freeze decisions rely only on the LLM label.
- Because false positives (blocking normal usage / unnecessary freezes) are extremely disruptive, prompts are written to return UNSAFE only for clear high-signal cases.
- On outages / parsing failures:
  - precheck: return SAFE (low confidence) with a warning (no deterministic block)
  - postaudit (worker): write TxNote only (no deterministic freeze)

## Prereqs

- Run a Hardhat node and deploy from `aegis-contract/` (local or Sepolia)
- Environment variables:
  - `aegis-contract/.env.testnet`: `PUBLISHER_PK`, `SENTINEL_PK`, `PUBLISHER_ADDRESS`, `SENTINEL_ADDRESS`, etc.
  - `aegis-ai-core/ai/.env`: `OPENAI_API_KEY`

## Install (Python)

```bash
cd aegis-ai-core
python3 -m venv .venv
. .venv/bin/activate
pip install -r server/requirements.txt
```

## Config

### RPC URL mapping

- `RPC_URL_{chainId}`: set per-chain RPC URL (recommended)
- Fallbacks:
  - `chainId=31337`: `LOCAL_RPC_URL` or `http://127.0.0.1:8545`
  - `chainId=11155111`: `SEPOLIA_RPC_URL` or a public RPC

Example:

```bash
export RPC_URL_31337=http://127.0.0.1:8545
export RPC_URL_11155111=https://ethereum-sepolia-rpc.publicnode.com
```

### CORS (Browser)

If your frontend calls the API directly from a browser, CORS preflight (OPTIONS) will happen.

- Default: `Access-Control-Allow-Origin: *` (allowed without credentials)
- To allow specific origins only:

```bash
export CORS_ALLOW_ORIGINS=http://localhost:3000,https://your-frontend.example
```

If you need cookies/credentials (e.g. `credentials: "include"`):

```bash
export CORS_ALLOW_ORIGINS=https://your-frontend.example
export CORS_ALLOW_CREDENTIALS=true
```

### LLM config

- `AEGIS_LLM_PROVIDER`: `openai` (default) | `grok`
- `AEGIS_LLM_MODEL`: (default `gpt-4o-mini`)
- `AEGIS_LLM_REASONING`: `none` | `low` | `medium` | `high` (only if supported by the model)

## Run (Local)

1) Hardhat node:

```bash
cd aegis-contract
npm run node
```

2) Local deploy + generate deployments json:

```bash
cd aegis-contract
npm run deploy:local:all
```

3) API server:

```bash
cd aegis-ai-core
. .venv/bin/activate
export WORKER_CHAIN_IDS=31337
export WORKER_INTERVAL_SEC=60
export CONFIRMATIONS=0
python -m server.main
```

API spec: `server/API_SPEC.md`

## E2E (Local, automated)

This runs Hardhat node + deploy + API server + real transactions end-to-end.

```bash
cd aegis-ai-core
. .venv/bin/activate
python -m server.e2e_local
```

Options:
- Force OpenAI provider explicitly: `E2E_LLM_PROVIDER=openai python -m server.e2e_local`

## Mutable state files

Location: `aegis-ai-core/server_state/`

- `watchlist.json`: per-chain list of watched wallets
- `cursor.json`: per-chain last processed block for the worker
