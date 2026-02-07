from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ai.scan_tx_postaudit import audit_tx_postaudit
import os
from server.config import (
    cursor_path,
    load_deployments,
    publisher_private_key,
    rpc_url_for_chain,
    sentinel_private_key,
    watchlist_path,
    worker_confirmations,
    worker_interval_sec,
)
from server.contracts import ImplSafetyRegistryClient, TxNote, WalletGuardClient
from server.rpc import JsonRpcClient
from server.state import get_cursor, list_watch, set_cursor


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lower(s: Optional[str]) -> str:
    return (s or "").lower()


def _join_reasons(reasons: Any) -> str:
    if isinstance(reasons, list):
        parts = [str(x).strip() for x in reasons if str(x).strip()]
    else:
        parts = [str(reasons).strip()] if str(reasons).strip() else []
    return "\n".join(parts)


def _tx_minimal(tx: Dict[str, Any]) -> Dict[str, Any]:
    # Keep the LLM input bounded while preserving core context.
    return {
        "hash": tx.get("hash"),
        "from": tx.get("from"),
        "to": tx.get("to"),
        "value": tx.get("value"),
        "input": tx.get("input"),
        "type": tx.get("type"),
        "nonce": tx.get("nonce"),
    }


def _receipt_minimal(receipt: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": receipt.get("status"),
        "gasUsed": receipt.get("gasUsed"),
        "effectiveGasPrice": receipt.get("effectiveGasPrice"),
        "contractAddress": receipt.get("contractAddress"),
        "logsBloom": receipt.get("logsBloom"),
        "logs": receipt.get("logs"),
    }


def _debug_enabled() -> bool:
    v = (os.getenv("AEGIS_WORKER_DEBUG") or os.getenv("WORKER_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def tick_chain(chain_id: int) -> None:
    items = list_watch(watchlist_path(), chain_id=chain_id)
    if not items:
        return

    rpc = JsonRpcClient(rpc_url_for_chain(chain_id))
    deployments = load_deployments(chain_id)
    registry = ImplSafetyRegistryClient(rpc, deployments.registry)
    guard = WalletGuardClient(rpc)
    impl_record_cache: Dict[str, Dict[str, Any]] = {}

    latest = rpc.eth_block_number()
    conf = worker_confirmations(chain_id)
    to_block = latest - conf
    if to_block < 0:
        return

    cursor = get_cursor(cursor_path(), chain_id=chain_id)
    if cursor is None:
        start = min(x.startBlock for x in items)
        cursor = set_cursor(cursor_path(), chain_id=chain_id, last_processed_block=max(0, start - 1), updated_at=_now_iso())

    from_block = cursor.lastProcessedBlock + 1
    if from_block > to_block:
        return

    watched = {x.wallet.lower(): x.startBlock for x in items}
    sentinel_pk = sentinel_private_key()

    if _debug_enabled():
        print(
            json.dumps(
                {
                    "ts": _now_iso(),
                    "msg": "tick_chain",
                    "chainId": chain_id,
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "watchedCount": len(watched),
                }
            )
        )

    for bn in range(from_block, to_block + 1):
        block = rpc.eth_get_block_by_number(bn, full_txs=True)
        txs: List[Dict[str, Any]] = block.get("transactions") or []
        block_complete = True

        for tx in txs:
            tx_from = _lower(tx.get("from"))
            if not tx_from:
                continue
            start_block = watched.get(tx_from)
            if start_block is None or bn < start_block:
                continue

            tx_hash = tx.get("hash")
            if not tx_hash:
                continue

            wallet = tx.get("from")
            assert wallet is not None

            # If a TxNote already exists on-chain, do not re-audit.
            try:
                existing = guard.get_tx_note(wallet, tx_hash)
                if existing.updated_at > 0:
                    continue
            except Exception:
                # If the wallet isn't delegated / call fails, fall through and let the rest error-handling decide.
                pass

            receipt = rpc.eth_get_transaction_receipt(tx_hash)
            if receipt is None:
                # On local chains with 0 confirmations, receipts can lag slightly behind block visibility.
                # Do not advance the cursor past this block; retry next tick.
                block_complete = False
                break

            # Pull current impl registry note (supporting context only).
            impl_addr = None
            impl_record_json: Dict[str, Any] = {}
            wallet_l = wallet.lower()
            cached = impl_record_cache.get(wallet_l)
            if cached is not None:
                impl_record_json = cached
            else:
                try:
                    impl_addr = guard.get_implementation(wallet)
                    rec = registry.get_record_current(impl_addr)
                    impl_record_json = {
                        "implAddress": impl_addr,
                        "verdict": rec.verdict,
                        "name": rec.name,
                        "summary": rec.summary,
                        "description": rec.description,
                        "reasons": rec.reasons,
                        "updatedAt": rec.updated_at,
                        "codehash": rec.codehash,
                    }
                except Exception as e:
                    impl_record_json = {"error": f"failed to load current impl record: {e!r}", "implAddress": impl_addr}
                impl_record_cache[wallet_l] = impl_record_json

            try:
                audit = audit_tx_postaudit(
                    chain_id=chain_id,
                    tx=_tx_minimal(tx),
                    receipt=_receipt_minimal(receipt),
                    impl_record=impl_record_json,
                )
            except Exception as e:
                audit = {
                    "label": "UNSAFE",
                    "confidence": 0.0,
                    "name": "PostAuditError",
                    "summary": "Post-audit failed",
                    "description": "LLM or parser error during postaudit.\nWallet was frozen as a precaution.\nSee reasons for details.",
                    "reasons": [f"postaudit error: {e!r}"],
                    "matched_patterns": [],
                }

            reasons_text = _join_reasons(audit.get("reasons", []))
            note = TxNote(
                name=str(audit.get("name") or f"tx:{tx_hash[:10]}"),
                summary=str(audit.get("summary") or ""),
                description=str(audit.get("description") or ""),
                reasons=reasons_text,
                updated_at=0,
            )

            label = str(audit.get("label") or "UNSAFE").upper()
            if label == "UNSAFE":
                freeze_reason = str(audit.get("summary") or "UNSAFE")
                try:
                    guard.freeze_with_tx_note_and_wait(
                        chain_id=chain_id,
                        sentinel_pk=sentinel_pk,
                        wallet=wallet,
                        tx_hash=tx_hash,
                        freeze_reason=freeze_reason,
                        note=note,
                    )
                except Exception as e:
                    print(json.dumps({"chainId": chain_id, "wallet": wallet, "txHash": tx_hash, "error": f"freeze failed: {e!r}"}))
            else:
                try:
                    guard.set_tx_note_and_wait(
                        chain_id=chain_id,
                        sentinel_pk=sentinel_pk,
                        wallet=wallet,
                        tx_hash=tx_hash,
                        note=note,
                    )
                except Exception as e:
                    print(json.dumps({"chainId": chain_id, "wallet": wallet, "txHash": tx_hash, "error": f"set note failed: {e!r}"}))

            print(json.dumps({"chainId": chain_id, "wallet": wallet, "txHash": tx_hash, "label": label}, ensure_ascii=False))

        if not block_complete:
            if _debug_enabled():
                print(
                    json.dumps(
                        {
                            "ts": _now_iso(),
                            "msg": "block_incomplete",
                            "chainId": chain_id,
                            "blockNumber": bn,
                        }
                    )
                )
            break

        # Persist cursor after each complete block so we don't re-audit old blocks.
        set_cursor(cursor_path(), chain_id=chain_id, last_processed_block=bn, updated_at=_now_iso())


def tick_all_chains(chain_ids: List[int]) -> None:
    for cid in chain_ids:
        try:
            tick_chain(cid)
        except Exception as e:
            print(f"[worker] chainId={cid} tick error: {e!r}")


class WorkerThread:
    def __init__(self, chain_ids: List[int]):
        self.chain_ids = list(chain_ids)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="aegis-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = worker_interval_sec()
        while not self._stop.is_set():
            if _debug_enabled():
                print(json.dumps({"ts": _now_iso(), "msg": "worker_tick", "chainIds": self.chain_ids, "interval": interval}))
            tick_all_chains(self.chain_ids)
            self._stop.wait(interval)
