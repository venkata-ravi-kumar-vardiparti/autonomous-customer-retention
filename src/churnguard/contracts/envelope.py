"""Transport envelope shared by every agent-to-agent call.

AgentEnvelope wraps a request payload with tracing, deadline and policy
context. AgentResult wraps a response payload with status, confidence and
evidence. Every other contract module defines payload types that travel
inside these two generics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Freshness = Literal["fresh", "aging", "stale"]
ResultStatus = Literal["ok", "partial", "stale", "failed", "insufficient_evidence"]


class EvidenceRef(BaseModel):
    """Pointer to the governed-data-layer record backing a claim."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_system: str
    record_ref: str
    as_of: datetime
    freshness: Freshness
    masked: bool


class Telemetry(BaseModel):
    """Per-call cost/latency accounting attached to every AgentResult."""

    model_config = ConfigDict(extra="forbid")

    model: str
    latency_ms: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    cache_hit: bool


class AgentEnvelope[T](BaseModel):
    """Request envelope: tracing, invocation context, and the payload."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    parent_span_id: str | None
    call_id: str
    invoked_by: str
    policy_pack_version: str
    redaction_level: str
    deadline_ms: int = Field(ge=0)
    attempt: int = Field(ge=1)
    payload: T


class AgentResult[T](BaseModel):
    """Response envelope: status, confidence, evidence, and the payload."""

    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    confidence: float = Field(ge=0.0, le=1.0)
    data: T | None
    evidence: list[EvidenceRef]
    missing_evidence: list[str]
    warnings: list[str]
    telemetry: Telemetry
