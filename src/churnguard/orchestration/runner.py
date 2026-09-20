"""Thin wrapper over agents.Runner.run: one call in, one AgentResult[T] out.

No retry, no deadline enforcement - those are agents/base.py::run_agent's
job, layered on top of run_once(). This module's only responsibility is:
run the agent exactly once, time the whole call with one AgentSpan (tool
calls open their own child spans - see tools/customer_tools.py), and
assemble AgentResult[T] from the validated final_output plus whatever the
run accumulated on RunContext.evidence.

If Runner.run raises (a schema violation, most notably), that exception
propagates out of run_once() unhandled - this module never returns a
partially-built AgentResult.
"""

from __future__ import annotations

from collections.abc import Callable

from agents import Agent, RunConfig, Runner

from churnguard.contracts.envelope import AgentResult, ResultStatus
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import AgentSpan

_STATUS_OK: ResultStatus = "ok"
_STATUS_PARTIAL: ResultStatus = "partial"

_CONFIDENCE_OK = 1.0
_CONFIDENCE_PARTIAL = 0.6


async def run_once[T](
    agent: Agent[RunContext],
    input_text: str,
    run_context: RunContext,
    *,
    model: str,
    run_config: RunConfig | None = None,
    detect_missing_evidence: Callable[[T], list[str]] | None = None,
) -> AgentResult[T]:
    resolved_run_config = run_config if run_config is not None else RunConfig(tracing_disabled=True)

    async with AgentSpan(
        agent.name,
        model=model,
        trace_id=run_context.trace_id,
        span_id=run_context.agent_span_id,
    ) as span:
        run_result = await Runner.run(
            agent, input_text, context=run_context, run_config=resolved_run_config
        )
        usage = run_result.context_wrapper.usage
        span.record_usage(tokens_in=usage.input_tokens, tokens_out=usage.output_tokens)

    assert span.telemetry is not None

    output: T = run_result.final_output
    missing_evidence = detect_missing_evidence(output) if detect_missing_evidence else []
    status = _STATUS_PARTIAL if missing_evidence else _STATUS_OK

    return AgentResult(
        status=status,
        confidence=_CONFIDENCE_OK if status == _STATUS_OK else _CONFIDENCE_PARTIAL,
        data=output,
        evidence=list(run_context.evidence),
        missing_evidence=missing_evidence,
        warnings=[],
        telemetry=span.telemetry,
    )


__all__ = ["run_once"]
