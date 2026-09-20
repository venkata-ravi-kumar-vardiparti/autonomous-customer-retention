"""guardrails/injection.py: classifier correctness plus the ADVERSARIAL GATE.

The gate: every one of the 30 corpus cases must produce zero instruction
following - no raw injected text reaching the model input, and a block-tier
case must never even reach the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from churnguard.agents.conversation import build_input_text, run_conversation_agent
from churnguard.contracts.conversation import ConversationInput, TranscriptTurn
from churnguard.guardrails.injection import classify_text, hash_span, sanitize_transcript
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import new_span_id, new_trace_id

CORPUS_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "churnguard"
    / "guardrails"
    / "corpus"
    / "injections.jsonl"
)


@dataclass(frozen=True)
class InjectionCase:
    id: str
    category: str
    verdict: str
    text: str


def _load_corpus() -> list[InjectionCase]:
    cases = []
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            cases.append(InjectionCase(**raw))
    return cases


CORPUS = _load_corpus()

_VALID_SIGNALS_JSON = json.dumps(
    {
        "intents": [],
        "churn_signals": [],
        "competitor_claims": [],
        "customer_stated_figures": [],
        "unresolved_concerns": [],
        "sentiment_trajectory": "neutral",
        "verification_tasks": [],
    }
)


class _AssertNeverCalledModel(Model):
    """A model that fails the test loudly if the guardrail lets a call through."""

    called = False

    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        type(self).called = True
        raise AssertionError("model must never be called for a block-tier window")

    async def stream_response(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


class _StaticModel(Model):
    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        message = ResponseOutputMessage(
            id="m",
            role="assistant",
            status="completed",
            type="message",
            content=[
                ResponseOutputText(text=_VALID_SIGNALS_JSON, type="output_text", annotations=[])
            ],
        )
        return ModelResponse(
            output=[message],
            usage=Usage(requests=1, input_tokens=5, output_tokens=5, total_tokens=10),
            response_id="static",
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


def _single_turn_input(text: str) -> ConversationInput:
    window_start = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    turn = TranscriptTurn(ts=window_start, speaker="customer", text=text)
    return ConversationInput(
        call_id="CALL_****9999",
        transcript_window=[turn],
        window_start_ts=window_start,
        prior_signals=None,
        locale="en-US",
        guardrail_flags=[],
    )


def test_corpus_has_exactly_30_cases() -> None:
    assert len(CORPUS) == 30


@pytest.mark.parametrize("case", CORPUS, ids=[c.id for c in CORPUS])
def test_classifier_matches_the_expected_verdict_and_category(case: InjectionCase) -> None:
    verdict, category = classify_text(case.text)
    assert verdict == case.verdict
    assert category == case.category


@pytest.mark.parametrize("case", CORPUS, ids=[c.id for c in CORPUS])
def test_sanitization_never_leaves_the_raw_text_in_the_rendered_transcript(
    case: InjectionCase,
) -> None:
    conversation_input = _single_turn_input(case.text)
    sanitization = sanitize_transcript(conversation_input.transcript_window)
    input_text = build_input_text(conversation_input, sanitization.sanitized_turns)

    assert case.text not in input_text
    assert hash_span(case.text) in input_text


@pytest.mark.parametrize(
    "case", [c for c in CORPUS if c.verdict == "block"], ids=lambda c: c.id
)
async def test_block_tier_cases_never_reach_the_model(case: InjectionCase) -> None:
    conversation_input = _single_turn_input(case.text)
    run_context = _make_run_context(conversation_input)
    model = _AssertNeverCalledModel()

    result = await run_conversation_agent(conversation_input, run_context, model_override=model)

    assert model.called is False
    assert result.status == "insufficient_evidence"
    assert result.data is None
    assert case.text not in " ".join(result.warnings)


@pytest.mark.parametrize(
    "case", [c for c in CORPUS if c.verdict == "allow_with_quarantine"], ids=lambda c: c.id
)
async def test_quarantine_tier_cases_still_produce_a_result_with_the_verification_task(
    case: InjectionCase,
) -> None:
    conversation_input = _single_turn_input(case.text)
    run_context = _make_run_context(conversation_input)
    model = _StaticModel()

    result = await run_conversation_agent(conversation_input, run_context, model_override=model)

    assert result.status == "ok"
    assert result.data is not None
    assert f"verify_{case.category}" in result.data.verification_tasks
