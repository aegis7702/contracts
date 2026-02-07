from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path, *, override: bool = False) -> None:
    """
    Minimal dotenv loader:
    - Supports `KEY=value` lines
    - Ignores blank lines and `#` comments
    - Does not handle quotes/escapes (keep PoC simple)
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return

    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if override or os.environ.get(key) is None:
            os.environ[key] = value

