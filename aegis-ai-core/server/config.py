from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from server.env import load_env_file


REPO_ROOT = Path(__file__).resolve().parents[2]
AEGIS_CONTRACT_DIR = REPO_ROOT / "aegis-contract"
AEGIS_AI_CORE_DIR = REPO_ROOT / "aegis-ai-core"


def load_default_env() -> None:
    # Keys live in aegis-contract/.env.testnet
    load_env_file(AEGIS_CONTRACT_DIR / ".env.testnet", override=False)
    # OpenAI key lives in aegis-ai-core/ai/.env
    load_env_file(AEGIS_AI_CORE_DIR / "ai" / ".env", override=False)


load_default_env()


def rpc_url_for_chain(chain_id: int) -> str:
    direct = os.getenv(f"RPC_URL_{chain_id}")
    if direct:
        return direct
    if chain_id == 11155111:
        return (
            os.getenv("SEPOLIA_RPC_URL")
            or os.getenv("RPC_URL")
            or "https://ethereum-sepolia-rpc.publicnode.com"
        )
    if chain_id == 31337:
        return os.getenv("LOCAL_RPC_URL") or os.getenv("RPC_URL") or "http://127.0.0.1:8545"
    raise ValueError(f"Missing RPC URL for chainId={chain_id}. Set RPC_URL_{chain_id}.")


def worker_confirmations(chain_id: int) -> int:
    env = os.getenv("CONFIRMATIONS")
    if env is not None:
        try:
            return max(0, int(env))
        except Exception:
            pass
    return 2 if chain_id == 11155111 else 0


def worker_interval_sec() -> int:
    env = os.getenv("WORKER_INTERVAL_SEC")
    if env is not None:
        try:
            return max(1, int(env))
        except Exception:
            pass
    return 60


@dataclass(frozen=True)
class Deployments:
    chain_id: int
    registry: str
    guard: str


@lru_cache(maxsize=8)
def load_deployments(chain_id: int) -> Deployments:
    path = AEGIS_CONTRACT_DIR / "deployments" / f"chain-{chain_id}-latest.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing deployments file: {path}")
    data = path.read_text(encoding="utf-8")
    obj: Dict[str, Any] = __import__("json").loads(data)
    contracts: Dict[str, Any] = obj.get("contracts") or {}

    def _addr(name: str) -> str:
        v = contracts.get(name) or {}
        a = v.get("address")
        if not a:
            raise KeyError(f"Missing contract address for {name} in {path}")
        return str(a)

    return Deployments(
        chain_id=int(obj.get("chainId") or chain_id),
        registry=_addr("ImplSafetyRegistry"),
        guard=_addr("AegisGuardDelegator"),
    )


def publisher_private_key() -> str:
    pk = os.getenv("PUBLISHER_PK")
    if not pk:
        raise RuntimeError("Missing PUBLISHER_PK in env/.env.testnet")
    return pk


def sentinel_private_key() -> str:
    pk = os.getenv("SENTINEL_PK")
    if not pk:
        raise RuntimeError("Missing SENTINEL_PK in env/.env.testnet")
    return pk


def state_dir() -> Path:
    # Keep mutable state out of the code directory.
    return AEGIS_AI_CORE_DIR / "server_state"


def watchlist_path() -> Path:
    return state_dir() / "watchlist.json"


def cursor_path() -> Path:
    return state_dir() / "cursor.json"

