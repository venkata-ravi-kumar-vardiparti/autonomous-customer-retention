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
) -> ConfidenceAggregate:
    adjustments: list[ConfidenceAdjustment] = []
    adjustments.extend(customer_missing_evidence_adjustments(customer_result, concerns))

    blocked_adjustment = conversation_blocked_adjustment(conversation_result)
    if blocked_adjustment is not None:
        adjustments.append(blocked_adjustment)

    freshness_adjustment = competitor_freshness_adjustment(competitor)
    if freshness_adjustment is not None:
        adjustments.append(freshness_adjustment)

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
    "BLOCKED_CONVERSATION_PENALTY",
    "GENERIC_MISSING_EVIDENCE_PENALTY",
    "MAX_REREQUEST_ATTEMPTS",
    "MISSING_USAGE_CORROBORATION_PENALTY",
    "ConfidenceAggregate",
    "aggregate_confidence",
    "build_mandatory_actions",
    "competitor_freshness_adjustment",
    "conversation_blocked_adjustment",
    "customer_missing_evidence_adjustments",
    "has_uncorroborated_gap",
]
