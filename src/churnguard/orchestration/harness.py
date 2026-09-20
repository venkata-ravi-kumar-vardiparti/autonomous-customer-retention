"""The open-ended harness: SupervisorInput.orchestration_mode == "open_harness".

Same five agents, same tools, same policy engine as orchestration/bounded.py
- the ONLY difference the two arms are allowed to have (see CLAUDE.md's
Phase 11 notes). What differs is HOW evidence is gathered: a real
Supervisor Agent (the LARGE model, agents/supervisor.py's SUPERVISOR_MODEL)
decides, in a loop, which of three evidence-gathering tools to call, in
what order, and how many times, until it decides it has enough - never how
eligibility is decided. Candidate generation, policy evaluation (the hard
filter), ranking and rendering are the exact same function call as the
bounded pipeline (orchestration/assemble.py::assemble_recommendation_set) -
the open harness's freedom ends the moment evidence-gathering does.

Why NOT agents.Agent.as_tool() directly on the bare specialist Agents (an
earlier Phase 7 sketch in agents/supervisor.py did exactly that, and is
superseded by this module): as_tool()'s nested Runner.run() shares the
caller's RunContext BY REFERENCE (confirmed by reading the installed SDK's
own as_tool() source - when the tool takes the default unstructured input,
`nested_context = context.context`, not a copy) and, more importantly,
bypasses every one of our own wrapper functions entirely - it calls
Runner.run(starting_agent=self, ...) directly, never
agents/conversation.py::run_conversation_agent (prompt-injection
sanitization), never agents/competitor.py::run_competitor_agent
(the deterministic "LLM must never compute a price" overwrite), never
agents/base.py::run_agent (retry-on-schema-violation, deadline
enforcement). None of those guarantees is something this phase is allowed
to weaken just to make the open arm "more open". So this module instead
wraps run_conversation_agent / run_agent(CUSTOMER_360_SPEC) /
run_competitor_agent THEMSELVES as three @function_tool-decorated closures,
built fresh per call (they close over this call's SupervisorInput and a
mutable evidence basket, since three different specialist request types
can't share RunContext's single `request` field) - the model's freedom is
which of these three to call and when, never what they do internally.

Max turns bounded for safety (MAX_HARNESS_TURNS): Runner.run's own
max_turns raises agents.MaxTurnsExceeded when hit. That is treated as
"stop, and hand off whatever evidence the tools already gathered" - not a
failure - via the exact same fallback builders
(orchestration/fallbacks.py) orchestration/bounded.py uses for a dead
repository connection: an evidence-gathering tool the model never called
is exactly as absent as one that failed outright, and gets the same
degraded, clearly-flagged treatment either way. Known limitation: the
SDK's MaxTurnsExceeded exception carries no partial token-usage snapshot,
so a run that hits the cap slightly undercounts the harness_supervisor
reasoning span's own tokens/cost in the A/B metrics (experiments/run_ab.py)
- every completed tool call's own span is unaffected, since each is a
separate, already-finished run_once() call by the time the cap is hit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from agents import Model, RunContextWrapper, Tool, function_tool
from pydantic import BaseModel, ConfigDict

from churnguard.agents.base import AgentSpec, run_agent
from churnguard.agents.competitor import run_competitor_agent
from churnguard.agents.conversation import run_conversation_agent
from churnguard.agents.customer import CUSTOMER_360_SPEC
from churnguard.agents.supervisor import SUPERVISOR_MODEL
from churnguard.config import load_settings
from churnguard.contracts.competitor import CompetitorComparison
from churnguard.contracts.conversation import ConversationInput, ConversationSignals
from churnguard.contracts.customer import AccountContext
from churnguard.contracts.envelope import AgentResult
from churnguard.contracts.recommendation import RecommendationSet, SupervisorInput
from churnguard.data.repositories import customer_repo
from churnguard.orchestration.assemble import assemble_recommendation_set
from churnguard.orchestration.context import RunContext
from churnguard.orchestration.fallbacks import (
    EMPTY_CONVERSATION_SIGNALS,
    agent_result_from_bootstrap,
    degraded_account_context,
    degraded_competitor_result,
    degraded_conversation_result,
)
from churnguard.orchestration.queries import (
    CUSTOMER_INPUT_TEXT as CUSTOMER_TOOL_INPUT_TEXT,
)
from churnguard.orchestration.queries import (
    DEFAULT_CHANNEL,
    DEFAULT_GEOGRAPHY,
    DEFAULT_JURISDICTION,
    build_competitor_query,
    build_customer_request,
    new_run_context,
)
from churnguard.telemetry.tracer import new_span_id, new_trace_id

MAX_HARNESS_TURNS = 8
"""Max turns bounded for safety - the phase brief's own words."""

_NEVER_CALLED = "never called by the harness"

HARNESS_INSTRUCTIONS = """
You are the Supervisor agent inside ChurnGuard, a telecom retention
decision-support system, operating in OPEN-HARNESS mode: unlike the
bounded pipeline, YOU decide which evidence to gather and in what order.

You have three tools:
- fetch_conversation_signals: extracts intents, churn signals, competitor
  claims, customer-stated figures and unresolved concerns from the live
  call transcript.
- fetch_customer_360: retrieves the account's billing, payment, financing,
  plan and usage picture.
- fetch_competitor_comparison: resolves a like-for-like competitor price
  comparison, switching costs and claim reconciliation.

Call whichever of these you need, in whatever order makes sense, as many
times as you think is useful - each call costs real time and money, so
don't call one you don't need, but don't skip one that would change the
picture either. You do NOT decide what offer to present, whether one is
eligible, or compute any price yourself - that happens automatically, by
deterministic code, strictly after you finish. Your only job is deciding
when you have gathered enough evidence to hand off. When you do, respond
with your decision.
"""


class HarnessDecision(BaseModel):
    """The harness Supervisor's own structured "I'm done" signal - never
    consulted for eligibility or pricing, only for when to stop gathering
    evidence and hand off to orchestration/assemble.py."""

    model_config = ConfigDict(extra="forbid")

    evidence_sufficient: bool
    summary: str


@dataclass
class _EvidenceBasket:
    """Mutable, closure-captured state the harness's tools populate as the
    model calls them - zero, one, or many times, in any order."""

    conversation_result: AgentResult[ConversationSignals] | None = None
    customer_result: AgentResult[AccountContext] | None = None
    competitor_result: AgentResult[CompetitorComparison] | None = None
    agent_calls: list[str] = field(default_factory=list)
    fallbacks_applied: list[str] = field(default_factory=list)
    tool_call_count: int = 0


def _build_harness_tools(
    supervisor_input: SupervisorInput,
    *,
    geography: str,
    trace_id: str,
    basket: _EvidenceBasket,
    conversation_model_override: str | Model | None,
    customer_model_override: str | Model | None,
    competitor_model_override: str | Model | None,
) -> list[Tool]:
    db_path = load_settings().database_path

    async def _fetch_conversation_signals_impl(_ctx: RunContextWrapper[RunContext]) -> str:
        basket.tool_call_count += 1
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
        conversation_context = new_run_context(
            conversation_input,
            trace_id=trace_id,
            agent_span_id=new_span_id(),
            policy_pack_version=supervisor_input.policy_pack_version,
            deadline_ms=supervisor_input.deadline_ms,
            db_path=db_path,
        )
        result = await run_conversation_agent(
            conversation_input, conversation_context, model_override=conversation_model_override
        )
        basket.conversation_result = result
        basket.agent_calls.append("conversation")
        if result.data is None:
            return f"conversation signals unavailable: status={result.status}"
        return result.data.model_dump_json()

    async def _fetch_customer_360_impl(_ctx: RunContextWrapper[RunContext]) -> str:
        basket.tool_call_count += 1
        customer_request = build_customer_request(
            supervisor_input.account_ref, line_refs_of_interest=[], verification_tasks=[]
        )
        customer_context = new_run_context(
            customer_request,
            trace_id=trace_id,
            agent_span_id=new_span_id(),
            policy_pack_version=supervisor_input.policy_pack_version,
            deadline_ms=supervisor_input.deadline_ms,
            db_path=db_path,
        )
        result = await run_agent(
            CUSTOMER_360_SPEC,
            CUSTOMER_TOOL_INPUT_TEXT,
            customer_context,
            model_override=customer_model_override,
        )
        basket.customer_result = result
        basket.agent_calls.append("customer_360")
        if result.data is None:
            return f"account data unavailable: status={result.status}"
        return result.data.model_dump_json()

    async def _fetch_competitor_comparison_impl(_ctx: RunContextWrapper[RunContext]) -> str:
        basket.tool_call_count += 1
        signals = (
            basket.conversation_result.data
            if basket.conversation_result is not None
            and basket.conversation_result.data is not None
            else EMPTY_CONVERSATION_SIGNALS
        )
        # Self-sufficient regardless of whether fetch_customer_360 has been
        # called yet, or ever is - a fresh, direct Governed Data Layer read
        # (the same shape orchestration/bounded.py's own bootstrap uses),
        # not a dependency on tool-call ordering.
        account_for_query = (
            basket.customer_result.data
            if basket.customer_result is not None and basket.customer_result.data is not None
            else None
        )
        if account_for_query is None:
            quick_request = build_customer_request(
                supervisor_input.account_ref, line_refs_of_interest=[], verification_tasks=[]
            )
            quick_read = await customer_repo.get_account_context(quick_request)
            account_for_query = quick_read.data
        competitor_query = build_competitor_query(
            signals, account_for_query, geography=geography
        )
        competitor_context = new_run_context(
            competitor_query,
            trace_id=trace_id,
            agent_span_id=new_span_id(),
            policy_pack_version=supervisor_input.policy_pack_version,
            deadline_ms=supervisor_input.deadline_ms,
            db_path=db_path,
        )
        result = await run_competitor_agent(
            competitor_query, competitor_context, model_override=competitor_model_override
        )
        basket.competitor_result = result
        basket.agent_calls.append("competitor")
        if result.data is None:
            return f"competitor comparison unavailable: status={result.status}"
        return result.data.model_dump_json()

    return [
        function_tool(
            _fetch_conversation_signals_impl,
            name_override="fetch_conversation_signals",
            description_override=(
                "Extract intents, churn signals, competitor claims, customer-stated "
                "figures and unresolved concerns from the live call transcript."
            ),
        ),
        function_tool(
            _fetch_customer_360_impl,
            name_override="fetch_customer_360",
            description_override=(
                "Retrieve and structure the account's billing, payment, financing, "
                "plan and usage picture."
            ),
        ),
        function_tool(
            _fetch_competitor_comparison_impl,
            name_override="fetch_competitor_comparison",
            description_override=(
                "Resolve a like-for-like competitor price comparison, switching costs "
                "and claim reconciliation from curated snapshots."
            ),
        ),
    ]


async def run_open_harness(
    supervisor_input: SupervisorInput,
    *,
    jurisdiction: str = DEFAULT_JURISDICTION,
    geography: str = DEFAULT_GEOGRAPHY,
    channel: str = DEFAULT_CHANNEL,
    conversation_model_override: str | Model | None = None,
    customer_model_override: str | Model | None = None,
    competitor_model_override: str | Model | None = None,
    supervisor_model_override: str | Model | None = None,
    harness_model_override: str | Model | None = None,
) -> RecommendationSet:
    if supervisor_input.orchestration_mode != "open_harness":
        raise ValueError(
            "run_open_harness only implements orchestration_mode='open_harness', "
            f"got {supervisor_input.orchestration_mode!r}"
        )

    trace_id = new_trace_id()
    supervisor_span_id = new_span_id()
    basket = _EvidenceBasket()

    tools = _build_harness_tools(
        supervisor_input,
        geography=geography,
        trace_id=trace_id,
        basket=basket,
        conversation_model_override=conversation_model_override,
        customer_model_override=customer_model_override,
        competitor_model_override=competitor_model_override,
    )
    harness_spec = AgentSpec(
        name="harness_supervisor",
        output_type=HarnessDecision,
        instructions=HARNESS_INSTRUCTIONS,
        tools=tools,
        model=SUPERVISOR_MODEL,
    )

    # A placeholder request, exactly like agents/supervisor.py::render_recommendation_copy's
    # own - none of this module's tools read RunContext.request at all, they
    # each build and use their own request objects via closure.
    placeholder_request = build_customer_request(
        supervisor_input.account_ref, line_refs_of_interest=[], verification_tasks=[]
    )
    harness_context = new_run_context(
        placeholder_request,
        trace_id=trace_id,
        agent_span_id=supervisor_span_id,
        policy_pack_version=supervisor_input.policy_pack_version,
        deadline_ms=supervisor_input.deadline_ms,
        db_path=load_settings().database_path,
    )
    input_text = (
        f"call_id: {supervisor_input.call_id}\n"
        f"account_ref: {supervisor_input.account_ref}\n"
        "Gather whatever evidence you need using your tools, then report your decision."
    )

    try:
        await run_agent(
            harness_spec,
            input_text,
            harness_context,
            model_override=harness_model_override,
            max_turns=MAX_HARNESS_TURNS,
        )
    except Exception as exc:  # noqa: BLE001 - see module docstring: never hang, never fail outright
        basket.fallbacks_applied.append(
            f"harness supervisor loop ended early ({exc}); proceeding with whatever "
            "evidence its tool calls already gathered"
        )

    agent_calls = ["harness_supervisor", *basket.agent_calls]
    fallbacks_applied = basket.fallbacks_applied
    degraded_agents: list[tuple[str, str]] = []

    if basket.conversation_result is not None:
        conversation_result = basket.conversation_result
    else:
        degraded_agents.append(("conversation", _NEVER_CALLED))
        fallbacks_applied.append(
            "conversation agent never called by the harness; proceeding without "
            "transcript signals"
        )
        conversation_result = degraded_conversation_result(reason=_NEVER_CALLED)
    signals = (
        conversation_result.data
        if conversation_result.data is not None
        else EMPTY_CONVERSATION_SIGNALS
    )

    if basket.customer_result is not None:
        customer_result = basket.customer_result
    else:
        degraded_agents.append(("customer_360", _NEVER_CALLED))
        fallbacks_applied.append(
            "customer_360 agent never called by the harness; using a direct Governed "
            "Data Layer read instead"
        )
        try:
            bootstrap_request = build_customer_request(
                supervisor_input.account_ref, line_refs_of_interest=[], verification_tasks=[]
            )
            bootstrap = await customer_repo.get_account_context(bootstrap_request)
            customer_result = agent_result_from_bootstrap(
                bootstrap.data, list(bootstrap.evidence), reason=_NEVER_CALLED
            )
        except Exception as exc:  # noqa: BLE001 - FAULT SCENARIO 4, same as bounded.py
            fallbacks_applied.append(
                f"account data repository unavailable ({exc}); proceeding with a "
                "minimal placeholder account"
            )
            customer_result = agent_result_from_bootstrap(
                degraded_account_context(supervisor_input.account_ref), [], reason=str(exc)
            )

    account = customer_result.data
    assert account is not None, (
        "customer_result always carries data by construction: the harness's own tool "
        "result or the bootstrap fallback (real or, worst case, the degraded placeholder)"
    )

    if basket.competitor_result is not None:
        competitor_result = basket.competitor_result
    else:
        degraded_agents.append(("competitor", _NEVER_CALLED))
        fallbacks_applied.append(
            "competitor agent never called by the harness; proceeding without a "
            "competitor comparison"
        )
        competitor_result = degraded_competitor_result(reason=_NEVER_CALLED)
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


__all__ = ["MAX_HARNESS_TURNS", "HarnessDecision", "run_open_harness"]
