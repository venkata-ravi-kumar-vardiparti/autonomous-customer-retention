"""Payloads for the human-approval boundary and the handoff to execution.

A human APPROVES a RecommendationSet; a separate service EXECUTES. This
module is the seam between the two: ApprovalDecision is produced by the
approval UI, ExecutionRequest is what gets hard-committed downstream.

ASSUMPTIONS (see CLAUDE.md "ASSUMPTIONS TO CONFIRM"): OfferEdit and
ExecutionOperation are not spelled out in the phase brief.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ApprovalDecisionType = Literal["approved", "rejected", "escalated"]


class OfferEdit(BaseModel):
    """A single field the approving agent changed before approving."""

    model_config = ConfigDict(extra="forbid")

    component_code: str
    field: str
    old_value: str
    new_value: str


class ApprovalDecision(BaseModel):
    """The human approver's decision on a RecommendationSet."""

    model_config = ConfigDict(extra="forbid")

    recommendation_set_id: str
    decision: ApprovalDecisionType
    selected_offer_id: str | None
    approver_ref: str
    approver_tier: int = Field(ge=0)
    approved_at: datetime
    edits: list[OfferEdit]
    disclosures_read: list[str]
    policy_pack_version: str


class ExecutionOperation(BaseModel):
    """One atomic operation the execution service must carry out."""

    model_config = ConfigDict(extra="forbid")

    op_code: str
    params: dict[str, Any]


class ExecutionRequest(BaseModel):
    """Handoff payload from approval to the (out-of-scope) execution service."""

    model_config = ConfigDict(extra="forbid")

    approval_ref: str
    account_ref: str
    operations: list[ExecutionOperation]
    idempotency_key: str
