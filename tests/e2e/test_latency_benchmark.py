"""ACCEPTANCE 1 (p95 < 1500ms after prefetch, gated in CI) and ACCEPTANCE 4
(cold start with a warm cache completes in < 3s) for Phase 10's latency
work: orchestration/prefetch.py + data/cache.py.

Both benchmarks reset orchestration/prefetch.py's and data/cache.py's
process-global state before and after themselves - see
tests/chaos/test_fault_injection.py's fixture docstring for why that
matters when state is memoized across the whole pytest session.
"""

from __future__ import annotations

import math
import time

import pytest

from churnguard.config import load_settings
from churnguard.data import cache as data_cache
from churnguard.orchestration import prefetch
from churnguard.orchestration.bounded import DEFAULT_GEOGRAPHY, run_bounded_pipeline
from tests.e2e import support

_RUNS = 10
_P95_BUDGET_MS = 1500
_COLD_START_BUDGET_MS = 3000


@pytest.fixture(autouse=True)
def _reset_global_caches():
    prefetch.reset_state()
    data_cache.reset_state()
    yield
    prefetch.reset_state()
    data_cache.reset_state()


def _run_pipeline_kwargs():
    return dict(
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )


async def test_p95_latency_under_1500ms_after_prefetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """ACCEPTANCE 1. DB_LATENCY_MS=40 for realism (Phase 4/7's own
    precedent); the boot cache is warmed and the competitor prefetch is
    kicked off before the clock starts on each run, matching how a real
    deployment would already have both warm well before "call connect"."""
    monkeypatch.setenv("DB_LATENCY_MS", "40")
    data_cache.load_cache(load_settings().database_path)

    latencies_ms: list[float] = []
    for _ in range(_RUNS):
        prefetch.reset_state()
        prefetch.start_prefetch(DEFAULT_GEOGRAPHY)

        supervisor_input = support.build_supervisor_input(support.reference_transcript())
        start = time.perf_counter()
        result = await run_bounded_pipeline(supervisor_input, **_run_pipeline_kwargs())
        latencies_ms.append((time.perf_counter() - start) * 1000)
        assert result.recommendations

    latencies_ms.sort()
    p95_index = min(len(latencies_ms) - 1, math.ceil(0.95 * len(latencies_ms)) - 1)
    p95_latency_ms = latencies_ms[p95_index]

    assert p95_latency_ms < _P95_BUDGET_MS, (
        f"p95 latency {p95_latency_ms:.1f}ms exceeded the {_P95_BUDGET_MS}ms budget"
    )


async def test_cold_start_with_warm_cache_completes_under_3s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACCEPTANCE 4: "cold start (cache warm)" - the cache load itself
    (data/cache.py's sqlite backup) plus the very first pipeline call
    right after, timed together, must complete in under 3 seconds."""
    monkeypatch.setenv("DB_LATENCY_MS", "40")

    start = time.perf_counter()
    data_cache.load_cache(load_settings().database_path)
    prefetch.start_prefetch(DEFAULT_GEOGRAPHY)

    supervisor_input = support.build_supervisor_input(support.reference_transcript())
    result = await run_bounded_pipeline(supervisor_input, **_run_pipeline_kwargs())
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.recommendations
    assert elapsed_ms < _COLD_START_BUDGET_MS, (
        f"cold start took {elapsed_ms:.1f}ms, exceeding the {_COLD_START_BUDGET_MS}ms budget"
    )
