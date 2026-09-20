"""@function_tool wrappers over data/repositories for the Customer 360 agent.

Every domain tool (all but get_account_summary, which is base identity data,
not an optional domain) checks the run's requested_domains BEFORE touching
the repository layer, and refuses - raises DomainNotAuthorizedError - if its
domain was not requested. The check is against RunContext.request, which is
server-side truth set once from the original CustomerContextRequest; nothing
about it is model-controlled, so a hallucinated or adversarial tool call
cannot talk its way past the check by supplying a different domain as an
argument (these tools take no arguments at all beyond the injected context).

A refusal raises rather than returning a null/placeholder value: raising
inside a @function_tool function is caught by the SDK's own tool-error
handling and turned into a tool-output message the model sees (never a
crash of the run) - see agents.default_tool_error_function. Any other
exception (a repository LookupError, for instance) is left to propagate the
same way: refuse loudly to the model, never fabricate or silently swallow.

Every tool returns pre-serialized JSON text rather than a bare Python object
or pydantic model. The SDK's default tool-output stringification is
`str(value)` (Python repr, not JSON) unless the tool declares an
`output_type=`/`output_json_schema=`, and that path additionally requires
the *top-level* shape to be a JSON object - it rejects a bare list, which
several of these tools return. Pre-serializing with `.model_dump_json()` /
`json.dumps()` ourselves sidesteps both problems uniformly, for every
tool, regardless of its return shape.

Evidence is a side channel, not something the model ever sees: every tool
appends the RepoResult's EvidenceRef(s) to ctx.context.evidence, which
orchestration/runner.py reads back after Runner.run() to populate
AgentResult.evidence.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable

from agents import RunContextWrapper, Tool, function_tool
from pydantic import BaseModel, ConfigDict

from churnguard.data.repositories import billing_repo, catalog_repo, customer_repo, promo_repo
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.cost import TOOL_CALL_MODEL
from churnguard.telemetry.tracer import AgentSpan

DOMAIN_BILLING = "billing"
DOMAIN_PAYMENT_HISTORY = "payment_history"
DOMAIN_DEVICE_FINANCING = "device_financing"
DOMAIN_PLAN_PROFILE = "plan_profile"
DOMAIN_USAGE = "usage"
DOMAIN_PROMOTIONS = "promotions"

ALL_CUSTOMER_360_DOMAINS = [
    DOMAIN_BILLING,
    DOMAIN_PAYMENT_HISTORY,
    DOMAIN_DEVICE_FINANCING,
    DOMAIN_PLAN_PROFILE,
    DOMAIN_USAGE,
    DOMAIN_PROMOTIONS,
]


class DomainNotAuthorizedError(Exception):
    """Raised by a tool when its domain is not in the request's requested_domains."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        super().__init__(
            f"domain {domain!r} was not requested for this call - refusing to fetch it"
        )


class AccountSummary(BaseModel):
    """The parts of AccountContext that aren't gated behind a domain."""

    model_config = ConfigDict(extra="forbid")

    account_ref: str
    tenure_months: int
    line_count: int
    verification_results: list[dict[str, object]]
    excluded_fields: list[str]


def _require_domain(ctx: RunContextWrapper[RunContext], domain: str) -> None:
    if domain not in ctx.context.request.requested_domains:
        raise DomainNotAuthorizedError(domain)


async def _traced_repo_call[T](
    ctx: RunContextWrapper[RunContext], tool_name: str, awaitable: Awaitable[T]
) -> T:
    """One child AgentSpan per repo call, nested under the agent's own span.

    model=TOOL_CALL_MODEL prices this at exactly $0 - a tool call never
    invokes an LLM, so its cost isn't a token computation, it's a constant.
    """
    async with AgentSpan(
        f"tool:{tool_name}",
        model=TOOL_CALL_MODEL,
        trace_id=ctx.context.trace_id,
        parent_span_id=ctx.context.agent_span_id,
    ):
        return await awaitable


async def _get_account_summary_impl(ctx: RunContextWrapper[RunContext]) -> str:
    result = await _traced_repo_call(
        ctx, "get_account_summary", customer_repo.get_account_context(ctx.context.request)
    )
    ctx.context.evidence.extend(
        e for e in result.evidence if e.evidence_id.startswith("EVID_ACCT_")
    )
    summary = AccountSummary(
        account_ref=result.data.account_ref,
        tenure_months=result.data.tenure_months,
        line_count=result.data.line_count,
        verification_results=[v.model_dump(mode="json") for v in result.data.verification_results],
        excluded_fields=result.data.excluded_fields,
    )
    return summary.model_dump_json()


async def _get_billing_impl(ctx: RunContextWrapper[RunContext]) -> str:
    _require_domain(ctx, DOMAIN_BILLING)
    result = await _traced_repo_call(
        ctx, "get_billing", billing_repo.get_billing(ctx.context.request.account_ref)
    )
    ctx.context.evidence.extend(result.evidence)
    return result.data.model_dump_json()


async def _get_payment_history_impl(ctx: RunContextWrapper[RunContext]) -> str:
    _require_domain(ctx, DOMAIN_PAYMENT_HISTORY)
    result = await _traced_repo_call(
        ctx,
        "get_payment_history",
        billing_repo.get_payment_history(ctx.context.request.account_ref),
    )
    ctx.context.evidence.extend(result.evidence)
    return result.data.model_dump_json()


async def _get_plan_profile_impl(ctx: RunContextWrapper[RunContext]) -> str:
    _require_domain(ctx, DOMAIN_PLAN_PROFILE)
    result = await _traced_repo_call(
        ctx, "get_plan_profile", catalog_repo.get_plan_profile(ctx.context.request.account_ref)
    )
    ctx.context.evidence.extend(result.evidence)
    return result.data.model_dump_json()


async def _get_device_financing_impl(ctx: RunContextWrapper[RunContext]) -> str:
    _require_domain(ctx, DOMAIN_DEVICE_FINANCING)
    result = await _traced_repo_call(
        ctx,
        "get_device_financing",
        catalog_repo.get_device_financing(ctx.context.request.account_ref),
    )
    ctx.context.evidence.extend(result.evidence)
    return json.dumps([line.model_dump(mode="json") for line in result.data])


async def _get_usage_by_line_impl(ctx: RunContextWrapper[RunContext]) -> str:
    _require_domain(ctx, DOMAIN_USAGE)
    result = await _traced_repo_call(
        ctx, "get_usage_by_line", catalog_repo.get_usage_by_line(ctx.context.request.account_ref)
    )
    ctx.context.evidence.extend(result.evidence)
    return json.dumps(result.data)


async def _get_active_promotions_impl(ctx: RunContextWrapper[RunContext]) -> str:
    _require_domain(ctx, DOMAIN_PROMOTIONS)
    result = await _traced_repo_call(
        ctx,
        "get_active_promotions",
        promo_repo.get_active_promotions(ctx.context.request.account_ref),
    )
    ctx.context.evidence.extend(result.evidence)
    return json.dumps(result.data)


get_account_summary = function_tool(_get_account_summary_impl, name_override="get_account_summary")
get_billing = function_tool(_get_billing_impl, name_override="get_billing")
get_payment_history = function_tool(_get_payment_history_impl, name_override="get_payment_history")
get_plan_profile = function_tool(_get_plan_profile_impl, name_override="get_plan_profile")
get_device_financing = function_tool(
    _get_device_financing_impl, name_override="get_device_financing"
)
get_usage_by_line = function_tool(_get_usage_by_line_impl, name_override="get_usage_by_line")
get_active_promotions = function_tool(
    _get_active_promotions_impl, name_override="get_active_promotions"
)

CUSTOMER_360_TOOLS: list[Tool] = [
    get_account_summary,
    get_billing,
    get_payment_history,
    get_plan_profile,
    get_device_financing,
    get_usage_by_line,
    get_active_promotions,
]


__all__ = [
    "ALL_CUSTOMER_360_DOMAINS",
    "CUSTOMER_360_TOOLS",
    "AccountSummary",
    "DomainNotAuthorizedError",
    "get_account_summary",
    "get_active_promotions",
    "get_billing",
    "get_device_financing",
    "get_payment_history",
    "get_plan_profile",
    "get_usage_by_line",
]
