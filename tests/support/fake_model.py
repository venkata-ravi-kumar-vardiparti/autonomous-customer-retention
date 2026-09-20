"""Deterministic agents.Model test doubles - no network, no real LLM.

ScriptedModel: replays a fixed, ordered list of turns regardless of the
conversation so far. Use it when the test wants to dictate exactly what
"the model" says, e.g. a deliberately malformed final turn to exercise the
retry-once path.

ToolCallingEchoModel: a "well-behaved" fake - turn 1 calls every tool the
agent was given; once every tool call has a function_call_output in the
conversation, it hands the caller's `assemble_output` callback a
{tool_name: parsed_json_output} mapping and returns the resulting JSON
string as the final message. This is what the golden tests use to exercise
the real tool -> repository -> data wiring for many different seeded
accounts without hardcoding per-account expected JSON or depending on a
live model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from agents import Model, ModelResponse, Tool, Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

FAKE_TOKENS_IN = 10
FAKE_TOKENS_OUT = 10


def _usage() -> Usage:
    return Usage(
        requests=1,
        input_tokens=FAKE_TOKENS_IN,
        output_tokens=FAKE_TOKENS_OUT,
        total_tokens=FAKE_TOKENS_IN + FAKE_TOKENS_OUT,
    )


def _final_message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg_final",
        role="assistant",
        status="completed",
        type="message",
        content=[ResponseOutputText(text=text, type="output_text", annotations=[])],
    )


def _function_call_outputs_by_call_id(input_items: object) -> dict[str, Any]:
    """call_id -> parsed JSON output, for every function_call_output item so far."""
    if not isinstance(input_items, list):
        return {}
    outputs: dict[str, Any] = {}
    for item in input_items:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            raw_output = item.get("output")
            if isinstance(raw_output, str):
                try:
                    outputs[item["call_id"]] = json.loads(raw_output)
                except json.JSONDecodeError:
                    outputs[item["call_id"]] = raw_output
            else:
                outputs[item["call_id"]] = raw_output
    return outputs


class _UnusedStreamMixin:
    async def stream_response(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError("ChurnGuard's tests never stream")


class ScriptedModel(_UnusedStreamMixin, Model):
    """Replays `turns` (a list of output-item lists) in order, one per call."""

    def __init__(self, turns: list[list[Any]]) -> None:
        self._turns = turns
        self.call_count = 0

    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        turn_index = min(self.call_count, len(self._turns) - 1)
        self.call_count += 1
        return ModelResponse(
            output=self._turns[turn_index], usage=_usage(), response_id=f"fake_{turn_index}"
        )


class ToolCallingEchoModel(_UnusedStreamMixin, Model):
    """Calls every tool once, then echoes their outputs into `assemble_output`.

    delay_seconds (Phase 7): an optional artificial `asyncio.sleep` on every
    call, so tests/e2e's FAN-OUT TEST can prove two agent calls dispatched
    via asyncio.gather genuinely overlap in wall-clock time - an instant
    fake response can complete so fast that even truly concurrent coroutines
    never visibly overlap. Defaults to 0.0 (no behavior change for existing
    callers).
    """

    def __init__(
        self,
        tools: list[Tool],
        assemble_output: Callable[[dict[str, Any]], str],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._tool_names = [tool.name for tool in tools]
        self._assemble_output = assemble_output
        self._delay_seconds = delay_seconds

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[Any],
        *args: object,
        **kwargs: object,
    ) -> ModelResponse:
        if self._delay_seconds > 0.0:
            await asyncio.sleep(self._delay_seconds)

        # Stateless on `input` (not an instance flag) so one instance can be
        # reused safely across independent Runner.run() calls - e.g. a
        # bounded-pipeline re-request retry (orchestration/bounded.py) that
        # passes the same model_override in twice, exactly like a real,
        # stateless LLM would tolerate.
        outputs_by_call_id = _function_call_outputs_by_call_id(input)
        if not outputs_by_call_id:
            calls = [
                ResponseFunctionToolCall(
                    arguments="{}",
                    call_id=f"call_{index}",
                    name=name,
                    type="function_call",
                    id=f"fc_{index}",
                )
                for index, name in enumerate(self._tool_names)
            ]
            return ModelResponse(output=calls, usage=_usage(), response_id="fake_tool_calls")

        outputs_by_tool_name = {
            name: outputs_by_call_id.get(f"call_{index}")
            for index, name in enumerate(self._tool_names)
        }
        final_json = self._assemble_output(outputs_by_tool_name)
        return ModelResponse(
            output=[_final_message(final_json)], usage=_usage(), response_id="fake_final"
        )


class StaticJSONModel(_UnusedStreamMixin, Model):
    """Single-turn fake: always returns the same final JSON message, no tool
    calls. Used for tool-less agents (e.g. agents/supervisor.py's render
    step) where ToolCallingEchoModel's tool-calling turn isn't needed.
    """

    def __init__(self, json_text: str, *, delay_seconds: float = 0.0) -> None:
        self._json_text = json_text
        self._delay_seconds = delay_seconds

    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        if self._delay_seconds > 0.0:
            await asyncio.sleep(self._delay_seconds)
        return ModelResponse(
            output=[_final_message(self._json_text)], usage=_usage(), response_id="fake_static"
        )


__all__ = ["ScriptedModel", "StaticJSONModel", "ToolCallingEchoModel"]
