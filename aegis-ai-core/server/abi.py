from __future__ import annotations

from typing import Any, Iterable, List, Sequence, Tuple

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_bytes, to_checksum_address


def function_selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def encode_calldata(signature: str, arg_types: Sequence[str], args: Sequence[Any]) -> str:
    sel = function_selector(signature)
    enc = abi_encode(list(arg_types), list(args))
    return "0x" + (sel + enc).hex()


def decode_call_result(output_hex: str, out_types: Sequence[str]) -> Tuple[Any, ...]:
    if output_hex is None:
        raise ValueError("Missing output")
    if not isinstance(output_hex, str) or not output_hex.startswith("0x"):
        raise ValueError(f"Invalid output hex: {output_hex!r}")
    data = to_bytes(hexstr=output_hex)
    if len(data) == 0:
        return tuple()
    return tuple(abi_decode(list(out_types), data))


def checksum(addr: str) -> str:
    return to_checksum_address(addr)

