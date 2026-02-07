from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def extract_first_json_block(text: str) -> Optional[str]:
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


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        v = json.loads(text)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\\s*(\\{.*?\\})\\s*```", text, flags=re.S)
    if fenced:
        try:
            v = json.loads(fenced.group(1))
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    block = extract_first_json_block(text)
    if not block:
        return None
    try:
        v = json.loads(block)
        if isinstance(v, dict):
            return v
    except Exception:
        return None
    return None

