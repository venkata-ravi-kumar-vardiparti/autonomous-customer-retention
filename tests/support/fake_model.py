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
    """Calls every tool once, then echoes their outputs into `assemble_output`."""

    def __init__(self, tools: list[Tool], assemble_output: Callable[[dict[str, Any]], str]) -> None:
        self._tool_names = [tool.name for tool in tools]
        self._assemble_output = assemble_output
        self._emitted_calls = False

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[Any],
        *args: object,
        **kwargs: object,
    ) -> ModelResponse:
        if not self._emitted_calls:
            self._emitted_calls = True
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

        outputs_by_call_id = _function_call_outputs_by_call_id(input)
        outputs_by_tool_name = {
            name: outputs_by_call_id.get(f"call_{index}")
            for index, name in enumerate(self._tool_names)
        }
        final_json = self._assemble_output(outputs_by_tool_name)
        return ModelResponse(
            output=[_final_message(final_json)], usage=_usage(), response_id="fake_final"
        )


__all__ = ["ScriptedModel", "ToolCallingEchoModel"]
