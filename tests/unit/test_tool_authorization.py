"""A tool must refuse a domain not listed in requested_domains.

Calls the tool implementations directly (bypassing the LLM entirely) with a
hand-built RunContextWrapper, so this exercises exactly the authorization
check in tools/customer_tools.py.
"""

from __future__ import annotations

import json
import random

import pytest
from agents import RunContextWrapper

from churnguard.contracts.customer import CustomerContextRequest
from churnguard.data import masking
from churnguard.data.seed.generate import SEED, build_all_accounts
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import new_span_id, new_trace_id
from churnguard.tools import customer_tools as tools

_SCENARIOS = build_all_accounts(random.Random(SEED))
WORKED_EXAMPLE_REF = masking.mask_account_number(_SCENARIOS[0].account_id)

_DOMAIN_TOOL_IMPLS = {
    tools.DOMAIN_BILLING: tools._get_billing_impl,
    tools.DOMAIN_PAYMENT_HISTORY: tools._get_payment_history_impl,
    tools.DOMAIN_PLAN_PROFILE: tools._get_plan_profile_impl,
    tools.DOMAIN_DEVICE_FINANCING: tools._get_device_financing_impl,
    tools.DOMAIN_USAGE: tools._get_usage_by_line_impl,
    tools.DOMAIN_PROMOTIONS: tools._get_active_promotions_impl,
}


def _make_ctx(requested_domains: list[str]) -> RunContextWrapper[RunContext]:
    request = CustomerContextRequest(
        account_ref=WORKED_EXAMPLE_REF,
        requested_domains=requested_domains,
        lookback_months=3,
        line_refs_of_interest=[],
        verification_tasks=["confirm_current_bill_amount"],
        reason_code="cancel_request",
    )
    run_context = RunContext(
        request=request,
        trace_id=new_trace_id(),
        agent_span_id=new_span_id(),
        policy_pack_version="2026.09.1",
        deadline_ms=2000,
        db_path="unused-in-this-test",
    )
    return RunContextWrapper(context=run_context)


@pytest.mark.parametrize("domain", sorted(_DOMAIN_TOOL_IMPLS))
async def test_tool_refuses_when_its_domain_is_not_requested(domain: str) -> None:
    ctx = _make_ctx(requested_domains=[])

    with pytest.raises(tools.DomainNotAuthorizedError) as exc_info:
        await _DOMAIN_TOOL_IMPLS[domain](ctx)

    assert exc_info.value.domain == domain
    assert ctx.context.evidence == []  # refusal happens before the repository is ever touched


@pytest.mark.parametrize("domain", sorted(_DOMAIN_TOOL_IMPLS))
async def test_tool_refuses_when_only_other_domains_are_requested(domain: str) -> None:
    other_domains = [d for d in _DOMAIN_TOOL_IMPLS if d != domain]
    ctx = _make_ctx(requested_domains=other_domains)

    with pytest.raises(tools.DomainNotAuthorizedError):
        await _DOMAIN_TOOL_IMPLS[domain](ctx)


@pytest.mark.parametrize("domain", sorted(_DOMAIN_TOOL_IMPLS))
async def test_tool_succeeds_when_its_domain_is_requested(domain: str) -> None:
    ctx = _make_ctx(requested_domains=[domain])

    raw_output = await _DOMAIN_TOOL_IMPLS[domain](ctx)

    assert json.loads(raw_output) is not None


async def test_authorized_call_collects_evidence() -> None:
    # The worked example account has no active promotions, so "promotions" is
    # deliberately excluded here - it's the one domain where a real, correctly
    # authorized call legitimately collects zero evidence for this account.
    ctx = _make_ctx(requested_domains=[tools.DOMAIN_BILLING])

    await tools._get_billing_impl(ctx)

    assert len(ctx.context.evidence) >= 1


@pytest.mark.parametrize("requested_domains", [[], list(_DOMAIN_TOOL_IMPLS)])
async def test_account_summary_is_never_domain_gated(requested_domains: list[str]) -> None:
    ctx = _make_ctx(requested_domains=requested_domains)

    raw_output = await tools._get_account_summary_impl(ctx)

    summary = json.loads(raw_output)
    assert summary["account_ref"] == WORKED_EXAMPLE_REF
    assert ctx.context.evidence  # account identity evidence was still collected
