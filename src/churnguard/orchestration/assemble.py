"""generator -> policy.evaluate (hard filter) -> rank -> render -> assemble.

Factored out of orchestration/bounded.py in Phase 11 so
orchestration/harness.py can call the EXACT SAME function - this is what
makes the NON-NEGOTIABLE rule ("the ONLY difference between arms is
SupervisorInput.orchestration_mode... the policy engine remains a hard
filter in BOTH arms") a structural guarantee rather than a promise two
independently-written code paths happen to keep. Both orchestration
arms gather evidence differently (bounded.py: a fixed fan-out;
harness.py: a model-driven tool-calling loop) and then hand the exact same
(account, signals, competitor, agent results) tuple to
assemble_recommendation_set - there is no second implementation of
candidate generation, policy evaluation, ranking or rendering anywhere in
this codebase.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from agents import Model

from churnguard.agents.supervisor import render_recommendation_copy
from churnguard.contracts.competitor import CompetitorComparison
from churnguard.contracts.conversation import ConversationSignals
from churnguard.contracts.customer import AccountContext
from churnguard.contracts.envelope import AgentResult
from churnguard.contracts.offers import CandidateOffer
from churnguard.contracts.policy import PolicyEvaluationRequest, Verdict
from churnguard.contracts.recommendation import (
    ApprovalRequirements,
    BlockedCandidate,
    CustomerImpact,
    Recommendation,
    RecommendationSet,
    SupervisorInput,
    TraceContext,
)
from churnguard.offers import generator, ranker
from churnguard.orchestration import aggregate
from churnguard.policy import engine as policy_engine

SECOND_APPROVER_TIER_THRESHOLD = 2

_BILL_FIGURE_FIELDS = {"bill", "bill_increase", "monthly_bill", "current_bill", "total_bill"}

_TITLE_FALLBACK_BY_KIND = {
    ranker.KIND_CREDIT_REINSTATEMENT: "Restore and explain",
    ranker.KIND_PLAN_MIGRATION: "Switch to a lower-hotspot plan",
    ranker.KIND_RETENTION_BUNDLE: "Loyalty and device credit bundle",
    ranker.KIND_COMPETITOR_PRICE_MATCH: "Match competitor pricing",
    ranker.KIND_UNKNOWN: "Retention offer",
}

_HOLD_CONDITION_BY_KIND = {
    ranker.KIND_RETENTION_BUNDLE: (
        "Confirm the device financing payoff amount with the customer before finalizing."
    ),
}


def _build_context_notes(signals: ConversationSignals, account: AccountContext) -> list[str]:
    notes: list[str] = []
    for figure in signals.customer_stated_figures:
        if figure.field.lower() not in _BILL_FIGURE_FIELDS:
            continue
        actual_delta = account.billing.delta
        if abs(figure.value - actual_delta) < 1.0:
            continue
        causes = ", ".join(cause.cause for cause in account.billing.delta_attribution) or (
            "no billing-side cause on file"
        )
        notes.append(
            f"Customer believes their bill rose by ${figure.value:.2f}; the actual increase "
            f"is ${actual_delta:.2f}, attributed to: {causes}. Reframing this is itself the "
            "top retention lever."
        )
    return notes


def _build_approval(recommendations: list[Recommendation]) -> ApprovalRequirements:
    tiers = [
        r.approval_tier_required for r in recommendations if r.approval_tier_required is not None
    ]
    min_tier = max(tiers, default=0)
    disclosures_pending = sorted(
        {disclosure.code for r in recommendations for disclosure in r.required_disclosures}
    )
    return ApprovalRequirements(
        min_tier_required=min_tier,
        requires_second_approver=min_tier >= SECOND_APPROVER_TIER_THRESHOLD,
        disclosures_pending=disclosures_pending,
    )


def _build_blocked_candidates(verdicts: list[Verdict]) -> list[BlockedCandidate]:
    return [
        BlockedCandidate(
            candidate_id=verdict.candidate_id,
            reason="; ".join(verdict.constraint_violations)
            or "; ".join(verdict.governing_rules)
            or "blocked by policy",
            governing_rules=list(verdict.governing_rules),
        )
        for verdict in verdicts
        if verdict.verdict == "blocked"
    ]


def _build_recommendation(
    rank: int,
    ranked_candidate: ranker.RankedCandidate,
    verdict: Verdict,
    account: AccountContext,
    customer_result: AgentResult[AccountContext],
    competitor_result: AgentResult[CompetitorComparison],
    rendered_by_offer_id: dict[str, tuple[str, str, str]],
) -> Recommendation:
    candidate = ranked_candidate.candidate
    monthly_delta = candidate.total_monthly_impact
    new_monthly = round(account.billing.current_bill + monthly_delta, 2)

    rendered = rendered_by_offer_id.get(candidate.candidate_id)
    if rendered is not None:
        title, rationale, talk_track = rendered
    else:
        title = _TITLE_FALLBACK_BY_KIND[ranked_candidate.kind]
        rationale = (
            f"{title}: adjusts the bill from ${account.billing.current_bill:.2f} to "
            f"${new_monthly:.2f}/mo (retention likelihood {ranked_candidate.retention_likelihood})."
        )
        talk_track = (
            f"I can offer you a {title.lower()} that brings your bill to ${new_monthly:.2f} "
            "per month starting next cycle."
        )

    evidence_ids = [e.evidence_id for e in customer_result.evidence]
    if ranked_candidate.kind == ranker.KIND_COMPETITOR_PRICE_MATCH:
        evidence_ids = evidence_ids + [e.evidence_id for e in competitor_result.evidence]

    return Recommendation(
        rank=rank,
        offer_id=candidate.candidate_id,
        title=title,
        components=candidate.components,
        customer_impact=CustomerImpact(
            monthly_delta=monthly_delta,
            annualized_delta=round(monthly_delta * 12, 2),
            description=(
                f"Bill changes from ${account.billing.current_bill:.2f} to ${new_monthly:.2f}/mo"
            ),
        ),
        rationale=rationale,
        confidence=ranker.CONFIDENCE_BY_KIND[ranked_candidate.kind],
        approval_tier_required=verdict.approval_tier_required,
        required_disclosures=list(verdict.required_disclosures),
        evidence_ids=evidence_ids,
        talk_track=talk_track,
        hold_condition=_HOLD_CONDITION_BY_KIND.get(ranked_candidate.kind),
    )


async def assemble_recommendation_set(
    supervisor_input: SupervisorInput,
    *,
    trace_id: str,
    supervisor_span_id: str,
    agent_calls: list[str],
    fallbacks_applied: list[str],
    degraded_agents: list[tuple[str, str]],
    signals: ConversationSignals,
    conversation_result: AgentResult[ConversationSignals],
    account: AccountContext,
    customer_result: AgentResult[AccountContext],
    competitor: CompetitorComparison | None,
    competitor_result: AgentResult[CompetitorComparison],
    jurisdiction: str,
    channel: str,
    supervisor_model_override: str | Model | None = None,
) -> RecommendationSet:
    """The one place candidate generation, policy evaluation, ranking and
    rendering happen - called by both orchestration/bounded.py and
    orchestration/harness.py with whatever evidence each arm gathered.
    `agent_calls`/`fallbacks_applied` are mutated in place (both callers
    already have a list going; this function appends "policy_engine" /
    "supervisor_render" and any assembly-stage fallback to the SAME list,
    rather than returning a second list the caller has to merge).
    """
    # account.billing.current_bill <= 0 only for the degraded placeholder
    # (every real seeded account has a positive bill) - policy/digest.py
    # deliberately requires current_monthly > 0 and never defaults it, so
    # this skips generation entirely rather than fabricate a policy digest.
    verdict_set = None
    ranked: list[ranker.RankedCandidate] = []
    verdicts_by_id: dict[str, Verdict] = {}
    if account.billing.current_bill > 0:
        candidates: list[CandidateOffer] = generator.generate_candidates(
            account, signals, competitor
        )
        digest = generator.account_digest_from_context(account)
        policy_request = PolicyEvaluationRequest(
            policy_pack_version=supervisor_input.policy_pack_version,
            jurisdiction=jurisdiction,
            channel=channel,
            agent_authority_tier=supervisor_input.agent_authority_tier,
            account_digest=digest,
            candidate_offers=candidates,
        )
        verdict_set = policy_engine.evaluate(policy_request)
        agent_calls.append("policy_engine")

        verdicts_by_id = {v.candidate_id: v for v in verdict_set.verdicts}
        eligible_candidates = [
            c for c in candidates if verdicts_by_id[c.candidate_id].verdict != "blocked"
        ]
        ranked = ranker.rank_candidates(
            eligible_candidates, current_monthly=account.billing.current_bill
        )
    else:
        fallbacks_applied.append(
            "account billing data unavailable; no candidates could be generated"
        )

    rendered_by_offer_id, render_failed = await render_recommendation_copy(
        ranked,
        verdicts_by_id=verdicts_by_id,
        account=account,
        signals=signals,
        trace_id=trace_id,
        supervisor_span_id=supervisor_span_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
        model_override=supervisor_model_override,
    )
    if ranked:
        agent_calls.append("supervisor_render")
    if render_failed:
        fallbacks_applied.append("supervisor_render fell back to templated copy")

    recommendations = [
        _build_recommendation(
            index,
            ranked_candidate,
            verdicts_by_id[ranked_candidate.candidate.candidate_id],
            account,
            customer_result,
            competitor_result,
            rendered_by_offer_id,
        )
        for index, ranked_candidate in enumerate(ranked, start=1)
    ]

    blocked_candidates = _build_blocked_candidates(verdict_set.verdicts) if verdict_set else []
    confidence_aggregate = aggregate.aggregate_confidence(
        customer_result=customer_result,
        conversation_result=conversation_result,
        competitor=competitor,
        concerns=signals.unresolved_concerns,
        account=account,
        degraded_agents=degraded_agents,
    )
    mandatory_actions = aggregate.build_mandatory_actions(signals.unresolved_concerns)
    agent_context_notes = _build_context_notes(signals, account)
    contradiction_note = aggregate.billing_contradiction_note(account)
    if contradiction_note is not None:
        agent_context_notes.append(contradiction_note)
    staleness_note = aggregate.competitor_staleness_note(competitor)
    if staleness_note is not None:
        agent_context_notes.append(staleness_note)
    approval = _build_approval(recommendations)

    return RecommendationSet(
        recommendation_set_id=f"REC_{uuid4().hex}",
        call_id=supervisor_input.call_id,
        generated_at=datetime.now(UTC),
        overall_confidence=confidence_aggregate.overall_confidence,
        confidence_adjustments=confidence_aggregate.adjustments,
        recommendations=recommendations,
        mandatory_actions=mandatory_actions,
        blocked_candidates=blocked_candidates,
        agent_context_notes=agent_context_notes,
        fallbacks_applied=fallbacks_applied,
        commitment_status="none",
        approval=approval,
        trace=TraceContext(
            trace_id=trace_id, span_id=supervisor_span_id, agent_calls=agent_calls
        ),
    )


__all__ = ["assemble_recommendation_set"]
