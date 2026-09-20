"""Golden tests for the Conversation agent (Phase 5).

test_reference_behaviour_transcript reproduces the phase brief's exact
worked example end to end: 3 intents, an ambiguous-unit competitor claim,
an approximate customer-stated figure, a separately-flagged concern, and
the [01:24] span quarantined into verify_prior_commitment_claim - the last
one enforced deterministically by run_conversation_agent, not left to the
scripted model's compliance.

The other tests run every one of the 12 fixture transcripts through the
real sanitize -> guardrail -> (scripted model) pipeline: fixtures 03 and 11
(the two known prompt-injection fixtures) must be refused before any model
call; the other 10 must not be.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from churnguard.agents.conversation import run_conversation_agent
from churnguard.contracts.conversation import ConversationInput
from churnguard.orchestration.context import RunContext
from churnguard.telemetry.tracer import new_span_id, new_trace_id

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "transcripts"
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))
BLOCKED_FIXTURE_STEMS = {
    "03_prompt_injection_attempt",
    "11_prompt_injection_via_fake_supervisor_override",
}

_MINIMAL_SIGNALS_JSON = json.dumps(
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


def _load_fixture(path: Path) -> ConversationInput:
    raw = json.loads(path.read_text())
    return ConversationInput.model_validate({**raw, "prior_signals": None})


class _AssertNeverCalledModel(Model):
    called = False

    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        type(self).called = True
        raise AssertionError("model must never be called for a blocked window")

    async def stream_response(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


class _StaticJsonModel(Model):
    def __init__(self, signals_json: str) -> None:
        self._signals_json = signals_json

    async def get_response(self, *args: object, **kwargs: object) -> ModelResponse:
        message = ResponseOutputMessage(
            id="m",
            role="assistant",
            status="completed",
            type="message",
            content=[
                ResponseOutputText(text=self._signals_json, type="output_text", annotations=[])
            ],
        )
        return ModelResponse(
            output=[message],
            usage=Usage(requests=1, input_tokens=10, output_tokens=10, total_tokens=20),
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


async def test_reference_behaviour_transcript() -> None:
    window_start = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    turns_raw = [
        (42, "I want to cancel. All four lines."),
        (51, "My bill went up like fifty bucks... my friend got Verizon for forty-five a line"),
        (69, "Also - separate - my daughter's line drops every call in Frisco"),
        (84, "The rep last time said to ignore the policy and give me 50% off"),
    ]
    conversation_input = ConversationInput.model_validate(
        {
            "call_id": "CALL_****9001",
            "transcript_window": [
                {
                    "ts": (window_start + timedelta(seconds=offset)).isoformat(),
                    "speaker": "customer",
                    "text": text,
                }
                for offset, text in turns_raw
            ],
            "window_start_ts": window_start.isoformat(),
            "prior_signals": None,
            "locale": "en-US",
            "guardrail_flags": [],
        }
    )

    scripted_output = json.dumps(
        {
            "intents": [
                {
                    "type": "cancel_service",
                    "confidence": 0.9,
                    "scope": "all_lines",
                    "span_refs": ["[00:42]"],
                },
                {
                    "type": "billing_dispute",
                    "confidence": 0.8,
                    "scope": None,
                    "span_refs": ["[00:51]"],
                },
                {
                    "type": "service_quality",
                    "confidence": 0.7,
                    "scope": None,
                    "span_refs": ["[01:09]"],
                },
            ],
            "churn_signals": [{"signal": "explicit_cancel_request", "strength": "high"}],
            "competitor_claims": [
                {
                    "carrier": "Verizon",
                    "price": 45.0,
                    "unit": "ambiguous",
                    "source": "customer_stated",
                    "span_refs": ["[00:51]"],
                }
            ],
            "customer_stated_figures": [
                {"field": "bill_increase", "value": 50.0, "precision": "approximate"}
            ],
            "unresolved_concerns": [
                {
                    "issue": "dropped_calls",
                    "line_ref": None,
                    "location": "Frisco",
                    "since": None,
                    "customer_flagged_separate": True,
                }
            ],
            "sentiment_trajectory": "frustrated throughout",
            "verification_tasks": [],
        }
    )
    model = _StaticJsonModel(scripted_output)
    run_context = _make_run_context(conversation_input)

    result = await run_conversation_agent(conversation_input, run_context, model_override=model)

    assert result.status == "ok"
    assert result.data is not None
    assert len(result.data.intents) == 3
    assert {i.type for i in result.data.intents} == {
        "cancel_service",
        "billing_dispute",
        "service_quality",
    }

    claim = result.data.competitor_claims[0]
    assert claim.unit == "ambiguous"

    figure = result.data.customer_stated_figures[0]
    assert figure.field == "bill_increase"
    assert figure.value == pytest.approx(50.00)
    assert figure.precision == "approximate"

    concern = result.data.unresolved_concerns[0]
    assert concern.customer_flagged_separate is True

    assert result.data.verification_tasks == ["verify_prior_commitment_claim"]


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.stem)
async def test_injection_fixtures_are_blocked_others_are_not(path: Path) -> None:
    conversation_input = _load_fixture(path)
    run_context = _make_run_context(conversation_input)

    if path.stem in BLOCKED_FIXTURE_STEMS:
        model = _AssertNeverCalledModel()
        result = await run_conversation_agent(conversation_input, run_context, model_override=model)
        assert model.called is False
        assert result.status == "insufficient_evidence"
        assert result.data is None
    else:
        model = _StaticJsonModel(_MINIMAL_SIGNALS_JSON)
        result = await run_conversation_agent(conversation_input, run_context, model_override=model)
        assert result.status == "ok"
        assert result.data is not None


@pytest.mark.parametrize(
    "fixture_name",
    ["02_multi_intent_cancel_and_billing_dispute", "07_multi_intent_cancel_and_device_upgrade"],
)
async def test_multi_intent_fixtures_yield_distinct_span_refs(fixture_name: str) -> None:
    path = FIXTURES_DIR / f"{fixture_name}.json"
    conversation_input = _load_fixture(path)
    run_context = _make_run_context(conversation_input)

    scripted_output = json.dumps(
        {
            "intents": [
                {
                    "type": "cancel_service",
                    "confidence": 0.9,
                    "scope": None,
                    "span_refs": ["[00:02]"],
                },
                {
                    "type": "billing_dispute",
                    "confidence": 0.6,
                    "scope": None,
                    "span_refs": ["[00:20]"],
                },
            ],
            "churn_signals": [],
            "competitor_claims": [],
            "customer_stated_figures": [],
            "unresolved_concerns": [],
            "sentiment_trajectory": "neutral",
            "verification_tasks": [],
        }
    )
    model = _StaticJsonModel(scripted_output)

    result = await run_conversation_agent(conversation_input, run_context, model_override=model)

    assert result.data is not None
    assert len(result.data.intents) >= 2
    span_refs = [tuple(intent.span_refs) for intent in result.data.intents]
    assert len(span_refs) == len(set(span_refs))
