"""Payloads for the deterministic Offer Policy engine.

The policy engine itself is plain code, not an LLM call — an LLM only
renders the Disclosure/Verdict text for the agent-facing talk track later.
`account_digest` and `ComputedLimits` are typed loosely (Any-valued dict /
a small summary model) since the exact rule inputs are defined in Phase 3
when the rule set itself is written; see CLAUDE.md "ASSUMPTIONS TO CONFIRM".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from churnguard.contracts.offers import CandidateOffer

CandidateVerdict = Literal["pass", "pass_with_disclosure", "blocked"]
EvaluationMode = Literal["deterministic"]


class PolicyEvaluationRequest(BaseModel):
    """Request payload for the deterministic policy engine."""

    model_config = ConfigDict(extra="forbid")

    policy_pack_version: str
    jurisdiction: str
    channel: str
    agent_authority_tier: int = Field(ge=0)
    account_digest: dict[str, Any]
    candidate_offers: list[CandidateOffer]


class Disclosure(BaseModel):
    """A disclosure statement required before an offer can be presented."""

    model_config = ConfigDict(extra="forbid")

    code: str
    text: str
    must_be_read_verbatim: bool


class Verdict(BaseModel):
    """The policy engine's ruling on one candidate offer."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    verdict: CandidateVerdict
    governing_rules: list[str]
    approval_tier_required: int | None
    required_disclosures: list[Disclosure]
    constraint_violations: list[str]


class ComputedLimits(BaseModel):
    """Ceilings the policy pack derived for this account/channel/tier."""

    model_config = ConfigDict(extra="forbid")

    max_discount_pct: float = Field(ge=0.0, le=1.0)
    max_monthly_credit: float = Field(ge=0.0)
    max_bundle_duration_months: int = Field(ge=0)
    agent_authority_ceiling: int = Field(ge=0)
    notes: list[str]


class PolicyVerdictSet(BaseModel):
    """Structured output of the Offer Policy agent for one evaluation."""

    model_config = ConfigDict(extra="forbid")

    policy_pack_version: str
    policy_pack_hash: str
    evaluated_at: datetime
    evaluation_mode: EvaluationMode
    computed_limits: ComputedLimits
    verdicts: list[Verdict]
    prohibited_actions_triggered: list[str]
