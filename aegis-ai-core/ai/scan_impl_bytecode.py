from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ai.call_llm import build_llm_caller
from ai.json_utils import extract_json


BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_IMPL = BASE_DIR / "ai" / "prompt_impl_bytecode.md"
PROMPT_SWAP = BASE_DIR / "ai" / "prompt_swap_bytecode.md"
SAMPLES_JSON = BASE_DIR / "ai" / "samples" / "bytecode" / "sample_impl_bytecode.json"
REFERENCE_DIR = BASE_DIR / "ai" / "samples" / "reference"


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


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    alt = BASE_DIR / path
    if alt.exists():
        return alt.read_text(encoding="utf-8")
    alt2 = BASE_DIR.parent / path
    if alt2.exists():
        return alt2.read_text(encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _extract_section(md_text: str, heading: str) -> str:
    pattern = rf"## {re.escape(heading)}\s*(.*?)(?:\n## |\Z)"
    match = re.search(pattern, md_text, flags=re.S)
    if not match:
        return ""
    block = match.group(1).strip()
    lines = [line.strip("- ").strip() for line in block.splitlines() if line.strip()]
    return " | ".join(lines)


def _extract_summary(md_text: str) -> str:
    return _extract_section(md_text, "Summary")


def _extract_label(md_text: str) -> str:
    match = re.search(r"`(SAFE|UNSAFE)`", md_text)
    return match.group(1) if match else "UNKNOWN"


def load_impl_samples() -> List[Dict[str, str]]:
    samples: List[Dict[str, str]] = []
    if not SAMPLES_JSON.exists():
        return samples
    with open(SAMPLES_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    modules: List[Dict[str, Any]] = data.get("modules", [])
    for m in modules:
        name = m.get("contractName") or Path(str(m.get("sourceName", ""))).stem
        md_path = REFERENCE_DIR / f"{name}.md"
        md_text = _read_text(md_path) if md_path.exists() else ""
        summary = _extract_summary(md_text)
        label = _extract_label(md_text)
        runtime_size = m.get("runtimeSizeBytes")
        bytecode_hash = m.get("bytecodeHash")
        entry = {
            "name": name,
            "label": label,
            "summary": summary,
            "runtime_size": str(runtime_size) if runtime_size is not None else "",
            "bytecode_hash": bytecode_hash or "",
        }
        samples.append(entry)
    return samples


def build_fewshot_context(samples: List[Dict[str, str]]) -> str:
    if not samples:
        return ""
    parts: List[str] = ["Few-shot examples (ground truth):"]
    for s in samples:
        line = (
            f"[{s['name']}] Label: {s['label']} "
            f"| Runtime bytes: {s.get('runtime_size','')} "
            f"| Bytecode hash: {s.get('bytecode_hash','')}"
        ).strip()
        summary = s.get("summary") or ""
        if summary:
            line += f"\nSummary: {summary}"
        parts.append(line)
    return "\n\n".join(parts)


def _extract_first_json_block(text: str) -> Optional[str]:
    in_string = False
    escape = False
    depth = 0
    start_idx = None
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start_idx is not None:
                    return text[start_idx : i + 1]
    return None


def _extract_json_loose(text: str) -> Optional[Dict[str, Any]]:
    # 1) 우선 기존 extractor 시도
    try:
        data = extract_json(text)
        if data:
            return data
    except Exception:
        pass
    # 2) fenced codeblock ```json {...}``` 처리
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    # 3) 텍스트 내 첫 JSON 블록 파싱
    block = _extract_first_json_block(text)
    if block:
        try:
            return json.loads(block)
        except Exception:
            pass
    return None


def _supports_response_format(model: Optional[str]) -> bool:
    return bool(model and model.strip())


def _format_with_model(raw_text: str, provider: str, model: Optional[str]) -> Optional[Dict[str, Any]]:
    if provider != "openai" or not model or not _supports_response_format(model):
        return None
    caller = build_llm_caller(provider)
    system_prompt = (
        "You are a formatter. Convert the input into a JSON object that strictly follows the schema. "
        "Return JSON only, no markdown or extra text."
    )
    user_prompt = f"<raw_output>\n{raw_text}\n</raw_output>"
    text = caller.chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        max_tokens=400,
        response_format=_response_format(),
    )
    return _extract_json_loose(text)


def _call_with_retry(
    caller,
    prompt: str,
    provider: str,
    model: Optional[str],
    kwargs: Dict[str, Any],
    format_model: Optional[str] = None,
) -> Dict[str, Any]:
    def _chat(system_prompt: str) -> str:
        return caller.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Return JSON only."},
            ],
            **kwargs,
        )

    last_text = _chat(prompt)
    data = _extract_json_loose(last_text)
    if data:
        return data

    strict_prompt = (
        prompt
        + "\n\nSTRICT OUTPUT: Return JSON only that matches the schema. "
        "No markdown, code fences, or extra text."
    )
    last_text = _chat(strict_prompt)
    data = _extract_json_loose(last_text)
    if data:
        return data

    formatted = _format_with_model(last_text, provider=provider, model=format_model)
    if formatted:
        return formatted

    raise RuntimeError("LLM did not return valid JSON.")


def audit_impl_bytecode(
    *,
    chain_id: int,
    impl_address: str,
    bytecode_hex: str,
    provider: str = "",
    model: str = "",
    reasoning: str = "",
    format_model: Optional[str] = None,
) -> Dict[str, Any]:
    provider = provider or os.getenv("AEGIS_LLM_PROVIDER", "openai")
    model = model or os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14")
    reasoning = reasoning or os.getenv("AEGIS_LLM_REASONING", "none")
    format_model = format_model or os.getenv("AEGIS_LLM_FORMAT_MODEL", "gpt-4o-mini")

    prompt = _read_prompt(PROMPT_IMPL)
    samples = load_impl_samples()
    fewshot = build_fewshot_context(samples)
    if fewshot:
        prompt = f"{prompt}\n\n{fewshot}"
    prompt = prompt.replace("{chain_id}", str(chain_id))
    prompt = prompt.replace("{impl_address}", impl_address)
    prompt = prompt.replace("{bytecode_hex}", bytecode_hex)
    caller = build_llm_caller(provider)
    kwargs: Dict[str, Any] = {"model": model, "max_tokens": 900}
    if provider == "openai" and _supports_response_format(model):
        kwargs["response_format"] = _response_format()
    if reasoning and reasoning != "none":
        kwargs["reasoning"] = {"effort": reasoning}
    return _call_with_retry(caller, prompt, provider, model, kwargs, format_model=format_model)


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
    format_model: Optional[str] = None,
) -> Dict[str, Any]:
    provider = provider or os.getenv("AEGIS_LLM_PROVIDER", "openai")
    model = model or os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14")
    reasoning = reasoning or os.getenv("AEGIS_LLM_REASONING", "none")
    format_model = format_model or os.getenv("AEGIS_LLM_FORMAT_MODEL", "gpt-4o-mini")

    prompt = _read_prompt(PROMPT_SWAP)
    prompt = prompt.replace("{chain_id}", str(chain_id))
    prompt = prompt.replace("{current_impl_address}", current_impl_address)
    prompt = prompt.replace("{current_bytecode_hex}", current_bytecode_hex)
    prompt = prompt.replace("{new_impl_address}", new_impl_address)
    prompt = prompt.replace("{new_bytecode_hex}", new_bytecode_hex)
    caller = build_llm_caller(provider)
    kwargs: Dict[str, Any] = {"model": model, "max_tokens": 900}
    if provider == "openai" and _supports_response_format(model):
        kwargs["response_format"] = _response_format()
    if reasoning and reasoning != "none":
        kwargs["reasoning"] = {"effort": reasoning}
    return _call_with_retry(caller, prompt, provider, model, kwargs, format_model=format_model)


def main() -> None:
    p = argparse.ArgumentParser(description="Audit EIP-7702 impl bytecode (PoC)")
    p.add_argument("--chain-id", type=int, required=True)
    p.add_argument("--impl-address", required=True)
    p.add_argument("--bytecode-hex", required=True)
    p.add_argument("--provider", default=os.getenv("AEGIS_LLM_PROVIDER", "openai"))
    p.add_argument("--model", default=os.getenv("AEGIS_LLM_MODEL", "gpt-4.1-2025-04-14"))
    p.add_argument("--reasoning", default=os.getenv("AEGIS_LLM_REASONING", "none"))
    p.add_argument("--format-model", default=os.getenv("AEGIS_LLM_FORMAT_MODEL", "gpt-4o-mini"))
    args = p.parse_args()

    result = audit_impl_bytecode(
        chain_id=args.chain_id,
        impl_address=args.impl_address,
        bytecode_hex=args.bytecode_hex,
        provider=args.provider,
        model=args.model,
        reasoning=args.reasoning,
        format_model=args.format_model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
