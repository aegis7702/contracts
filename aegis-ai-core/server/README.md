# Aegis AI Core Server (PoC)

목표: EIP-7702(로컬/포크) 지갑에 대해

- tx **사전차단(precheck)**: 지갑이 tx 전송 전에 API에 문맥 분석 요청
- tx **사후감사(postaudit)**: watchlist 지갑들의 새 tx를 모니터링해서 위험하면 freeze + TxNote 온체인 기록
- impl **조회(scan)**: impl 주소를 받아 `eth_getCode`(bytecode) 기반 분석 후 `ImplSafetyRegistry`에 기록
- impl **신규/교체 감사(audit-apply)**: impl 신규등록/교체 전 감사 + (swap mode) 호환성 리스크도 별도 기록

이 PoC는 **DB 없이** `watchlist.json`/`cursor.json` 파일로 상태를 관리합니다.

## Prereqs

- `aegis-contract/`에서 Hardhat node 실행 + 배포(로컬 또는 Sepolia)
- 환경변수:
  - `aegis-contract/.env.testnet`: `PUBLISHER_PK`, `SENTINEL_PK`, `PUBLISHER_ADDRESS`, `SENTINEL_ADDRESS` 등
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

- `RPC_URL_{chainId}`: chainId별 직접 지정 (권장)
- fallback:
  - `chainId=31337`: `LOCAL_RPC_URL` 또는 `http://127.0.0.1:8545`
  - `chainId=11155111`: `SEPOLIA_RPC_URL` 또는 public RPC

예:

```bash
export RPC_URL_31337=http://127.0.0.1:8545
export RPC_URL_11155111=https://ethereum-sepolia-rpc.publicnode.com
```

### LLM config

- `AEGIS_LLM_PROVIDER`: `openai` (default) | `mock`
- `AEGIS_LLM_MODEL`: (default `gpt-4o-mini`)
- `AEGIS_LLM_REASONING`: `none` | `low` | `medium` | `high` (모델이 지원할 때만)

`mock` 모드는 E2E 플로우 테스트용입니다:
- `AEGIS_MOCK_UNSAFE_IMPLS`: 쉼표로 구분된 `implAddress` 목록(UNSAFE로 강제)
- `AEGIS_MOCK_UNSAFE_SWAPS`: 쉼표로 구분된 `from->to` 목록(UNSAFE로 강제)

## Run (Local)

1) Hardhat node:

```bash
cd aegis-contract
npm run node
```

2) Local deploy + deployments json 생성:

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

Hardhat node + deploy + API server + 실제 tx를 모두 자동으로 돌려봅니다.

```bash
cd aegis-ai-core
. .venv/bin/activate
python -m server.e2e_local
```

옵션:
- 실제 OpenAI 호출로 테스트: `E2E_LLM_PROVIDER=openai python -m server.e2e_local`

## Mutable state files

생성 위치: `aegis-ai-core/server_state/`

- `watchlist.json`: chainId별 모니터링 지갑 목록
- `cursor.json`: chainId별 worker 마지막 처리 block

