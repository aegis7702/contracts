# 7702-Aegis PoC (Hardhat + ethers)

이 저장소는 **EIP-7702(Prague/Pectra)** 환경에서 동작하는 "Aegis Guard(Delegator)" + "Impl Safety Registry" + 샘플 7702 impl 세트를 **해커톤 PoC 용도**로 제공하는 Hardhat 프로젝트 스캐폴딩입니다.

> ⚠️ 주의
>
> - 이 코드는 **학습/데모/해커톤** 목적의 PoC입니다.
> - 메인넷 실사용 금지. 테스트넷/로컬에서만 사용하세요.

---

## 구성

### On-chain
- `ImplSafetyRegistry.sol`
  - `(implAddress, extcodehash(impl)) -> SAFE/UNSAFE` verdict를 저장하는 공용 Registry
  - PoC 편의 기능:
    - verdict reason(`string`) 저장
    - 최근 N개 업데이트(impl, codehash) ring buffer 저장 (기본 N=5, 배포 스크립트도 5로 설정)

- `AegisGuardDelegator.sol`
  - 7702에서 EOA가 위임하는 Guard(Delegator)
  - 역할:
    1) (개별 지갑 상태) `activeImplementation`, `frozen`, `recovery`, `sentinel` 등을 **EOA storage의 해시 슬롯(EIP-1967 + ERC-7201)** 에 저장
    2) `ImplSafetyRegistry`를 조회해 현재 impl이 SAFE인지 확인
    3) `AegisFeePolicy`를 조회해 실행 시 토큰 fee 징수(충전형)
    4) SAFE면 impl로 `delegatecall`

- `AegisFeePolicy.sol`
  - 서비스 제공자(owner/operator)가 지갑별/기본 fee 정책을 설정하는 컨트랙트

- 샘플 7702 impl (`samples/**`)
  - `ModuleA7702.sol`
  - `ModuleB7702.sol`
  - `ModuleC7702.sol`
  - `ModuleD7702.sol`
  - `ModuleE7702.sol`
  - `ModuleF7702.sol`
  - `ModuleG7702.sol`
  - `ModuleH7702.sol`
  - `ModuleI7702.sol`
  - 상세 설명: 각 파일과 동일 경로의 `*.md` 문서 참고

- `AegisToken.sol`
  - PoC용 단순 ERC20(충전/fee 징수 테스트)

> 참고: 샘플 구현체 코퍼스는 별도 레포 `aegis-ai-core`에 있고,
> 이 레포에서는 개발 편의를 위해 `samples/`를 `../aegis-ai-core/ai/samples`로 symlink 해둔 상태를 가정합니다.

---

## 실행(예시)

### 1) 설치

```bash
npm install
```

### 2) 로컬 포크 체인 실행

Hardhat에서 fork + prague 하드포크로 실행합니다.

- `hardhat.config.js`의 `forking.url`을 자신의 RPC로 바꾼 뒤:

```bash
npx hardhat node
```

> 참고: Hardhat 문서에 따르면, simulated network에서 **account code overwrite**가 발생할 수 있으며 그 과정에서 EIP-7702 delegation이 제거될 수 있습니다.
> 실험 중 delegation이 "갑자기" 풀리는 현상이 보이면 이 부분을 의심하세요.

### 3) 배포

```bash
npx hardhat run scripts/deploy.js --network localhost
```

fee 설정 없이(무과금) 배포하려면:

```bash
npx hardhat run scripts/deployNoFee.js --network localhost
```

또는, **ethers.js의 `authorize()` + tx override `authorizationList`** 를 이용해
"delegation 설치(type=4) + aegis_init"까지 한 번에 수행하는 간단 데모:

```bash
npx hardhat run scripts/demo7702.js --network localhost
```

무과금 7702 단일 트랜잭션 데모:

```bash
npx hardhat run scripts/demo7702NoFee.js --network localhost
```

### 4) (중요) EIP-7702 delegation(set-code)

이 PoC는 "EOA -> Guard" 위임이 있어야 EOA 주소를 호출할 때 Guard 코드가 실행됩니다.

- Hardhat + ethers 환경에서 이미 7702를 테스트해봤다고 했으니,
  여러분의 방식대로 **type 0x04(txType=4)** 트랜잭션으로 EOA code를 `0xef0100 || <GuardAddress>`로 설정하세요.

---

## 데모 시나리오 추천

1) SAFE impl로 설정 후 `dispatch` 호출 → 성공 + fee 차감
2) UNSAFE impl로 설정 시도 → Guard가 Registry를 보고 차단
3) (UNSAFE 데모) `ModuleE7702` 실행 후 `ModuleF7702`로 전환 → 스토리지 충돌 DoS 확인
4) `ModuleG7702` 상태 플래그 변경 후 동작 확인

---

## SAFE/UNSAFE 분류 기준 (공통)

이 레포에서 `ImplSafetyRegistry`에 찍는 verdict는 아래 공통 기준을 따릅니다.

- `SAFE`: 비인가 제3자가 이 impl을 통해 지갑에 **임의 실행(arbitrary execution)** 을 시키거나 **영구/지속 DoS** 를 만들 수 없음
- `SAFE`에는 “비인가 제3자가 성공 호출을 반복해 fee 토큰을 소모시키는(fee griefing)” 케이스도 포함합니다(비인가 호출은 보통 revert되어야 함).
- `UNSAFE`: 위 위험이 현실적으로 가능한 경로가 존재(숨은 side effect, 지연 트리거, 스토리지 충돌 기반 auth/상태 혼선 포함)

샘플 분류(A~I):

- `SAFE`: `ModuleA7702`, `ModuleB7702`, `ModuleG7702`
- `UNSAFE`: `ModuleC7702`, `ModuleD7702`, `ModuleE7702`, `ModuleF7702`, `ModuleH7702`, `ModuleI7702`

> 참고: `UNSAFE` 샘플은 Guard의 normal forwarding(fallback) 경로에서는 막히며,
> 데모/테스트에서는 `aegis_forceSetImplementation` + `aegis_forceExecute`로만 실행합니다.

---

## Sepolia 배포 주소 (2026-02-07)

> 참고: 이 PoC는 자주 ABI가 바뀝니다(예: Registry의 recent N ring buffer, reason 타입 string 전환 등).
> 아래 주소가 현재 워킹트리의 ABI와 다를 수 있으니, 필요하면 `scripts/deploySepoliaAll.js`로 재배포 후 `deployments/sepolia-latest.json`을 갱신하세요.

- deployer: `0x75c027b280F063BAf49A71c548Aa46Ed84434600`
- `ImplSafetyRegistry`: `0x5707169276D19D9209c7aDB7c2DdC2FA256F8aA1`
- `AegisToken`: `0x4175046d14cf65BFFcF51ec6A470e4A8FbA1a402`
- `AegisFeePolicy`: `0x6fDdD46E25F51512F46b1b0ac9759Cc683aB43c7`
- `AegisGuardDelegator`: `0x45715e7E41098de7B1726a7a182268da4aEB9804`
- `ModuleA7702`: `0x16b0e675C0CE766e82bf9B58dC2d2F247985B302`
- `ModuleB7702`: `0xE6C896ac6B6195Da7daDF66Fe5DC39FBb0e7321b`
- `ModuleC7702`: `0x373325c876eF8437069453e050a5f963a20Bd928`
- `ModuleD7702`: `0xC62036a6C1ca310ab6029D2c4630383a68674073`
- `ModuleE7702`: `0x8Cbc082c5A2F5235c18aEF310124CcA8372195bb`
- `ModuleF7702`: `0x8871e4009E48B2b1C9b2B0b5fc37e4D187a3f037`
- `ModuleG7702`: `0x7450981F49fd218B7751B0E828fFBaeEf7307258`

배포 상세(tx hash, SAFE/UNSAFE 시딩 tx 포함):
- `deployments/sepolia-latest.json`
- `deployments/sepolia-2026-02-07T04-57-31-823Z.json`

7702 위임만 따로 적용(논스 자동 계산):

```bash
npm run delegate:sepolia
# optional: WITH_INIT=1 IMPL=<implAddr> RECOVERY=<addr> SENTINEL=<addr> npm run delegate:sepolia
```

---

## 라이선스
MIT
