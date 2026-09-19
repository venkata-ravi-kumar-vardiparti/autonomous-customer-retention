"""Payloads for the Competitor agent: curated, timestamped price snapshots.

ASSUMPTIONS (see CLAUDE.md "ASSUMPTIONS TO CONFIRM"): ResolvedCompetitorOffer,
Normalization, SwitchingCosts and ClaimReconciliation are not spelled out in
the phase brief; shapes below are a starting point for confirmation before
Phase 1.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from churnguard.contracts.conversation import CompetitorClaim
from churnguard.contracts.envelope import Freshness

ClaimVerdict = Literal["confirmed", "overstated", "understated", "unverifiable"]


class CompetitorQuery(BaseModel):
    """Request payload for the Competitor agent."""

    model_config = ConfigDict(extra="forbid")

    geography: str
    carriers: list[str]
    line_count: int = Field(ge=1)
    current_plan_profile: str
    customer_claim: CompetitorClaim | None
    switching_context: str
    max_snapshot_age_days: int = Field(ge=0)


class ResolvedCompetitorOffer(BaseModel):
    """A single competitor plan, normalized for comparison."""

    model_config = ConfigDict(extra="forbid")

    carrier: str
    plan_name: str
    monthly_price: float = Field(ge=0.0)
    line_count: int = Field(ge=1)
    includes: list[str]
    normalized_monthly_equivalent: float = Field(ge=0.0)
    source_snapshot_id: str


class Normalization(BaseModel):
    """How disparate competitor prices were made comparable."""

    model_config = ConfigDict(extra="forbid")

    method: str
    assumptions: list[str]


class SwitchingCosts(BaseModel):
    """One-time costs the customer would incur by switching carriers."""

    model_config = ConfigDict(extra="forbid")

    device_payoff_total: float = Field(ge=0.0)
    early_termination_fees_total: float = Field(ge=0.0)
    activation_fees: float = Field(ge=0.0)
    other_costs: float = Field(ge=0.0)
    total: float = Field(ge=0.0)


class ClaimReconciliation(BaseModel):
    """Verdict on whether the customer's stated competitor claim holds up."""

    model_config = ConfigDict(extra="forbid")

    customer_claim: CompetitorClaim | None
    verdict: ClaimVerdict
    resolved_price: float | None
    explanation: str


class SnapshotMeta(BaseModel):
    """Provenance of the price snapshot(s) used to build the comparison."""

    model_config = ConfigDict(extra="forbid")

    as_of: date
    age_days: int = Field(ge=0)
    geography: str
    capture_method: str
    snapshot_id: str


class CompetitorComparison(BaseModel):
    """Structured output of the Competitor agent for one query."""

    model_config = ConfigDict(extra="forbid")

    resolved_offers: list[ResolvedCompetitorOffer]
    normalization: Normalization
    switching_costs: SwitchingCosts
    breakeven_months: float | None
    claim_reconciliation: ClaimReconciliation
    snapshot: SnapshotMeta
    freshness: Freshness
    confidence_penalty: float = Field(ge=0.0, le=1.0)
