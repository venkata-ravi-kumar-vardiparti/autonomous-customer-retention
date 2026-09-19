"""The deterministic Offer Policy engine. No LLM, no I/O in the evaluation path.

evaluate() is the public entry point matching the phase brief's literal
signature (PolicyEvaluationRequest) -> PolicyVerdictSet. It resolves the
named policy pack via loader.get_pack (an lru_cache'd, load-once-per-version
lookup — the only I/O anywhere near this package, and it happens before
evaluation, not during it) and delegates to evaluate_with_pack, which is
the actual pure computational core: given a request, an already-loaded
PolicyPack, and an explicit `now`, it is a total, deterministic function of
its inputs. Tests that need a specific (possibly mutated) pack call
evaluate_with_pack directly.

`now` is threaded through explicitly rather than read from the wall clock
inside evaluate_with_pack, so PolicyVerdictSet.evaluated_at doesn't itself
become a hidden source of nondeterminism — a genuinely pure function
cannot read the clock internally and still claim to be pure.
"""

from __future__ import annotations

from datetime import UTC, datetime

from churnguard.contracts.offers import CandidateOffer
from churnguard.contracts.policy import (
    CandidateVerdict,
    ComputedLimits,
    PolicyEvaluationRequest,
    PolicyVerdictSet,
    Verdict,
)
from churnguard.policy import loader
from churnguard.policy.digest import AccountDigest, parse_account_digest
from churnguard.policy.loader import PolicyPack
from churnguard.policy.rules import RuleOutcome, billing, financing, plan, prohibited, retention


def _component_outcomes(
    candidate: CandidateOffer, digest: AccountDigest, tier: int, pack: PolicyPack
) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    for component in candidate.components:
        for outcome in (
            retention.evaluate_retention_credit_tier(component, digest, pack),
            billing.evaluate_autopay_restore(component, digest, pack),
            plan.evaluate_plan_migration_disclosure(component, digest, pack),
            financing.evaluate_financing_credit(component, digest, pack),
            prohibited.evaluate_competitor_price_match(component, tier, pack),
        ):
            if outcome is not None:
                outcomes.append(outcome)
    return outcomes


def _combine_verdict(outcomes: list[RuleOutcome]) -> CandidateVerdict:
    """The one place a verdict is decided. blocked wins, unconditionally, first.

    No other function in this module is allowed to construct a Verdict's
    `verdict` field directly — they all go through this, so there is no
    code path that can promote a blocked outcome to "pass".
    """
    if any(outcome.blocked for outcome in outcomes):
        return "blocked"
    if any(outcome.disclosure is not None for outcome in outcomes):
        return "pass_with_disclosure"
    return "pass"


def _evaluate_candidate(
    candidate: CandidateOffer, digest: AccountDigest, tier: int, pack: PolicyPack
) -> Verdict:
    outcomes = _component_outcomes(candidate, digest, tier, pack)

    universal_outcome = retention.evaluate_discount_percentage_ceiling(candidate, digest, pack)
    if universal_outcome is not None:
        outcomes.append(universal_outcome)

    verdict = _combine_verdict(outcomes)
    constraint_violations = [
        outcome.violation_reason for outcome in outcomes if outcome.blocked
    ]

    if verdict == "blocked":
        assert constraint_violations, "a blocked verdict must always cite a violation"
        approval_tier_required = None
    else:
        tier_candidates = [
            outcome.tier_required for outcome in outcomes if outcome.tier_required is not None
        ]
        approval_tier_required = max(tier_candidates, default=0)

    return Verdict(
        candidate_id=candidate.candidate_id,
        verdict=verdict,
        governing_rules=[outcome.rule_id for outcome in outcomes],
        approval_tier_required=approval_tier_required,
        required_disclosures=[
            outcome.disclosure for outcome in outcomes if outcome.disclosure is not None
        ],
        constraint_violations=constraint_violations,
    )


def _computed_limits(request: PolicyEvaluationRequest, pack: PolicyPack) -> ComputedLimits:
    max_monthly_credit = (
        pack.limits.max_monthly_discount_tier2
        if request.agent_authority_tier >= 2
        else pack.limits.max_monthly_discount_tier1
    )
    return ComputedLimits(
        max_discount_pct=pack.limits.max_discount_pct,
        max_monthly_credit=max_monthly_credit,
        max_bundle_duration_months=pack.limits.max_bundle_duration_months,
        agent_authority_ceiling=request.agent_authority_tier,
        notes=[],
    )


def evaluate_with_pack(
    request: PolicyEvaluationRequest, pack: PolicyPack, *, now: datetime | None = None
) -> PolicyVerdictSet:
    """Pure: a total function of (request, pack, now). No I/O, no LLM, no transcript."""
    if request.policy_pack_version != pack.version:
        raise ValueError(
            f"request.policy_pack_version {request.policy_pack_version!r} does not match "
            f"the supplied pack's version {pack.version!r}"
        )

    digest = parse_account_digest(request.account_digest)
    verdicts = [
        _evaluate_candidate(candidate, digest, request.agent_authority_tier, pack)
        for candidate in request.candidate_offers
    ]

    prohibited_triggered: list[str] = []
    for verdict in verdicts:
        for rule_id in verdict.governing_rules:
            if (
                rule_id in loader.KNOWN_PROHIBITED_IDS
                and verdict.verdict == "blocked"
                and rule_id not in prohibited_triggered
            ):
                prohibited_triggered.append(rule_id)

    evaluated_at = now if now is not None else datetime.now(UTC)

    return PolicyVerdictSet(
        policy_pack_version=pack.version,
        policy_pack_hash=pack.pack_hash,
        evaluated_at=evaluated_at,
        evaluation_mode="deterministic",
        computed_limits=_computed_limits(request, pack),
        verdicts=verdicts,
        prohibited_actions_triggered=prohibited_triggered,
    )


def evaluate(request: PolicyEvaluationRequest, *, now: datetime | None = None) -> PolicyVerdictSet:
    """Public entry point. Resolves the pack by version, then delegates."""
    pack = loader.get_pack(request.policy_pack_version)
    return evaluate_with_pack(request, pack, now=now)


__all__ = ["evaluate", "evaluate_with_pack"]
