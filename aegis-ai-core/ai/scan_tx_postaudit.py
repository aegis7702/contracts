from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from ai.call_llm import build_llm_caller
from ai.json_utils import extract_json


BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT = (BASE_DIR / "ai" / "prompt_tx_postaudit.md").read_text(encoding="utf-8")


def _response_format() -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "aegis_tx_postaudit_result",
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


def audit_tx_postaudit(
    *,
    chain_id: int,
    tx: Dict[str, Any],
    receipt: Dict[str, Any],
    impl_record: Dict[str, Any],
    provider: str = "",
    model: str = "",
    reasoning: str = "",
) -> Dict[str, Any]:
    provider = provider or os.getenv("AEGIS_LLM_PROVIDER", "openai")
    model = model or os.getenv("AEGIS_LLM_MODEL", "gpt-4o-mini")
    reasoning = reasoning or os.getenv("AEGIS_LLM_REASONING", "none")

    if provider == "mock":
        status = receipt.get("status")
        # JSON-RPC receipts return hex strings ("0x1"/"0x0") on most nodes.
        status_i = int(status, 16) if isinstance(status, str) and status.startswith("0x") else int(status or 0)
        label = "SAFE" if status_i == 1 else "UNSAFE"
        reasons = []
        if status_i == 1:
            reasons.append("receipt.status=1 (success)")
        else:
            reasons.append(f"receipt.status={status} (failure)")
        return {
            "label": label,
            "confidence": 0.65 if label == "SAFE" else 0.95,
            "name": f"PostAudit@{str(tx.get('hash') or '')[:10]}",
            "summary": f"Mock postaudit: {label}",
            "description": "Mock analysis result.\nBased on receipt status only.\nFor e2e flow testing only.",
            "reasons": reasons,
            "matched_patterns": [],
        }

    system_prompt = PROMPT
    system_prompt = system_prompt.replace("{chain_id}", str(chain_id))
    system_prompt = system_prompt.replace("{tx_json}", json.dumps(tx, ensure_ascii=False, indent=2))
    system_prompt = system_prompt.replace("{receipt_json}", json.dumps(receipt, ensure_ascii=False, indent=2))
    system_prompt = system_prompt.replace("{impl_record_json}", json.dumps(impl_record, ensure_ascii=False, indent=2))
    caller = build_llm_caller(provider)
    kwargs: Dict[str, Any] = {"model": model, "max_tokens": 900, "response_format": _response_format()}
    if reasoning and reasoning != "none":
        kwargs["reasoning"] = {"effort": reasoning}
    text = caller.chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Return JSON only."},
        ],
        **kwargs,
    )
    data = extract_json(text)
    if not data:
        raise RuntimeError("LLM did not return valid JSON.")
    return data
