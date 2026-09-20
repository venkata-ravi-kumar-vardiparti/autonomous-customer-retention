"""agents/base.py::run_agent - retry-once on schema violation, deadline enforcement.

Uses a tool-free spec and a ScriptedModel test double so this exercises
exactly the retry/deadline plumbing in agents/base.py, independent of the
Customer 360 agent's own tools/domains (covered by tests/golden and
test_tool_authorization.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agents import ModelBehaviorError
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from pydantic import BaseModel, ConfigDict

from churnguard.agents.base import (
    AgentSpec,
    DeadlineExceededError,
    SchemaViolationError,
    run_agent,
)
from churnguard.contracts.customer import CustomerContextRequest
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import new_span_id, new_trace_id
from tests.support.fake_model import ScriptedModel


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


def _spec() -> AgentSpec[_Out]:
    return AgentSpec(
        name="retry_test_agent",
        output_type=_Out,
        instructions="test",
        tools=[],
        model="gpt-4.1-mini",
    )


def _run_context(*, deadline_ms: int = 2000) -> RunContext:
    request = CustomerContextRequest(
        account_ref="ACCT_****0000",
        requested_domains=[],
        lookback_months=0,
        line_refs_of_interest=[],
        verification_tasks=[],
        reason_code="cancel_request",
    )
    return RunContext(
        request=request,
        trace_id=new_trace_id(),
        agent_span_id=new_span_id(),
        policy_pack_version="2026.09.1",
        deadline_ms=deadline_ms,
        db_path="unused-in-this-test",
    )


def _message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg",
        role="assistant",
        status="completed",
        type="message",
        content=[ResponseOutputText(text=text, type="output_text", annotations=[])],
    )


def _bad_message() -> ResponseOutputMessage:
    return _message("not valid json{{{")


def _good_message(value: int) -> ResponseOutputMessage:
    return _message(f'{{"value": {value}}}')


async def test_malformed_json_is_retried_once_then_raises_a_typed_error() -> None:
    model = ScriptedModel(turns=[[_bad_message()], [_bad_message()]])

    with pytest.raises(SchemaViolationError) as exc_info:
        await run_agent(_spec(), "go", _run_context(), model_override=model)

    assert isinstance(exc_info.value.__cause__, ModelBehaviorError)
    assert model.call_count == 2  # exactly one retry: two attempts total, then give up


async def test_malformed_json_then_a_good_retry_succeeds() -> None:
    model = ScriptedModel(turns=[[_bad_message()], [_good_message(7)]])

    result = await run_agent(_spec(), "go", _run_context(), model_override=model)

    assert result.status == "ok"
    assert result.data is not None
    assert result.data.value == 7
    assert model.call_count == 2


async def test_no_retry_needed_on_first_success() -> None:
    model = ScriptedModel(turns=[[_good_message(1)]])

    result = await run_agent(_spec(), "go", _run_context(), model_override=model)

    assert result.data is not None
    assert result.data.value == 1
    assert model.call_count == 1


async def test_deadline_exceeded_raises_a_typed_error_not_asyncio_timeout() -> None:
    class _SlowModel(ScriptedModel):
        async def get_response(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0.2)
            return await super().get_response(*args, **kwargs)

    model = _SlowModel(turns=[[_good_message(1)]])

    with pytest.raises(DeadlineExceededError):
        await run_agent(_spec(), "go", _run_context(deadline_ms=50), model_override=model)
