"""The bounded pipeline: SupervisorInput.orchestration_mode == "bounded_pipeline".

Plain code decides fan-out, filtering and ranking - never an LLM. Pipeline:

    guardrail -> conversation -> asyncio.gather(customer, competitor)
        -> generator -> policy.evaluate -> rank -> render -> assemble

"guardrail -> conversation" is one step: run_conversation_agent already
sanitizes the transcript and enforces the input_guardrail internally
(agents/conversation.py) before its own model call.

Genuine fan-out (FAN-OUT TEST: customer and competitor agent spans must
overlap in wall-clock time) requires the Competitor agent's query
(current_monthly, line_count, plan profile, known switching-cost facts) to
be available WITHOUT waiting for the Customer 360 AGENT's own LLM
round-trip. Phase 6 originally assumed Customer 360 would already have run
by the time the Competitor agent was queried (see CLAUDE.md's Phase 6
notes) - Phase 7 supersedes that: this module does one fast, direct
Governed Data Layer read (customer_repo.get_account_context, no LLM
involved) to seed the CompetitorQuery, then launches the Customer 360 AGENT
(for the full, evidence-stamped, verified AccountContext used downstream)
and the Competitor agent concurrently. The two repository reads this
implies (one direct, one via the agent's own tools) are a deliberate,
documented trade-off for real parallelism, not an oversight.

Policy evaluation is a HARD FILTER: only "pass"/"pass_with_disclosure"
verdicts ever reach offers/ranker.py or Recommendation. A "blocked" verdict
becomes a BlockedCandidate - candidate_id, reason, governing_rules only,
never its priced components - and is structurally excluded from every
other part of the payload (see tests/e2e/test_blocked_offer_leak.py).

Bounded re-request (aggregate.MAX_REREQUEST_ATTEMPTS = 1 per agent) is a
hard-coded loop count, never left to model judgement: customer_360 is
re-run at most once if it has a genuine (uncorroborated) evidence gap;
competitor is re-run at most once if it resolved no offers at all.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from agents import Model

from churnguard.agents.base import run_agent
from churnguard.agents.competitor import run_competitor_agent
from churnguard.agents.conversation import run_conversation_agent
from churnguard.agents.customer import CUSTOMER_360_SPEC
from churnguard.agents.supervisor import render_recommendation_copy
from churnguard.config import load_settings
from churnguard.contracts.competitor import CompetitorComparison, CompetitorQuery
from churnguard.contracts.conversation import ConversationInput, ConversationSignals
from churnguard.contracts.customer import AccountContext, CustomerContextRequest
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
from churnguard.data.repositories import customer_repo
from churnguard.offers import generator, ranker
from churnguard.orchestration import aggregate
from churnguard.orchestration.context import AgentRequest, RunContext
from churnguard.policy import engine as policy_engine
from churnguard.telemetry.tracer import new_span_id, new_trace_id
from churnguard.tools.customer_tools import ALL_CUSTOMER_360_DOMAINS

DEFAULT_JURISDICTION = "US-TX"
DEFAULT_GEOGRAPHY = "TX-DFW"
DEFAULT_CHANNEL = "voice"
DEFAULT_CARRIERS = ["RivalCo", "MetroWave"]
DEFAULT_MAX_SNAPSHOT_AGE_DAYS = 90
DEFAULT_SWITCHING_CONTEXT = "live retention call - customer requested cancellation"
DEFAULT_LOOKBACK_MONTHS = 3
CUSTOMER_INPUT_TEXT = "Retrieve and structure this account's data for a live retention call."

SECOND_APPROVER_TIER_THRESHOLD = 2

_EMPTY_SIGNALS = ConversationSignals(
    intents=[],
    churn_signals=[],
    competitor_claims=[],
    customer_stated_figures=[],
    unresolved_concerns=[],
    sentiment_trajectory="unknown - transcript unavailable",
    verification_tasks=[],
)

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


def _new_run_context(
    request: AgentRequest,
    *,
    trace_id: str,
    policy_pack_version: str,
    deadline_ms: int,
) -> RunContext:
    return RunContext(
        request=request,
        trace_id=trace_id,
        agent_span_id=new_span_id(),
        policy_pack_version=policy_pack_version,
        deadline_ms=deadline_ms,
        db_path=load_settings().database_path,
    )


def _customer_request(
    supervisor_input: SupervisorInput,
    *,
    line_refs_of_interest: list[str],
    verification_tasks: list[str],
) -> CustomerContextRequest:
    return CustomerContextRequest(
        account_ref=supervisor_input.account_ref,
        requested_domains=list(ALL_CUSTOMER_360_DOMAINS),
        lookback_months=DEFAULT_LOOKBACK_MONTHS,
        line_refs_of_interest=line_refs_of_interest,
        verification_tasks=verification_tasks,
        reason_code="cancel_request",
    )


def _carriers_from_signals(signals: ConversationSignals) -> list[str]:
    claimed = list(dict.fromkeys(claim.carrier for claim in signals.competitor_claims))
    return claimed or list(DEFAULT_CARRIERS)


def _build_context_notes(
    signals: ConversationSignals, account: AccountContext
) -> list[str]:
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


async def _maybe_reretry_customer(
    customer_result: AgentResult[AccountContext],
    *,
    signals: ConversationSignals,
    customer_request: CustomerContextRequest,
    trace_id: str,
    policy_pack_version: str,
    deadline_ms: int,
    model_override: str | Model | None,
    fallbacks_applied: list[str],
) -> AgentResult[AccountContext]:
    if not aggregate.has_uncorroborated_gap(customer_result, signals.unresolved_concerns):
        return customer_result
    retry_context = _new_run_context(
        customer_request,
        trace_id=trace_id,
        policy_pack_version=policy_pack_version,
        deadline_ms=deadline_ms,
    )
    retried = await run_agent(
        CUSTOMER_360_SPEC, CUSTOMER_INPUT_TEXT, retry_context, model_override=model_override
    )
    fallbacks_applied.append(
        "customer_360 re-requested once for an uncorroborated missing-evidence gap"
    )
    if len(retried.missing_evidence) < len(customer_result.missing_evidence):
        return retried
    return customer_result


async def _maybe_reretry_competitor(
    competitor_result: AgentResult[CompetitorComparison],
    *,
    competitor_query: CompetitorQuery,
    trace_id: str,
    policy_pack_version: str,
    deadline_ms: int,
    model_override: str | Model | None,
    fallbacks_applied: list[str],
) -> AgentResult[CompetitorComparison]:
    if competitor_result.data is not None and competitor_result.data.resolved_offers:
        return competitor_result
    retry_context = _new_run_context(
        competitor_query,
        trace_id=trace_id,
        policy_pack_version=policy_pack_version,
        deadline_ms=deadline_ms,
    )
    retried = await run_competitor_agent(
        competitor_query, retry_context, model_override=model_override
    )
    fallbacks_applied.append("competitor re-requested once for empty resolved_offers")
    if retried.data is not None and retried.data.resolved_offers:
        return retried
    return competitor_result


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


async def run_bounded_pipeline(
    supervisor_input: SupervisorInput,
    *,
    jurisdiction: str = DEFAULT_JURISDICTION,
    geography: str = DEFAULT_GEOGRAPHY,
    channel: str = DEFAULT_CHANNEL,
    conversation_model_override: str | Model | None = None,
    customer_model_override: str | Model | None = None,
    competitor_model_override: str | Model | None = None,
    supervisor_model_override: str | Model | None = None,
) -> RecommendationSet:
    if supervisor_input.orchestration_mode != "bounded_pipeline":
        raise ValueError(
            "run_bounded_pipeline only implements orchestration_mode='bounded_pipeline', "
            f"got {supervisor_input.orchestration_mode!r}"
        )

    trace_id = new_trace_id()
    supervisor_span_id = new_span_id()
    agent_calls: list[str] = []
    fallbacks_applied: list[str] = []

    # --- guardrail -> conversation ---
    window_start_ts = (
        supervisor_input.transcript_window[0].ts
        if supervisor_input.transcript_window
        else datetime.now(UTC)
    )
    conversation_input = ConversationInput(
        call_id=supervisor_input.call_id,
        transcript_window=supervisor_input.transcript_window,
        window_start_ts=window_start_ts,
        prior_signals=None,
        locale="en-US",
        guardrail_flags=[],
    )
    conversation_context = _new_run_context(
        conversation_input,
        trace_id=trace_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
    )
    conversation_result = await run_conversation_agent(
        conversation_input, conversation_context, model_override=conversation_model_override
    )
    agent_calls.append("conversation")
    signals = conversation_result.data if conversation_result.data is not None else _EMPTY_SIGNALS

    # --- fast, direct Governed Data Layer bootstrap (no LLM) to seed the
    # competitor query without a false dependency on the Customer 360 agent ---
    customer_request = _customer_request(
        supervisor_input,
        line_refs_of_interest=[
            c.line_ref for c in signals.unresolved_concerns if c.line_ref is not None
        ],
        verification_tasks=signals.verification_tasks,
    )
    bootstrap = await customer_repo.get_account_context(customer_request)
    bootstrap_account = bootstrap.data

    competitor_query = CompetitorQuery(
        geography=geography,
        carriers=_carriers_from_signals(signals),
        line_count=bootstrap_account.line_count,
        current_plan_profile=bootstrap_account.plan_profile.plan_code,
        current_monthly=bootstrap_account.billing.current_bill,
        customer_claim=signals.competitor_claims[0] if signals.competitor_claims else None,
        switching_context=DEFAULT_SWITCHING_CONTEXT,
        max_snapshot_age_days=DEFAULT_MAX_SNAPSHOT_AGE_DAYS,
        known_device_financing_payoff=round(
            sum(line.remaining_balance for line in bootstrap_account.device_financing), 2
        ),
        known_one_time_switching_fees=round(
            sum(line.early_termination_fee for line in bootstrap_account.device_financing), 2
        ),
    )

    customer_context = _new_run_context(
        customer_request,
        trace_id=trace_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
    )
    competitor_context = _new_run_context(
        competitor_query,
        trace_id=trace_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
    )

    # --- asyncio.gather(customer, competitor): the real fan-out ---
    customer_result, competitor_result = await asyncio.gather(
        run_agent(
            CUSTOMER_360_SPEC,
            CUSTOMER_INPUT_TEXT,
            customer_context,
            model_override=customer_model_override,
        ),
        run_competitor_agent(
            competitor_query, competitor_context, model_override=competitor_model_override
        ),
    )
    agent_calls.extend(["customer_360", "competitor"])

    customer_result = await _maybe_reretry_customer(
        customer_result,
        signals=signals,
        customer_request=customer_request,
        trace_id=trace_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
        model_override=customer_model_override,
        fallbacks_applied=fallbacks_applied,
    )
    competitor_result = await _maybe_reretry_competitor(
        competitor_result,
        competitor_query=competitor_query,
        trace_id=trace_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
        model_override=competitor_model_override,
        fallbacks_applied=fallbacks_applied,
    )

    account = customer_result.data
    assert account is not None, "customer_360 never returns None data on a normal run"
    competitor = competitor_result.data

    # --- generator -> policy.evaluate (hard filter) -> rank ---
    candidates: list[CandidateOffer] = generator.generate_candidates(account, signals, competitor)
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

    # --- render: LARGE-model prose around already-computed facts only ---
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

    blocked_candidates = _build_blocked_candidates(verdict_set.verdicts)
    confidence_aggregate = aggregate.aggregate_confidence(
        customer_result=customer_result,
        conversation_result=conversation_result,
        competitor=competitor,
        concerns=signals.unresolved_concerns,
    )
    mandatory_actions = aggregate.build_mandatory_actions(signals.unresolved_concerns)
    agent_context_notes = _build_context_notes(signals, account)
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


__all__ = ["run_bounded_pipeline"]
