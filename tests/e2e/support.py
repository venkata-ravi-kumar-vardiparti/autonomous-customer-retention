"""Shared scenario builders for tests/e2e - the bounded pipeline exercised
against real seeded accounts, with deterministic model doubles standing in
for every LLM call (no network, no live model, ever - same convention as
tests/golden and tests/support/fake_model.py).

Two named scenarios:

- reference_transcript()/reference_conversation_model(): the Phase 7
  worked example against ACCT_****4471 - a bill-increase dispute, a
  network complaint on line 3 (LINE_****03, the account's own null-usage
  line - deliberately corroborating), and a RivalCo price claim that
  happens to resolve to the seeded, deliberately-stale RivalCo snapshot
  (see the carriers=["RivalCo"] tie-break note below). Competitor pricing
  here normalizes ABOVE the account's own bill, so no competitor-price-match
  candidate is generated at all - this scenario is for exercising ranking,
  confidence adjustments and stability, not the policy hard filter.
- metrowave_transcript()/metrowave_conversation_model(): a churn-signal-only
  scenario naming MetroWave, whose cheapest normalized offer genuinely
  undercuts the account's bill - offers/generator.py proposes a competitor
  price match, and PRO-007 blocks it at agent_authority_tier=1. This is the
  scenario tests/e2e/test_blocked_offer_leak.py uses.

Why carriers=["RivalCo"] alone produces a STALE match: TX-DFW's seeded
snapshots (data/seed/generate.py::build_competitor_snapshots) include two
RivalCo rows tied at $45/line - "RivalCo Unlimited" (deliberately 41 days
old) and "RivalCo Value" (5 days old) - inserted in that order, so the
stale one gets the lower snapshot_id. agents/competitor.py's
`min(offers, key=normalized_monthly_equivalent)` breaks the tie by
returning the first-encountered (lowest snapshot_id) offer, i.e. the stale
one - real seeded data, no fake competitor payload needed.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

from churnguard.contracts.competitor import (
    ClaimReconciliation,
    CompetitorComparison,
    Normalization,
    ResolvedCompetitorOffer,
    SnapshotMeta,
    SwitchingCosts,
)
from churnguard.contracts.conversation import (
    ChurnSignal,
    CompetitorClaim,
    Concern,
    ConversationSignals,
    Intent,
    StatedFigure,
    TranscriptTurn,
)
from churnguard.contracts.recommendation import SupervisorInput
from churnguard.data import masking
from churnguard.data.seed.generate import SEED, build_all_accounts
from churnguard.tools.competitor_tools import COMPETITOR_TOOLS
from churnguard.tools.customer_tools import CUSTOMER_360_TOOLS
from tests.support.fake_model import StaticJSONModel, ToolCallingEchoModel

POLICY_PACK_VERSION = "2026.09.1"

_SCENARIOS = build_all_accounts(random.Random(SEED))
_GOLDEN_REFS = [masking.mask_account_number(account.account_id) for account in _SCENARIOS[:10]]
WORKED_EXAMPLE_REF = _GOLDEN_REFS[0]

_BASE_TS = datetime(2026, 9, 19, 14, 0, 0, tzinfo=UTC)


# --- generic model doubles ---------------------------------------------------


def _assemble_account_context(outputs: dict[str, Any]) -> str:
    summary = outputs["get_account_summary"]
    payload = {
        "account_ref": summary["account_ref"],
        "tenure_months": summary["tenure_months"],
        "line_count": summary["line_count"],
        "billing": outputs["get_billing"],
        "payment_history": outputs["get_payment_history"],
        "device_financing": outputs["get_device_financing"],
        "plan_profile": outputs["get_plan_profile"],
        "usage_by_line": outputs["get_usage_by_line"],
        "active_promotions": outputs["get_active_promotions"],
        "verification_results": summary["verification_results"],
        "excluded_fields": summary["excluded_fields"],
    }
    return json.dumps(payload)


def customer_model(*, delay_seconds: float = 0.0) -> ToolCallingEchoModel:
    return ToolCallingEchoModel(
        tools=CUSTOMER_360_TOOLS,
        assemble_output=_assemble_account_context,
        delay_seconds=delay_seconds,
    )


def _dummy_competitor_comparison_json() -> str:
    """Schema-valid placeholder only - agents/competitor.py overwrites every
    numeric field from real repository data regardless of what this says."""
    return CompetitorComparison(
        resolved_offers=[
            ResolvedCompetitorOffer(
                carrier="Placeholder",
                plan_name="Placeholder Plan",
                monthly_price=0.0,
                line_count=1,
                includes=[],
                normalized_monthly_equivalent=0.0,
                source_snapshot_id="SNAP_PLACEHOLDER",
            )
        ],
        normalization=Normalization(method="none", assumptions=[]),
        switching_costs=SwitchingCosts(
            device_payoff_total=0.0,
            early_termination_fees_total=0.0,
            activation_fees=0.0,
            other_costs=0.0,
            total=0.0,
        ),
        breakeven_months=None,
        claim_reconciliation=ClaimReconciliation(
            customer_claim=None,
            verdict="unverifiable",
            resolved_price=None,
            explanation="placeholder - overwritten by run_competitor_agent",
        ),
        snapshot=SnapshotMeta(
            as_of=date(2026, 9, 19),
            age_days=0,
            geography="TX-DFW",
            capture_method="curated_manual",
            snapshot_id="SNAP_PLACEHOLDER",
        ),
        freshness="fresh",
        confidence_penalty=0.0,
    ).model_dump_json()


def competitor_model(*, delay_seconds: float = 0.0) -> ToolCallingEchoModel:
    dummy_json = _dummy_competitor_comparison_json()
    return ToolCallingEchoModel(
        tools=COMPETITOR_TOOLS,
        assemble_output=lambda _outputs: dummy_json,
        delay_seconds=delay_seconds,
    )


def render_model_with_canned_copy() -> StaticJSONModel:
    """Covers C1..C4 generically - offers/generator.py always renumbers
    candidates from C1, so this works for any scenario regardless of which
    subset of categories actually triggered."""
    entries = [
        {
            "offer_id": f"C{i}",
            "title": f"Offer {i}",
            "rationale": f"Deterministically ranked offer {i} for this call.",
            "talk_track": f"Here's what I can offer you today (option {i}).",
        }
        for i in range(1, 5)
    ]
    return StaticJSONModel(json.dumps({"recommendations": entries}))


# --- reference scenario: ACCT_****4471, bill dispute + network + RivalCo ---


def reference_transcript() -> list[TranscriptTurn]:
    raw = [
        (
            0,
            "customer",
            "Hi, I want to cancel my service. My bill jumped by fifty dollars and I don't "
            "understand why.",
        ),
        (8, "agent", "I'm sorry to hear that - let me pull up your account."),
        (
            15,
            "customer",
            "Also - separate from the billing thing - my phone on line 3 keeps dropping calls "
            "near my house in Frisco, Texas. It's been happening for weeks.",
        ),
        (
            28,
            "customer",
            "RivalCo is offering me forty five dollars a line, way less than what I'm paying now.",
        ),
    ]
    return [
        TranscriptTurn(ts=_BASE_TS + timedelta(seconds=offset), speaker=speaker, text=text)
        for offset, speaker, text in raw
    ]


def reference_conversation_signals() -> ConversationSignals:
    return ConversationSignals(
        intents=[
            Intent(type="cancel_service", confidence=0.9, scope=None, span_refs=["[00:00]"]),
            Intent(type="billing_dispute", confidence=0.85, scope=None, span_refs=["[00:00]"]),
        ],
        churn_signals=[ChurnSignal(signal="explicit_cancel_request", strength="high")],
        competitor_claims=[
            CompetitorClaim(
                carrier="RivalCo", price=45.0, unit="per_line", source="customer_stated",
                span_refs=["[00:28]"],
            )
        ],
        customer_stated_figures=[
            StatedFigure(field="bill_increase", value=50.0, precision="approximate")
        ],
        unresolved_concerns=[
            Concern(
                issue="dropped calls / network issue",
                line_ref=masking.mask_line_ref(3),
                location="Frisco, TX",
                since=None,
                customer_flagged_separate=True,
            )
        ],
        sentiment_trajectory="frustrated, seeking to cancel",
        verification_tasks=[],
    )


def reference_conversation_model() -> StaticJSONModel:
    return StaticJSONModel(reference_conversation_signals().model_dump_json())


# --- MetroWave scenario: competitor genuinely undercuts, PRO-007 blocks it -


def metrowave_transcript() -> list[TranscriptTurn]:
    raw = [
        (0, "customer", "I'm thinking about cancelling - MetroWave is a lot cheaper."),
        (6, "agent", "I understand - let's see what we can do."),
    ]
    return [
        TranscriptTurn(ts=_BASE_TS + timedelta(seconds=offset), speaker=speaker, text=text)
        for offset, speaker, text in raw
    ]


def metrowave_conversation_signals() -> ConversationSignals:
    return ConversationSignals(
        intents=[Intent(type="cancel_service", confidence=0.9, scope=None, span_refs=["[00:00]"])],
        churn_signals=[ChurnSignal(signal="explicit_cancel_threat", strength="high")],
        competitor_claims=[
            CompetitorClaim(
                carrier="MetroWave", price=35.0, unit="per_line", source="customer_stated",
                span_refs=["[00:00]"],
            )
        ],
        customer_stated_figures=[],
        unresolved_concerns=[],
        sentiment_trajectory="considering leaving",
        verification_tasks=[],
    )


def metrowave_conversation_model() -> StaticJSONModel:
    return StaticJSONModel(metrowave_conversation_signals().model_dump_json())


# --- SupervisorInput builder --------------------------------------------------


def build_supervisor_input(
    transcript: list[TranscriptTurn],
    *,
    account_ref: str = WORKED_EXAMPLE_REF,
    agent_authority_tier: int = 1,
    deadline_ms: int = 2000,
    call_id: str = "CALL_****9001",
) -> SupervisorInput:
    return SupervisorInput(
        call_id=call_id,
        account_ref=account_ref,
        transcript_window=transcript,
        agent_authority_tier=agent_authority_tier,
        agent_ref="AGT_****0192",
        deadline_ms=deadline_ms,
        policy_pack_version=POLICY_PACK_VERSION,
        orchestration_mode="bounded_pipeline",
    )


__all__ = [
    "POLICY_PACK_VERSION",
    "WORKED_EXAMPLE_REF",
    "build_supervisor_input",
    "competitor_model",
    "customer_model",
    "metrowave_conversation_model",
    "metrowave_conversation_signals",
    "metrowave_transcript",
    "reference_conversation_model",
    "reference_conversation_signals",
    "reference_transcript",
    "render_model_with_canned_copy",
]
