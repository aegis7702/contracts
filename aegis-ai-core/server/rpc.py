from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


_id_counter = itertools.count(1)


class JsonRpcError(RuntimeError):
    def __init__(self, message: str, *, data: Any = None):
        super().__init__(message)
        self.data = data


@dataclass(frozen=True)
class JsonRpcClient:
    url: str
    timeout_sec: float = 30.0

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_sec)

    def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(_id_counter),
            "method": method,
            "params": params or [],
        }
        with self._client() as client:
            resp = client.post(self.url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data and data["error"] is not None:
            err = data["error"]
            raise JsonRpcError(f"RPC error calling {method}: {err}", data=err)
        return data.get("result")

    # --- Convenience wrappers
    def eth_chain_id(self) -> int:
        return int(self.call("eth_chainId"), 16)

    def eth_block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def eth_get_code(self, address: str, block: str = "latest") -> str:
        return self.call("eth_getCode", [address, block])

    def eth_get_transaction_by_hash(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self.call("eth_getTransactionByHash", [tx_hash])

    def eth_get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def eth_get_block_by_number(self, block_number: int, full_txs: bool = True) -> Dict[str, Any]:
        return self.call("eth_getBlockByNumber", [hex(block_number), bool(full_txs)])

    def eth_call(self, tx: Dict[str, Any], block: str = "latest") -> str:
        return self.call("eth_call", [tx, block])

    def eth_gas_price(self) -> int:
        return int(self.call("eth_gasPrice"), 16)

    def eth_estimate_gas(self, tx: Dict[str, Any]) -> int:
        return int(self.call("eth_estimateGas", [tx]), 16)

    def eth_get_transaction_count(self, address: str, tag: str = "pending") -> int:
        return int(self.call("eth_getTransactionCount", [address, tag]), 16)

    def eth_send_raw_transaction(self, raw_tx_hex: str) -> str:
        return self.call("eth_sendRawTransaction", [raw_tx_hex])

