"""Confidence aggregation across the four specialist agents into one
overall_confidence for a RecommendationSet, with an itemised, auditable
list of ConfidenceAdjustment reasons - never a single opaque number.

BASE_CONFIDENCE (1.0) represents "every upstream agent returned a complete,
fresh, uncontested picture." Every deviation from that is named:

- Missing Customer 360 evidence that happens to corroborate a customer-
  flagged concern (the exact line the customer says is failing has no
  usage telemetry on file) is NOT treated as a generic data gap - the
  Phase 7 brief calls this out explicitly ("corroborating evidence, not a
  dead end"). It gets a small, named penalty
  (MISSING_USAGE_CORROBORATION_PENALTY) instead of the larger generic one.
- Any OTHER missing evidence (not tied to a flagged concern) is a real,
  unexplained gap and gets the larger generic penalty.
- Stale/aging competitor pricing surfaces agents/competitor.py's own
  confidence_penalty as a named, itemised reason here, rather than only
  living inside the competitor AgentResult's opaque confidence number.
- A conversation run blocked by the prompt-injection guardrail
  (status="insufficient_evidence") means the whole reconciliation is
  standing on no real transcript signal at all - the worst case - and
  drops confidence sharply.

overall_confidence = clamp(BASE_CONFIDENCE + sum(adjustment.delta), 0, 1).
Every adjustment.delta is signed (negative moves confidence down), so
`base_confidence + sum(delta for adjustments) == overall_confidence`
exactly, by construction - see tests/e2e/test_bounded_pipeline.py's
confidence-adjustment invariant check.

Bounded re-request (MAX_REREQUEST_ATTEMPTS = 1 per agent) is enforced in
orchestration/bounded.py by a plain loop-count, never left to model
judgement; this module only decides *whether* a gap is worth re-requesting
(an uncorroborated one) via has_uncorroborated_gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from churnguard.contracts.competitor import CompetitorComparison
from churnguard.contracts.conversation import Concern, ConversationSignals
from churnguard.contracts.customer import AccountContext
from churnguard.contracts.envelope import AgentResult
from churnguard.contracts.recommendation import ConfidenceAdjustment

BASE_CONFIDENCE = 1.0
MISSING_USAGE_CORROBORATION_PENALTY = 0.03
GENERIC_MISSING_EVIDENCE_PENALTY = 0.10
BLOCKED_CONVERSATION_PENALTY = 0.5
MAX_REREQUEST_ATTEMPTS = 1
BILLING_CONTRADICTION_TOLERANCE_USD = 1.00
BILLING_CONTRADICTION_PENALTY = 0.05
DEGRADED_AGENT_PENALTY = 0.20

_USAGE_MISSING_PREFIX = "usage_by_line."

_NETWORK_ISSUE_KEYWORDS = (
    "network",
    "dropped call",
    "dropped calls",
    "signal",
    "outage",
    "no service",
    "coverage",
    "dead zone",
)


def _line_ref_from_missing_usage(missing_evidence_item: str) -> str | None:
    if missing_evidence_item.startswith(_USAGE_MISSING_PREFIX):
        return missing_evidence_item[len(_USAGE_MISSING_PREFIX) :]
    return None


def _flagged_line_refs(concerns: list[Concern]) -> set[str]:
    return {concern.line_ref for concern in concerns if concern.line_ref}


def has_uncorroborated_gap(
    customer_result: AgentResult[AccountContext], concerns: list[Concern]
) -> bool:
    """True when customer_result has missing evidence NOT explained by any
    customer-flagged concern - i.e. a genuine gap worth one re-request, as
    opposed to a null usage entry that corroborates what the customer
    already told us."""
    flagged = _flagged_line_refs(concerns)
    for item in customer_result.missing_evidence:
        line_ref = _line_ref_from_missing_usage(item)
        if line_ref is None or line_ref not in flagged:
            return True
    return False


def customer_missing_evidence_adjustments(
    customer_result: AgentResult[AccountContext], concerns: list[Concern]
) -> list[ConfidenceAdjustment]:
    flagged = _flagged_line_refs(concerns)
    adjustments: list[ConfidenceAdjustment] = []
    for item in customer_result.missing_evidence:
        line_ref = _line_ref_from_missing_usage(item)
        if line_ref is not None and line_ref in flagged:
            adjustments.append(
                ConfidenceAdjustment(
                    reason=(
                        f"missing usage telemetry on {line_ref} corroborates the "
                        "customer-reported issue on that line"
                    ),
                    delta=-MISSING_USAGE_CORROBORATION_PENALTY,
                )
            )
        else:
            adjustments.append(
                ConfidenceAdjustment(
                    reason=f"missing evidence: {item}",
                    delta=-GENERIC_MISSING_EVIDENCE_PENALTY,
                )
            )
    return adjustments


def competitor_freshness_adjustment(
    competitor: CompetitorComparison | None,
) -> ConfidenceAdjustment | None:
    if competitor is None or competitor.confidence_penalty <= 0.0:
        return None
    return ConfidenceAdjustment(
        reason=(
            f"competitor pricing is {competitor.freshness} "
            f"({competitor.snapshot.age_days}d old)"
        ),
        delta=-competitor.confidence_penalty,
    )


def conversation_blocked_adjustment(
    conversation_result: AgentResult[ConversationSignals],
) -> ConfidenceAdjustment | None:
    if conversation_result.status != "insufficient_evidence":
        return None
    return ConfidenceAdjustment(
        reason="conversation transcript blocked by prompt-injection guardrail",
        delta=-BLOCKED_CONVERSATION_PENALTY,
    )


def _billing_attributed_total(account: AccountContext) -> float:
    return round(sum(cause.amount for cause in account.billing.delta_attribution), 2)


def billing_contradiction_adjustment(account: AccountContext) -> ConfidenceAdjustment | None:
    """FAULT SCENARIO 3: a legacy billing system can hand back
    delta_attribution rows that don't sum to the bill's actual delta
    (data/seed/generate.py's "contradictory_billing" scenario is built
    exactly to exercise this). The Governed Data Layer surfaces the rows
    as-is - reconciling them is not a repository's job (see CLAUDE.md's
    Phase 1 notes) - so this is where the contradiction is finally
    detected: a confidence penalty, never a silent pick-one-side."""
    attributed_total = _billing_attributed_total(account)
    if abs(attributed_total - account.billing.delta) <= BILLING_CONTRADICTION_TOLERANCE_USD:
        return None
    return ConfidenceAdjustment(
        reason=(
            f"billing delta_attribution amounts (${attributed_total:.2f}) do not reconcile "
            f"with the actual bill delta (${account.billing.delta:.2f}) - the underlying "
            "billing rows are contradictory"
        ),
        delta=-BILLING_CONTRADICTION_PENALTY,
    )


def billing_contradiction_note(account: AccountContext) -> str | None:
    """The human-facing half of billing_contradiction_adjustment: a
    warning the Supervisor surfaces rather than silently picking either
    the attributed causes or the raw delta as "the" explanation."""
    attributed_total = _billing_attributed_total(account)
    if abs(attributed_total - account.billing.delta) <= BILLING_CONTRADICTION_TOLERANCE_USD:
        return None
    return (
        f"WARNING: billing rows are contradictory - attributed causes sum to "
        f"${attributed_total:.2f} but the bill actually changed by "
        f"${account.billing.delta:.2f}. Do not present a single explanation as fact; "
        "flag for billing team review before committing to either figure."
    )


def competitor_staleness_note(competitor: CompetitorComparison | None) -> str | None:
    """FAULT SCENARIO 1's "indicative banner": a stale competitor snapshot
    already earns its own confidence_adjustment (competitor_freshness_adjustment)
    - this is the accompanying human-readable note marking any competitor-based
    comparison as approximate, not exact."""
    if competitor is None or competitor.freshness != "stale":
        return None
    return (
        f"Competitor pricing snapshot is {competitor.snapshot.age_days} days old (stale) - "
        "treat any competitor-based comparison as indicative only, not exact."
    )


def degraded_agent_adjustment(agent_name: str, reason: str) -> ConfidenceAdjustment:
    """FAULT SCENARIOS 4/5: an agent that failed outright (a dead
    repository connection, a deadline breach, or any other exception
    orchestration/deadlines.py caught) still needs to be visible in
    confidence_adjustments, not just in fallbacks_applied - clearly
    flagged, per the phase brief, wherever a human is likely to look."""
    return ConfidenceAdjustment(
        reason=f"{agent_name} did not complete ({reason}) - proceeding without it",
        delta=-DEGRADED_AGENT_PENALTY,
    )


@dataclass(frozen=True)
class ConfidenceAggregate:
    base_confidence: float
    adjustments: list[ConfidenceAdjustment]
    overall_confidence: float


def aggregate_confidence(
    *,
    customer_result: AgentResult[AccountContext],
    conversation_result: AgentResult[ConversationSignals],
    competitor: CompetitorComparison | None,
    concerns: list[Concern],
    account: AccountContext | None = None,
    degraded_agents: list[tuple[str, str]] | None = None,
) -> ConfidenceAggregate:
    """account/degraded_agents are optional and keyword-only, added in
    Phase 10: `account` lets billing_contradiction_adjustment run inline
    here (FAULT SCENARIO 3) instead of every caller remembering to append
    it separately; `degraded_agents` is a list of (agent_name, reason)
    pairs for any agent orchestration/deadlines.py caught a timeout or
    exception from (FAULT SCENARIOS 4/5). Both default to "none" so this
    stays a pure superset of the Phase 7 signature."""
    adjustments: list[ConfidenceAdjustment] = []
    adjustments.extend(customer_missing_evidence_adjustments(customer_result, concerns))

    blocked_adjustment = conversation_blocked_adjustment(conversation_result)
    if blocked_adjustment is not None:
        adjustments.append(blocked_adjustment)

    freshness_adjustment = competitor_freshness_adjustment(competitor)
    if freshness_adjustment is not None:
        adjustments.append(freshness_adjustment)

    if account is not None:
        contradiction_adjustment = billing_contradiction_adjustment(account)
        if contradiction_adjustment is not None:
            adjustments.append(contradiction_adjustment)

    for agent_name, reason in degraded_agents or []:
        adjustments.append(degraded_agent_adjustment(agent_name, reason))

    overall = round(
        min(1.0, max(0.0, BASE_CONFIDENCE + sum(a.delta for a in adjustments))), 4
    )
    return ConfidenceAggregate(
        base_confidence=BASE_CONFIDENCE, adjustments=adjustments, overall_confidence=overall
    )


def build_mandatory_actions(concerns: list[Concern]) -> list[str]:
    """unresolved_concerns with customer_flagged_separate=True become
    mandatory actions - NEVER folded into a priced offer. A network/service
    keyword match becomes a structured "open_network_ticket:<line>:<location>"
    action; anything else becomes a generic escalation, so nothing a
    customer explicitly separated out is ever silently dropped."""
    actions: list[str] = []
    for concern in concerns:
        if not concern.customer_flagged_separate:
            continue
        issue_lower = concern.issue.lower()
        if any(keyword in issue_lower for keyword in _NETWORK_ISSUE_KEYWORDS):
            line_part = concern.line_ref or "unspecified_line"
            location_part = concern.location or "unspecified_location"
            actions.append(f"open_network_ticket:{line_part}:{location_part}")
        else:
            actions.append(f"escalate_concern:{concern.issue}")
    return actions


__all__ = [
    "BASE_CONFIDENCE",
    "BILLING_CONTRADICTION_PENALTY",
    "BILLING_CONTRADICTION_TOLERANCE_USD",
    "BLOCKED_CONVERSATION_PENALTY",
    "DEGRADED_AGENT_PENALTY",
    "GENERIC_MISSING_EVIDENCE_PENALTY",
    "MAX_REREQUEST_ATTEMPTS",
    "MISSING_USAGE_CORROBORATION_PENALTY",
    "ConfidenceAggregate",
    "aggregate_confidence",
    "billing_contradiction_adjustment",
    "billing_contradiction_note",
    "build_mandatory_actions",
    "competitor_freshness_adjustment",
    "competitor_staleness_note",
    "conversation_blocked_adjustment",
    "customer_missing_evidence_adjustments",
    "degraded_agent_adjustment",
    "has_uncorroborated_gap",
]
