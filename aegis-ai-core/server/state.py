from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


@dataclass(frozen=True)
class WatchItem:
    wallet: str
    startBlock: int
    addedAt: str


def load_watchlist(path: Path) -> Dict[str, List[WatchItem]]:
    raw = _load_json(path, default={})
    out: Dict[str, List[WatchItem]] = {}
    if isinstance(raw, dict):
        for chain_id, items in raw.items():
            if not isinstance(items, list):
                continue
            parsed: List[WatchItem] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                wallet = str(it.get("wallet") or "")
                start = int(it.get("startBlock") or 0)
                added = str(it.get("addedAt") or "")
                if wallet and start >= 0:
                    parsed.append(WatchItem(wallet=wallet, startBlock=start, addedAt=added))
            out[str(chain_id)] = parsed
    return out


def save_watchlist(path: Path, watchlist: Dict[str, List[WatchItem]]) -> None:
    payload = {cid: [asdict(x) for x in items] for cid, items in watchlist.items()}
    _atomic_write_json(path, payload)


def add_watch(path: Path, *, chain_id: int, wallet: str, start_block: int, added_at: str) -> WatchItem:
    watchlist = load_watchlist(path)
    cid = str(chain_id)
    items = watchlist.get(cid, [])
    wallet_l = wallet.lower()
    items = [x for x in items if x.wallet.lower() != wallet_l]
    item = WatchItem(wallet=wallet, startBlock=int(start_block), addedAt=str(added_at))
    items.append(item)
    watchlist[cid] = items
    save_watchlist(path, watchlist)
    return item


def remove_watch(path: Path, *, chain_id: int, wallet: str) -> bool:
    watchlist = load_watchlist(path)
    cid = str(chain_id)
    items = watchlist.get(cid, [])
    wallet_l = wallet.lower()
    new_items = [x for x in items if x.wallet.lower() != wallet_l]
    changed = len(new_items) != len(items)
    watchlist[cid] = new_items
    save_watchlist(path, watchlist)
    return changed


def list_watch(path: Path, *, chain_id: int) -> List[WatchItem]:
    watchlist = load_watchlist(path)
    return watchlist.get(str(chain_id), [])


@dataclass(frozen=True)
class CursorState:
    lastProcessedBlock: int
    updatedAt: str


def load_cursors(path: Path) -> Dict[str, CursorState]:
    raw = _load_json(path, default={})
    out: Dict[str, CursorState] = {}
    if isinstance(raw, dict):
        for cid, v in raw.items():
            if not isinstance(v, dict):
                continue
            out[str(cid)] = CursorState(
                lastProcessedBlock=int(v.get("lastProcessedBlock") or 0),
                updatedAt=str(v.get("updatedAt") or ""),
            )
    return out


def save_cursors(path: Path, cursors: Dict[str, CursorState]) -> None:
    payload = {cid: asdict(v) for cid, v in cursors.items()}
    _atomic_write_json(path, payload)


def get_cursor(path: Path, *, chain_id: int) -> Optional[CursorState]:
    return load_cursors(path).get(str(chain_id))


def set_cursor(path: Path, *, chain_id: int, last_processed_block: int, updated_at: str) -> CursorState:
    cursors = load_cursors(path)
    st = CursorState(lastProcessedBlock=int(last_processed_block), updatedAt=str(updated_at))
    cursors[str(chain_id)] = st
    save_cursors(path, cursors)
    return st

