"""The bounded pipeline: SupervisorInput.orchestration_mode == "bounded_pipeline".

Plain code decides fan-out, filtering and ranking - never an LLM. Pipeline:

    prefetch -> guardrail -> conversation -> asyncio.gather(customer, competitor)
        -> assemble_recommendation_set (generator -> policy.evaluate -> rank -> render)

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

Everything from candidate generation onward
(orchestration/assemble.py::assemble_recommendation_set) is shared,
verbatim, with orchestration/harness.py's open-ended arm - Phase 11's
NON-NEGOTIABLE rule ("the ONLY difference between arms is
orchestration_mode... the policy engine remains a hard filter in BOTH
arms") is a structural guarantee because there is only one implementation
of that logic in the codebase, not two that happen to agree today.

Bounded re-request (aggregate.MAX_REREQUEST_ATTEMPTS = 1 per agent) is a
hard-coded loop count, never left to model judgement: customer_360 is
re-run at most once if it has a genuine (uncorroborated) evidence gap;
competitor is re-run at most once if it resolved no offers at all. A
degraded (failed/timed-out) agent is never re-retried - see
"Phase 10: fault tolerance" below for why.

Phase 10: fault tolerance
==========================

Every external call this pipeline makes (an LLM agent call, a repository
read) can fail outright or run past its deadline. Fallback belongs in the
schema, not in exception handlers: this module's job is to turn any such
failure into one of the six FAULT SCENARIOS below, never an unhandled
exception, and never a hang. orchestration/deadlines.py is the shared
mechanism - see its own docstring for why a timeout and an arbitrary
exception get the exact same handling here. orchestration/fallbacks.py
holds the degraded-result builders themselves, shared with
orchestration/harness.py.

1. Stale competitor snapshot -> agents/competitor.py already sets
   status="stale" and a confidence_penalty; orchestration/assemble.py
   additionally adds aggregate.competitor_staleness_note to
   agent_context_notes - the "indicative banner" data.
2. Missing usage data -> already status="partial" (agents/customer.py) and
   penalized (aggregate.customer_missing_evidence_adjustments) since
   Phase 4/7 - unchanged here.
3. Contradictory billing rows -> aggregate.billing_contradiction_adjustment
   / billing_contradiction_note: a confidence penalty plus a WARNING note,
   never a silent pick-one-side.
4. Repository unavailable -> the account bootstrap read, the Customer 360
   agent, and the Competitor agent are each independently guarded; any one
   failing falls back (bootstrap failure -> a minimal placeholder account;
   Customer 360 agent failure -> the already-fetched bootstrap account;
   Competitor agent failure -> no competitor comparison) rather than
   raising, and is recorded in both fallbacks_applied and
   confidence_adjustments (aggregate.degraded_agent_adjustment).
5. Deadline breach -> orchestration/deadlines.run_with_deadline actually
   cancels the slow coroutine (asyncio.wait_for's standard behaviour) and
   returns a DeadlineOutcome instead of raising; handled identically to
   scenario 4.
6. Guardrail tripwire -> agents/conversation.py already lets the call
   proceed with a degraded ConversationSignals (Phase 5); this module adds
   an audit_log write when that happens - "logged", per the phase brief.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agents import Model

from churnguard.agents.base import run_agent
from churnguard.agents.competitor import run_competitor_agent
from churnguard.agents.conversation import run_conversation_agent
from churnguard.agents.customer import CUSTOMER_360_SPEC
from churnguard.config import load_settings
from churnguard.contracts.competitor import CompetitorComparison, CompetitorQuery
from churnguard.contracts.conversation import ConversationInput, ConversationSignals
from churnguard.contracts.customer import AccountContext, CustomerContextRequest
from churnguard.contracts.envelope import AgentResult
from churnguard.contracts.recommendation import RecommendationSet, SupervisorInput
from churnguard.data.repositories import customer_repo
from churnguard.orchestration import aggregate, deadlines, prefetch
from churnguard.orchestration.assemble import assemble_recommendation_set
from churnguard.orchestration.context import AgentRequest, RunContext
from churnguard.orchestration.fallbacks import (
    EMPTY_CONVERSATION_SIGNALS,
    agent_result_from_bootstrap,
    degraded_account_context,
    degraded_competitor_result,
    degraded_conversation_result,
    outcome_reason,
)
from churnguard.orchestration.queries import (
    CUSTOMER_INPUT_TEXT,
    DEFAULT_CHANNEL,
    DEFAULT_GEOGRAPHY,
    DEFAULT_JURISDICTION,
    build_competitor_query,
    build_customer_request,
    new_run_context,
)
from churnguard.telemetry.audit import write_audit_record
from churnguard.telemetry.tracer import new_span_id, new_trace_id


def _new_run_context(
    request: AgentRequest,
    *,
    trace_id: str,
    policy_pack_version: str,
    deadline_ms: int,
) -> RunContext:
    return new_run_context(
        request,
        trace_id=trace_id,
        agent_span_id=new_span_id(),
        policy_pack_version=policy_pack_version,
        deadline_ms=deadline_ms,
        db_path=load_settings().database_path,
    )


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
    degraded_agents: list[tuple[str, str]] = []

    # --- prefetch: kick off the competitor snapshot fetch at call connect,
    # before the transcript has named a carrier - see orchestration/prefetch.py ---
    prefetch.start_prefetch(geography)

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
    conversation_outcome = await deadlines.run_with_deadline(
        run_conversation_agent(
            conversation_input, conversation_context, model_override=conversation_model_override
        ),
        deadline_ms=supervisor_input.deadline_ms,
    )
    agent_calls.append("conversation")
    if conversation_outcome.completed:
        assert conversation_outcome.value is not None
        conversation_result = conversation_outcome.value
    else:
        reason = outcome_reason(conversation_outcome)
        degraded_agents.append(("conversation", reason))
        fallbacks_applied.append(
            f"conversation agent unavailable ({reason}); proceeding without transcript signals"
        )
        conversation_result = degraded_conversation_result(reason=reason)

    if conversation_result.status == "insufficient_evidence":
        # FAULT SCENARIO 6: the call proceeds (never blocked outright), the
        # offending span(s) were already quarantined by
        # guardrails/injection.py - this is the "logged" half.
        await write_audit_record(
            trace_id=trace_id,
            prompt="prompt-injection guardrail tripped; offending span(s) quarantined",
            evidence_ids=[],
            policy_pack_version=supervisor_input.policy_pack_version,
            decision="conversation_guardrail_tripped",
            approver_ref=supervisor_input.agent_ref,
        )

    signals = (
        conversation_result.data
        if conversation_result.data is not None
        else EMPTY_CONVERSATION_SIGNALS
    )

    # --- fast, direct Governed Data Layer bootstrap (no LLM) to seed the
    # competitor query without a false dependency on the Customer 360 agent ---
    customer_request = build_customer_request(
        supervisor_input.account_ref,
        line_refs_of_interest=[
            c.line_ref for c in signals.unresolved_concerns if c.line_ref is not None
        ],
        verification_tasks=signals.verification_tasks,
    )
    try:
        bootstrap = await customer_repo.get_account_context(customer_request)
        bootstrap_account = bootstrap.data
        bootstrap_evidence = list(bootstrap.evidence)
    except Exception as exc:  # noqa: BLE001 - FAULT SCENARIO 4, see module docstring
        reason = str(exc)
        degraded_agents.append(("account_bootstrap", reason))
        fallbacks_applied.append(
            f"account data repository unavailable ({reason}); proceeding with a minimal "
            "placeholder account"
        )
        bootstrap_account = degraded_account_context(supervisor_input.account_ref)
        bootstrap_evidence = []

    competitor_query = build_competitor_query(signals, bootstrap_account, geography=geography)

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

    # --- asyncio.gather(customer, competitor): the real fan-out, now with
    # a per-agent deadline + exception guard (orchestration/deadlines.py) ---
    outcomes = await deadlines.gather_with_deadlines(
        {
            "customer_360": (
                run_agent(
                    CUSTOMER_360_SPEC,
                    CUSTOMER_INPUT_TEXT,
                    customer_context,
                    model_override=customer_model_override,
                ),
                supervisor_input.deadline_ms,
            ),
            "competitor": (
                run_competitor_agent(
                    competitor_query, competitor_context, model_override=competitor_model_override
                ),
                supervisor_input.deadline_ms,
            ),
        }
    )
    agent_calls.extend(["customer_360", "competitor"])

    customer_outcome = outcomes["customer_360"]
    if customer_outcome.completed:
        assert customer_outcome.value is not None
        customer_result: AgentResult[AccountContext] = await _maybe_reretry_customer(
            customer_outcome.value,
            signals=signals,
            customer_request=customer_request,
            trace_id=trace_id,
            policy_pack_version=supervisor_input.policy_pack_version,
            deadline_ms=supervisor_input.deadline_ms,
            model_override=customer_model_override,
            fallbacks_applied=fallbacks_applied,
        )
    else:
        reason = outcome_reason(customer_outcome)
        degraded_agents.append(("customer_360", reason))
        fallbacks_applied.append(
            f"customer_360 agent unavailable ({reason}); using the direct Governed Data "
            "Layer read instead"
        )
        customer_result = agent_result_from_bootstrap(
            bootstrap_account, bootstrap_evidence, reason=reason
        )

    competitor_outcome = outcomes["competitor"]
    if competitor_outcome.completed:
        assert competitor_outcome.value is not None
        competitor_result: AgentResult[CompetitorComparison] = await _maybe_reretry_competitor(
            competitor_outcome.value,
            competitor_query=competitor_query,
            trace_id=trace_id,
            policy_pack_version=supervisor_input.policy_pack_version,
            deadline_ms=supervisor_input.deadline_ms,
            model_override=competitor_model_override,
            fallbacks_applied=fallbacks_applied,
        )
    else:
        reason = outcome_reason(competitor_outcome)
        degraded_agents.append(("competitor", reason))
        fallbacks_applied.append(
            f"competitor agent unavailable ({reason}); proceeding without a competitor comparison"
        )
        competitor_result = degraded_competitor_result(reason=reason)

    account = customer_result.data
    assert account is not None, (
        "customer_result always carries data by construction: the real agent result, the "
        "bootstrap fallback, or (worst case) the degraded placeholder"
    )
    competitor = competitor_result.data

    return await assemble_recommendation_set(
        supervisor_input,
        trace_id=trace_id,
        supervisor_span_id=supervisor_span_id,
        agent_calls=agent_calls,
        fallbacks_applied=fallbacks_applied,
        degraded_agents=degraded_agents,
        signals=signals,
        conversation_result=conversation_result,
        account=account,
        customer_result=customer_result,
        competitor=competitor,
        competitor_result=competitor_result,
        jurisdiction=jurisdiction,
        channel=channel,
        supervisor_model_override=supervisor_model_override,
    )


__all__ = ["run_bounded_pipeline"]
