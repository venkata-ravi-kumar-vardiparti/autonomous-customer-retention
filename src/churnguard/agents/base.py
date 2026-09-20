"""Shared agent factory + invocation wrapper - the template every later
ChurnGuard agent (Conversation, Competitor, Offer Policy renderer,
Supervisor) copies. Phase 4 proved the pattern on Customer 360; Phase 5
(Conversation) extends it with input_guardrails rather than inventing a
second way to build/run an agent.

Layered on orchestration/runner.py's run_once(), which does exactly one
Runner.run() call and assembles an AgentResult[T] from it. This module adds
what a *single* run_once() call can't do on its own:

- retries a schema-invalid model output exactly once (Runner.run raises
  agents.ModelBehaviorError for malformed/invalid-schema JSON; there is no
  built-in retry for this in the SDK - see CLAUDE.md's Phase 4 notes for why
  that's not a bug to work around, just a gap this module fills), then
  converts a still-failing second attempt into SchemaViolationError - a
  typed failure the caller can catch, never a bare SDK exception and never
  a partially-assembled AgentResult.
- enforces RunContext.deadline_ms as a hard wall-clock budget across both
  attempts combined, via asyncio.wait_for, converting a timeout into
  DeadlineExceededError.
- converts a tripped agents.InputGuardrailTripwireTriggered (e.g.
  guardrails/injection.py's block-tier verdict) into InputBlockedError -
  never retried, since the same input would trip the same guardrail again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agents import (
    Agent,
    AgentOutputSchema,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    Model,
    ModelBehaviorError,
    RunConfig,
    Tool,
)

from churnguard.contracts.envelope import AgentResult
from churnguard.orchestration.context import RunContext
from churnguard.orchestration.runner import run_once


class SchemaViolationError(Exception):
    """The model produced schema-invalid output on every attempt, including the retry."""


class DeadlineExceededError(Exception):
    """The agent run did not complete within RunContext.deadline_ms."""


class InputBlockedError(Exception):
    """An input_guardrail tripped its tripwire before the model was ever called.

    guardrail_output_info carries whatever the guardrail's
    GuardrailFunctionOutput.output_info was, so a caller can build a
    specific degraded AgentResult (agents/base.py can't do this generically
    - it doesn't know what an empty/blocked T looks like for every agent).
    """

    def __init__(self, message: str, *, guardrail_output_info: Any = None) -> None:
        super().__init__(message)
        self.guardrail_output_info = guardrail_output_info


@dataclass(frozen=True)
class AgentSpec[T]:
    """Everything needed to build and run one ChurnGuard agent.

    detect_missing_evidence inspects the validated output and returns a
    list of missing_evidence strings; a non-empty list downgrades
    AgentResult.status to "partial". None means "this agent never reports
    partial results" (not every agent's output type has a concept of a gap).
    """

    name: str
    output_type: type[T]
    instructions: str
    tools: list[Tool]
    model: str
    detect_missing_evidence: Callable[[T], list[str]] | None = None
    input_guardrails: list[InputGuardrail[Any]] = field(default_factory=list)


def build_agent(
    spec: AgentSpec[Any], *, model_override: str | Model | None = None
) -> Agent[RunContext]:
    """model_override swaps in a test double (or a different real model) for
    the *invocation* while spec.model (a plain string) stays the identity
    telemetry/cost accounting bills against - see run_agent.

    output_type is wrapped with strict_json_schema=False: several contract
    payloads (AccountContext.usage_by_line, keyed by a line ref that varies
    per account) are open-ended dicts, which OpenAI's *strict* structured-
    output mode cannot represent (it requires every object's keys to be
    enumerable up front). Non-strict mode still validates the model's JSON
    against the full pydantic schema - see contracts/ 's own extra="forbid" -
    it just doesn't get the stronger, provider-enforced schema constraints
    strict mode adds on top of that.
    """
    return Agent[RunContext](
        name=spec.name,
        instructions=spec.instructions,
        tools=spec.tools,
        output_type=AgentOutputSchema(spec.output_type, strict_json_schema=False),
        model=model_override if model_override is not None else spec.model,
        input_guardrails=spec.input_guardrails,
    )


async def run_agent[T](
    spec: AgentSpec[T],
    input_text: str,
    run_context: RunContext,
    *,
    max_retries: int = 1,
    model_override: str | Model | None = None,
) -> AgentResult[T]:
    agent = build_agent(spec, model_override=model_override)
    run_config = RunConfig(tracing_disabled=True)

    async def _attempt() -> AgentResult[T]:
        attempts = 0
        while True:
            attempts += 1
            try:
                return await run_once(
                    agent,
                    input_text,
                    run_context,
                    model=spec.model,
                    run_config=run_config,
                    detect_missing_evidence=spec.detect_missing_evidence,
                )
            except InputGuardrailTripwireTriggered as exc:
                # Never retried: the same input would trip the same guardrail again.
                raise InputBlockedError(
                    f"{spec.name}'s input was blocked by "
                    f"{exc.guardrail_result.guardrail.get_name()!r}",
                    guardrail_output_info=exc.guardrail_result.output.output_info,
                ) from exc
            except ModelBehaviorError as exc:
                if attempts > max_retries:
                    raise SchemaViolationError(
                        f"{spec.name} produced schema-invalid output after "
                        f"{attempts} attempt(s)"
                    ) from exc
                # A fresh attempt re-runs every tool from scratch; without this,
                # evidence from the failed attempt's tool calls would double up
                # alongside the retry's.
                run_context.evidence.clear()

    try:
        return await asyncio.wait_for(_attempt(), timeout=run_context.deadline_ms / 1000)
    except TimeoutError as exc:
        raise DeadlineExceededError(
            f"{spec.name} exceeded its {run_context.deadline_ms}ms deadline"
        ) from exc


__all__ = [
    "AgentSpec",
    "DeadlineExceededError",
    "InputBlockedError",
    "SchemaViolationError",
    "build_agent",
    "run_agent",
]
