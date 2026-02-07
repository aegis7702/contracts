from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from eth_account import Account  # type: ignore
from eth_utils import to_checksum_address, to_hex

from server.rpc import JsonRpcClient
from server.rpc import JsonRpcError


@dataclass(frozen=True)
class SentTx:
    tx_hash: str
    from_address: str
    nonce: int


GWEI = 10**9


def _int_to_hex(i: int) -> str:
    return hex(int(i))


def sign_and_send_legacy(
    rpc: JsonRpcClient,
    *,
    chain_id: int,
    private_key: str,
    to: str,
    data: str,
    value_wei: int = 0,
    gas: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
) -> SentTx:
    acct = Account.from_key(private_key)
    from_addr = acct.address
    to_addr = to_checksum_address(to)
    nonce = rpc.eth_get_transaction_count(from_addr, "pending")

    gas_price = gas_price_wei if gas_price_wei is not None else rpc.eth_gas_price()

    tx_for_estimate: Dict[str, Any] = {
        "from": from_addr,
        "to": to_addr,
        "value": _int_to_hex(value_wei),
        "data": data,
    }
    gas_est = gas if gas is not None else rpc.eth_estimate_gas(tx_for_estimate)
    gas_final = int(max(gas_est + 50_000, int(gas_est * 1.2)))

    tx: Dict[str, Any] = {
        "nonce": nonce,
        "to": to_addr,
        "value": value_wei,
        "gas": gas_final,
        "gasPrice": gas_price,
        "data": data,
        "chainId": chain_id,
    }
    signed = Account.sign_transaction(tx, private_key)
    raw_hex = to_hex(signed.raw_transaction)
    local_hash = to_hex(signed.hash)
    try:
        tx_hash = rpc.eth_send_raw_transaction(raw_hex)
        return SentTx(tx_hash=tx_hash, from_address=from_addr, nonce=nonce)
    except JsonRpcError:
        # Hardhat can return a JSON-RPC error on submission for transactions that revert,
        # even though the tx is still accepted/mined. Best-effort: check by local hash.
        for _ in range(15):
            try:
                if rpc.eth_get_transaction_by_hash(local_hash) is not None:
                    return SentTx(tx_hash=local_hash, from_address=from_addr, nonce=nonce)
                if rpc.eth_get_transaction_receipt(local_hash) is not None:
                    return SentTx(tx_hash=local_hash, from_address=from_addr, nonce=nonce)
            except Exception:
                pass
            time.sleep(0.1)
        raise


def _latest_base_fee_wei(rpc: JsonRpcClient) -> Optional[int]:
    try:
        blk = rpc.call("eth_getBlockByNumber", ["latest", False])
        if not isinstance(blk, dict):
            return None
        bf = blk.get("baseFeePerGas")
        if isinstance(bf, str) and bf.startswith("0x"):
            return int(bf, 16)
    except Exception:
        return None
    return None


def _max_priority_fee_wei(rpc: JsonRpcClient) -> Optional[int]:
    try:
        v = rpc.call("eth_maxPriorityFeePerGas")
        if isinstance(v, str) and v.startswith("0x"):
            return int(v, 16)
    except Exception:
        return None
    return None


def sign_and_send_eip1559(
    rpc: JsonRpcClient,
    *,
    chain_id: int,
    private_key: str,
    to: str,
    data: str,
    value_wei: int = 0,
    gas: Optional[int] = None,
    max_fee_per_gas_wei: Optional[int] = None,
    max_priority_fee_per_gas_wei: Optional[int] = None,
) -> SentTx:
    acct = Account.from_key(private_key)
    from_addr = acct.address
    to_addr = to_checksum_address(to)
    nonce = rpc.eth_get_transaction_count(from_addr, "pending")

    base_fee = _latest_base_fee_wei(rpc)
    prio = max_priority_fee_per_gas_wei if max_priority_fee_per_gas_wei is not None else _max_priority_fee_wei(rpc)
    if prio is None:
        # Conservative fallback for public RPCs that don't expose maxPriorityFeePerGas.
        prio = int(1 * GWEI)
    # For Sepolia and PoCs, keep a minimum tip so txs don't sit forever near baseFee.
    prio = int(max(prio, int(1 * GWEI)))

    if base_fee is None:
        # Legacy-only networks: approximate via eth_gasPrice.
        base_fee = rpc.eth_gas_price()

    if max_fee_per_gas_wei is None:
        # Common heuristic: 2x baseFee + tip.
        max_fee_per_gas_wei = int(base_fee * 2 + prio)

    tx_for_estimate: Dict[str, Any] = {
        "from": from_addr,
        "to": to_addr,
        "value": _int_to_hex(value_wei),
        "data": data,
    }
    gas_est = gas if gas is not None else rpc.eth_estimate_gas(tx_for_estimate)
    gas_final = int(max(gas_est + 50_000, int(gas_est * 1.2)))

    tx: Dict[str, Any] = {
        "type": 2,
        "nonce": nonce,
        "to": to_addr,
        "value": value_wei,
        "gas": gas_final,
        "maxFeePerGas": int(max_fee_per_gas_wei),
        "maxPriorityFeePerGas": int(prio),
        "data": data,
        "chainId": chain_id,
    }
    signed = Account.sign_transaction(tx, private_key)
    raw_hex = to_hex(signed.raw_transaction)
    local_hash = to_hex(signed.hash)
    try:
        tx_hash = rpc.eth_send_raw_transaction(raw_hex)
        return SentTx(tx_hash=tx_hash, from_address=from_addr, nonce=nonce)
    except JsonRpcError:
        # Best-effort (same rationale as legacy path).
        for _ in range(15):
            try:
                if rpc.eth_get_transaction_by_hash(local_hash) is not None:
                    return SentTx(tx_hash=local_hash, from_address=from_addr, nonce=nonce)
                if rpc.eth_get_transaction_receipt(local_hash) is not None:
                    return SentTx(tx_hash=local_hash, from_address=from_addr, nonce=nonce)
            except Exception:
                pass
            time.sleep(0.1)
        raise


def sign_and_send(
    rpc: JsonRpcClient,
    *,
    chain_id: int,
    private_key: str,
    to: str,
    data: str,
    value_wei: int = 0,
    gas: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
) -> SentTx:
    # Hardhat/local: legacy is simplest.
    if int(chain_id) == 31337:
        return sign_and_send_legacy(
            rpc,
            chain_id=chain_id,
            private_key=private_key,
            to=to,
            data=data,
            value_wei=value_wei,
            gas=gas,
            gas_price_wei=gas_price_wei,
        )

    # Public networks: use EIP-1559 so the tx does not get stuck around baseFee.
    return sign_and_send_eip1559(
        rpc,
        chain_id=chain_id,
        private_key=private_key,
        to=to,
        data=data,
        value_wei=value_wei,
        gas=gas,
    )


def wait_for_receipt(rpc: JsonRpcClient, tx_hash: str, *, timeout_sec: float = 120.0, poll_sec: float = 1.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        receipt = rpc.eth_get_transaction_receipt(tx_hash)
        if receipt is not None:
            return receipt
        time.sleep(poll_sec)
    raise TimeoutError(f"Timed out waiting for receipt: {tx_hash}")
