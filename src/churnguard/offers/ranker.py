"""Rank policy-cleared candidate offers by retention likelihood x margin -
never by discount size alone.

The caller (orchestration/bounded.py) is responsible for the hard policy
filter: only candidates whose Verdict is "pass" or "pass_with_disclosure"
are ever passed in here. This module never re-applies or second-guesses
that filter - it only orders what it's given.

Retention likelihood is a small, versioned table keyed by "candidate kind"
(the same curated-table pattern as offers/generator.py's template amounts
and policy/packs/*.yaml's limits), reflecting how directly the offer is
grounded in a real, stated signal:
  - credit_reinstatement reverses a billing cause the customer is already
    upset about (highest, most certain save).
  - plan_migration only ever gets proposed when a churn_signal exists
    (offers/generator.py's own trigger condition), so it's always
    signal-grounded, but it's a template estimate, not a verified figure.
  - retention_bundle is proposed whenever financing exists, whether or not
    the customer raised it - the most speculative of the three.
  - competitor_price_match is rarely reachable at all (PRO-007 blocks it
    below regional_manager tier) but is scored for completeness.

Margin is real dollars, not a normalized percentage: the account's monthly
bill *after* the credit (current_monthly + total_monthly_impact, since
impact is negative) - the same figure surfaced to the human agent as
CustomerImpact.monthly_delta's counterpart. Scoring likelihood x real
remaining revenue (rather than likelihood x percent-off) is what keeps a
big, low-certainty discount (e.g. plan migration, -38.44) from ever
outranking a smaller, highly-certain one (credit reinstatement, -28.00) on
size alone - see CLAUDE.md's Phase 7 notes for the worked ACCT_****4471
example this produces.
"""

from __future__ import annotations

from dataclasses import dataclass

from churnguard.contracts.offers import CandidateOffer

CandidateKind = str

KIND_CREDIT_REINSTATEMENT = "credit_reinstatement"
KIND_PLAN_MIGRATION = "plan_migration"
KIND_RETENTION_BUNDLE = "retention_bundle"
KIND_COMPETITOR_PRICE_MATCH = "competitor_price_match"
KIND_UNKNOWN = "unknown"

RETENTION_LIKELIHOOD_BY_KIND: dict[CandidateKind, float] = {
    KIND_CREDIT_REINSTATEMENT: 0.95,
    KIND_PLAN_MIGRATION: 0.80,
    KIND_RETENTION_BUNDLE: 0.70,
    KIND_COMPETITOR_PRICE_MATCH: 0.50,
    KIND_UNKNOWN: 0.40,
}

CONFIDENCE_BY_KIND: dict[CandidateKind, float] = {
    KIND_CREDIT_REINSTATEMENT: 0.95,
    KIND_PLAN_MIGRATION: 0.75,
    KIND_RETENTION_BUNDLE: 0.85,
    KIND_COMPETITOR_PRICE_MATCH: 0.50,
    KIND_UNKNOWN: 0.50,
}
"""A distinct table from retention likelihood: this reflects data-
groundedness (how much of the candidate's amount is a verified account
figure vs. a curated template estimate), not business priority. A
retention_bundle's FIN_ component is a real, verified device-financing
balance, so it scores *higher* on confidence than plan_migration's flat
template amount, even though it ranks lower on retention likelihood."""


def classify_candidate_kind(candidate: CandidateOffer) -> CandidateKind:
    prefixes = {component.code.split("_", 1)[0] + "_" for component in candidate.components}
    if "PRC_" in prefixes:
        return KIND_COMPETITOR_PRICE_MATCH
    if "FIN_" in prefixes:
        return KIND_RETENTION_BUNDLE
    if "PLN_" in prefixes:
        return KIND_PLAN_MIGRATION
    if "RET_" in prefixes or "BIL_" in prefixes:
        return KIND_CREDIT_REINSTATEMENT
    return KIND_UNKNOWN


@dataclass(frozen=True)
class RankedCandidate:
    """One candidate's ranking inputs and the resulting score - not a contract type."""

    candidate: CandidateOffer
    kind: CandidateKind
    retention_likelihood: float
    margin: float
    score: float


def rank_candidates(
    candidates: list[CandidateOffer], *, current_monthly: float
) -> list[RankedCandidate]:
    """Descending by score; ties broken by candidate_id for determinism."""
    ranked = []
    for candidate in candidates:
        kind = classify_candidate_kind(candidate)
        likelihood = RETENTION_LIKELIHOOD_BY_KIND[kind]
        margin = max(0.0, round(current_monthly + candidate.total_monthly_impact, 2))
        score = round(likelihood * margin, 4)
        ranked.append(
            RankedCandidate(
                candidate=candidate, kind=kind, retention_likelihood=likelihood,
                margin=margin, score=score,
            )
        )
    return sorted(ranked, key=lambda rc: (-rc.score, rc.candidate.candidate_id))


__all__ = [
    "CONFIDENCE_BY_KIND",
    "KIND_COMPETITOR_PRICE_MATCH",
    "KIND_CREDIT_REINSTATEMENT",
    "KIND_PLAN_MIGRATION",
    "KIND_RETENTION_BUNDLE",
    "KIND_UNKNOWN",
    "RETENTION_LIKELIHOOD_BY_KIND",
    "RankedCandidate",
    "classify_candidate_kind",
    "rank_candidates",
]
