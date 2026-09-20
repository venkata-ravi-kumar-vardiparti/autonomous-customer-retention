"""End-to-end tests for orchestration/bounded.py - the Phase 7 milestone:
guardrail -> conversation -> asyncio.gather(customer, competitor) ->
generator -> policy.evaluate -> rank -> render -> assemble, exercised
against the real seeded Governed Data Layer with deterministic model
doubles standing in for every LLM call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from churnguard.contracts.conversation import ConversationSignals, TranscriptTurn
from churnguard.contracts.recommendation import RecommendationSet
from churnguard.offers import ranker
from churnguard.orchestration import aggregate
from churnguard.orchestration.bounded import run_bounded_pipeline
from tests.e2e import support
from tests.support.fake_model import StaticJSONModel

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "transcripts"


def _generic_conversation_model() -> StaticJSONModel:
    """Schema-valid, content-agnostic signals - used for the 12-fixture
    schema-validity sweep, where we care that the pipeline produces a valid
    RecommendationSet for every fixture, not what the extracted signals are."""
    return StaticJSONModel(
        ConversationSignals(
            intents=[],
            churn_signals=[],
            competitor_claims=[],
            customer_stated_figures=[],
            unresolved_concerns=[],
            sentiment_trajectory="neutral",
            verification_tasks=[],
        ).model_dump_json()
    )


async def _run_reference_pipeline(*, agent_authority_tier: int = 1) -> RecommendationSet:
    supervisor_input = support.build_supervisor_input(
        support.reference_transcript(), agent_authority_tier=agent_authority_tier
    )
    return await run_bounded_pipeline(
        supervisor_input,
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )


def _fixture_transcript(path: Path) -> list[TranscriptTurn]:
    raw = json.loads(path.read_text())
    return [TranscriptTurn(**turn) for turn in raw["transcript_window"]]


@pytest.mark.parametrize("fixture_path", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem)
async def test_all_twelve_fixture_transcripts_produce_schema_valid_recommendation_sets(
    fixture_path: Path,
) -> None:
    supervisor_input = support.build_supervisor_input(
        _fixture_transcript(fixture_path), account_ref=support.WORKED_EXAMPLE_REF
    )
    result = await run_bounded_pipeline(
        supervisor_input,
        conversation_model_override=_generic_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )
    assert isinstance(result, RecommendationSet)
    assert result.commitment_status == "none"


def test_exactly_twelve_fixtures_are_exercised() -> None:
    assert len(list(FIXTURES_DIR.glob("*.json"))) == 12


async def test_reference_scenario_ranks_by_likelihood_times_margin_not_discount_size() -> None:
    result = await _run_reference_pipeline()

    # C2's discount (-38.44) is bigger than C1's (-28.00), but C1 still ranks
    # first: retention likelihood (a reversible, already-flagged billing
    # cause) beats raw discount size, per offers/ranker.py.
    assert [r.offer_id for r in result.recommendations] == ["C1", "C2", "C3"]
    assert [r.rank for r in result.recommendations] == [1, 2, 3]

    by_id = {r.offer_id: r for r in result.recommendations}
    assert by_id["C1"].confidence == ranker.CONFIDENCE_BY_KIND[ranker.KIND_CREDIT_REINSTATEMENT]
    assert by_id["C2"].confidence == ranker.CONFIDENCE_BY_KIND[ranker.KIND_PLAN_MIGRATION]
    assert by_id["C3"].confidence == ranker.CONFIDENCE_BY_KIND[ranker.KIND_RETENTION_BUNDLE]

    assert by_id["C1"].approval_tier_required == 1
    assert by_id["C2"].approval_tier_required == 1
    assert by_id["C3"].approval_tier_required == 2
    assert by_id["C3"].hold_condition is not None
    assert by_id["C1"].hold_condition is None
    assert [d.code for d in by_id["C2"].required_disclosures] == ["DISC_HOTSPOT_REDUCTION"]


async def test_reference_scenario_reconciliation_logic() -> None:
    result = await _run_reference_pipeline()

    # A network complaint the customer explicitly separated out becomes a
    # mandatory action, never folded into a priced offer.
    assert result.mandatory_actions == ["open_network_ticket:LINE_****03:Frisco, TX"]

    # The $50 the customer believes vs. the real $33.23 delta is surfaced as
    # the top retention lever, not silently dropped.
    assert any("50.00" in note and "33.23" in note for note in result.agent_context_notes)

    # Missing usage telemetry on exactly the line the customer flagged is
    # corroborating evidence (small penalty), not a generic unexplained gap
    # (which would trigger a bounded re-request instead).
    assert "customer_360 re-requested" not in " ".join(result.fallbacks_applied)
    reasons = [a.reason for a in result.confidence_adjustments]
    assert any("corroborates" in reason for reason in reasons)
    assert any("stale" in reason for reason in reasons)


async def test_confidence_adjustments_sum_to_base_minus_overall() -> None:
    result = await _run_reference_pipeline()

    delta_sum = sum(a.delta for a in result.confidence_adjustments)
    assert round(aggregate.BASE_CONFIDENCE + delta_sum - result.overall_confidence, 6) == 0


async def test_top_ranked_offer_id_is_stable_across_five_runs() -> None:
    top_offer_ids = set()
    for _ in range(5):
        result = await _run_reference_pipeline()
        top_offer_ids.add(result.recommendations[0].offer_id)

    assert top_offer_ids == {"C1"}


async def test_recommendation_set_never_claims_a_commitment() -> None:
    result = await _run_reference_pipeline()
    assert result.commitment_status == "none"
    assert result.approval.min_tier_required >= 0
