"""Acceptance: run each fixture 10x; the intent SET is stable, confidence
varies within a range - never assert an exact confidence value.

There is no live model in this environment (no network calls anywhere in
this test suite - see CLAUDE.md), so true model-sampling variance can't be
produced here. This test instead exercises the *assertion methodology* the
brief asks for - set equality, confidence range checks, no float equality -
against a scripted model whose confidence values are randomized per call
(bounded, real-ish jitter) while its intent set is held fixed, which is the
one thing an actual non-deterministic model run could safely be asserted
on too. A future phase with real OPENAI_API_KEY-gated runs should replace
the scripted model here with a live one and keep these same assertions.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime

from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from churnguard.agents.conversation import run_conversation_agent
from churnguard.contracts.conversation import ConversationInput, TranscriptTurn
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import new_span_id, new_trace_id

_RUNS_PER_FIXTURE = 10
_EXPECTED_INTENT_TYPES = {"cancel_service", "billing_dispute"}


class _JitteredConfidenceModel(Model):
    """Same intent set every call; confidence values jitter within a fixed range."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        payload = {
            "intents": [
                {
                    "type": intent_type,
                    "confidence": round(self._rng.uniform(0.55, 0.95), 2),
                    "scope": None,
                    "span_refs": [f"[00:{i:02d}]"],
                }
                for i, intent_type in enumerate(sorted(_EXPECTED_INTENT_TYPES))
            ],
            "churn_signals": [],
            "competitor_claims": [],
            "customer_stated_figures": [],
            "unresolved_concerns": [],
            "sentiment_trajectory": "neutral",
            "verification_tasks": [],
        }
        message = ResponseOutputMessage(
            id="m",
            role="assistant",
            status="completed",
            type="message",
            content=[
                ResponseOutputText(text=json.dumps(payload), type="output_text", annotations=[])
            ],
        )
        return ModelResponse(
            output=[message],
            usage=Usage(requests=1, input_tokens=10, output_tokens=10, total_tokens=20),
            response_id="jittered",
        )

    async def stream_response(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


def _make_run_context(conversation_input: ConversationInput) -> RunContext:
    return RunContext(
        request=conversation_input,
        trace_id=new_trace_id(),
        agent_span_id=new_span_id(),
        policy_pack_version="2026.09.1",
        deadline_ms=2000,
        db_path="",
    )


async def test_intent_set_is_stable_and_confidence_stays_in_range_across_ten_runs() -> None:
    window_start = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    conversation_input = ConversationInput(
        call_id="CALL_****5000",
        transcript_window=[
            TranscriptTurn(ts=window_start, speaker="customer", text="I want to cancel."),
        ],
        window_start_ts=window_start,
        prior_signals=None,
        locale="en-US",
        guardrail_flags=[],
    )

    rng = random.Random(20260920)
    observed_intent_sets: list[frozenset[str]] = []
    observed_confidences: list[float] = []

    for _ in range(_RUNS_PER_FIXTURE):
        model = _JitteredConfidenceModel(rng)
        result = await run_conversation_agent(
            conversation_input, _make_run_context(conversation_input), model_override=model
        )
        assert result.data is not None
        observed_intent_sets.append(frozenset(i.type for i in result.data.intents))
        observed_confidences.extend(i.confidence for i in result.data.intents)

    # The SET is stable across all 10 runs - assert on sets, never on a
    # specific ordering or an exact confidence value.
    assert len(set(observed_intent_sets)) == 1
    assert observed_intent_sets[0] == _EXPECTED_INTENT_TYPES

    # Confidence varies (it's not the exact same float every time) but stays
    # within the plausible range - a range assertion, never an exact value.
    assert len(set(observed_confidences)) > 1
    assert all(0.5 <= c <= 1.0 for c in observed_confidences)
