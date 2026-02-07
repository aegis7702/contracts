import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ai.call_llm import build_llm_caller, fetch_x_search_context


BASE_DIR = Path(__file__).resolve().parents[1]
SAMPLES_DIR = BASE_DIR / "ai" / "samples" / "reference"
DEFAULT_X_SEARCH_KEYWORDS = [
    "EIP-7702 vulnerability",
    "account abstraction exploit",
    "delegatecall vulnerability",
    "tx.origin auth bug",
    "token approval exploit",
    "self-call execution risk",
    "storage collision vulnerability",
]


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


def _extract_summary(md_text: str) -> str:
    match = re.search(r"## Summary\s*(.*?)(?:\n## |\Z)", md_text, flags=re.S)
    if not match:
        return ""
    summary_block = match.group(1).strip()
    summary_lines = [line.strip("- ").strip() for line in summary_block.splitlines() if line.strip()]
    return " | ".join(summary_lines)


def _extract_label(md_text: str) -> str:
    match = re.search(r"`(SAFE|UNSAFE)`", md_text)
    return match.group(1) if match else "UNKNOWN"


def load_samples() -> List[Dict[str, str]]:
    samples: List[Dict[str, str]] = []
    for md_path in sorted(SAMPLES_DIR.glob("Module*7702.md")):
        sol_path = md_path.with_suffix(".sol")
        md_text = _read_text(md_path)
        sol_text = _read_text(sol_path) if sol_path.exists() else ""
        samples.append(
            {
                "name": md_path.stem,
                "label": _extract_label(md_text),
                "summary": _extract_summary(md_text),
                "md": md_text.strip(),
                "code": sol_text.strip(),
            }
        )
    return samples


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,64}", text.lower())


def select_top_k_samples(code: str, samples: List[Dict[str, str]], k: int) -> List[Dict[str, str]]:
    if k <= 0 or k >= len(samples):
        return samples
    code_tokens = set(_tokenize(code))
    scored: List[Tuple[float, Dict[str, str]]] = []
    for s in samples:
        sample_text = f"{s.get('summary','')}\n{s.get('code','')}"
        sample_tokens = set(_tokenize(sample_text))
        if not sample_tokens:
            score = 0.0
        else:
            overlap = code_tokens.intersection(sample_tokens)
            score = len(overlap) / max(1, len(sample_tokens))
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:k]]


def static_signals(code: str) -> List[str]:
    signals: List[str] = []
    if "tx.origin" in code:
        signals.append("uses tx.origin for authorization")
    if re.search(r"approve\s*\(\s*address\s*,\s*uint256\s*\)", code) and "type(uint256).max" in code:
        signals.append("post-action approve(max) behavior")
    if re.search(r"\bprimary\b", code) and re.search(r"\bmsg\.sender\b", code) and "primary" in code:
        if re.search(r"primary\s*=\s*msg\.sender", code):
            signals.append("mutable primary assignment to msg.sender")
    return signals


def build_rag_context(
    samples: List[Dict[str, str]],
    include_code: bool = False,
    extra_sections: Optional[List[str]] = None,
) -> str:
    parts: List[str] = []
    for s in samples:
        lines = [
            f"[{s['name']}]",
            f"Label: {s['label']}",
            "Doc:",
            s["md"],
        ]
        if include_code and s.get("code"):
            lines.extend(["Code:", s["code"]])
        parts.append("\n".join(lines))
    if extra_sections:
        for section in extra_sections:
            if section:
                parts.append(section)
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


def _extract_json(text: str) -> Optional[Dict]:
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    block = _extract_first_json_block(text)
    if not block:
        return None
    try:
        return json.loads(block)
    except Exception:
        return None


def _json_response_format() -> Dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "module_risk_result",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string", "enum": ["SAFE", "UNSAFE"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasons": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                    "matched_patterns": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "confidence", "reasons", "matched_patterns"],
            },
        },
    }


def _supports_response_format(model: Optional[str]) -> bool:
    if not model:
        return False
    model = model.strip().lower()
    return model.startswith("gpt-4o-mini")


def _format_with_model(raw_text: str, provider: str, model: Optional[str]) -> Optional[Dict]:
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
        response_format=_json_response_format(),
    )
    return _extract_json(text)


def llm_classify(
    code: str,
    provider: str,
    model: Optional[str] = None,
    samples: Optional[List[Dict[str, str]]] = None,
    reasoning: Optional[str] = None,
    prompt_path: Optional[str] = None,
    include_rag_code: bool = False,
    retry_on_format: bool = True,
    format_model: Optional[str] = None,
    extra_rag_sections: Optional[List[str]] = None,
) -> Dict:
    rag_context = build_rag_context(
        samples or [],
        include_code=include_rag_code,
        extra_sections=extra_rag_sections,
    )

    prompt_file_path = Path(prompt_path) if prompt_path else (BASE_DIR / "ai" / "prompt_detail.md")
    with open(prompt_file_path, "r", encoding="utf-8") as file:
        prompt = file.read()
    system_prompt = prompt.replace("{rag_context}", rag_context)
    user_prompt = f"<target_code>\n{code}\n</target_code>"
    caller = build_llm_caller(provider)
    call_kwargs = dict(
        model=model,
        max_tokens=600,
    )
    if reasoning and reasoning != "none":
        call_kwargs["reasoning"] = {"effort": reasoning}
    if provider == "openai" and _supports_response_format(model):
        call_kwargs["response_format"] = _json_response_format()

    def _call(system_text: str) -> str:
        return caller.chat(
            [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_prompt},
            ],
            **call_kwargs,
        )

    text = _call(system_prompt)
    data = _extract_json(text)
    if data:
        return data

    if retry_on_format:
        strict_system = (
            system_prompt
            + "\n\n"
            + "STRICT OUTPUT: Return JSON only that matches the schema. "
            + "Do not include markdown, commentary, or extra text. "
            + "Return a single-line JSON object (no line breaks inside strings)."
        )
        text = _call(strict_system)
        data = _extract_json(text)
        if data:
            return data

    if format_model:
        formatted = _format_with_model(text, provider=provider, model=format_model)
        if formatted:
            return formatted

    raise RuntimeError("LLM did not return valid JSON.")


def analyze_code(
    code: str,
    provider: str,
    brief_model: str,
    detail_model: str,
    reasoning: str = "none",
    brief_safe_threshold: float = 0.85,
    rag_top_k: int = 5,
    format_model: Optional[str] = None,
    x_search: bool = False,
    x_search_days: int = 30,
    x_search_limit: int = 5,
    x_search_model: str = "grok-4-1-fast-reasoning",
) -> Dict:
    brief_prompt_path = BASE_DIR / "ai" / "prompt_brief.md"
    detail_prompt_path = BASE_DIR / "ai" / "prompt_detail.md"
    signals = static_signals(code)
    samples = load_samples()
    if rag_top_k:
        samples = select_top_k_samples(code, samples, rag_top_k)
    x_context = ""
    if x_search:
        x_context = fetch_x_search_context(
            DEFAULT_X_SEARCH_KEYWORDS,
            days=x_search_days,
            limit=x_search_limit,
            model=x_search_model,
        )
    brief = llm_classify(
        code,
        provider=provider,
        model=brief_model,
        samples=samples,
        reasoning=reasoning,
        prompt_path=brief_prompt_path,
        format_model=format_model,
        extra_rag_sections=[x_context] if x_context else None,
    )
    brief["analysis_source"] = "llm-brief"
    if brief.get("label") == "SAFE" and float(brief.get("confidence", 0)) >= brief_safe_threshold:
        data = brief
    else:
        data = llm_classify(
            code,
            provider=provider,
            model=detail_model,
            samples=samples,
            reasoning=reasoning,
            prompt_path=detail_prompt_path,
            format_model=format_model,
            extra_rag_sections=[x_context] if x_context else None,
        )
        data["analysis_source"] = "llm-detail"
    if signals:
        data.setdefault("reasons", [])
        data.setdefault("matched_patterns", [])
        data["reasons"].append("Static signals observed (non-binding).")
        data["matched_patterns"] = list(dict.fromkeys(data["matched_patterns"] + signals))
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="EIP-7702 module risk detector")
    parser.add_argument("--path", help="Solidity file path to analyze", required=True)
    parser.add_argument("--provider", default="openai", help="LLM provider (openai|gemini|grok)")
    parser.add_argument(
        "--brief-model",
        dest="brief_model",
        default="gpt-5-mini",
        help="Brief LLM model name",
    )
    parser.add_argument(
        "--detail-model",
        dest="detail_model",
        default="gpt-5.2",
        help="Detail LLM model name",
    )
    parser.add_argument("--reasoning", default="none", help="Reasoning effort (minimal|low|medium|high|none)")
    parser.add_argument(
        "--brief-safe-threshold",
        dest="brief_safe_threshold",
        type=float,
        default=0.6,
        help="Safe confidence to skip detail model",
    )
    parser.add_argument(
        "--rag-top-k",
        dest="rag_top_k",
        type=int,
        default=5,
        help="Use top-k most similar samples for RAG (0 = all)",
    )
    parser.add_argument(
        "--format-model",
        dest="format_model",
        default="gpt-4o-mini",
        help="Formatter model for JSON repair (OpenAI only)",
    )
    parser.add_argument(
        "--x-search",
        dest="x_search",
        action="store_true",
        help="Enable X search RAG via xAI",
    )
    parser.add_argument(
        "--x-search-days",
        dest="x_search_days",
        type=int,
        default=5,
        help="X search lookback window in days",
    )
    parser.add_argument(
        "--x-search-limit",
        dest="x_search_limit",
        type=int,
        default=3,
        help="Max bullet points per keyword",
    )
    parser.add_argument(
        "--x-search-model",
        dest="x_search_model",
        default="grok-4-1-fast-reasoning",
        help="xAI model for x_search",
    )
    args = parser.parse_args()

    code = _read_text(Path(args.path))
    result = analyze_code(
        code,
        provider=args.provider,
        brief_model=args.brief_model,
        detail_model=args.detail_model,
        reasoning=args.reasoning,
        brief_safe_threshold=args.brief_safe_threshold,
        rag_top_k=args.rag_top_k,
        format_model=args.format_model,
        x_search=args.x_search,
        x_search_days=args.x_search_days,
        x_search_limit=args.x_search_limit,
        x_search_model=args.x_search_model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
