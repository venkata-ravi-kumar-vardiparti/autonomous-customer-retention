"""FIN-009: a device-financing credit requires tier-2 authority and active financing."""

from __future__ import annotations

from churnguard.contracts.offers import OfferComponent
from churnguard.policy.digest import AccountDigest
from churnguard.policy.loader import PolicyPack
from churnguard.policy.rules import RuleOutcome

RULE_ID = "FIN-009"


def evaluate_financing_credit(
    component: OfferComponent, digest: AccountDigest, pack: PolicyPack
) -> RuleOutcome | None:
    prefix = pack.raw["rules"][RULE_ID]["component_prefix"]
    if not component.code.startswith(prefix):
        return None

    if not digest.financing_active:
        return RuleOutcome(
            rule_id=RULE_ID,
            blocked=True,
            tier_required=None,
            violation_reason=(
                f"{RULE_ID}: financing credit requires an active financing plan on the account"
            ),
        )

    amount = abs(component.monthly)
    tier2_ceiling = pack.limits.max_monthly_discount_tier2
    if amount > tier2_ceiling:
        return RuleOutcome(
            rule_id=RULE_ID,
            blocked=True,
            tier_required=None,
            violation_reason=(
                f"{RULE_ID}: financing credit {amount:.2f} exceeds the tier-2 ceiling "
                f"{tier2_ceiling:.2f}"
            ),
        )

    return RuleOutcome(rule_id=RULE_ID, blocked=False, tier_required=2, violation_reason=None)


__all__ = ["evaluate_financing_credit"]
