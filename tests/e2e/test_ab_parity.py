"""ACCEPTANCE 1 (both arms run from the identical envelope with one field
changed) and ACCEPTANCE 4 (zero policy violations in both arms, or a
clearly reported finding if not) for Phase 11's A/B comparison.

NON-NEGOTIABLE: the ONLY difference between arms is
SupervisorInput.orchestration_mode. This module asserts that structurally
(model_dump comparison, not just "the code happened to pass the same
values") before ever running either pipeline - a test that only checked
outputs could pass even if someone quietly drifted the two envelopes apart.
"""

from __future__ import annotations

import json

from churnguard.contracts.recommendation import RecommendationSet, SupervisorInput
from churnguard.orchestration.bounded import run_bounded_pipeline
from churnguard.orchestration.harness import _build_harness_tools, _EvidenceBasket, run_open_harness
from tests.e2e import support
from tests.support.fake_model import StaticJSONModel, ToolCallingEchoModel


def _harness_tools_for_all_call_model(supervisor_input: SupervisorInput) -> ToolCallingEchoModel:
    """A harness reasoning model that calls every evidence tool once, then
    reports done - the open arm's "gather everything" behaviour, for a
    like-for-like comparison against the bounded arm's fixed fan-out."""
    dummy_basket = _EvidenceBasket()
    dummy_tools = _build_harness_tools(
        supervisor_input,
        geography="TX-DFW",
        trace_id="dummy",
        basket=dummy_basket,
        conversation_model_override=None,
        customer_model_override=None,
        competitor_model_override=None,
    )

    def assemble(outputs: dict[str, object]) -> str:
        return json.dumps({"evidence_sufficient": True, "summary": f"gathered {list(outputs)}"})

    return ToolCallingEchoModel(tools=dummy_tools, assemble_output=assemble)


def _base_envelope() -> SupervisorInput:
    """Reuses tests/e2e/support.py's own reference scenario builder - the
    same fixture both tests/e2e/test_bounded_pipeline.py and this A/B
    parity test are built on, per the phase brief's "reuse tests/e2e
    fixtures across both arms"."""
    return support.build_supervisor_input(support.reference_transcript())


def test_the_two_envelopes_differ_only_in_orchestration_mode() -> None:
    bounded_input = _base_envelope()
    harness_input = bounded_input.model_copy(update={"orchestration_mode": "open_harness"})

    assert bounded_input.orchestration_mode == "bounded_pipeline"
    assert harness_input.orchestration_mode == "open_harness"

    bounded_dump = bounded_input.model_dump(exclude={"orchestration_mode"})
    harness_dump = harness_input.model_dump(exclude={"orchestration_mode"})
    assert bounded_dump == harness_dump, (
        "the two arms' envelopes must be byte-for-byte identical except orchestration_mode"
    )


async def _run_both_arms(
    bounded_input: SupervisorInput, harness_input: SupervisorInput
) -> tuple[RecommendationSet, RecommendationSet]:
    bounded_result = await run_bounded_pipeline(
        bounded_input,
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )
    harness_result = await run_open_harness(
        harness_input,
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
        harness_model_override=_harness_tools_for_all_call_model(harness_input),
    )
    return bounded_result, harness_result


async def test_both_arms_produce_schema_valid_recommendation_sets_from_the_same_envelope() -> None:
    bounded_input = _base_envelope()
    harness_input = bounded_input.model_copy(update={"orchestration_mode": "open_harness"})

    bounded_result, harness_result = await _run_both_arms(bounded_input, harness_input)

    assert isinstance(bounded_result, RecommendationSet)
    assert isinstance(harness_result, RecommendationSet)
    assert bounded_result.commitment_status == "none"
    assert harness_result.commitment_status == "none"


async def test_zero_policy_violations_in_both_arms() -> None:
    """ACCEPTANCE 4: a "policy violation" here means a blocked candidate ID
    leaking into the live recommendations - the policy engine
    (orchestration/assemble.py, shared by both arms) is called exactly
    once per arm and is the only place a verdict is ever decided, so this
    should hold trivially in both; asserted directly rather than assumed."""
    bounded_input = _base_envelope()
    harness_input = bounded_input.model_copy(update={"orchestration_mode": "open_harness"})

    bounded_result, harness_result = await _run_both_arms(bounded_input, harness_input)

    arms = ((bounded_result, "bounded_pipeline"), (harness_result, "open_harness"))
    for result, arm_name in arms:
        blocked_ids = {b.candidate_id for b in result.blocked_candidates}
        offered_ids = {r.offer_id for r in result.recommendations}
        violations = blocked_ids & offered_ids
        assert not violations, f"{arm_name} offered a policy-blocked candidate: {violations}"


async def test_harness_gathering_nothing_still_uses_the_same_policy_engine() -> None:
    """The open harness's freedom is scoped to evidence-gathering only -
    even when it gathers nothing at all, whatever it does produce must
    still have gone through the identical policy hard filter (never a
    second, looser implementation)."""
    harness_input = _base_envelope().model_copy(update={"orchestration_mode": "open_harness"})

    never_call_a_tool_model = StaticJSONModel(
        '{"evidence_sufficient": true, "summary": "not used - no tools called"}'
    )
    result = await run_open_harness(
        harness_input,
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
        harness_model_override=never_call_a_tool_model,
    )

    assert isinstance(result, RecommendationSet)
    assert result.commitment_status == "none"
    blocked_ids = {b.candidate_id for b in result.blocked_candidates}
    offered_ids = {r.offer_id for r in result.recommendations}
    assert not (blocked_ids & offered_ids)
