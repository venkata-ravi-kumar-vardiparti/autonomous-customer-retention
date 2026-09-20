"""Acceptance: context tokens stay flat across a 15-turn call.

Runs the Conversation agent turn-by-turn over a simulated 15-turn call,
windowing via orchestration/windowing.py. Each call's input text is sized
by "new turns since last call" (bounded, here always 1), never by total
call length so far - contrasted explicitly against what re-sending the
whole growing history would cost.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from churnguard.agents.conversation import build_input_text, run_conversation_agent
from churnguard.contracts.conversation import TranscriptTurn
from churnguard.orchestration.context import RunContext
from churnguard.orchestration.windowing import WindowState, advance, next_window
from churnguard.telemetry.tracer import new_span_id, new_trace_id

_EMPTY_SIGNALS_JSON = json.dumps(
    {
        "intents": [],
        "churn_signals": [],
        "competitor_claims": [],
        "customer_stated_figures": [],
        "unresolved_concerns": [],
        "sentiment_trajectory": "steady",
        "verification_tasks": [],
    }
)


class _StaticModel(Model):
    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        message = ResponseOutputMessage(
            id="m",
            role="assistant",
            status="completed",
            type="message",
            content=[
                ResponseOutputText(text=_EMPTY_SIGNALS_JSON, type="output_text", annotations=[])
            ],
        )
        return ModelResponse(
            output=[message],
            usage=Usage(requests=1, input_tokens=10, output_tokens=10, total_tokens=20),
            response_id="static",
        )

    async def stream_response(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


async def test_windowed_input_size_stays_flat_across_a_15_turn_call() -> None:
    window_start = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    state = WindowState()
    transcript: list[TranscriptTurn] = []
    input_text_lengths: list[int] = []
    full_history_lengths: list[int] = []

    for turn_index in range(15):
        transcript.append(
            TranscriptTurn(
                ts=window_start + timedelta(seconds=turn_index * 12),
                speaker="customer" if turn_index % 2 == 0 else "agent",
                text=f"This is turn number {turn_index}, discussing the account in some detail.",
            )
        )

        conversation_input = next_window(
            transcript, state, call_id="CALL_****4200", locale="en-US"
        )
        run_context = RunContext(
            request=conversation_input,
            trace_id=new_trace_id(),
            agent_span_id=new_span_id(),
            policy_pack_version="2026.09.1",
            deadline_ms=2000,
            db_path="",
        )

        input_text = build_input_text(conversation_input, conversation_input.transcript_window)
        input_text_lengths.append(len(input_text))
        # What the input would have cost if we re-sent the whole history instead
        # of windowing - the comparison this test exists to make.
        full_history_lengths.append(sum(len(t.text) for t in transcript))

        result = await run_conversation_agent(
            conversation_input, run_context, model_override=_StaticModel()
        )
        assert result.status == "ok"
        assert result.data is not None

        state = advance(state, len(transcript), result.data)

    # Windowed input size stays within a narrow band (one turn's worth each
    # time) regardless of how long the call has run. The very first call is
    # excluded: it's the one-time, bounded jump from prior_signals=null to a
    # populated (but from then on constantly-sized) prior_signals payload -
    # not the start of an unbounded growth trend.
    steady_state = input_text_lengths[1:]
    assert max(steady_state) - min(steady_state) < 20

    # Whereas re-sending the whole history would have grown monotonically -
    # this is exactly the growth windowing avoids.
    assert full_history_lengths == sorted(full_history_lengths)
    assert full_history_lengths[-1] > full_history_lengths[0] * 10
