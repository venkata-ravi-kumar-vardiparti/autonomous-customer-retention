"""ACCEPTANCE 2/3: the six fault scenarios from CLAUDE.md's Phase 10 brief,
each producing a defined, schema-valid RecommendationSet - zero unhandled
exceptions - plus the specific "kill a repository connection mid-run"
test ACCEPTANCE 3 calls out by name.

Every test resets orchestration/prefetch.py's and data/cache.py's
process-global state before and after itself: both are memoized across
the whole pytest session, and a stale prefetch hit or a lingering loaded
cache from one test could silently bypass another test's monkeypatched
repository failure - see the _reset_global_caches fixture.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import aiosqlite
import pytest

from churnguard.contracts.conversation import TranscriptTurn
from churnguard.data import cache as data_cache
from churnguard.data.repositories import competitor_repo
from churnguard.data.seed.generate import SEED, build_all_accounts
from churnguard.orchestration import prefetch
from churnguard.orchestration.bounded import run_bounded_pipeline
from churnguard.telemetry.audit import read_audit_records
from tests.e2e import support
from tests.support.fake_model import StaticJSONModel

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "transcripts"

_SCENARIOS = build_all_accounts(random.Random(SEED))
CONTRADICTORY_BILLING_REF = support._GOLDEN_REFS[4]  # data/seed/generate.py's named scenario


@pytest.fixture(autouse=True)
def _reset_global_caches():
    prefetch.reset_state()
    data_cache.reset_state()
    yield
    prefetch.reset_state()
    data_cache.reset_state()


def _empty_conversation_model() -> StaticJSONModel:
    return StaticJSONModel(
        json.dumps(
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
    )


def _run_pipeline_kwargs(**overrides):
    kwargs = dict(
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )
    kwargs.update(overrides)
    return kwargs


# --- 1. Stale competitor snapshot -------------------------------------------


async def test_stale_competitor_snapshot_produces_penalty_and_indicative_note() -> None:
    """The reference scenario's carriers=["RivalCo"] deliberately resolves
    to the seeded, deliberately-stale RivalCo snapshot (tests/e2e/support.py's
    module docstring explains the tie-break mechanism)."""
    supervisor_input = support.build_supervisor_input(support.reference_transcript())
    result = await run_bounded_pipeline(supervisor_input, **_run_pipeline_kwargs())

    stale_adjustments = [
        a for a in result.confidence_adjustments if "stale" in a.reason.lower()
    ]
    assert stale_adjustments, "expected a stale-competitor confidence adjustment"
    assert stale_adjustments[0].delta == pytest.approx(-0.08)

    indicative_notes = [n for n in result.agent_context_notes if "indicative" in n.lower()]
    assert indicative_notes, "expected an indicative-banner note for stale competitor pricing"
    assert "days old" in indicative_notes[0] or "stale" in indicative_notes[0].lower()


# --- 2. Missing usage data ---------------------------------------------------


async def test_missing_usage_data_is_flagged_partial_with_a_penalty() -> None:
    """LINE_****03's null usage (ACCT_****4471's seeded gap) corroborates
    the Frisco/TX network concern in the reference transcript - a small,
    named penalty, not a generic unexplained one."""
    supervisor_input = support.build_supervisor_input(support.reference_transcript())
    result = await run_bounded_pipeline(supervisor_input, **_run_pipeline_kwargs())

    corroboration_adjustments = [
        a for a in result.confidence_adjustments if "corroborates" in a.reason.lower()
    ]
    assert corroboration_adjustments, "expected a missing-usage corroboration adjustment"
    assert corroboration_adjustments[0].delta == pytest.approx(-0.03)
    assert result.overall_confidence < 1.0


# --- 3. Contradictory billing rows -------------------------------------------


async def test_contradictory_billing_rows_surface_a_warning_and_lower_confidence() -> None:
    """data/seed/generate.py's build_contradictory_billing scenario: causes
    sum to $35.00 but the bill only changed by $22.10 - the Supervisor must
    surface this as a warning and lower confidence, never silently pick
    either figure as "the" answer."""
    supervisor_input = support.build_supervisor_input(
        [], account_ref=CONTRADICTORY_BILLING_REF
    )
    result = await run_bounded_pipeline(
        supervisor_input,
        conversation_model_override=_empty_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )

    contradiction_adjustments = [
        a for a in result.confidence_adjustments if "contradict" in a.reason.lower()
    ]
    assert contradiction_adjustments, "expected a billing-contradiction confidence adjustment"
    assert contradiction_adjustments[0].delta == pytest.approx(-0.05)

    warnings = [n for n in result.agent_context_notes if n.startswith("WARNING")]
    assert warnings, "expected a WARNING note surfacing the billing contradiction"
    warning = warnings[0]
    # Both figures are named - neither is silently chosen as "the" explanation.
    assert "35.00" in warning
    assert "22.10" in warning


# --- 4. Repository unavailable (ACCEPTANCE 3: kill a connection mid-run) ---


async def test_repository_unavailable_yields_a_degraded_but_schema_valid_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kills the competitor repository mid-run (simulating a dropped
    connection) and asserts the pipeline still returns a graceful,
    schema-valid, clearly-flagged partial RecommendationSet built from the
    agents that DID return - never an unhandled exception."""

    async def _dead_connection(*args: object, **kwargs: object) -> None:
        raise aiosqlite.OperationalError("simulated: database connection is closed")

    monkeypatch.setattr(competitor_repo, "get_snapshots", _dead_connection)

    supervisor_input = support.build_supervisor_input(support.reference_transcript())
    result = await run_bounded_pipeline(supervisor_input, **_run_pipeline_kwargs())

    # The customer_360 agent (unaffected) still returned - real recommendations exist.
    assert result.recommendations, "expected recommendations from the agents that did return"

    assert any(
        "competitor" in fallback.lower() and "unavailable" in fallback.lower()
        for fallback in result.fallbacks_applied
    )
    assert any(
        "competitor" in a.reason.lower() and "did not complete" in a.reason.lower()
        for a in result.confidence_adjustments
    )
    assert result.commitment_status == "none"


# --- 5. Deadline breach -------------------------------------------------------


async def test_deadline_breach_yields_a_partial_result_never_a_hang() -> None:
    """The competitor model is deliberately slower than the pipeline's own
    deadline - orchestration/deadlines.py must cancel it and continue with
    whatever else completed, never hang and never raise."""
    supervisor_input = support.build_supervisor_input(
        support.reference_transcript(), deadline_ms=100
    )
    result = await run_bounded_pipeline(
        supervisor_input,
        conversation_model_override=support.reference_conversation_model(),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(delay_seconds=1.0),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )

    assert result.recommendations, "expected a non-empty, degraded-but-real result"
    assert any("deadline exceeded" in fallback for fallback in result.fallbacks_applied)
    assert any(
        "deadline exceeded" in a.reason and "competitor" in a.reason
        for a in result.confidence_adjustments
    )


# --- 6. Guardrail tripwire ----------------------------------------------------


async def test_guardrail_tripwire_lets_the_call_proceed_and_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real prompt-injection transcript (fixture 03) trips the guardrail
    before the model is ever called (Phase 5) - the call must still
    proceed to a schema-valid result, and the trip must be audit-logged."""
    monkeypatch.setenv("CHURNGUARD_AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("CHURNGUARD_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    raw = json.loads((FIXTURES_DIR / "03_prompt_injection_attempt.json").read_text())
    transcript = [TranscriptTurn(**turn) for turn in raw["transcript_window"]]

    supervisor_input = support.build_supervisor_input(transcript)
    result = await run_bounded_pipeline(
        supervisor_input,
        # The guardrail must trip before this model is ever invoked - if it
        # weren't tripping, ToolCallingEchoModel-shaped JSON would be needed
        # instead, so a mismatched fake here is itself a safety net.
        conversation_model_override=StaticJSONModel("not valid conversation signals json"),
        customer_model_override=support.customer_model(),
        competitor_model_override=support.competitor_model(),
        supervisor_model_override=support.render_model_with_canned_copy(),
    )

    assert result.commitment_status == "none"

    audit_rows = await read_audit_records(result.trace.trace_id)
    tripped_rows = [r for r in audit_rows if r.decision == "conversation_guardrail_tripped"]
    assert tripped_rows, "expected an audit row logging the guardrail trip"
    assert tripped_rows[0].approver_ref == supervisor_input.agent_ref
