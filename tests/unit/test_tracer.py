"""AgentSpan: telemetry accounting, status, and error propagation."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from churnguard.telemetry.tracer import AgentSpan, export_trace, new_trace_id, reset_state


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    reset_state()
    yield
    reset_state()


async def test_span_produces_telemetry_with_recorded_usage_and_cost() -> None:
    trace_id = new_trace_id()
    async with AgentSpan("customer_360", model="gpt-4.1-mini", trace_id=trace_id) as span:
        span.record_usage(tokens_in=1_000_000, tokens_out=0)

    assert span.telemetry is not None
    assert span.telemetry.model == "gpt-4.1-mini"
    assert span.telemetry.tokens_in == 1_000_000
    assert span.telemetry.tokens_out == 0
    assert span.telemetry.cost_usd == pytest.approx(0.40)
    assert span.telemetry.latency_ms >= 0
    assert span.telemetry.cache_hit is False


async def test_record_usage_accumulates_across_multiple_calls() -> None:
    trace_id = new_trace_id()
    async with AgentSpan("conversation", model="gpt-4.1-nano", trace_id=trace_id) as span:
        span.record_usage(tokens_in=10, tokens_out=5)
        span.record_usage(tokens_in=20, tokens_out=5)

    assert span.tokens_in == 30
    assert span.tokens_out == 10


async def test_cache_hit_flag_passes_through_to_telemetry() -> None:
    trace_id = new_trace_id()
    async with AgentSpan(
        "competitor", model="gpt-4.1-nano", trace_id=trace_id, cache_hit=True
    ) as span:
        pass

    assert span.telemetry is not None
    assert span.telemetry.cache_hit is True


async def test_status_is_error_and_exception_propagates_on_failure() -> None:
    trace_id = new_trace_id()
    with pytest.raises(RuntimeError, match="boom"):
        async with AgentSpan("offer_policy", model="gpt-4.1-nano", trace_id=trace_id) as span:
            raise RuntimeError("boom")

    assert span.telemetry is not None

    tree = export_trace(trace_id)
    assert tree["spans"][0]["status"] == "error"


async def test_ok_status_recorded_for_successful_span() -> None:
    trace_id = new_trace_id()
    async with AgentSpan("supervisor", model="gpt-4.1-nano", trace_id=trace_id):
        pass

    tree = export_trace(trace_id)
    assert tree["spans"][0]["status"] == "ok"


async def test_export_trace_for_unknown_trace_id_returns_empty_span_list() -> None:
    tree = export_trace("not-a-real-trace-id")
    assert tree == {"trace_id": "not-a-real-trace-id", "spans": []}
