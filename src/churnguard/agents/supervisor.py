"""The Supervisor: reconciles four specialist views into one ranked,
policy-cleared RecommendationSet.

Two orchestration modes exist on the frozen SupervisorInput.orchestration_mode
contract field, with two different implementations:

- "bounded_pipeline" (orchestration/bounded.py): plain code decides the
  fan-out, the hard policy filter and the ranking - no LLM anywhere in that
  decision path. This is what every acceptance test in tests/e2e/
  exercises (deterministic rank order, a policy filter that can't be
  bypassed by model behaviour, a <2s p95 budget). The one LLM call in that
  path is `render_recommendation_copy` below - the LARGE model renders
  title/rationale/talk_track prose around numbers that are already fully
  computed, exactly like the Offer Policy pack's disclosure text and the
  Competitor agent's narrative framing. It can never change a rank, a
  price, or a verdict; if it fails or times out, orchestration/bounded.py
  falls back to templated copy (see fallbacks_applied) rather than ever
  blocking on it.
- "open_harness" (build_supervisor_tools/SUPERVISOR_SPEC below): a real
  Agent, on the gpt-4.1 LARGE model - the only agent in ChurnGuard on that
  tier; every specialist stays on gpt-4.1-mini (see each agent's own MODEL
  constant) - whose tools ARE the other three specialist agents themselves,
  wired in via Agent.as_tool(...): agents-as-tools, never handoffs. A
  handoff would transfer the whole run to one sub-agent and lose
  ChurnGuard's own turn; agents-as-tools lets the Supervisor call several
  specialists as ordinary tool calls in the same turn (the SDK already runs
  same-turn tool calls concurrently - see agents/base.py's Phase 4 notes)
  and still see every result itself, to reconcile. This mode is exploratory
  (an LLM decides call order/count, so it has none of the bounded
  pipeline's latency or determinism guarantees) and is not exercised by
  tests/e2e - it exists so orchestration_mode has a real implementation on
  both sides of the Literal, not a dead enum value. A known, documented
  limitation: RunContext.request is a single field per run (Phase 4/5/6's
  design for narrowing a tool's own request type), so a Supervisor agent
  actually driving this mode end-to-end would need a per-tool-call context
  scheme beyond what's built here - out of scope for this phase.

Regardless of mode, the Supervisor never decides an offer's eligibility
itself: policy.engine.evaluate's verdicts are an unconditional hard filter,
called directly in the bounded pipeline or via the evaluate_policy tool in
open_harness mode - the model only ever renders text around an
already-computed verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import Model, RunContextWrapper, Tool, function_tool
from pydantic import BaseModel, ConfigDict

from churnguard.agents.base import AgentSpec, build_agent, run_agent
from churnguard.agents.competitor import COMPETITOR_SPEC
from churnguard.agents.conversation import CONVERSATION_SPEC
from churnguard.agents.customer import CUSTOMER_360_SPEC
from churnguard.contracts.conversation import ConversationSignals
from churnguard.contracts.customer import AccountContext, CustomerContextRequest
from churnguard.contracts.policy import PolicyEvaluationRequest, Verdict
from churnguard.contracts.recommendation import RecommendationSet
from churnguard.orchestration.context import RunContext
from churnguard.policy import engine as policy_engine
from churnguard.telemetry.tracer import new_span_id

if TYPE_CHECKING:
    from churnguard.offers.ranker import RankedCandidate

SUPERVISOR_MODEL = "gpt-4.1"
"""The one LARGE-tier agent in ChurnGuard - see each specialist's own
MODEL constant (agents/customer.py, agents/conversation.py,
agents/competitor.py), all gpt-4.1-mini."""


# --- open_harness: agents-as-tools wiring -----------------------------------


async def _evaluate_policy_impl(ctx: RunContextWrapper[RunContext], request_json: str) -> str:
    """Even in open_harness mode the verdict stays deterministic code - the
    model supplies a PolicyEvaluationRequest, never a verdict."""
    request = PolicyEvaluationRequest.model_validate_json(request_json)
    result = policy_engine.evaluate(request)
    return result.model_dump_json()


evaluate_policy = function_tool(_evaluate_policy_impl, name_override="evaluate_policy")


def build_supervisor_tools() -> list[Tool]:
    """Wraps the three specialist agents as callable tools via Agent.as_tool -
    agents-as-tools, not handoffs. Each call still runs the specialist's own
    real Agent object (same instructions/tools/output_type build_agent()
    always uses); it does not go through agents/base.py's run_agent, so a
    Supervisor-driven call in this mode does not get its own retry/deadline
    handling layered on top - open_harness's tradeoff, not the bounded
    pipeline's, which calls run_agent directly for exactly that guarantee.
    """
    return [
        build_agent(CUSTOMER_360_SPEC).as_tool(
            tool_name="customer_360",
            tool_description=(
                "Retrieve and structure the account's billing, payment, financing, "
                "plan and usage picture."
            ),
        ),
        build_agent(CONVERSATION_SPEC).as_tool(
            tool_name="conversation",
            tool_description=(
                "Extract intents, churn signals, competitor claims, customer-stated "
                "figures and unresolved concerns from the live transcript."
            ),
        ),
        build_agent(COMPETITOR_SPEC).as_tool(
            tool_name="competitor",
            tool_description=(
                "Resolve a like-for-like competitor price comparison, switching "
                "costs and claim reconciliation from curated snapshots."
            ),
        ),
        evaluate_policy,
    ]


SUPERVISOR_INSTRUCTIONS = """
You are the Supervisor agent inside ChurnGuard, a telecom retention
decision-support system. A human retention agent is on a live call; you
reconcile the Conversation, Customer 360 and Competitor agents' views into
one ranked, evidence-cited RecommendationSet.

You NEVER decide whether an offer is eligible yourself - always call
evaluate_policy and treat its verdicts as final; an offer it blocks must
never appear as a recommendation. You NEVER compute a price, a discount,
or a confidence score - report exactly what your tools return. ChurnGuard
only recommends; it never executes anything, and commitment_status must
always be "none".
"""

SUPERVISOR_SPEC = AgentSpec(
    name="supervisor",
    output_type=RecommendationSet,
    instructions=SUPERVISOR_INSTRUCTIONS,
    tools=build_supervisor_tools(),
    model=SUPERVISOR_MODEL,
)


# --- bounded_pipeline: render-only LLM step ---------------------------------


class _RenderedCopy(BaseModel):
    """One candidate's rendered prose - never a number the pipeline relies on."""

    model_config = ConfigDict(extra="forbid")

    offer_id: str
    title: str
    rationale: str
    talk_track: str


class _RenderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendations: list[_RenderedCopy]


RENDER_INSTRUCTIONS = """
You are the Supervisor agent inside ChurnGuard, a telecom retention
decision-support system, in its text-rendering role: every number below
(monthly deltas, new monthly bills, disclosures, tiers) has already been
computed and policy-cleared by deterministic code. Your ONLY job is to
write a short title, a one-to-two sentence rationale, and a natural-sounding
talk_track a human agent can read to the customer, for each candidate.

Never invent, adjust, or restate a different number than the one given -
quote the given new monthly bill and delta figures exactly. Ground the
rationale in the given reconciliation facts (e.g. a mismatch between what
the customer believes their bill is and what it actually is) when present.
Return one entry per offer_id given, using that exact offer_id.
"""

RENDER_SPEC = AgentSpec(
    name="supervisor_render",
    output_type=_RenderOutput,
    instructions=RENDER_INSTRUCTIONS,
    tools=[],
    model=SUPERVISOR_MODEL,
)


def _render_input_text(
    ranked: list[RankedCandidate],
    *,
    verdicts_by_id: dict[str, Verdict],
    account: AccountContext,
    signals: ConversationSignals,
) -> str:
    lines = [f"current_monthly: {account.billing.current_bill:.2f}"]
    for figure in signals.customer_stated_figures:
        lines.append(f"customer_stated_figure: {figure.field}={figure.value} ({figure.precision})")
    for cause in account.billing.delta_attribution:
        lines.append(
            f"billing_delta_cause: {cause.cause} amount={cause.amount:.2f} "
            f"reversible={cause.reversible}"
        )
    for ranked_candidate in ranked:
        candidate = ranked_candidate.candidate
        verdict = verdicts_by_id[candidate.candidate_id]
        new_monthly = round(account.billing.current_bill + candidate.total_monthly_impact, 2)
        lines.append(
            f"candidate offer_id={candidate.candidate_id} kind={ranked_candidate.kind} "
            f"monthly_delta={candidate.total_monthly_impact:.2f} new_monthly={new_monthly:.2f} "
            f"disclosures={[d.code for d in verdict.required_disclosures]}"
        )
    return "\n".join(lines)


async def render_recommendation_copy(
    ranked: list[RankedCandidate],
    *,
    verdicts_by_id: dict[str, Verdict],
    account: AccountContext,
    signals: ConversationSignals,
    trace_id: str,
    supervisor_span_id: str,
    policy_pack_version: str,
    deadline_ms: int,
    model_override: str | Model | None,
) -> tuple[dict[str, tuple[str, str, str]], bool]:
    """Returns ({offer_id: (title, rationale, talk_track)}, render_failed).

    render_failed=True means the caller should fall back to templated copy
    for every offer_id not present in the returned dict - this function
    never raises, so a render-step failure can never take down the whole
    pipeline.
    """
    if not ranked:
        return {}, False

    placeholder_request = CustomerContextRequest(
        account_ref=account.account_ref,
        requested_domains=[],
        lookback_months=0,
        line_refs_of_interest=[],
        verification_tasks=[],
        reason_code="supervisor_render",
    )
    run_context = RunContext(
        request=placeholder_request,
        trace_id=trace_id,
        agent_span_id=new_span_id(),
        policy_pack_version=policy_pack_version,
        deadline_ms=deadline_ms,
        db_path="",
    )
    input_text = _render_input_text(
        ranked, verdicts_by_id=verdicts_by_id, account=account, signals=signals
    )
    try:
        result = await run_agent(
            RENDER_SPEC, input_text, run_context, model_override=model_override
        )
    except Exception:  # a render failure must never block a recommendation
        return {}, True

    if result.data is None:
        return {}, True

    rendered = {
        entry.offer_id: (entry.title, entry.rationale, entry.talk_track)
        for entry in result.data.recommendations
    }
    return rendered, False


__all__ = [
    "RENDER_SPEC",
    "SUPERVISOR_INSTRUCTIONS",
    "SUPERVISOR_MODEL",
    "SUPERVISOR_SPEC",
    "build_supervisor_tools",
    "evaluate_policy",
    "render_recommendation_copy",
]
