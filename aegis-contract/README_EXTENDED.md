# 7702-Aegis PoC — README (Extended)

> **요약**: 이 프로젝트는 **EIP-7702(Prague/Pectra)** 환경에서, “임의의 7702 구현체(implementation)를 쓰고 싶지만 위험해서 지갑들이 막는 현실”을 PoC로 재현하고, 그에 대한 해법으로 **Aegis Guard(Delegator) + Impl Safety Registry + AegisFeePolicy** 구조를 구현한 데모입니다.
>
> - **Registry**: `(implAddress, extcodehash)` 조합에 대해 **SAFE / UNSAFE**만 저장하는 *공용 DB*
> - **Guard(Delegator)**: EOA가 7702로 위임하는 “문지기”.
>   - per-wallet 상태(impl, freeze, recovery, sentinel)는 **EOA storage**에 최소한으로 저장
>   - 매 호출마다 Registry를 확인하고 SAFE면 impl로 `delegatecall`
>   - 실행할 때마다 **FeePolicy를 조회해 토큰 fee를 자동 차감(충전형)**
> - **FeePolicy**: 서비스 운영자가 지갑별 fee 설정을 관리
> - **Sample impls**: 정상/악성/스토리지 충돌 DoS/중앙화 위험 등 다양한 사례를 한 번에 시연

⚠️ **경고**
- 이 저장소는 **학습/데모/해커톤 PoC** 목적입니다.
- 일부 샘플 구현체는 **의도적으로 위험 패턴**을 포함합니다.
- **메인넷 실사용 금지**, 로컬/테스트 환경에서만 사용하세요.

---

## 1) 왜 이 프로젝트가 필요한가?

### 1.1 EIP-7702가 주는 것
EIP-7702는 기존 EOA(일반 지갑 주소)가 **스마트 컨트랙트 지갑처럼 동작**할 수 있도록 “위임(delegation)” 메커니즘을 제공합니다.

- 사용자는 기존 EOA 주소를 유지하면서도
- 배치 실행, 세션키, 가스 추상화 등 “스마트 지갑 UX”를 얻을 수 있습니다.

### 1.2 그런데 왜 지갑들은 ‘임의 구현체 위임’을 막나?
EIP-7702에서 “어떤 구현체로 위임하느냐”는 사실상 **지갑 로직 자체를 바꾸는 업그레이드**에 가깝습니다.

- 사용자가 악성 구현체로 위임하면 지갑이 지속적으로 위험해질 수 있고,
- 숙련자도 구현체 간 **스토리지 충돌(storage collision)**, 초기화, 숨은 권한 경로 등을 즉시 검증하기가 어렵습니다.

그래서 실제 지갑들은 “임의의 7702 구현체 위임 UI”를 쉽게 열지 않는 방향으로 가는 편입니다.

### 1.3 Aegis의 접근
**Aegis는 ‘막는 대신, 검증 후 허용’**하는 방향입니다.

- “안전한 구현체”만 실행되도록 **온체인 Registry로 검열/판정**
- 실행 시마다 Guard가 Registry를 확인하고, SAFE면 실행 / 아니면 차단
- 실행은 **충전형 토큰 fee(인프라 비용)**로 과금

이 PoC는 그 핵심 골격을 “컨트랙트 + 스크립트 + 테스트”로 구현합니다.

---

## 2) 전체 아키텍처 한 눈에 보기

### 2.1 구성 요소

- **EOA Wallet Address**: 사용자의 기존 지갑 주소
- **AegisGuardDelegator**: EOA가 7702로 위임하는 “문지기/프록시”
- **ImplSafetyRegistry**: `(implAddress, codehash) -> SAFE/UNSAFE` 저장
- **AegisFeePolicy**: 서비스 운영자가 지갑별 fee 정책을 관리하는 컨트랙트
- **Implementation(impl)**: 실제 지갑 기능(배치 실행 등)을 제공하는 컨트랙트
- **AegisToken(AGT)**: PoC용 충전형 fee 토큰(ERC20)

### 2.2 호출 흐름(개념도)

```text
사용자(EOA) 주소 = W

(1) 7702 위임 설치: W.code = 0xef0100 || Guard

(2) 이후 누구든 W로 CALL/Tx를 보내면,
    W는 Guard 코드를 실행 (W의 storage 컨텍스트에서 실행)

W (delegated)
  └─ Guard.fallback()
      ├─ (A) wallet frozen? (W.storage)
      ├─ (B) active impl? (W.storage)
      ├─ (C) Registry에서 (impl, extcodehash(impl))가 SAFE인지 확인
      ├─ (D) FeePolicy에서 fee 설정 조회 후 fee 토큰 transfer
      └─ (E) impl로 delegatecall (msg.data 그대로 전달)

impl은 W 컨텍스트에서 실행되므로,
- address(this) == W
- storage는 W.storage
- token/ETH 이동은 W의 자산을 기준으로 일어남
```

---

## 3) 구현 핵심: Guard가 ‘최소 상태’로 안전을 강제하는 방법

### 3.1 Registry는 “impl 안전 판정만” 저장한다
**ImplSafetyRegistry.sol**은 Guard가 참조하는 핵심 정보는 "verdict" 하나로 유지합니다.

- Guard가 강제(enforce)하는 것: 오직
  - `(implAddress, extcodehash(impl)) -> Verdict(Safe/Unsafe/Unknown)`
- PoC 편의 기능(강제에는 미사용)
  - verdict reason(`string`) 저장
  - 최근 N개 업데이트(impl, codehash) ring buffer 저장 (기본 N=5, 배포 스크립트도 5로 설정)
- wallet 별 상태(impl 선택, freeze 여부, recovery 키)는 저장하지 않습니다.

왜 이렇게 하냐면:
- Registry는 **공용 데이터셋**(다른 지갑/프로젝트도 참조 가능)으로 남기고,
- 개인 지갑의 상태(동결, 복구키)는 **개별 지갑 단위로 독립적**이어야 하기 때문입니다.

Registry는 `publisher` 허용 목록을 갖고 있어서, PoC에서는 배포자가 판정을 기록합니다.

### 3.2 Guard의 per-wallet 상태는 “EOA storage”에 저장된다
EIP-7702에서는 Guard 코드가 실행될 때, storage 컨텍스트가 Guard 주소가 아니라 **위임한 EOA(W)** 쪽이 됩니다.

즉, Guard가 어떤 상태를 저장하면 그것은 결국 **W.storage에 저장**됩니다.

이 PoC에서 Guard는 상태를 최소화합니다:
- active implementation 주소
- freeze 여부 + 이유
- recovery 주소
- sentinel 주소(옵션)
- fee 설정은 Guard 저장소가 아니라 `AegisFeePolicy`에서 조회

### 3.3 스토리지 충돌을 피하기 위한 “표준 해시 슬롯”
EOA storage는 impl들도 함께 공유합니다(impl이 `delegatecall`로 실행되기 때문).

그래서 Guard는:
- implementation 주소를 **EIP-1967 implementation slot**에 저장
- 나머지 config는 **ERC-7201 namespaced storage 방식의 해시 슬롯**로 저장

즉, slot0 같은 낮은 슬롯을 쓰지 않습니다.

> 중요: 이 방식은 “Guard 자기 상태” 충돌 확률을 낮추지만,
> **임의 impl이 slot0을 쓰는 문제를 Guard가 대신 remap 해주는 건 불가능**합니다.
> (EVM에서 SSTORE/SLOAD를 프록시가 가로채서 슬롯을 바꿀 수 없음)
> 따라서 impl이 슬롯을 막 쓰면 “위험 impl”로 판정/차단하는 게 정석입니다.

### 3.4 Guard가 impl의 ‘설정 변조’를 막는 방법(불변성 체크)
impl은 delegatecall로 실행되므로, 악의적이면 Guard config 슬롯을 건드려
- recovery 바꾸기
- impl 주소 바꾸기
같은 시도를 할 수 있습니다.

이 PoC Guard는 이를 막기 위해:

1) delegatecall 전에 GuardConfig 스냅샷 + impl 스냅샷을 떠두고
2) delegatecall 후에 동일한지 비교
3) 다르면 지갑을 즉시 freeze 처리

즉, 실행 중에 config/impl을 바꾸려는 시도를 감지하면 추가 손상을 막기 위해 freeze로 전환합니다.

---

## 4) Fee(충전형 토큰 과금) 모델

**AegisGuardDelegator**는 forwarding(impl 실행) 시마다 다음을 수행합니다.

- `AegisFeePolicy.getFeeConfig(wallet)`로 fee 설정을 조회
- `feeToken.transfer(feeRecipient, feePerCall)`을 low-level call로 수행
- feeToken/recipient/feePerCall 중 하나라도 0이면 fee 비활성

### 중요한 성질
- fee는 delegatecall 전에 전송을 시도하지만,
- `fallback`/`aegis_forceExecute` 경로는 delegatecall 실패를 revert로 버블링하므로, 실패 케이스에서는 fee 전송도 함께 롤백됩니다.

단, 동결 상태/미설정 impl/UNSAFE impl/fee transfer 실패처럼 Guard 레벨에서 먼저 revert되는 케이스는 트랜잭션이 실패합니다.

---

## 5) Freeze / Recovery / Sentinel (개별 지갑 단위)

이 프로젝트에서 동결/복구는 **Registry가 아니라 각 지갑(W) 단위로 동작**합니다.

### 5.1 Freeze
- `aegis_freeze(string reason)`
  - 호출자: **self-call**(지갑이 자기 자신에게 tx) 또는 **sentinel**
  - frozen이면 Guard가 모든 forwarding을 차단

### 5.2 Unfreeze
- `aegis_unfreeze()`
  - 호출자: **recovery 주소만 가능**

### 5.3 왜 sentinel이 있나?
sentinel은 “실행 후 모니터링(Watcher)” 같은 외부 구성 요소가
이상 징후를 감지했을 때 즉시 동결시키는 용도입니다.

PoC에서는 sentinel은 그냥 하나의 주소로 설정되며,
실전에서는 오프체인 감시 서비스/봇이 될 수 있습니다.

---

## 6) 포함된 샘플 7702 구현체(impl)

모든 구현체는 `samples/**`에 있으며, 파일명/심볼은 모델 학습 편의를 위해 중립 네이밍(`ModuleA7702` ~ `ModuleI7702`)을 사용합니다.

- `ModuleA7702.sol`
- `ModuleB7702.sol`
- `ModuleC7702.sol`
- `ModuleD7702.sol`
- `ModuleE7702.sol`
- `ModuleF7702.sol`
- `ModuleG7702.sol`
- `ModuleH7702.sol`
- `ModuleI7702.sol`

각 구현체의 의도/시나리오 설명은 같은 경로의 개별 문서(`samples/**/Module*.md`)를 참고하세요.

> 참고: `samples/`는 보통 별도 레포 `aegis-ai-core`의 `ai/samples/`를 가리키는 symlink로 두고 씁니다.

### 6.1 SAFE/UNSAFE 분류 기준(공통)

이 PoC에서 `ImplSafetyRegistry`의 verdict는 아래 공통 기준으로 찍습니다.

- `SAFE`: 비인가 제3자가 이 impl을 통해 지갑에 **임의 실행(arbitrary execution)** 을 시키거나 **지속/영구 DoS** 를 만들 수 없음
- `SAFE`에는 “비인가 제3자가 성공 호출을 반복해 fee 토큰을 소모시키는(fee griefing)” 케이스도 포함합니다(비인가 호출은 보통 revert되어야 함).
- `UNSAFE`: 위 위험이 현실적으로 가능한 경로가 존재(숨은 side effect, 지연 트리거, 스토리지 충돌 기반 auth/상태 혼선 포함)

현재 샘플 분류(A~I):

- `SAFE`: `ModuleA7702`, `ModuleB7702`, `ModuleG7702`
- `UNSAFE`: `ModuleC7702`, `ModuleD7702`, `ModuleE7702`, `ModuleF7702`, `ModuleH7702`, `ModuleI7702`

> 참고: `UNSAFE` 샘플은 Guard의 normal forwarding(fallback) 경로에서는 막히며,
> 데모/테스트에서는 `aegis_forceSetImplementation` + `aegis_forceExecute`로만 실행합니다.

---

## 7) 레포 구조

```text
aegis-contract/
  contracts/                   # AegisGuard + registry + fee policy + token
  scripts/                     # demo/deploy scripts (Hardhat)
  test/                        # end-to-end test (Hardhat + hardhat_setCode)
  samples/                     # (optional) symlink -> ../aegis-ai-core/ai/samples

aegis-ai-core/
  ai/                          # scanner core (python)
    samples/                   # sample corpus used for RAG/evaluation
```

Hardhat은 `hardhat.config.js`의 `paths.sources`에서 `contracts/`와 (옵션) `samples/`를 함께 컴파일합니다.

---

## 8) 실행 방법

### 8.1 설치

```bash
npm install
```

### 8.2 로컬 포크 체인 실행(Hardhat)

```bash
FORK_URL=<YOUR_RPC_URL> npx hardhat node
# 또는 특정 블록 고정
FORK_URL=<YOUR_RPC_URL> FORK_BLOCK_NUMBER=22222222 npx hardhat node
```

`hardhat.config.js`에서 `hardfork: "prague"`로 설정되어 있습니다.

### 8.3 배포 + Registry seeding

```bash
npx hardhat run scripts/deploy.js --network localhost
```

### 8.4 “진짜 7702(type=4) 한 방 데모”

```bash
npx hardhat run scripts/demo7702.js --network localhost
```

이 스크립트는:
1) Registry/FeePolicy/Guard/FeeToken/ModuleA 배포
2) ModuleA를 SAFE로 마킹
3) **단 1번의 type=4 트랜잭션**으로
   - delegation 설치 + aegis_init 호출
4) 실행 1회 후 fee가 빠져나가는 것을 출력

### 8.4-b 무과금(no-fee) 배포/데모

FeePolicy를 `token=0x0, recipient=0x0, feePerCall=0`으로 두고 과금 없이 실행하는 별도 스크립트:

```bash
npx hardhat run scripts/deployNoFee.js --network localhost
npx hardhat run scripts/demo7702NoFee.js --network localhost
```

### 8.5 테스트

```bash
npx hardhat test
```

테스트는 편의를 위해 `hardhat_setCode`로 지갑 주소에 delegation indicator를 직접 써서
“위임 상태”를 빠르게 재현합니다.

### 8.6 Sepolia 배포

`.env.testnet`의 `PK`(및 선택적으로 `SEPOLIA_RPC_URL`)를 사용해 Sepolia에 전체 컨트랙트 세트를 배포:

```bash
npx hardhat run scripts/deploySepoliaAll.js --network sepolia
# 또는
npm run deploy:sepolia
```

2026-02-07 배포 주소는 `README.md`에 정리되어 있고, 상세 산출물은 아래 파일을 참고:
- `deployments/sepolia-latest.json`
- `deployments/sepolia-2026-02-07T04-57-31-823Z.json`

---

## 9) 자주 겪는 이슈 / 트러블슈팅

### 9.1 delegation이 갑자기 풀리거나 코드가 덮어씌워진다
Hardhat simulated 환경에서 account code overwrite가 발생할 수 있습니다.
테스트/스크립트에서 지갑 주소의 코드가 의도치 않게 변경되면
delegation indicator가 사라진 것처럼 보일 수 있습니다.

### 9.2 type=4 tx가 실패한다(authorization nonce / chainId)
- `demo7702.js`는 “non-sponsored” 기준으로 `auth.nonce = currentNonce + 1`을 사용합니다.
- 네 환경에서 nonce/chainId 때문에 실패한다면,
  - `wallet.authorize({ ..., nonce: ..., chainId: ... })`에서 chainId를 명시하거나
  - nonce를 조정해서 맞추세요.

### 9.3 Fee가 안 빠져나간다
- 지갑 주소(W)에 feeToken 잔액이 있어야 합니다.
- `AegisFeePolicy`에서 조회된 `feeToken`, `feeRecipient`, `feePerCall` 중 하나가 0이면 fee가 비활성입니다.

---

## 10) 보안/설계상의 한계(정직하게)

이 PoC는 “Aegis Guard + Registry” 골격을 보여주기 위한 것이고, 완전한 제품이 아닙니다.

- Registry가 잘못 SAFE를 찍으면 Guard는 그 impl을 실행합니다.
  - 즉, **AI/정적분석/리뷰 프로세스가 본체**이고
  - Guard/Registry는 그 결과를 “강제하는 레일”입니다.

- Freeze는 “Guard를 통한 실행”을 막습니다.
  - 공격자가 지갑 키를 완전히 탈취해 다시 다른 delegation을 설치하면,
    Guard 레벨의 동결만으로는 한계가 있습니다.

- 실패 실행에서도 과금하려면 온체인 원자성과 별도로, 별도 과금 레일(예: 선불/오프체인 정산)이 필요합니다.

---

## 11) 다음 확장 아이디어(실제품 방향)

- AI 분석 결과 리포트를 IPFS/Arweave에 저장하고, Registry에 `reportHash`(CID 해시)를 함께 기록
- publisher를 다중화하고(검증자 네트워크), 스테이킹/슬래싱으로 신뢰 모델 강화
- “실행 전/후 감시”를 sentinel 봇과 연결해 자동 freeze까지 이어지는 운영 플로우 구현
- ERC-5792(배치 실행 표준), ERC-6900(모듈형 계정) 등과의 결합

---

## 라이선스
MIT
