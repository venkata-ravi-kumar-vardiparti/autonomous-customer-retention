"""The Supervisor: reconciles four specialist views into one ranked,
policy-cleared RecommendationSet.

Two orchestration modes exist on the frozen SupervisorInput.orchestration_mode
contract field:

- "bounded_pipeline" (orchestration/bounded.py): plain code decides the
  fan-out, the hard policy filter and the ranking - no LLM anywhere in that
  decision path.
- "open_harness" (orchestration/harness.py, Phase 11): a real Supervisor
  Agent, on the gpt-4.1 LARGE model (SUPERVISOR_MODEL below - the only
  agent in ChurnGuard on that tier; every specialist stays on
  gpt-4.1-mini), freely decides which evidence to gather and in what
  order, in a loop, until it decides it has enough (max turns bounded for
  safety). See orchestration/harness.py's own docstring for why its tools
  are hand-wrapped versions of run_conversation_agent /
  run_agent(CUSTOMER_360_SPEC) / run_competitor_agent, NOT the SDK's
  Agent.as_tool() applied directly to the bare specialist Agents (an
  earlier Phase 7 sketch tried that and is superseded here - as_tool()'s
  nested Runner.run() bypasses agents/conversation.py's prompt-injection
  sanitization and agents/competitor.py's deterministic price-normalizer
  overwrite, neither of which any phase is allowed to weaken).

In BOTH modes, candidate generation, policy evaluation (an unconditional
hard filter - see orchestration/assemble.py), ranking and rendering are
the exact same function call (orchestration/assemble.py::assemble_recommendation_set) -
the Supervisor (in either mode) never decides an offer's eligibility
itself. `render_recommendation_copy` below is that shared assembly step's
one LLM call: the LARGE model renders title/rationale/talk_track prose
around numbers that are already fully computed, exactly like the Offer
Policy pack's disclosure text and the Competitor agent's narrative
framing. It can never change a rank, a price, or a verdict; if it fails or
times out, the caller falls back to templated copy (fallbacks_applied)
rather than ever blocking on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import Model
from pydantic import BaseModel, ConfigDict

from churnguard.agents.base import AgentSpec, run_agent
from churnguard.contracts.conversation import ConversationSignals
from churnguard.contracts.customer import AccountContext, CustomerContextRequest
from churnguard.contracts.policy import Verdict
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import new_span_id

if TYPE_CHECKING:
    from churnguard.offers.ranker import RankedCandidate

SUPERVISOR_MODEL = "gpt-4.1"
"""The one LARGE-tier agent in ChurnGuard - see each specialist's own
MODEL constant (agents/customer.py, agents/conversation.py,
agents/competitor.py), all gpt-4.1-mini. orchestration/harness.py's
open-ended Supervisor loop also runs on this model."""


# --- shared assembly step: render-only LLM call -----------------------------


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
    "SUPERVISOR_MODEL",
    "render_recommendation_copy",
]
