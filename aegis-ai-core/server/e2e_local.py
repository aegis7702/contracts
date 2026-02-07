from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from eth_account import Account  # type: ignore

from server.abi import encode_calldata
from server.config import (
    AEGIS_CONTRACT_DIR,
    load_deployments,
    publisher_private_key,
    rpc_url_for_chain,
    sentinel_private_key,
)
from server.contracts import ImplSafetyRegistryClient, WalletGuardClient
from server.eth import sign_and_send, wait_for_receipt
from server.rpc import JsonRpcClient


REPO_ROOT = Path(__file__).resolve().parents[2]
API_BASE = os.getenv("E2E_API_BASE", "http://127.0.0.1:8000")
CHAIN_ID = int(os.getenv("E2E_CHAIN_ID", "31337"))


def _delegation_code(guard_address: str) -> str:
    return "0xef0100" + guard_address.lower().replace("0x", "")


def _wait_rpc(url: str, *, timeout_sec: float = 20.0) -> JsonRpcClient:
    rpc = JsonRpcClient(url)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            cid = rpc.eth_chain_id()
            if cid:
                return rpc
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"RPC not ready: {url}")


def _wait_http_ok(url: str, *, timeout_sec: float = 20.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"HTTP not ready: {url}")


@dataclass
class Proc:
    name: str
    p: subprocess.Popen
    log_path: Path


def _start_proc(name: str, cmd: list[str], *, cwd: Path, env: Dict[str, str], log_path: Path) -> Proc:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(log_path, "w", encoding="utf-8")
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=f,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return Proc(name=name, p=p, log_path=log_path)


def _stop_proc(proc: Optional[Proc]) -> None:
    if proc is None:
        return
    if proc.p.poll() is not None:
        return
    try:
        proc.p.terminate()
        proc.p.wait(timeout=8)
    except Exception:
        try:
            proc.p.kill()
        except Exception:
            pass


def _run(cmd: list[str], *, cwd: Path, env: Dict[str, str]) -> None:
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = API_BASE.rstrip("/") + path
    r = httpx.post(url, json=payload, timeout=60.0)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed: {r.status_code} {r.text}")
    return r.json()


def _get(path: str) -> Dict[str, Any]:
    url = API_BASE.rstrip("/") + path
    r = httpx.get(url, timeout=30.0)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {path} failed: {r.status_code} {r.text}")
    return r.json()


def _send_with_fallback_gas(
    rpc: JsonRpcClient,
    *,
    chain_id: int,
    private_key: str,
    to: str,
    data: str,
    value_wei: int = 0,
    gas_fallback: int = 1_500_000,
) -> str:
    try:
        sent = sign_and_send(
            rpc,
            chain_id=chain_id,
            private_key=private_key,
            to=to,
            data=data,
            value_wei=value_wei,
        )
        wait_for_receipt(rpc, sent.tx_hash, timeout_sec=60)
        return sent.tx_hash
    except Exception:
        sent = sign_and_send(
            rpc,
            chain_id=chain_id,
            private_key=private_key,
            to=to,
            data=data,
            value_wei=value_wei,
            gas=gas_fallback,
        )
        wait_for_receipt(rpc, sent.tx_hash, timeout_sec=60)
        return sent.tx_hash


def main() -> None:
    hardhat = None
    server = None
    env = dict(os.environ)

    rpc_url = rpc_url_for_chain(CHAIN_ID)
    if CHAIN_ID != 31337:
        raise RuntimeError("This e2e script is for local chainId=31337 only.")

    # Clean mutable server state (watchlist/cursor) for deterministic runs.
    state_dir = REPO_ROOT / "aegis-ai-core" / "server_state"
    for p in [state_dir / "watchlist.json", state_dir / "cursor.json"]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    # Choose LLM provider for this run.
    llm_provider = os.getenv("E2E_LLM_PROVIDER", "mock")
    env["AEGIS_LLM_PROVIDER"] = llm_provider
    env.setdefault("AEGIS_LLM_MODEL", os.getenv("AEGIS_LLM_MODEL", "gpt-4o-mini"))
    env.setdefault("AEGIS_LLM_REASONING", os.getenv("AEGIS_LLM_REASONING", "none"))

    # Faster worker loop for e2e.
    env["WORKER_CHAIN_IDS"] = str(CHAIN_ID)
    # Use E2E_* overrides so user shell env doesn't accidentally slow down the test run.
    env["WORKER_INTERVAL_SEC"] = os.getenv("E2E_WORKER_INTERVAL_SEC", "2")
    env["CONFIRMATIONS"] = os.getenv("E2E_CONFIRMATIONS", "0")
    env["RPC_URL_31337"] = rpc_url
    env["AEGIS_WORKER_DEBUG"] = os.getenv("E2E_WORKER_DEBUG", "1")
    env["PYTHONUNBUFFERED"] = "1"

    try:
        # 1) Start hardhat node if not already running.
        try:
            _wait_rpc(rpc_url, timeout_sec=2.0)
            started_node = False
        except Exception:
            started_node = True
            hardhat = _start_proc(
                "hardhat",
                ["npm", "run", "node"],
                cwd=AEGIS_CONTRACT_DIR,
                env=env,
                log_path=REPO_ROOT / "tmp" / "e2e-hardhat.log",
            )
            _wait_rpc(rpc_url, timeout_sec=20.0)

        rpc = JsonRpcClient(rpc_url)

        # 2) Deploy local contracts + write deployments json.
        _run(["npm", "run", "deploy:local:all"], cwd=AEGIS_CONTRACT_DIR, env=env)

        deps = load_deployments(CHAIN_ID)
        deployments_path = AEGIS_CONTRACT_DIR / "deployments" / "chain-31337-latest.json"
        deployments = json.loads(deployments_path.read_text(encoding="utf-8"))
        contracts = deployments.get("contracts") or {}
        guard_addr = deps.guard
        registry_addr = deps.registry

        # Setup mock unsafe mapping based on seeded sample set.
        if llm_provider == "mock":
            unsafe_names = ["ModuleC7702", "ModuleD7702", "ModuleE7702", "ModuleF7702", "ModuleH7702", "ModuleI7702"]
            unsafe_impls = [str((contracts.get(n) or {}).get("address") or "") for n in unsafe_names]
            env["AEGIS_MOCK_UNSAFE_IMPLS"] = ",".join([x for x in unsafe_impls if x])

        # 3) Start API server.
        server = _start_proc(
            "api",
            [sys.executable, "-m", "server.main"],
            cwd=REPO_ROOT / "aegis-ai-core",
            env=env,
            log_path=REPO_ROOT / "tmp" / "e2e-api.log",
        )
        _wait_http_ok(API_BASE.rstrip("/") + "/v1/health", timeout_sec=20.0)

        # 4) Create a new EOA wallet for delegated execution.
        acct = Account.create()
        wallet_pk = acct.key.hex()
        wallet = acct.address

        pub_pk = publisher_private_key()
        sent_pk = sentinel_private_key()
        pub_addr = Account.from_key(pub_pk).address
        sent_addr = Account.from_key(sent_pk).address

        # Fund wallet from publisher.
        fund_hash = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=pub_pk,
            to=wallet,
            data="0x",
            value_wei=10**18 // 20,  # 0.05 ETH
        )

        # Install delegation code.
        code = _delegation_code(guard_addr)
        rpc.call("hardhat_setCode", [wallet, code])

        # Init: set safe impl (ModuleA), recovery=publisher, sentinel=sentinel.
        module_a = str((contracts.get("ModuleA7702") or {}).get("address"))
        if not module_a or module_a == "None":
            raise RuntimeError("Missing ModuleA7702 in deployments json")

        init_data = encode_calldata(
            "aegis_init(address,address,address)",
            ["address", "address", "address"],
            [module_a, pub_addr, sent_addr],
        )
        init_hash = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet,
            data=init_data,
        )

        guard = WalletGuardClient(rpc)
        impl_now = guard.get_implementation(wallet)
        if impl_now.lower() != module_a.lower():
            raise RuntimeError(f"init failed: impl={impl_now}, expected={module_a}")

        # 5) Register watch (postaudit worker).
        watch_res = _post("/v1/wallet/watch", {"chainId": CHAIN_ID, "wallet": wallet})
        assert watch_res.get("wallet", "").lower() == wallet.lower()

        # 6) tx precheck (SAFE path)
        dispatch_data = encode_calldata(
            "dispatch((address,uint256,bytes)[])",
            ["(address,uint256,bytes)[]"],
            [[(pub_addr, 0, b"")]],
        )
        pre = _post(
            "/v1/tx/precheck",
            {
                "chainId": CHAIN_ID,
                "from": wallet,
                "to": wallet,
                "value": "0",
                "data": dispatch_data,
                "txType": 4,
                "authorizationList": [],
            },
        )
        assert pre.get("chainId") == CHAIN_ID

        # 7) Send a SAFE tx and wait for postaudit note.
        safe_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet,
            data=dispatch_data,
        )

        deadline = time.time() + 25
        while time.time() < deadline:
            note = guard.get_tx_note(wallet, safe_tx)
            if note.updated_at > 0:
                break
            time.sleep(0.5)
        note = guard.get_tx_note(wallet, safe_tx)
        if note.updated_at == 0:
            raise RuntimeError("postaudit did not write TxNote for safe tx in time")

        # 8) impl scan + on-chain write (exercise publisher key path)
        module_c = str((contracts.get("ModuleC7702") or {}).get("address"))
        scan = _post("/v1/impl/scan", {"chainId": CHAIN_ID, "implAddress": module_c})
        assert scan.get("chainId") == CHAIN_ID

        # 9) swap audit-apply (SAFE) + apply via wallet self-call template
        module_b = str((contracts.get("ModuleB7702") or {}).get("address"))
        apply_safe = _post(
            "/v1/impl/audit-apply",
            {"chainId": CHAIN_ID, "wallet": wallet, "newImplAddress": module_b, "mode": "swap"},
        )
        if not apply_safe.get("allow"):
            raise RuntimeError(f"expected allow=true for safe swap, got: {apply_safe}")
        tpl = apply_safe.get("txTemplate") or {}
        swap_hash = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=str(tpl.get("to") or wallet),
            data=str(tpl.get("data") or "0x"),
        )
        impl_after = guard.get_implementation(wallet)
        if impl_after.lower() != module_b.lower():
            raise RuntimeError("swap apply failed: impl not updated")

        # 10) audit-apply (UNSAFE) should block (txTemplate omitted)
        apply_unsafe = _post(
            "/v1/impl/audit-apply",
            {"chainId": CHAIN_ID, "wallet": wallet, "newImplAddress": module_c, "mode": "swap"},
        )
        if apply_unsafe.get("allow") is True:
            raise RuntimeError("expected allow=false for unsafe swap")

        # 11) Create an UNSAFE tx (forceSet unsafe impl -> then fail via SAFE check) and verify freeze + note.
        force_set = encode_calldata("aegis_forceSetImplementation(address)", ["address"], [module_c])
        _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet,
            data=force_set,
        )

        # This tx should revert in fallback due to UnsafeImplementation registry check.
        unsafe_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet,
            data=dispatch_data,
        )

        deadline = time.time() + 25
        while time.time() < deadline:
            if guard.is_frozen(wallet):
                break
            time.sleep(0.5)
        if not guard.is_frozen(wallet):
            raise RuntimeError("expected wallet to be frozen by postaudit")

        note2 = guard.get_tx_note(wallet, unsafe_tx)
        if note2.updated_at == 0:
            raise RuntimeError("expected freeze path to store TxNote")

        # 12) Recovery (publisher) unfreezes.
        unfreeze = encode_calldata("aegis_unfreeze()", [], [])
        _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=pub_pk,
            to=wallet,
            data=unfreeze,
        )
        if guard.is_frozen(wallet):
            raise RuntimeError("unfreeze failed")

        # 13) Read back via API note endpoint.
        note_api = _get(f"/v1/wallet/{wallet}/tx/{safe_tx}?chainId={CHAIN_ID}")
        if (note_api.get("note") or {}).get("updated_at", 0) == 0:
            raise RuntimeError("API note endpoint returned empty note")

        # 14) Swap record exists (on-chain).
        registry = ImplSafetyRegistryClient(rpc, registry_addr)
        swap_rec = registry.get_swap_record_current(module_a, module_b)
        if swap_rec.updated_at == 0:
            raise RuntimeError("expected swap record to exist")

        print("E2E OK")
        print("wallet:", wallet)
        print("fundTx:", fund_hash)
        print("initTx:", init_hash)
        print("safeTx:", safe_tx)
        print("swapTx:", swap_hash)
        print("unsafeTx:", unsafe_tx)
        if started_node:
            print("logs:", str((REPO_ROOT / "tmp" / "e2e-hardhat.log").resolve()))
        print("api logs:", str((REPO_ROOT / "tmp" / "e2e-api.log").resolve()))

    finally:
        _stop_proc(server)
        _stop_proc(hardhat)


if __name__ == "__main__":
    # Ensure child processes are terminated on Ctrl+C.
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    main()
