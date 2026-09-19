"""PLN-021: a plan migration that reduces hotspot allowance always needs disclosure."""

from __future__ import annotations

from churnguard.contracts.offers import OfferComponent
from churnguard.contracts.policy import Disclosure
from churnguard.policy.digest import AccountDigest
from churnguard.policy.loader import PolicyPack
from churnguard.policy.rules import RuleOutcome

RULE_ID = "PLN-021"


def evaluate_plan_migration_disclosure(
    component: OfferComponent, digest: AccountDigest, pack: PolicyPack
) -> RuleOutcome | None:
    rule_cfg = pack.raw["rules"][RULE_ID]
    if not component.code.startswith(rule_cfg["component_prefix"]):
        return None

    disclosure_cfg = rule_cfg["disclosure"]
    disclosure = Disclosure(
        code=disclosure_cfg["code"],
        text=" ".join(disclosure_cfg["text"].split()),
        must_be_read_verbatim=bool(disclosure_cfg["must_be_read_verbatim"]),
    )
    return RuleOutcome(
        rule_id=RULE_ID,
        blocked=False,
        tier_required=1,
        violation_reason=None,
        disclosure=disclosure,
    )


__all__ = ["evaluate_plan_migration_disclosure"]
