"""Shared builders for approval/execution tests - a minimal, valid
RecommendationSet + Recommendation + ApprovalDecision + ExecutionRequest,
so test_approval_gate.py, test_execution_rejections.py and
test_idempotency.py don't each hand-roll the same boilerplate. Same
convention as tests/unit/policy_fixtures.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from churnguard.contracts.approval import ApprovalDecision, ExecutionOperation, ExecutionRequest
from churnguard.contracts.offers import OfferComponent
from churnguard.contracts.policy import Disclosure
from churnguard.contracts.recommendation import (
    ApprovalRequirements,
    BlockedCandidate,
    CustomerImpact,
    Recommendation,
    RecommendationSet,
    TraceContext,
)

FIXED_NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
POLICY_PACK_VERSION = "2026.09.1"

DISCLOSURE_HOTSPOT = Disclosure(
    code="DISC_HOTSPOT_REDUCTION",
    text="This plan change reduces your mobile hotspot allowance.",
    must_be_read_verbatim=True,
)


def make_recommendation(
    *,
    offer_id: str = "C1",
    approval_tier_required: int | None = 1,
    required_disclosures: list[Disclosure] | None = None,
) -> Recommendation:
    return Recommendation(
        rank=1,
        offer_id=offer_id,
        title="Restore and explain",
        components=[
            OfferComponent(
                code="RET_LOYALTY_CREDIT_REINSTATEMENT", monthly=-20.0, duration_months=12
            )
        ],
        customer_impact=CustomerImpact(
            monthly_delta=-20.0, annualized_delta=-240.0, description="Bill changes by -20.00/mo"
        ),
        rationale="A reversible billing credit already owed to the customer.",
        confidence=0.95,
        approval_tier_required=approval_tier_required,
        required_disclosures=required_disclosures or [],
        evidence_ids=["EVID_ACCT_4471"],
        talk_track="Here's what I can offer you today.",
        hold_condition=None,
    )


def make_recommendation_set(
    *,
    recommendation_set_id: str = "REC_test0001",
    recommendations: list[Recommendation] | None = None,
    blocked_candidates: list[BlockedCandidate] | None = None,
    corrupt_commitment_status: str | None = None,
) -> RecommendationSet:
    """corrupt_commitment_status simulates a tampered/corrupted
    RecommendationSet: RecommendationSet.commitment_status is a frozen
    Literal["none"], so it can never be constructed with any other value
    normally - but this contract doesn't set validate_assignment=True, so a
    plain attribute assignment after construction bypasses that check. This
    is exactly the "what if" the commitment_status rejection path defends
    against, and the only way to exercise it in a test.
    """
    recs = recommendations if recommendations is not None else [make_recommendation()]
    rec_set = RecommendationSet(
        recommendation_set_id=recommendation_set_id,
        call_id="CALL_****9001",
        generated_at=FIXED_NOW,
        overall_confidence=0.9,
        confidence_adjustments=[],
        recommendations=recs,
        mandatory_actions=[],
        blocked_candidates=blocked_candidates or [],
        agent_context_notes=[],
        fallbacks_applied=[],
        commitment_status="none",
        approval=ApprovalRequirements(
            min_tier_required=1, requires_second_approver=False, disclosures_pending=[]
        ),
        trace=TraceContext(trace_id="trace_test", span_id="span_test", agent_calls=[]),
    )
    if corrupt_commitment_status is not None:
        rec_set.commitment_status = corrupt_commitment_status  # type: ignore[assignment]
    return rec_set


def make_approval_decision(
    *,
    recommendation_set_id: str = "REC_test0001",
    decision: str = "approved",
    selected_offer_id: str | None = "C1",
    approver_tier: int = 1,
    disclosures_read: list[str] | None = None,
) -> ApprovalDecision:
    return ApprovalDecision(
        recommendation_set_id=recommendation_set_id,
        decision=decision,  # type: ignore[arg-type]
        selected_offer_id=selected_offer_id,
        approver_ref="AGT_****0192",
        approver_tier=approver_tier,
        approved_at=FIXED_NOW,
        edits=[],
        disclosures_read=disclosures_read or [],
        policy_pack_version=POLICY_PACK_VERSION,
    )


def make_execution_request(
    *,
    approval_ref: str = "APR_test0001",
    account_ref: str = "ACCT_****4471",
    offer_id: str | None = "C1",
    idempotency_key: str = "idem-test-0001",
) -> ExecutionRequest:
    params: dict[str, object] = {"offer_id": offer_id} if offer_id is not None else {}
    return ExecutionRequest(
        approval_ref=approval_ref,
        account_ref=account_ref,
        operations=[ExecutionOperation(op_code="apply_bill_credit", params=params)],
        idempotency_key=idempotency_key,
    )


__all__ = [
    "DISCLOSURE_HOTSPOT",
    "FIXED_NOW",
    "POLICY_PACK_VERSION",
    "make_approval_decision",
    "make_execution_request",
    "make_recommendation",
    "make_recommendation_set",
]
