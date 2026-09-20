"""ACCEPTANCE 4 (p95 < 2000ms) and ACCEPTANCE 5 (FAN-OUT TEST: customer and
competitor agent spans genuinely overlap in wall-clock time) for
orchestration/bounded.py.
"""

from __future__ import annotations

import math
import time
from datetime import datetime

import pytest

from churnguard.orchestration.bounded import run_bounded_pipeline
from churnguard.telemetry.tracer import export_trace
from tests.e2e import support

_RUNS = 10
_FAN_OUT_DELAY_SECONDS = 0.15


async def test_p95_latency_under_2000ms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_LATENCY_MS", "40")

    latencies_ms: list[float] = []
    for _ in range(_RUNS):
        supervisor_input = support.build_supervisor_input(support.reference_transcript())
        start = time.perf_counter()
        result = await run_bounded_pipeline(
            supervisor_input,
            conversation_model_override=support.reference_conversation_model(),
            customer_model_override=support.customer_model(),
            competitor_model_override=support.competitor_model(),
            supervisor_model_override=support.render_model_with_canned_copy(),
        )
        latencies_ms.append((time.perf_counter() - start) * 1000)
        assert result.recommendations

    latencies_ms.sort()
    p95_index = min(len(latencies_ms) - 1, math.ceil(0.95 * len(latencies_ms)) - 1)
    p95_latency_ms = latencies_ms[p95_index]

    assert p95_latency_ms < 2000, f"p95 latency {p95_latency_ms:.1f}ms exceeded the 2000ms budget"


async def test_customer_and_competitor_agents_overlap_in_wall_clock_time() -> None:
    """Proves asyncio.gather(customer, competitor) is a real fan-out, not a
    hidden sequential call: both model doubles sleep long enough
    (_FAN_OUT_DELAY_SECONDS) that a serialized implementation would show
    non-overlapping span windows, while a genuine concurrent dispatch shows
    overlapping ones - independent of any absolute latency number."""
    supervisor_input = support.build_supervisor_input(support.reference_transcript())
    result = await run_bounded_pipeline(
        supervisor_input,
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(delay_seconds=_FAN_OUT_DELAY_SECONDS),
        competitor_model_override=support.competitor_model(delay_seconds=_FAN_OUT_DELAY_SECONDS),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )

    tree = export_trace(result.trace.trace_id)
    spans_by_name = {span["name"]: span for span in tree["spans"]}
    assert "customer_360" in spans_by_name
    assert "competitor" in spans_by_name

    customer_span = spans_by_name["customer_360"]
    competitor_span = spans_by_name["competitor"]

    def _window(span: dict[str, object]) -> tuple[datetime, datetime]:
        return (
            datetime.fromisoformat(str(span["started_at"])),
            datetime.fromisoformat(str(span["ended_at"])),
        )

    customer_start, customer_end = _window(customer_span)
    competitor_start, competitor_end = _window(competitor_span)

    overlap = customer_start < competitor_end and competitor_start < customer_end
    assert overlap, (
        f"expected overlapping spans, got customer=[{customer_start}, {customer_end}] "
        f"competitor=[{competitor_start}, {competitor_end}]"
    )
