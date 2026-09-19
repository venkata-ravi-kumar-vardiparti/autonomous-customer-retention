"""BIL-003: auto-restore an autopay discount, conditioned on a valid payment method."""

from __future__ import annotations

from churnguard.contracts.offers import OfferComponent
from churnguard.policy.digest import AccountDigest
from churnguard.policy.loader import PolicyPack
from churnguard.policy.rules import RuleOutcome

RULE_ID = "BIL-003"


def evaluate_autopay_restore(
    component: OfferComponent, digest: AccountDigest, pack: PolicyPack
) -> RuleOutcome | None:
    prefix = pack.raw["rules"][RULE_ID]["component_prefix"]
    if not component.code.startswith(prefix):
        return None

    if not digest.payment_current:
        return RuleOutcome(
            rule_id=RULE_ID,
            blocked=True,
            tier_required=None,
            violation_reason=(
                f"{RULE_ID}: cannot auto-restore the autopay discount without a "
                f"currently valid payment method"
            ),
        )

    # A billing correction, not a discretionary retention offer: no special
    # agent tier is required to auto-restore it.
    return RuleOutcome(rule_id=RULE_ID, blocked=False, tier_required=0, violation_reason=None)


__all__ = ["evaluate_autopay_restore"]
