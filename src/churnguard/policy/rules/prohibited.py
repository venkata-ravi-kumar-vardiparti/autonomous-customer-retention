"""PRO-007: a competitor price match is prohibited below regional_manager tier.

Prohibited-action rules behave like category rules (see retention.py's
docstring for the RET-002 contrast): they appear in governing_rules
whenever their component category is present, whether they block or not.
"""

from __future__ import annotations

from churnguard.contracts.offers import OfferComponent
from churnguard.policy.loader import PolicyPack
from churnguard.policy.rules import RuleOutcome

RULE_ID = "PRO-007"


def evaluate_competitor_price_match(
    component: OfferComponent, tier: int, pack: PolicyPack
) -> RuleOutcome | None:
    rule_cfg = pack.raw["prohibited_actions"][RULE_ID]
    if not component.code.startswith(rule_cfg["component_prefix"]):
        return None

    min_tier_name = rule_cfg["min_tier"]
    min_tier_value = int(pack.raw["authority_tiers"][min_tier_name])

    if tier < min_tier_value:
        return RuleOutcome(
            rule_id=RULE_ID,
            blocked=True,
            tier_required=None,
            violation_reason=(
                f"{RULE_ID}: competitor price match requires {min_tier_name} tier "
                f"({min_tier_value}); requesting agent is tier {tier}"
            ),
        )

    return RuleOutcome(
        rule_id=RULE_ID, blocked=False, tier_required=min_tier_value, violation_reason=None
    )


__all__ = ["evaluate_competitor_price_match"]
