from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ai.call_llm import build_llm_caller
from ai.scan_impl_bytecode import audit_impl_bytecode, audit_swap_bytecode
from ai.scan_tx_precheck import audit_tx_precheck
from server.config import (
    cursor_path,
    load_deployments,
    publisher_private_key,
    rpc_url_for_chain,
    sentinel_private_key,
    watchlist_path,
)
from server.contracts import ImplSafetyRegistryClient, TxNote, WalletGuardClient
from server.models import (
    AuditApplyResponse,
    ChatRequest,
    ChatResponse,
    ImplAuditApplyRequest,
    ImplScanRequest,
    ImplScanResponse,
    TxPrecheckRequest,
    TxPrecheckResponse,
    WatchItemResponse,
    WatchListResponse,
    WatchRequest,
)
from server.rpc import JsonRpcClient
from server.state import add_watch, list_watch, remove_watch
from server.worker import WorkerThread


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _join_reasons(reasons: Any) -> str:
    if isinstance(reasons, list):
        parts = [str(x).strip() for x in reasons if str(x).strip()]
    else:
        parts = [str(reasons).strip()] if str(reasons).strip() else []
    return "\n".join(parts)


def _verdict_from_label(label: str) -> int:
    label = (label or "").upper()
    if label == "SAFE":
        return 1
    return 2


def _hex_selector(data_hex: str) -> str:
    if not isinstance(data_hex, str) or not data_hex.startswith("0x"):
        return ""
    if len(data_hex) < 10:
        return ""
    return data_hex[:10].lower()


def _auth_delegate_address(auth: Any) -> Optional[str]:
    if not isinstance(auth, dict):
        return None
    for k in ("address", "delegateTo", "delegate_to", "delegate", "target"):
        v = auth.get(k)
        if isinstance(v, str) and v.startswith("0x") and len(v) == 42:
            return v
    return None


def _chain_ids_for_worker() -> List[int]:
    env = os.getenv("WORKER_CHAIN_IDS")
    if not env:
        return [31337, 11155111]
    out: List[int] = []
    for part in env.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


app = FastAPI(title="Aegis PoC API", version="0.1")
_worker: Optional[WorkerThread] = None


def _cors_allow_origins() -> List[str]:
    env = (os.getenv("CORS_ALLOW_ORIGINS") or "").strip()
    if not env:
        # PoC default: allow all origins (non-credentialed).
        return ["*"]
    if env == "*":
        return ["*"]
    return [x.strip() for x in env.split(",") if x.strip()]


_allow_origins = _cors_allow_origins()
_allow_credentials = os.getenv("CORS_ALLOW_CREDENTIALS", "").strip().lower() in ("1", "true", "yes")
if _allow_credentials and "*" in _allow_origins:
    # Spec disallows wildcard when credentials are enabled.
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    global _worker
    if _worker is None:
        _worker = WorkerThread(chain_ids=_chain_ids_for_worker())
        _worker.start()


@app.get("/v1/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """
    Minimal ChatGPT-style wrapper: send input text to the configured LLM and return the raw output text.
    """

    provider = os.getenv("AEGIS_LLM_PROVIDER", "openai")
    model = req.model or os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14")
    system_prompt = req.system or os.getenv("AEGIS_CHAT_SYSTEM_PROMPT", "You are a helpful assistant.")

    caller = build_llm_caller(provider)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.text},
    ]
    kwargs: Dict[str, Any] = {"model": model}
    if req.maxTokens is not None:
        kwargs["max_tokens"] = int(req.maxTokens)
    else:
        # PoC default: cap output to avoid runaway costs.
        kwargs["max_tokens"] = 800
    if req.temperature is not None:
        kwargs["temperature"] = float(req.temperature)

    try:
        out_text = caller.chat(messages, **kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM chat failed: {e!r}")

    return ChatResponse(text=str(out_text))


@app.post("/v1/impl/scan", response_model=ImplScanResponse)
def impl_scan(req: ImplScanRequest) -> ImplScanResponse:
    chain_id = int(req.chainId)
    rpc = JsonRpcClient(rpc_url_for_chain(chain_id))
    deps = load_deployments(chain_id)
    registry = ImplSafetyRegistryClient(rpc, deps.registry)

    bytecode = rpc.eth_get_code(req.implAddress, "latest")
    if not bytecode or bytecode == "0x":
        raise HTTPException(status_code=400, detail="No code at implAddress")

    audit = audit_impl_bytecode(chain_id=chain_id, impl_address=req.implAddress, bytecode_hex=bytecode)
    reasons_text = _join_reasons(audit.get("reasons", []))

    verdict = _verdict_from_label(str(audit.get("label")))
    sent, _receipt = registry.set_record_current_and_wait(
        chain_id=chain_id,
        publisher_pk=publisher_private_key(),
        impl=req.implAddress,
        verdict=verdict,
        name=str(audit.get("name") or ""),
        summary=str(audit.get("summary") or ""),
        description=str(audit.get("description") or ""),
        reasons=reasons_text,
    )

    return ImplScanResponse(
        chainId=chain_id,
        implAddress=req.implAddress,
        audit=audit,  # type: ignore[arg-type]
        reasonsText=reasons_text,
        registryTxHash=sent.tx_hash,
    )


@app.post("/v1/impl/audit-apply", response_model=AuditApplyResponse)
def impl_audit_apply(req: ImplAuditApplyRequest) -> AuditApplyResponse:
    chain_id = int(req.chainId)
    rpc = JsonRpcClient(rpc_url_for_chain(chain_id))
    deps = load_deployments(chain_id)
    registry = ImplSafetyRegistryClient(rpc, deps.registry)
    guard = WalletGuardClient(rpc)

    # Current impl (for swap mode)
    current_impl = None
    if req.mode == "swap":
        try:
            current_impl = guard.get_implementation(req.wallet)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read wallet current impl: {e!r}")

    # 1) Always audit + record newImpl (standalone)
    new_bytecode = rpc.eth_get_code(req.newImplAddress, "latest")
    if not new_bytecode or new_bytecode == "0x":
        raise HTTPException(status_code=400, detail="No code at newImplAddress")

    new_audit = audit_impl_bytecode(chain_id=chain_id, impl_address=req.newImplAddress, bytecode_hex=new_bytecode)
    new_reasons_text = _join_reasons(new_audit.get("reasons", []))
    new_verdict = _verdict_from_label(str(new_audit.get("label")))
    new_sent, _ = registry.set_record_current_and_wait(
        chain_id=chain_id,
        publisher_pk=publisher_private_key(),
        impl=req.newImplAddress,
        verdict=new_verdict,
        name=str(new_audit.get("name") or ""),
        summary=str(new_audit.get("summary") or ""),
        description=str(new_audit.get("description") or ""),
        reasons=new_reasons_text,
    )

    swap_audit = None
    swap_reasons_text = None
    swap_tx_hash = None
    allow = str(new_audit.get("label", "UNSAFE")).upper() == "SAFE"

    # 2) Swap compatibility record (optional second on-chain write)
    if req.mode == "swap" and current_impl is not None:
        current_bytecode = rpc.eth_get_code(current_impl, "latest")
        if not current_bytecode or current_bytecode == "0x":
            raise HTTPException(status_code=400, detail="No code at current impl (unexpected)")

        swap_audit = audit_swap_bytecode(
            chain_id=chain_id,
            current_impl_address=current_impl,
            current_bytecode_hex=current_bytecode,
            new_impl_address=req.newImplAddress,
            new_bytecode_hex=new_bytecode,
        )
        swap_reasons_text = _join_reasons(swap_audit.get("reasons", []))
        swap_verdict = _verdict_from_label(str(swap_audit.get("label")))
        swap_sent, _ = registry.set_swap_record_current_and_wait(
            chain_id=chain_id,
            publisher_pk=publisher_private_key(),
            from_impl=current_impl,
            to_impl=req.newImplAddress,
            verdict=swap_verdict,
            name=str(swap_audit.get("name") or ""),
            summary=str(swap_audit.get("summary") or ""),
            description=str(swap_audit.get("description") or ""),
            reasons=swap_reasons_text,
        )
        swap_tx_hash = swap_sent.tx_hash
        allow = allow and str(swap_audit.get("label", "UNSAFE")).upper() == "SAFE"

    # tx template (wallet self-call)
    tx_template: Optional[Dict[str, Any]] = None
    if allow:
        if req.mode == "swap":
            # to=wallet, data=aegis_setImplementation(newImpl)
            from server.abi import encode_calldata

            data = encode_calldata("aegis_setImplementation(address)", ["address"], [req.newImplAddress])
            tx_template = {"to": req.wallet, "data": data, "value": "0x0"}
        else:
            from server.abi import encode_calldata

            sentinel_addr = os.getenv("SENTINEL_ADDRESS") or "0x0000000000000000000000000000000000000000"
            data = encode_calldata(
                "aegis_init(address,address,address)",
                ["address", "address", "address"],
                [req.newImplAddress, req.wallet, sentinel_addr],
            )
            tx_template = {"to": req.wallet, "data": data, "value": "0x0"}

    return AuditApplyResponse(
        chainId=chain_id,
        wallet=req.wallet,
        mode=req.mode,
        currentImpl=current_impl,
        newImpl=req.newImplAddress,
        newImplAudit=new_audit,  # type: ignore[arg-type]
        newImplReasonsText=new_reasons_text,
        newImplRegistryTxHash=new_sent.tx_hash,
        swapAudit=swap_audit,  # type: ignore[arg-type]
        swapReasonsText=swap_reasons_text,
        swapRegistryTxHash=swap_tx_hash,
        allow=bool(allow),
        txTemplate=tx_template,
    )


@app.post("/v1/tx/precheck", response_model=TxPrecheckResponse)
def tx_precheck(req: TxPrecheckRequest) -> TxPrecheckResponse:
    chain_id = int(req.chainId)
    rpc = JsonRpcClient(rpc_url_for_chain(chain_id))
    deps = load_deployments(chain_id)
    registry = ImplSafetyRegistryClient(rpc, deps.registry)
    guard = WalletGuardClient(rpc)

    wallet = req.from_address
    auth_list = req.authorizationList or []
    selector = _hex_selector(req.data or "")
    delegate_targets = [x for x in (_auth_delegate_address(a) for a in auth_list) if x]
    tx_ctx = {
        "from": req.from_address,
        "to": req.to,
        "value": req.value,
        "data": req.data,
        "type": req.txType,
        "authorizationList": auth_list,
        # Derived fields (best-effort). These are NOT used for rule-based blocking, only to help the LLM.
        "selector": selector,
        "authorizationListDelegateTargets": delegate_targets,
        "aegis": {
            "expectedGuard": deps.guard,
            "registry": deps.registry,
        },
    }

    # Load current impl note (supporting context). If wallet isn't delegated yet, continue anyway.
    impl_record_json: Dict[str, Any] = {}
    try:
        impl = guard.get_implementation(wallet)
        rec = registry.get_record_current(impl)
        impl_record_json = {
            "implAddress": impl,
            "verdict": rec.verdict,
            "name": rec.name,
            "summary": rec.summary,
            "description": rec.description,
            "reasons": rec.reasons,
            "updatedAt": rec.updated_at,
            "codehash": rec.codehash,
        }
    except Exception as e:
        impl_record_json = {"error": f"failed to load wallet current impl record: {e!r}", "implAddress": None}

    try:
        audit = audit_tx_precheck(chain_id=chain_id, tx=tx_ctx, impl_record=impl_record_json)
    except Exception as e:
        # LLM-only blocking policy: if the precheck pipeline is down, do NOT block deterministically.
        # Return SAFE with low confidence and an explicit warning.
        audit = {
            "label": "SAFE",
            "confidence": 0.0,
            "name": "PrecheckError",
            "summary": "Precheck failed (LLM/service error). Allowing by policy.",
            "description": "The precheck service failed to produce a valid result.\nNo deterministic block was applied.\nProceed with caution.",
            "reasons": [f"precheck error: {e!r}"],
            "matched_patterns": [],
        }
    reasons_text = _join_reasons(audit.get("reasons", []))
    allow = str(audit.get("label", "UNSAFE")).upper() == "SAFE"

    return TxPrecheckResponse(
        chainId=chain_id,
        allow=allow,
        audit=audit,  # type: ignore[arg-type]
        reasonsText=reasons_text,
        walletCurrentImpl=impl_record_json.get("implAddress"),
        walletCurrentImplRecord=impl_record_json,
    )


@app.post("/v1/wallet/watch", response_model=WatchItemResponse)
def wallet_watch(req: WatchRequest) -> WatchItemResponse:
    chain_id = int(req.chainId)
    rpc = JsonRpcClient(rpc_url_for_chain(chain_id))
    latest = rpc.eth_block_number()
    item = add_watch(
        watchlist_path(),
        chain_id=chain_id,
        wallet=req.wallet,
        start_block=latest + 1,
        added_at=_now_iso(),
    )
    return WatchItemResponse(wallet=item.wallet, startBlock=item.startBlock, addedAt=item.addedAt)


@app.get("/v1/wallet/watch", response_model=WatchListResponse)
def wallet_watch_list(chainId: int = Query(..., description="chainId")) -> WatchListResponse:
    items = list_watch(watchlist_path(), chain_id=int(chainId))
    return WatchListResponse(
        chainId=int(chainId),
        items=[WatchItemResponse(wallet=x.wallet, startBlock=x.startBlock, addedAt=x.addedAt) for x in items],
    )


@app.delete("/v1/wallet/watch", response_model=Dict[str, Any])
def wallet_watch_delete(chainId: int = Query(...), wallet: str = Query(...)) -> Dict[str, Any]:
    ok = remove_watch(watchlist_path(), chain_id=int(chainId), wallet=wallet)
    return {"removed": bool(ok)}


@app.get("/v1/wallet/{wallet}/tx/{txHash}", response_model=Dict[str, Any])
def wallet_tx_note(wallet: str, txHash: str, chainId: int = Query(...)) -> Dict[str, Any]:
    chain_id = int(chainId)
    rpc = JsonRpcClient(rpc_url_for_chain(chain_id))
    guard = WalletGuardClient(rpc)
    note = guard.get_tx_note(wallet, txHash)
    return {
        "wallet": wallet,
        "txHash": txHash,
        "note": note.__dict__,
    }


def main() -> None:
    # Convenience entrypoint for `python3 -m server.main`
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
