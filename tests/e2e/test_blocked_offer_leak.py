"""ACCEPTANCE 2, the LEAK TEST: a policy-blocked candidate's identifiers and
priced components must never appear anywhere in the agent-visible payload -
only in the audit-only blocked_candidates list, which the human agent's
screen never renders (candidate_id, reason, governing_rules only - never
its components/pricing, structurally, by BlockedCandidate's own contract
shape).

The scenario: MetroWave's cheapest normalized offer genuinely undercuts the
account's bill (offers/generator.py proposes a competitor price match), and
PRO-007 blocks it below regional_manager tier - a real, policy-cleared
blocked candidate, not a fabricated one.
"""

from __future__ import annotations

import json

from churnguard.orchestration.bounded import run_bounded_pipeline
from tests.e2e import support


async def _run_metrowave_pipeline():
    supervisor_input = support.build_supervisor_input(
        support.metrowave_transcript(), agent_authority_tier=1
    )
    return await run_bounded_pipeline(
        supervisor_input,
        conversation_model_override=support.metrowave_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )


async def test_scenario_actually_produces_a_blocked_competitor_price_match() -> None:
    """Sanity check the scenario is real before trusting the leak assertions
    below - a leak test over an empty blocked_candidates list would prove
    nothing."""
    result = await _run_metrowave_pipeline()
    assert len(result.blocked_candidates) == 1
    blocked = result.blocked_candidates[0]
    assert "PRO-007" in blocked.governing_rules
    assert blocked.candidate_id not in [r.offer_id for r in result.recommendations]


async def test_blocked_candidate_never_leaks_into_the_agent_visible_payload() -> None:
    result = await _run_metrowave_pipeline()
    blocked_ids = [b.candidate_id for b in result.blocked_candidates]
    assert blocked_ids, "scenario must produce at least one blocked candidate"

    agent_visible_payload = json.dumps(
        result.model_dump(mode="json", exclude={"blocked_candidates"})
    )

    for blocked_id in blocked_ids:
        assert blocked_id not in agent_visible_payload, (
            f"blocked candidate id {blocked_id!r} leaked into the agent-visible payload"
        )

    # The blocked candidate's own priced component never surfaces either -
    # not just its id.
    assert "PRC_COMPETITOR_PRICE_MATCH" not in agent_visible_payload


async def test_blocked_candidates_field_itself_is_minimal() -> None:
    """BlockedCandidate structurally carries no priced components - the
    contract shape itself is what makes the leak test above possible."""
    result = await _run_metrowave_pipeline()
    for blocked in result.blocked_candidates:
        dumped = blocked.model_dump()
        assert set(dumped) == {"candidate_id", "reason", "governing_rules"}
