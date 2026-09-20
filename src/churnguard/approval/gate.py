"""Validate an ApprovalDecision against its RecommendationSet before the
decision is persisted or acted on: tier sufficiency, disclosures
acknowledged, the offer ID is one of this set's eligible (never blocked)
recommendations.

Pure — no I/O, no persistence. Deliberately NOT imported by
execution/service.py (see that module's docstring): execution enforces its
own, independent copy of the equivalent checks directly against plain
contract objects, so its guarantee never depends on this module (or
approval/ at all) being present, correct, or even called first. This
module exists for the approval API to give a human a useful, early
rejection reason — it is not the sole gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from churnguard.contracts.approval import ApprovalDecision
from churnguard.contracts.recommendation import Recommendation, RecommendationSet


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def _find_recommendation(
    recommendation_set: RecommendationSet, offer_id: str
) -> Recommendation | None:
    return next(
        (r for r in recommendation_set.recommendations if r.offer_id == offer_id), None
    )


def evaluate_gate(
    decision: ApprovalDecision, recommendation_set: RecommendationSet
) -> GateResult:
    """Only an "approved" decision commits to an offer, so only "approved"
    decisions are gated here — a "rejected"/"escalated" decision always
    passes (there is nothing to check an offer selection against)."""
    if decision.recommendation_set_id != recommendation_set.recommendation_set_id:
        return GateResult(
            passed=False,
            reasons=[
                f"decision.recommendation_set_id {decision.recommendation_set_id!r} does not "
                f"match recommendation_set_id {recommendation_set.recommendation_set_id!r}"
            ],
        )

    if decision.decision != "approved":
        return GateResult(passed=True, reasons=[])

    reasons: list[str] = []

    if recommendation_set.commitment_status != "none":
        reasons.append(
            "recommendation_set commitment_status is "
            f"{recommendation_set.commitment_status!r}, expected 'none'"
        )

    blocked_ids = {b.candidate_id for b in recommendation_set.blocked_candidates}
    if decision.selected_offer_id in blocked_ids:
        reasons.append(
            f"selected_offer_id {decision.selected_offer_id!r} was blocked by policy evaluation"
        )
        return GateResult(passed=False, reasons=reasons)

    matching = (
        _find_recommendation(recommendation_set, decision.selected_offer_id)
        if decision.selected_offer_id is not None
        else None
    )
    if matching is None:
        reasons.append(
            f"selected_offer_id {decision.selected_offer_id!r} is not one of this "
            "recommendation set's eligible offers"
        )
        return GateResult(passed=False, reasons=reasons)

    required_tier = matching.approval_tier_required or 0
    if decision.approver_tier < required_tier:
        reasons.append(
            f"approver_tier {decision.approver_tier} is below the required {required_tier}"
        )

    missing_disclosures = [
        d.code for d in matching.required_disclosures if d.code not in decision.disclosures_read
    ]
    if missing_disclosures:
        reasons.append(f"required disclosures not acknowledged: {missing_disclosures}")

    return GateResult(passed=not reasons, reasons=reasons)


__all__ = ["GateResult", "evaluate_gate"]
