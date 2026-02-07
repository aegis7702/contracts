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
CHAIN_ID = 11155111


def _wait_http_ok(url: str, *, timeout_sec: float = 30.0) -> None:
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
        proc.p.wait(timeout=10)
    except Exception:
        try:
            proc.p.kill()
        except Exception:
            pass


def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = API_BASE.rstrip("/") + path
    # On public networks we may be waiting for on-chain receipts inside the API handler.
    r = httpx.post(url, json=payload, timeout=300.0)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed: {r.status_code} {r.text}")
    return r.json()


def _get(path: str) -> Dict[str, Any]:
    url = API_BASE.rstrip("/") + path
    r = httpx.get(url, timeout=60.0)
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
    gas_fallback: int = 1_800_000,
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
        wait_for_receipt(rpc, sent.tx_hash, timeout_sec=240)
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
        wait_for_receipt(rpc, sent.tx_hash, timeout_sec=240)
        return sent.tx_hash


def _delegate_and_init_wallet(wallet_addr: str, wallet_pk: str, *, publisher_addr: str, sentinel_addr: str) -> str:
    # Use the hardhat script to send EIP-7702 type=4 tx with authorizationList.
    env = dict(os.environ)
    env["SIGNER_PK"] = wallet_pk
    env["ADDRESS"] = wallet_addr
    env["WITH_INIT"] = "1"
    env["RECOVERY"] = publisher_addr
    env["SENTINEL"] = sentinel_addr
    cmd = ["npm", "run", "delegate:sepolia"]
    subprocess.run(cmd, cwd=str(AEGIS_CONTRACT_DIR), env=env, check=True)
    # The script prints txHash; for test reporting we fetch code and return it instead.
    rpc = JsonRpcClient(rpc_url_for_chain(CHAIN_ID))
    return rpc.eth_get_code(wallet_addr, "latest")


def main() -> None:
    server: Optional[Proc] = None

    # Clean mutable server state (watchlist/cursor) for deterministic runs.
    state_dir = REPO_ROOT / "aegis-ai-core" / "server_state"
    for p in [state_dir / "watchlist.json", state_dir / "cursor.json"]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    # Load deployments.
    deps = load_deployments(CHAIN_ID)
    deployments_path = AEGIS_CONTRACT_DIR / "deployments" / "chain-11155111-latest.json"
    deployments = json.loads(deployments_path.read_text(encoding="utf-8"))
    contracts = deployments.get("contracts") or {}

    wallet_addr = os.getenv("E2E_WALLET_ADDRESS") or ""
    wallet_pk = os.getenv("E2E_WALLET_PK") or ""
    if not wallet_addr or not wallet_pk:
        raise RuntimeError("Missing E2E_WALLET_ADDRESS/E2E_WALLET_PK in env (.env.testnet).")

    pub_pk = publisher_private_key()
    sent_pk = sentinel_private_key()
    pub_addr = Account.from_key(pub_pk).address
    sent_addr = Account.from_key(sent_pk).address

    rpc = JsonRpcClient(rpc_url_for_chain(CHAIN_ID), timeout_sec=30.0)
    guard = WalletGuardClient(rpc)

    # Ensure wallet has ETH for a few txs.
    bal = int(rpc.call("eth_getBalance", [wallet_addr, "latest"]), 16)
    if bal < int(0.006 * 10**18):
        fund_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=sent_pk,
            to=wallet_addr,
            data="0x",
            value_wei=int(0.01 * 10**18),
        )
        print("funded wallet tx:", fund_tx)

    # Ensure delegation code is installed and init was performed.
    code = rpc.eth_get_code(wallet_addr, "latest")
    expected_indicator = "0xef0100" + deps.guard.lower().replace("0x", "")
    if code.lower() != expected_indicator.lower():
        code_after = _delegate_and_init_wallet(wallet_addr, wallet_pk, publisher_addr=pub_addr, sentinel_addr=sent_addr)
        if code_after.lower() != expected_indicator.lower():
            raise RuntimeError("Delegation indicator mismatch after delegate script.")

    # Sanity: read current impl.
    impl = guard.get_implementation(wallet_addr)
    module_a = str((contracts.get("ModuleA7702") or {}).get("address") or "")
    if not module_a:
        raise RuntimeError("Missing ModuleA7702 in deployments json.")
    if impl.lower() != module_a.lower():
        raise RuntimeError(f"Expected initial impl ModuleA, got {impl}")

    # If this wallet was frozen from a previous run, unfreeze via recovery (publisher) before starting.
    if guard.is_frozen(wallet_addr):
        unfreeze = encode_calldata("aegis_unfreeze()", [], [])
        unfreeze_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=pub_pk,
            to=wallet_addr,
            data=unfreeze,
        )
        print("pre-unfreezeTx:", unfreeze_tx)
        if guard.is_frozen(wallet_addr):
            raise RuntimeError("wallet is still frozen after pre-unfreeze")

    # Start API server (mock mode for deterministic verdicts).
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["HOST"] = "0.0.0.0"
    env["PORT"] = os.getenv("E2E_PORT", "8000")
    env["RPC_URL_11155111"] = rpc.url
    env["WORKER_CHAIN_IDS"] = str(CHAIN_ID)
    env["WORKER_INTERVAL_SEC"] = os.getenv("E2E_WORKER_INTERVAL_SEC", "10")
    env["CONFIRMATIONS"] = os.getenv("E2E_CONFIRMATIONS", "0")
    env["AEGIS_WORKER_DEBUG"] = os.getenv("E2E_WORKER_DEBUG", "1")

    llm_provider = os.getenv("E2E_LLM_PROVIDER", "mock")
    env["AEGIS_LLM_PROVIDER"] = llm_provider
    env.setdefault("AEGIS_LLM_MODEL", os.getenv("AEGIS_LLM_MODEL", "gpt-4o-mini"))
    env.setdefault("AEGIS_LLM_REASONING", os.getenv("AEGIS_LLM_REASONING", "none"))
    if llm_provider == "mock":
        unsafe_names = ["ModuleC7702", "ModuleD7702", "ModuleE7702", "ModuleF7702", "ModuleH7702", "ModuleI7702"]
        unsafe_impls = [str((contracts.get(n) or {}).get("address") or "") for n in unsafe_names]
        env["AEGIS_MOCK_UNSAFE_IMPLS"] = ",".join([x for x in unsafe_impls if x])

    server = _start_proc(
        "api",
        [sys.executable, "-m", "server.main"],
        cwd=REPO_ROOT / "aegis-ai-core",
        env=env,
        log_path=REPO_ROOT / "tmp" / "e2e-sepolia-api.log",
    )
    _wait_http_ok(API_BASE.rstrip("/") + "/v1/health", timeout_sec=30.0)

    try:
        # Register watch for postaudit.
        watch = _post("/v1/wallet/watch", {"chainId": CHAIN_ID, "wallet": wallet_addr})
        assert watch.get("wallet", "").lower() == wallet_addr.lower()

        # Precheck + send a SAFE tx (ModuleA dispatch a no-op call).
        # IMPORTANT: do not call the publisher address here, because it may itself be a delegated account.
        safe_target = sent_addr
        dispatch_data = encode_calldata(
            "dispatch((address,uint256,bytes)[])",
            ["(address,uint256,bytes)[]"],
            [[(safe_target, 0, b"")]],
        )
        pre = _post(
            "/v1/tx/precheck",
            {
                "chainId": CHAIN_ID,
                "from": wallet_addr,
                "to": wallet_addr,
                "value": "0",
                "data": dispatch_data,
                "txType": 0,
                "authorizationList": [],
            },
        )
        print("precheck.allow:", pre.get("allow"), "label:", (pre.get("audit") or {}).get("label"))

        safe_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet_addr,
            data=dispatch_data,
        )
        print("safeTx:", safe_tx)

        # Wait for worker to write TxNote.
        deadline = time.time() + 180
        while time.time() < deadline:
            note = guard.get_tx_note(wallet_addr, safe_tx)
            if note.updated_at > 0:
                break
            time.sleep(3)
        note = guard.get_tx_note(wallet_addr, safe_tx)
        if note.updated_at == 0:
            raise RuntimeError("postaudit did not write TxNote for safe tx in time")

        # impl scan (publisher on-chain write)
        module_c = str((contracts.get("ModuleC7702") or {}).get("address") or "")
        scan = _post("/v1/impl/scan", {"chainId": CHAIN_ID, "implAddress": module_c})
        print("impl.scan registryTxHash:", scan.get("registryTxHash"))

        # swap audit-apply (SAFE) + apply
        module_b = str((contracts.get("ModuleB7702") or {}).get("address") or "")
        apply_safe = _post(
            "/v1/impl/audit-apply",
            {"chainId": CHAIN_ID, "wallet": wallet_addr, "newImplAddress": module_b, "mode": "swap"},
        )
        if not apply_safe.get("allow"):
            raise RuntimeError(f"expected allow=true for safe swap, got: {apply_safe}")
        tpl = apply_safe.get("txTemplate") or {}
        swap_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=str(tpl.get("to") or wallet_addr),
            data=str(tpl.get("data") or "0x"),
        )
        print("swapTx:", swap_tx)
        if guard.get_implementation(wallet_addr).lower() != module_b.lower():
            raise RuntimeError("swap apply failed: impl not updated")

        # audit-apply (UNSAFE) should block
        apply_unsafe = _post(
            "/v1/impl/audit-apply",
            {"chainId": CHAIN_ID, "wallet": wallet_addr, "newImplAddress": module_c, "mode": "swap"},
        )
        if apply_unsafe.get("allow") is True:
            raise RuntimeError("expected allow=false for unsafe swap")

        # Create a failing tx to drive freeze path in mock postaudit:
        # 1) forceSet unsafe impl
        force_set = encode_calldata("aegis_forceSetImplementation(address)", ["address"], [module_c])
        force_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet_addr,
            data=force_set,
        )
        print("forceSetTx:", force_tx)

        # 2) dispatch -> should revert due to registry SAFE check
        unsafe_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=wallet_pk,
            to=wallet_addr,
            data=dispatch_data,
        )
        print("unsafeTx:", unsafe_tx)

        # Wait for freeze.
        deadline = time.time() + 240
        while time.time() < deadline:
            if guard.is_frozen(wallet_addr):
                break
            time.sleep(5)
        if not guard.is_frozen(wallet_addr):
            raise RuntimeError("expected wallet to be frozen by postaudit")

        # Unfreeze via recovery (publisher).
        unfreeze = encode_calldata("aegis_unfreeze()", [], [])
        unfreeze_tx = _send_with_fallback_gas(
            rpc,
            chain_id=CHAIN_ID,
            private_key=pub_pk,
            to=wallet_addr,
            data=unfreeze,
        )
        print("unfreezeTx:", unfreeze_tx)
        if guard.is_frozen(wallet_addr):
            raise RuntimeError("unfreeze failed")

        # API note endpoint smoke.
        note_api = _get(f"/v1/wallet/{wallet_addr}/tx/{safe_tx}?chainId={CHAIN_ID}")
        if (note_api.get("note") or {}).get("updated_at", 0) == 0:
            raise RuntimeError("API note endpoint returned empty note")

        # Verify swap record exists on registry.
        registry = ImplSafetyRegistryClient(rpc, deps.registry)
        swap_rec = registry.get_swap_record_current(module_a, module_b)
        if swap_rec.updated_at == 0:
            raise RuntimeError("expected swap record to exist")

        print("E2E(Sepolia) OK")
        print("wallet:", wallet_addr)
        print("api logs:", str((REPO_ROOT / "tmp" / "e2e-sepolia-api.log").resolve()))
    finally:
        _stop_proc(server)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    main()
