from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from server.abi import decode_call_result, encode_calldata
from server.eth import SentTx, sign_and_send, wait_for_receipt
from server.rpc import JsonRpcClient


@dataclass(frozen=True)
class ImplRecord:
    verdict: int  # 0=Unknown, 1=Safe, 2=Unsafe
    name: str
    summary: str
    description: str
    reasons: str
    updated_at: int
    codehash: str


@dataclass(frozen=True)
class SwapRecord:
    verdict: int
    name: str
    summary: str
    description: str
    reasons: str
    updated_at: int
    from_codehash: str
    to_codehash: str


class ImplSafetyRegistryClient:
    def __init__(self, rpc: JsonRpcClient, address: str):
        self.rpc = rpc
        self.address = address

    def extcodehash(self, addr: str) -> str:
        data = encode_calldata("extcodehash(address)", ["address"], [addr])
        out = self.rpc.eth_call({"to": self.address, "data": data})
        (h,) = decode_call_result(out, ["bytes32"])
        return "0x" + h.hex()

    def get_record_current(self, impl: str) -> ImplRecord:
        data = encode_calldata("getRecordCurrent(address)", ["address"], [impl])
        out = self.rpc.eth_call({"to": self.address, "data": data})
        verdict, name, summary, description, reasons, updated_at, codehash = decode_call_result(
            out, ["uint8", "string", "string", "string", "string", "uint64", "bytes32"]
        )
        return ImplRecord(
            verdict=int(verdict),
            name=str(name),
            summary=str(summary),
            description=str(description),
            reasons=str(reasons),
            updated_at=int(updated_at),
            codehash="0x" + codehash.hex(),
        )

    def get_swap_record_current(self, from_impl: str, to_impl: str) -> SwapRecord:
        data = encode_calldata("getSwapRecordCurrent(address,address)", ["address", "address"], [from_impl, to_impl])
        out = self.rpc.eth_call({"to": self.address, "data": data})
        verdict, name, summary, description, reasons, updated_at, from_codehash, to_codehash = decode_call_result(
            out,
            ["uint8", "string", "string", "string", "string", "uint64", "bytes32", "bytes32"],
        )
        return SwapRecord(
            verdict=int(verdict),
            name=str(name),
            summary=str(summary),
            description=str(description),
            reasons=str(reasons),
            updated_at=int(updated_at),
            from_codehash="0x" + from_codehash.hex(),
            to_codehash="0x" + to_codehash.hex(),
        )

    def set_record_current_and_wait(
        self,
        *,
        chain_id: int,
        publisher_pk: str,
        impl: str,
        verdict: int,
        name: str,
        summary: str,
        description: str,
        reasons: str,
        timeout_sec: float = 180.0,
    ) -> Tuple[SentTx, Dict[str, Any]]:
        data = encode_calldata(
            "setRecordCurrent(address,uint8,string,string,string,string)",
            ["address", "uint8", "string", "string", "string", "string"],
            [impl, int(verdict), name, summary, description, reasons],
        )
        sent = sign_and_send(
            self.rpc,
            chain_id=chain_id,
            private_key=publisher_pk,
            to=self.address,
            data=data,
            value_wei=0,
        )
        receipt = wait_for_receipt(self.rpc, sent.tx_hash, timeout_sec=timeout_sec)
        return sent, receipt

    def set_swap_record_current_and_wait(
        self,
        *,
        chain_id: int,
        publisher_pk: str,
        from_impl: str,
        to_impl: str,
        verdict: int,
        name: str,
        summary: str,
        description: str,
        reasons: str,
        timeout_sec: float = 180.0,
    ) -> Tuple[SentTx, Dict[str, Any]]:
        data = encode_calldata(
            "setSwapRecordCurrent(address,address,uint8,string,string,string,string)",
            ["address", "address", "uint8", "string", "string", "string", "string"],
            [from_impl, to_impl, int(verdict), name, summary, description, reasons],
        )
        sent = sign_and_send(
            self.rpc,
            chain_id=chain_id,
            private_key=publisher_pk,
            to=self.address,
            data=data,
            value_wei=0,
        )
        receipt = wait_for_receipt(self.rpc, sent.tx_hash, timeout_sec=timeout_sec)
        return sent, receipt


@dataclass(frozen=True)
class TxNote:
    name: str
    summary: str
    description: str
    reasons: str
    updated_at: int


class WalletGuardClient:
    """
    Interact with the guard logic at a *wallet address* (EIP-7702 delegated account).
    """

    def __init__(self, rpc: JsonRpcClient):
        self.rpc = rpc

    def get_implementation(self, wallet: str) -> str:
        data = encode_calldata("aegis_getImplementation()", [], [])
        out = self.rpc.eth_call({"to": wallet, "data": data})
        (addr,) = decode_call_result(out, ["address"])
        return str(addr)

    def is_frozen(self, wallet: str) -> bool:
        data = encode_calldata("aegis_isFrozen()", [], [])
        out = self.rpc.eth_call({"to": wallet, "data": data})
        (b,) = decode_call_result(out, ["bool"])
        return bool(b)

    def get_tx_note(self, wallet: str, tx_hash: str) -> TxNote:
        data = encode_calldata("aegis_getTxNote(bytes32)", ["bytes32"], [bytes.fromhex(tx_hash[2:])])
        out = self.rpc.eth_call({"to": wallet, "data": data})
        name, summary, description, reasons, updated_at = decode_call_result(
            out, ["string", "string", "string", "string", "uint64"]
        )
        return TxNote(
            name=str(name),
            summary=str(summary),
            description=str(description),
            reasons=str(reasons),
            updated_at=int(updated_at),
        )

    def set_tx_note_and_wait(
        self,
        *,
        chain_id: int,
        sentinel_pk: str,
        wallet: str,
        tx_hash: str,
        note: TxNote,
        timeout_sec: float = 180.0,
    ) -> Tuple[SentTx, Dict[str, Any]]:
        data = encode_calldata(
            "aegis_setTxNote(bytes32,string,string,string,string)",
            ["bytes32", "string", "string", "string", "string"],
            [bytes.fromhex(tx_hash[2:]), note.name, note.summary, note.description, note.reasons],
        )
        sent = sign_and_send(
            self.rpc,
            chain_id=chain_id,
            private_key=sentinel_pk,
            to=wallet,
            data=data,
            value_wei=0,
        )
        receipt = wait_for_receipt(self.rpc, sent.tx_hash, timeout_sec=timeout_sec)
        return sent, receipt

    def freeze_with_tx_note_and_wait(
        self,
        *,
        chain_id: int,
        sentinel_pk: str,
        wallet: str,
        tx_hash: str,
        freeze_reason: str,
        note: TxNote,
        timeout_sec: float = 180.0,
    ) -> Tuple[SentTx, Dict[str, Any]]:
        data = encode_calldata(
            "aegis_freezeWithTxNote(bytes32,string,string,string,string,string)",
            ["bytes32", "string", "string", "string", "string", "string"],
            [bytes.fromhex(tx_hash[2:]), freeze_reason, note.name, note.summary, note.description, note.reasons],
        )
        sent = sign_and_send(
            self.rpc,
            chain_id=chain_id,
            private_key=sentinel_pk,
            to=wallet,
            data=data,
            value_wei=0,
        )
        receipt = wait_for_receipt(self.rpc, sent.tx_hash, timeout_sec=timeout_sec)
        return sent, receipt
