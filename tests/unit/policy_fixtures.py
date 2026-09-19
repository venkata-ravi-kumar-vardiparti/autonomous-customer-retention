"""The reference scenario from the Phase 2 brief, shared across policy test modules.

tier=1, current_monthly=198.43, no active promos, financing active, payment
current. Four candidates (C1-C4) with required exact verdicts:

  C1 credit_reinstatement -28.00  -> pass,               [RET-014, BIL-003], tier 1
  C2 plan_migration       -38.44  -> pass_with_disclosure, [PLN-021],         tier 1
  C3 bundle               -38.00  -> pass,               [RET-014, FIN-009], tier 2
  C4 competitor_price_match -45.63 -> blocked,            [PRO-007, RET-002]
"""

from __future__ import annotations

from typing import Any

from churnguard.contracts.offers import CandidateOffer, OfferComponent
from churnguard.contracts.policy import PolicyEvaluationRequest

POLICY_PACK_VERSION = "2026.09.1"
REFERENCE_TIER = 1

REFERENCE_DIGEST: dict[str, Any] = {
    "current_monthly": 198.43,
    "active_promo_codes": [],
    "financing_active": True,
    "payment_current": True,
}


def make_c1_credit_reinstatement() -> CandidateOffer:
    return CandidateOffer(
        candidate_id="C1",
        type="bill_credit",
        components=[
            OfferComponent(
                code="RET_LOYALTY_CREDIT_REINSTATEMENT", monthly=-20.00, duration_months=12
            ),
            OfferComponent(
                code="BIL_AUTOPAY_DISCOUNT_RESTORE", monthly=-8.00, duration_months=None
            ),
        ],
        total_monthly_impact=-28.00,
    )


def make_c2_plan_migration() -> CandidateOffer:
    return CandidateOffer(
        candidate_id="C2",
        type="plan_change",
        components=[
            OfferComponent(
                code="PLN_HOTSPOT_REDUCTION_MIGRATION", monthly=-38.44, duration_months=None
            ),
        ],
        total_monthly_impact=-38.44,
    )


def make_c3_bundle() -> CandidateOffer:
    return CandidateOffer(
        candidate_id="C3",
        type="retention_bundle",
        components=[
            OfferComponent(code="RET_LOYALTY_CREDIT", monthly=-20.00, duration_months=12),
            OfferComponent(code="FIN_DEVICE_CREDIT", monthly=-18.00, duration_months=10),
        ],
        total_monthly_impact=-38.00,
    )


def make_c4_competitor_price_match() -> CandidateOffer:
    return CandidateOffer(
        candidate_id="C4",
        type="plan_discount",
        components=[
            OfferComponent(code="PRC_COMPETITOR_PRICE_MATCH", monthly=-45.63, duration_months=None),
        ],
        total_monthly_impact=-45.63,
    )


def make_reference_request(
    *, digest: dict[str, Any] | None = None, tier: int = REFERENCE_TIER
) -> PolicyEvaluationRequest:
    return PolicyEvaluationRequest(
        policy_pack_version=POLICY_PACK_VERSION,
        jurisdiction="US-TX",
        channel="voice",
        agent_authority_tier=tier,
        account_digest=digest if digest is not None else dict(REFERENCE_DIGEST),
        candidate_offers=[
            make_c1_credit_reinstatement(),
            make_c2_plan_migration(),
            make_c3_bundle(),
            make_c4_competitor_price_match(),
        ],
    )
