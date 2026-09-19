"""Candidate retention offers produced before deterministic policy evaluation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

OfferType = Literal[
    "bill_credit",
    "plan_discount",
    "device_credit",
    "plan_change",
    "retention_bundle",
    "contract_buyout",
]


class OfferComponent(BaseModel):
    """One priced line item within a candidate offer."""

    model_config = ConfigDict(extra="forbid")

    code: str
    monthly: float
    duration_months: int | None


class CandidateOffer(BaseModel):
    """An unevaluated offer proposal, before policy verdicts are applied."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    type: OfferType
    components: list[OfferComponent]
    total_monthly_impact: float
