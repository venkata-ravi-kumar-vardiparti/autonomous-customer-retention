"""Degraded-result builders shared by BOTH orchestration arms
(orchestration/bounded.py and orchestration/harness.py) - factored out in
Phase 11 specifically so the two arms can't drift: the same repository
outage, the same missing evidence, produces the exact same fallback shape
regardless of which SupervisorInput.orchestration_mode produced it.

None of this is agent- or arm-specific decision-making; it's just "what
does a schema-valid stand-in look like for a piece of evidence that never
arrived" - see orchestration/deadlines.py for the mechanism that decides
*when* one of these is needed, and CLAUDE.md's Phase 10 notes for the six
fault scenarios this exists to keep both arms honest about.
"""

from __future__ import annotations

from churnguard.agents.base import DeadlineExceededError
from churnguard.contracts.competitor import CompetitorComparison
from churnguard.contracts.conversation import ConversationSignals
from churnguard.contracts.customer import (
    AccountContext,
    Billing,
    PaymentHistorySummary,
    PlanProfile,
)
from churnguard.contracts.envelope import AgentResult, EvidenceRef, Telemetry
from churnguard.orchestration.deadlines import DeadlineOutcome

NO_TELEMETRY = Telemetry(
    model="none", latency_ms=0, tokens_in=0, tokens_out=0, cost_usd=0.0, cache_hit=False
)

EMPTY_CONVERSATION_SIGNALS = ConversationSignals(
    intents=[],
    churn_signals=[],
    competitor_claims=[],
    customer_stated_figures=[],
    unresolved_concerns=[],
    sentiment_trajectory="unknown - transcript unavailable",
    verification_tasks=[],
)


def outcome_reason(outcome: DeadlineOutcome[object]) -> str:
    if outcome.timed_out or isinstance(outcome.error, DeadlineExceededError):
        return "deadline exceeded"
    return str(outcome.error) if outcome.error is not None else "unknown failure"


def degraded_account_context(account_ref: str) -> AccountContext:
    """Worst case: the account bootstrap read itself failed. A schema-valid,
    obviously-empty AccountContext so the rest of the pipeline can still
    assemble a (empty-recommendations) response instead of crashing on a
    None."""
    return AccountContext(
        account_ref=account_ref,
        tenure_months=0,
        line_count=0,
        billing=Billing(current_bill=0.0, prior_bill=0.0, delta=0.0, delta_attribution=[]),
        payment_history=PaymentHistorySummary(
            on_time_count=0, late_count=0, last_late_date=None, current_past_due=0.0
        ),
        device_financing=[],
        plan_profile=PlanProfile(
            plan_code="UNKNOWN",
            plan_name="Unknown - account data unavailable",
            contract_type="unknown",
            contract_end_date=None,
        ),
        usage_by_line={},
        active_promotions=[],
        verification_results=[],
        excluded_fields=["ALL - account data repository unavailable"],
    )


def agent_result_from_bootstrap(
    account: AccountContext, evidence: list[EvidenceRef], *, reason: str
) -> AgentResult[AccountContext]:
    """The Customer 360 AGENT failed/timed out, but a direct Governed Data
    Layer read (bounded.py's own bootstrap; harness.py's own upfront read)
    is either real data or, in the worst case, the degraded placeholder
    above - either way, a real AgentResult the rest of the pipeline can
    treat uniformly."""
    return AgentResult(
        status="partial",
        confidence=0.6,
        data=account,
        evidence=evidence,
        missing_evidence=[],
        warnings=[
            f"customer_360 agent unavailable ({reason}); using a direct Governed Data "
            "Layer read instead"
        ],
        telemetry=NO_TELEMETRY,
    )


def degraded_competitor_result(*, reason: str) -> AgentResult[CompetitorComparison]:
    return AgentResult(
        status="failed",
        confidence=0.0,
        data=None,
        evidence=[],
        missing_evidence=["competitor_comparison"],
        warnings=[f"competitor agent unavailable: {reason}"],
        telemetry=NO_TELEMETRY,
    )


def degraded_conversation_result(*, reason: str) -> AgentResult[ConversationSignals]:
    return AgentResult(
        status="failed",
        confidence=0.0,
        data=None,
        evidence=[],
        missing_evidence=["conversation_signals"],
        warnings=[f"conversation agent unavailable: {reason}"],
        telemetry=NO_TELEMETRY,
    )


__all__ = [
    "EMPTY_CONVERSATION_SIGNALS",
    "NO_TELEMETRY",
    "agent_result_from_bootstrap",
    "degraded_account_context",
    "degraded_competitor_result",
    "degraded_conversation_result",
    "outcome_reason",
]
