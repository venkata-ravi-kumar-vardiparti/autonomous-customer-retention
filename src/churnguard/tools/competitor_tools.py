"""@function_tool wrappers over data/competitor_repo for the Competitor agent.

Only ever reads curated, timestamped snapshot rows - there is no scraping
tool, and no tool in this module reaches into churnguard.data outside of
competitor_repo (checked by tests/unit/test_competitor_tools_no_network.py
via an AST import scan, the same convention test_policy_engine.py uses).
These tools hand back the raw snapshot rows so the model can pick a match;
every number that involves arithmetic (like-for-like adjustment, switching
costs, breakeven, freshness/confidence penalty) is computed afterward by
offers/normalizer.py in agents/competitor.py - see that module's docstring.
"""

from __future__ import annotations

import json

from agents import RunContextWrapper, Tool, function_tool

from churnguard.contracts.competitor import CompetitorQuery
from churnguard.data.repositories import competitor_repo
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.cost import TOOL_CALL_MODEL
from churnguard.telemetry.tracer import AgentSpan


def _competitor_request(ctx: RunContextWrapper[RunContext]) -> CompetitorQuery:
    """RunContext.request is a union across agents - narrow it here, once,
    same pattern as tools/customer_tools.py::_customer_request."""
    request = ctx.context.request
    assert isinstance(request, CompetitorQuery), (
        f"competitor_tools invoked with a non-competitor RunContext.request: {type(request)!r}"
    )
    return request


async def _get_competitor_snapshots_impl(ctx: RunContextWrapper[RunContext]) -> str:
    query = _competitor_request(ctx)
    async with AgentSpan(
        "tool:get_competitor_snapshots",
        model=TOOL_CALL_MODEL,
        trace_id=ctx.context.trace_id,
        parent_span_id=ctx.context.agent_span_id,
    ):
        result = await competitor_repo.get_snapshots(
            query.geography,
            carriers=query.carriers,
            max_snapshot_age_days=query.max_snapshot_age_days,
        )
    ctx.context.evidence.extend(result.evidence)
    return json.dumps([offer.model_dump(mode="json") for offer in result.data])


async def _get_snapshot_meta_impl(ctx: RunContextWrapper[RunContext], snapshot_id: str) -> str:
    async with AgentSpan(
        "tool:get_snapshot_meta",
        model=TOOL_CALL_MODEL,
        trace_id=ctx.context.trace_id,
        parent_span_id=ctx.context.agent_span_id,
    ):
        meta = await competitor_repo.get_snapshot_meta(snapshot_id)
    return meta.model_dump_json()


get_competitor_snapshots = function_tool(
    _get_competitor_snapshots_impl, name_override="get_competitor_snapshots"
)
get_snapshot_meta = function_tool(_get_snapshot_meta_impl, name_override="get_snapshot_meta")

COMPETITOR_TOOLS: list[Tool] = [get_competitor_snapshots, get_snapshot_meta]


__all__ = ["COMPETITOR_TOOLS", "get_competitor_snapshots", "get_snapshot_meta"]
