from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ai.call_llm import build_llm_caller
from ai.json_utils import extract_json


BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_IMPL = BASE_DIR / "ai" / "prompt_impl_bytecode.md"
PROMPT_SWAP = BASE_DIR / "ai" / "prompt_swap_bytecode.md"


def _response_format() -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "aegis_audit_result",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string", "enum": ["SAFE", "UNSAFE"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "reasons": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                    "matched_patterns": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "confidence", "name", "summary", "description", "reasons", "matched_patterns"],
            },
        },
    }


def _read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def audit_impl_bytecode(
    *,
    chain_id: int,
    impl_address: str,
    bytecode_hex: str,
    provider: str = "",
    model: str = "",
    reasoning: str = "",
) -> Dict[str, Any]:
    provider = provider or os.getenv("AEGIS_LLM_PROVIDER", "openai")
    model = model or os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14")
    reasoning = reasoning or os.getenv("AEGIS_LLM_REASONING", "none")

    prompt = _read_prompt(PROMPT_IMPL)
    prompt = prompt.replace("{chain_id}", str(chain_id))
    prompt = prompt.replace("{impl_address}", impl_address)
    prompt = prompt.replace("{bytecode_hex}", bytecode_hex)
    caller = build_llm_caller(provider)
    kwargs: Dict[str, Any] = {"model": model, "max_tokens": 900, "response_format": _response_format()}
    if reasoning and reasoning != "none":
        kwargs["reasoning"] = {"effort": reasoning}
    text = caller.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Return JSON only."},
        ],
        **kwargs,
    )
    data = extract_json(text)
    if not data:
        raise RuntimeError("LLM did not return valid JSON.")
    return data


def audit_swap_bytecode(
    *,
    chain_id: int,
    current_impl_address: str,
    current_bytecode_hex: str,
    new_impl_address: str,
    new_bytecode_hex: str,
    provider: str = "",
    model: str = "",
    reasoning: str = "",
) -> Dict[str, Any]:
    provider = provider or os.getenv("AEGIS_LLM_PROVIDER", "openai")
    model = model or os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14")
    reasoning = reasoning or os.getenv("AEGIS_LLM_REASONING", "none")

    prompt = _read_prompt(PROMPT_SWAP)
    prompt = prompt.replace("{chain_id}", str(chain_id))
    prompt = prompt.replace("{current_impl_address}", current_impl_address)
    prompt = prompt.replace("{current_bytecode_hex}", current_bytecode_hex)
    prompt = prompt.replace("{new_impl_address}", new_impl_address)
    prompt = prompt.replace("{new_bytecode_hex}", new_bytecode_hex)
    caller = build_llm_caller(provider)
    kwargs: Dict[str, Any] = {"model": model, "max_tokens": 900, "response_format": _response_format()}
    if reasoning and reasoning != "none":
        kwargs["reasoning"] = {"effort": reasoning}
    text = caller.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Return JSON only."},
        ],
        **kwargs,
    )
    data = extract_json(text)
    if not data:
        raise RuntimeError("LLM did not return valid JSON.")
    return data


def main() -> None:
    p = argparse.ArgumentParser(description="Audit EIP-7702 impl bytecode (PoC)")
    p.add_argument("--chain-id", type=int, required=True)
    p.add_argument("--impl-address", required=True)
    p.add_argument("--bytecode-hex", required=True)
    p.add_argument("--provider", default=os.getenv("AEGIS_LLM_PROVIDER", "openai"))
    p.add_argument("--model", default=os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14"))
    p.add_argument("--reasoning", default=os.getenv("AEGIS_LLM_REASONING", "none"))
    args = p.parse_args()

    result = audit_impl_bytecode(
        chain_id=args.chain_id,
        impl_address=args.impl_address,
        bytecode_hex=args.bytecode_hex,
        provider=args.provider,
        model=args.model,
        reasoning=args.reasoning,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
