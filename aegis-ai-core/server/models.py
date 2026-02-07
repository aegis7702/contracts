from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["SAFE", "UNSAFE"]
    confidence: float
    name: str
    summary: str
    description: str
    reasons: List[str]
    matched_patterns: List[str] = Field(default_factory=list)


class ImplScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    implAddress: str


class ImplScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    implAddress: str
    audit: AuditResult
    reasonsText: str
    registryTxHash: str


class ImplAuditApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    wallet: str
    newImplAddress: str
    mode: Literal["init", "swap"] = "swap"


class TxPrecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    chainId: int
    from_address: str = Field(alias="from")
    to: Optional[str] = None
    value: str = "0"
    data: str = "0x"
    txType: Optional[int] = None
    authorizationList: Optional[List[Dict[str, Any]]] = None


class TxPrecheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    allow: bool
    audit: AuditResult
    reasonsText: str
    walletCurrentImpl: Optional[str] = None
    walletCurrentImplRecord: Optional[Dict[str, Any]] = None


class WatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    wallet: str


class WatchItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet: str
    startBlock: int
    addedAt: str


class WatchListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    items: List[WatchItemResponse]


class AuditApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chainId: int
    wallet: str
    mode: Literal["init", "swap"]
    currentImpl: Optional[str] = None
    newImpl: str
    newImplAudit: AuditResult
    newImplReasonsText: str
    newImplRegistryTxHash: str
    swapAudit: Optional[AuditResult] = None
    swapReasonsText: Optional[str] = None
    swapRegistryTxHash: Optional[str] = None
    allow: bool
    txTemplate: Optional[Dict[str, Any]] = None

