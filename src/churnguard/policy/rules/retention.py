"""RET-014 (retention credit tier ceiling) and RET-002 (discount % ceiling).

RET-002 is the one universal rule in this pack: it applies to every
candidate regardless of category and has no component_prefix. It only
ever returns a RuleOutcome when it's violated — a passing check contributes
nothing to governing_rules, since it imposed no constraint on the outcome.
"""

from __future__ import annotations

from churnguard.contracts.offers import CandidateOffer, OfferComponent
from churnguard.policy.digest import AccountDigest
from churnguard.policy.loader import PolicyPack
from churnguard.policy.rules import RuleOutcome

RULE_ID_TIER_CEILING = "RET-014"
RULE_ID_PCT_CEILING = "RET-002"


def evaluate_retention_credit_tier(
    component: OfferComponent, digest: AccountDigest, pack: PolicyPack
) -> RuleOutcome | None:
    prefix = pack.raw["rules"][RULE_ID_TIER_CEILING]["component_prefix"]
    if not component.code.startswith(prefix):
        return None

    if digest.active_promo_codes:
        return RuleOutcome(
            rule_id=RULE_ID_TIER_CEILING,
            blocked=True,
            tier_required=None,
            violation_reason=(
                f"{RULE_ID_TIER_CEILING}: cannot stack a retention credit with active "
                f"promotions {digest.active_promo_codes}"
            ),
        )

    amount = abs(component.monthly)
    if amount <= pack.limits.max_monthly_discount_tier1:
        return RuleOutcome(
            rule_id=RULE_ID_TIER_CEILING, blocked=False, tier_required=1, violation_reason=None
        )
    if amount <= pack.limits.max_monthly_discount_tier2:
        return RuleOutcome(
            rule_id=RULE_ID_TIER_CEILING, blocked=False, tier_required=2, violation_reason=None
        )
    return RuleOutcome(
        rule_id=RULE_ID_TIER_CEILING,
        blocked=True,
        tier_required=None,
        violation_reason=(
            f"{RULE_ID_TIER_CEILING}: retention credit {amount:.2f} exceeds the tier-2 "
            f"ceiling {pack.limits.max_monthly_discount_tier2:.2f}"
        ),
    )


def evaluate_discount_percentage_ceiling(
    candidate: CandidateOffer, digest: AccountDigest, pack: PolicyPack
) -> RuleOutcome | None:
    discount_pct = abs(candidate.total_monthly_impact) / digest.current_monthly
    if discount_pct <= pack.limits.max_discount_pct:
        return None
    return RuleOutcome(
        rule_id=RULE_ID_PCT_CEILING,
        blocked=True,
        tier_required=None,
        violation_reason=(
            f"{RULE_ID_PCT_CEILING}: discount {discount_pct:.2%} of the current bill "
            f"exceeds the ceiling {pack.limits.max_discount_pct:.0%}"
        ),
    )


__all__ = ["evaluate_discount_percentage_ceiling", "evaluate_retention_credit_tier"]
