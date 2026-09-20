"""Golden tests for the Customer 360 agent (Phase 4): the full
Runner.run -> AgentResult[AccountContext] path, against 10 real seeded
accounts, with a scripted "well-behaved" model standing in for a live LLM.

ToolCallingEchoModel calls every tool the agent has and echoes their real
(repository-backed) JSON outputs into the final structured message, so
these tests exercise the actual tool -> repository -> data wiring, not a
canned response - only the "which tokens to emit" decision is faked.
"""

from __future__ import annotations

import json
import math
import random
import time
from typing import Any

import pytest

from churnguard.agents.base import run_agent
from churnguard.agents.customer import CUSTOMER_360_SPEC
from churnguard.config import load_settings
from churnguard.contracts.customer import AccountContext, CustomerContextRequest
from churnguard.data import masking
from churnguard.data.seed.generate import SEED, build_all_accounts
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import export_trace, new_span_id, new_trace_id
from churnguard.tools.customer_tools import ALL_CUSTOMER_360_DOMAINS, CUSTOMER_360_TOOLS
from tests.support.fake_model import ToolCallingEchoModel

_SCENARIOS = build_all_accounts(random.Random(SEED))
_GOLDEN_REFS = [masking.mask_account_number(account.account_id) for account in _SCENARIOS[:10]]
WORKED_EXAMPLE_REF = _GOLDEN_REFS[0]


def _assemble_account_context(outputs: dict[str, Any]) -> str:
    summary = outputs["get_account_summary"]
    payload = {
        "account_ref": summary["account_ref"],
        "tenure_months": summary["tenure_months"],
        "line_count": summary["line_count"],
        "billing": outputs["get_billing"],
        "payment_history": outputs["get_payment_history"],
        "device_financing": outputs["get_device_financing"],
        "plan_profile": outputs["get_plan_profile"],
        "usage_by_line": outputs["get_usage_by_line"],
        "active_promotions": outputs["get_active_promotions"],
        "verification_results": summary["verification_results"],
        "excluded_fields": summary["excluded_fields"],
    }
    return json.dumps(payload)


def _build_run_context(account_ref: str, *, deadline_ms: int = 2000) -> RunContext:
    request = CustomerContextRequest(
        account_ref=account_ref,
        requested_domains=ALL_CUSTOMER_360_DOMAINS,
        lookback_months=3,
        line_refs_of_interest=[],
        verification_tasks=["confirm_current_bill_amount"],
        reason_code="cancel_request",
    )
    return RunContext(
        request=request,
        trace_id=new_trace_id(),
        agent_span_id=new_span_id(),
        policy_pack_version="2026.09.1",
        deadline_ms=deadline_ms,
        db_path=load_settings().database_path,
    )


async def _run_customer_360(account_ref: str, *, deadline_ms: int = 2000):
    run_context = _build_run_context(account_ref, deadline_ms=deadline_ms)
    model = ToolCallingEchoModel(
        tools=CUSTOMER_360_TOOLS, assemble_output=_assemble_account_context
    )
    result = await run_agent(
        CUSTOMER_360_SPEC,
        "Retrieve and structure this account's data for a live retention call.",
        run_context,
        model_override=model,
    )
    return result, run_context


@pytest.mark.parametrize("account_ref", _GOLDEN_REFS)
async def test_customer_360_produces_a_schema_valid_result_for_every_seeded_account(
    account_ref: str,
) -> None:
    result, _ = await _run_customer_360(account_ref)

    assert result.status in ("ok", "partial")
    assert isinstance(result.data, AccountContext)
    assert result.data.account_ref == account_ref
    assert result.evidence
    assert result.telemetry.model == "gpt-4.1-mini"
    assert result.telemetry.latency_ms >= 0


async def test_worked_example_matches_seeded_values() -> None:
    result, _ = await _run_customer_360(WORKED_EXAMPLE_REF)

    assert result.data is not None
    billing = result.data.billing
    assert billing.current_bill == pytest.approx(198.43)
    assert billing.delta == pytest.approx(33.23)
    assert len(billing.delta_attribution) == 3

    assert len(result.data.device_financing) == 1
    assert result.data.device_financing[0].remaining_balance == pytest.approx(312.40)


async def test_worked_example_null_usage_line_produces_partial_status() -> None:
    result, _ = await _run_customer_360(WORKED_EXAMPLE_REF)

    assert result.data is not None
    assert result.data.usage_by_line[masking.mask_line_ref(3)] is None
    assert result.status == "partial"
    assert "usage_by_line.LINE_****03" in result.missing_evidence


async def test_tool_calls_appear_as_child_spans_with_their_own_latency_and_cost() -> None:
    result, run_context = await _run_customer_360(WORKED_EXAMPLE_REF)
    assert result.status in ("ok", "partial")

    tree = export_trace(run_context.trace_id)
    assert len(tree["spans"]) == 1

    root = tree["spans"][0]
    assert root["name"] == "customer_360"
    assert len(root["children"]) == len(CUSTOMER_360_TOOLS)

    tool_names = {child["name"] for child in root["children"]}
    assert tool_names == {f"tool:{tool.name}" for tool in CUSTOMER_360_TOOLS}

    for child in root["children"]:
        assert child["parent_span_id"] == root["span_id"]
        assert child["telemetry"]["model"] == "tool_call"
        assert child["telemetry"]["cost_usd"] == 0.0
        assert child["telemetry"]["latency_ms"] >= 0


async def test_p95_latency_under_700ms_with_realistic_db_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_LATENCY_MS", "40")

    latencies_ms: list[float] = []
    for account_ref in _GOLDEN_REFS:
        start = time.perf_counter()
        result, _ = await _run_customer_360(account_ref)
        latencies_ms.append((time.perf_counter() - start) * 1000)
        assert result.status in ("ok", "partial")

    latencies_ms.sort()
    p95_index = min(len(latencies_ms) - 1, math.ceil(0.95 * len(latencies_ms)) - 1)
    p95_latency_ms = latencies_ms[p95_index]

    assert p95_latency_ms < 700, f"p95 latency {p95_latency_ms:.1f}ms exceeded the 700ms budget"
