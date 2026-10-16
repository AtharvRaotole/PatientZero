"""Typed API contracts (pydantic). Extra keys are allowed so tool payloads can grow without breaking clients."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["answered_with_path", "answered_from_memory", "not_in_graph", "unknown_entity"]


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    mode: Literal["auto", "llm", "deterministic"] = "auto"
    depth: int | None = Field(None, ge=1, le=6)
    fresh: bool = False
    session: str = Field("", max_length=64)


class FixRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    depth: int | None = Field(None, ge=1, le=6)
    session: str = Field("", max_length=64)


class TraceStep(Loose):
    tool: str
    query: str | None = None
    file: str | None = None
    cypher: str | None = None
    params: dict[str, Any] = {}
    ms: float = 0
    rows: int = 0
    write: bool = False
    note: str | None = None
    error: str | None = None


class PathInfo(Loose):
    via: list[str]
    requirements: list[str | None]
    requirement: str | None
    hops: int
    exposed: bool
    reason: str
    display: str


class Finding(Loose):
    package: str
    bad_version: str
    clean_version: str | None
    advisory_id: str | None
    exposed: bool
    path: PathInfo
    alternatives: list[PathInfo] = []
    path_count: int


class ServiceExposure(Loose):
    service: str
    tier: int | None
    team: str | None
    findings: list[Finding]
    min_hops: int
    exposed_packages: list[str]


class GraphView(Loose):
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


class AskResponse(Loose):
    query: str
    intent: str
    outcome: Outcome
    message: str
    depth: int
    trigger: str | None = None
    entity_kind: str | None = None
    entity_key: str | None = None
    exposed_count: int = 0
    entity: dict[str, Any] | None = None
    trace: list[TraceStep]
    scope: list[dict[str, Any]] = []
    exposed: list[ServiceExposure] = []
    safe: list[ServiceExposure] = []
    clean: list[str] = []
    fleet_size: int | None = None
    wake: list[dict[str, Any]] = []
    fix: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None
    spof: dict[str, Any] | None = None
    path: dict[str, Any] | None = None
    maintainers: dict[str, Any] | None = None
    graph: GraphView
    total_ms: float
    llm_summary: str | None = None
    llm_unverified: list[str] = []
    mode: Literal["llm", "deterministic"] = "deterministic"
    llm_model: str | None = None
    llm_tokens: int | None = None


class ErrorResponse(BaseModel):
    error: Literal["graph_unavailable", "bad_request", "internal_error"]
    message: str


class SummaryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    depth: int | None = Field(None, ge=1, le=6)
    fresh: bool = True


class SummaryResponse(BaseModel):
    summary: str | None
    unverified: list[str] = []
    model: str | None = None
    ms: float | None = None
    tokens: int | None = None
    cached: bool = False
    error: str | None = None
