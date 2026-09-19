"""Payloads for the Supervisor agent: fan-out input and the ranked output.

ASSUMPTIONS (see CLAUDE.md "ASSUMPTIONS TO CONFIRM"): CustomerImpact,
ConfidenceAdjustment, BlockedCandidate, ApprovalRequirements and TraceContext
are sub-models not spelled out in the phase brief. `approval` holds only the
approval *requirements* for this set — the actual human decision is a
separate ApprovalDecision (approval.py) created afterwards and linked back
by recommendation_set_id, since a RecommendationSet is generated before any
human has approved it. `trace` echoes AgentEnvelope tracing for audit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from churnguard.contracts.conversation import TranscriptTurn
from churnguard.contracts.offers import OfferComponent
from churnguard.contracts.policy import Disclosure

OrchestrationMode = Literal["bounded_pipeline", "open_harness"]
CommitmentStatus = Literal["none"]


class SupervisorInput(BaseModel):
    """Request payload for the Supervisor agent."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    account_ref: str
    transcript_window: list[TranscriptTurn]
    agent_authority_tier: int = Field(ge=0)
    agent_ref: str
    deadline_ms: int = Field(ge=0)
    policy_pack_version: str
    orchestration_mode: OrchestrationMode


class CustomerImpact(BaseModel):
    """The bill-level effect of a recommendation, in plain figures."""

    model_config = ConfigDict(extra="forbid")

    monthly_delta: float
    annualized_delta: float
    description: str


class Recommendation(BaseModel):
    """One ranked, policy-cleared retention offer with rationale."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    offer_id: str
    title: str
    components: list[OfferComponent]
    customer_impact: CustomerImpact
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    approval_tier_required: int | None
    required_disclosures: list[Disclosure]
    evidence_ids: list[str]
    talk_track: str
    hold_condition: str | None


class ConfidenceAdjustment(BaseModel):
    """A named reason the overall confidence score was moved up or down."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    delta: float


class BlockedCandidate(BaseModel):
    """A candidate offer the policy engine excluded, and why."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    reason: str
    governing_rules: list[str]


class ApprovalRequirements(BaseModel):
    """What a human approver must do before this set can be acted on."""

    model_config = ConfigDict(extra="forbid")

    min_tier_required: int = Field(ge=0)
    requires_second_approver: bool
    disclosures_pending: list[str]


class TraceContext(BaseModel):
    """Observability echo for audit and debugging."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    agent_calls: list[str]


class RecommendationSet(BaseModel):
    """Structured output of the Supervisor agent for one call."""

    model_config = ConfigDict(extra="forbid")

    recommendation_set_id: str
    call_id: str
    generated_at: datetime
    overall_confidence: float = Field(ge=0.0, le=1.0)
    confidence_adjustments: list[ConfidenceAdjustment]
    recommendations: list[Recommendation]
    mandatory_actions: list[str]
    blocked_candidates: list[BlockedCandidate]
    agent_context_notes: list[str]
    fallbacks_applied: list[str]
    commitment_status: CommitmentStatus
    approval: ApprovalRequirements
    trace: TraceContext
