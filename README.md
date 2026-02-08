# Aegis (PoC): LLM-Assisted Security for EIP-7702 Wallets

This repo is a hackathon-style **proof of concept** for securing **EIP-7702 delegated wallets** with an on-chain guard
and an off-chain LLM-driven auditor.

It is organized as a small monorepo:

- `aegis-contract/`: Solidity + Hardhat project (on-chain contracts + sample 7702 implementations)
- `aegis-ai-core/`: Python FastAPI service (LLM audit pipelines + worker that writes on-chain notes/freezes)

> WARNING
>
> This is a PoC for learning/demo/testing on local networks and testnets.
> Do not use on mainnet.

## What It Does (High Level)

- **Tx precheck (before broadcast)**: a 7702 wallet calls the API to decide SAFE/UNSAFE based on transaction context.
- **Tx post-audit (after mining)**: a worker monitors watched wallets; for each new tx it writes an on-chain TxNote,
  and if the LLM label is UNSAFE it freezes the wallet via the sentinel.
- **Implementation scan**: given an implementation address, fetch runtime bytecode via `eth_getCode`, analyze with the LLM,
  and store the verdict + note on-chain in the registry.
- **Implementation audit-apply (init/swap)**: audit an implementation before applying it; in swap mode also write a separate
  compatibility/migration-risk note on-chain.

On-chain data is stored as human-readable strings (PoC), including:
- impl note fields: `name`, `summary`, `description`, `reasons`, `updatedAt`
- tx notes: `txHash -> {name, summary, description, reasons, updatedAt}` stored in the wallet (EOA) storage

## `aegis-contract/` (On-Chain)

Core contracts:
- `ImplSafetyRegistry.sol`: stores SAFE/UNSAFE verdicts + notes for implementations (and swap-compat notes), plus a small
  “recent updates” ring buffer.
- `AegisGuardDelegator.sol`: the 7702 guard/delegator that wallets delegate to; enforces registry checks, supports per-wallet
  freeze/recovery, and allows the sentinel to write TxNotes / freeze-with-note.

Docs:
- `aegis-contract/README.md`
- `aegis-contract/README_EXTENDED.md`

## `aegis-ai-core/` (Off-Chain)

FastAPI server + worker:
- API endpoints for tx precheck, tx post-audit monitoring (watchlist), impl scan, impl audit-apply, and a minimal `/v1/chat`
  LLM wrapper.
- No DB: watchlist/cursor are stored in JSON files under `aegis-ai-core/server_state/`.
- LLM access is configured via `aegis-ai-core/ai/.env` (e.g., `OPENAI_API_KEY`).

Docs:
- `aegis-ai-core/server/README.md`
- `aegis-ai-core/server/API_SPEC.md`

## Notes

- This repo expects `aegis-contract/samples` to be a symlink to `aegis-ai-core/ai/samples` for convenience when compiling
  and deploying the sample implementations.
- Secrets (private keys, API keys) live in local `.env*` files that are gitignored.

