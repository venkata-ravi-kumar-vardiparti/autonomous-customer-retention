"""approval/gate.py: tier sufficiency, disclosures acknowledged, offer ID
membership - validated against a RecommendationSet before a decision is
ever persisted or acted on.
"""

from __future__ import annotations

from churnguard.approval.gate import evaluate_gate
from churnguard.contracts.recommendation import BlockedCandidate
from tests.unit.approval_fixtures import (
    DISCLOSURE_HOTSPOT,
    make_approval_decision,
    make_recommendation,
    make_recommendation_set,
)


def test_a_fully_compliant_approval_passes() -> None:
    recommendation_set = make_recommendation_set()
    decision = make_approval_decision()

    result = evaluate_gate(decision, recommendation_set)

    assert result.passed
    assert result.reasons == []


def test_rejection_and_escalation_decisions_are_never_gated_on_offer_selection() -> None:
    recommendation_set = make_recommendation_set()
    for decision_type in ("rejected", "escalated"):
        decision = make_approval_decision(decision=decision_type, selected_offer_id=None)
        result = evaluate_gate(decision, recommendation_set)
        assert result.passed


def test_mismatched_recommendation_set_id_is_rejected() -> None:
    recommendation_set = make_recommendation_set(recommendation_set_id="REC_real")
    decision = make_approval_decision(recommendation_set_id="REC_other")

    result = evaluate_gate(decision, recommendation_set)

    assert not result.passed
    assert any("does not match" in reason for reason in result.reasons)


def test_offer_id_not_in_recommendation_set_is_rejected() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1")]
    )
    decision = make_approval_decision(selected_offer_id="C9")

    result = evaluate_gate(decision, recommendation_set)

    assert not result.passed
    assert any("not one of this recommendation set" in reason for reason in result.reasons)


def test_blocked_offer_id_is_rejected_even_if_it_looks_like_a_valid_candidate_id() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1")],
        blocked_candidates=[
            BlockedCandidate(candidate_id="C4", reason="PRO-007", governing_rules=["PRO-007"])
        ],
    )
    decision = make_approval_decision(selected_offer_id="C4")

    result = evaluate_gate(decision, recommendation_set)

    assert not result.passed
    assert any("blocked by policy evaluation" in reason for reason in result.reasons)


def test_insufficient_approver_tier_is_rejected() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1", approval_tier_required=2)]
    )
    decision = make_approval_decision(selected_offer_id="C1", approver_tier=1)

    result = evaluate_gate(decision, recommendation_set)

    assert not result.passed
    assert any("below the required" in reason for reason in result.reasons)


def test_sufficient_approver_tier_passes() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1", approval_tier_required=2)]
    )
    decision = make_approval_decision(selected_offer_id="C1", approver_tier=2)

    result = evaluate_gate(decision, recommendation_set)

    assert result.passed


def test_missing_required_disclosure_is_rejected() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[
            make_recommendation(
                offer_id="C2", approval_tier_required=1, required_disclosures=[DISCLOSURE_HOTSPOT]
            )
        ]
    )
    decision = make_approval_decision(selected_offer_id="C2", disclosures_read=[])

    result = evaluate_gate(decision, recommendation_set)

    assert not result.passed
    assert any("required disclosures not acknowledged" in reason for reason in result.reasons)


def test_acknowledged_disclosure_passes() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[
            make_recommendation(
                offer_id="C2", approval_tier_required=1, required_disclosures=[DISCLOSURE_HOTSPOT]
            )
        ]
    )
    decision = make_approval_decision(
        selected_offer_id="C2", disclosures_read=[DISCLOSURE_HOTSPOT.code]
    )

    result = evaluate_gate(decision, recommendation_set)

    assert result.passed


def test_tampered_commitment_status_is_rejected() -> None:
    recommendation_set = make_recommendation_set(corrupt_commitment_status="committed")
    decision = make_approval_decision()

    result = evaluate_gate(decision, recommendation_set)

    assert not result.passed
    assert any("commitment_status" in reason for reason in result.reasons)
