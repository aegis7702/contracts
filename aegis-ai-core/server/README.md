# Aegis AI Core Server (PoC)

목표: EIP-7702(로컬/포크) 지갑에 대해

- tx **사전차단(precheck)**: 지갑이 tx 전송 전에 API에 문맥 분석 요청
- tx **사후감사(postaudit)**: watchlist 지갑들의 새 tx를 모니터링해서 위험하면 freeze + TxNote 온체인 기록
- impl **조회(scan)**: impl 주소를 받아 `eth_getCode`(bytecode) 기반 분석 후 `ImplSafetyRegistry`에 기록
- impl **신규/교체 감사(audit-apply)**: impl 신규등록/교체 전 감사 + (swap mode) 호환성 리스크도 별도 기록

이 PoC는 **DB 없이** `watchlist.json`/`cursor.json` 파일로 상태를 관리합니다.

## Policy (LLM-only blocking, minimize false positives)

- 백엔드에서 tx/impl을 **룰(하드코딩)로 차단하지 않습니다**. 차단/허용은 LLM 결과(label)에만 의존합니다.
- 오탐(정상 동작 차단/불필요 freeze)이 치명적이므로, 모든 프롬프트는 **명확한 하이시그널이 있을 때만 UNSAFE** 를 반환하도록 설계했습니다.
- 장애/파싱 실패 시:
  - precheck: deterministic block 없이 SAFE(낮은 confidence)로 반환
  - postaudit(worker): deterministic freeze 없이 TxNote만 기록

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

### CORS (Browser)

브라우저(프론트엔드)에서 직접 호출하면 CORS preflight(OPTIONS)가 발생합니다.

- 기본값: `Access-Control-Allow-Origin: *` (credentials 없이 허용)
- 특정 origin만 허용하려면:

```bash
export CORS_ALLOW_ORIGINS=http://localhost:3000,https://your-frontend.example
```

쿠키/인증정보를 포함해야 한다면(`credentials: "include"` 같은 케이스):

```bash
export CORS_ALLOW_ORIGINS=https://your-frontend.example
export CORS_ALLOW_CREDENTIALS=true
```

### LLM config

- `AEGIS_LLM_PROVIDER`: `openai` (default) | `grok`
- `AEGIS_LLM_MODEL`: (default `gpt-4o-mini`)
- `AEGIS_LLM_REASONING`: `none` | `low` | `medium` | `high` (모델이 지원할 때만)

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
