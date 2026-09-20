"""A three-level nested call (Supervisor -> Customer 360 -> a sub-lookup)
must produce a correctly shaped span tree from export_trace.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from churnguard.telemetry.tracer import AgentSpan, export_trace, new_trace_id, reset_state


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    reset_state()
    yield
    reset_state()


def _find(node: dict, span_id: str) -> dict | None:
    if node["span_id"] == span_id:
        return node
    for child in node["children"]:
        found = _find(child, span_id)
        if found is not None:
            return found
    return None


async def test_three_level_nested_call_produces_a_correctly_shaped_span_tree() -> None:
    trace_id = new_trace_id()

    async with AgentSpan("supervisor", model="gpt-4.1", trace_id=trace_id) as root:
        root.record_usage(tokens_in=100, tokens_out=50)

        async with AgentSpan(
            "customer_360", model="gpt-4.1-mini", trace_id=trace_id, parent_span_id=root.span_id
        ) as mid:
            mid.record_usage(tokens_in=40, tokens_out=10)

            async with AgentSpan(
                "billing_repo_lookup",
                model="gpt-4.1-nano",
                trace_id=trace_id,
                parent_span_id=mid.span_id,
            ) as leaf:
                leaf.record_usage(tokens_in=5, tokens_out=1)

    tree = export_trace(trace_id)

    assert tree["trace_id"] == trace_id
    assert len(tree["spans"]) == 1

    root_node = tree["spans"][0]
    assert root_node["span_id"] == root.span_id
    assert root_node["name"] == "supervisor"
    assert root_node["parent_span_id"] is None
    assert len(root_node["children"]) == 1

    mid_node = root_node["children"][0]
    assert mid_node["span_id"] == mid.span_id
    assert mid_node["name"] == "customer_360"
    assert mid_node["parent_span_id"] == root.span_id
    assert len(mid_node["children"]) == 1

    leaf_node = mid_node["children"][0]
    assert leaf_node["span_id"] == leaf.span_id
    assert leaf_node["name"] == "billing_repo_lookup"
    assert leaf_node["parent_span_id"] == mid.span_id
    assert leaf_node["children"] == []

    for node, span in ((root_node, root), (mid_node, mid), (leaf_node, leaf)):
        assert node["telemetry"]["tokens_in"] == span.tokens_in
        assert node["telemetry"]["tokens_out"] == span.tokens_out
        assert node["status"] == "ok"


async def test_sibling_branches_at_the_same_level_stay_separate() -> None:
    trace_id = new_trace_id()

    async with AgentSpan("supervisor", model="gpt-4.1", trace_id=trace_id) as root:
        async with AgentSpan(
            "customer_360", model="gpt-4.1-mini", trace_id=trace_id, parent_span_id=root.span_id
        ):
            pass
        async with AgentSpan(
            "competitor", model="gpt-4.1-mini", trace_id=trace_id, parent_span_id=root.span_id
        ):
            pass

    tree = export_trace(trace_id)
    root_node = tree["spans"][0]
    assert len(root_node["children"]) == 2
    names = {child["name"] for child in root_node["children"]}
    assert names == {"customer_360", "competitor"}
