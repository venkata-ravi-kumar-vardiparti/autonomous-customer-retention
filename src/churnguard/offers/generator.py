"""Produce candidate retention offers from what the other agents already
know - never from a fresh LLM judgment call about pricing. Every amount
either comes straight off AccountContext (a billing correction) or off a
small curated offer-template table (see CATALOG constants below), the same
"small versioned table" pattern policy/packs/*.yaml uses for limits.

This module never imports churnguard.policy: it proposes candidates, it
does not evaluate them. C4 (a competitor price match) is a deliberate
example of that separation - this module will happily propose one whenever
a competitor's like-for-like price beats the customer's own bill, and
policy.engine.evaluate is the thing that later blocks it (PRO-007 below
regional_manager tier, RET-002 if the implied discount is too big a
percentage of the bill). Proposing and evaluating are different jobs.

Every OfferComponent.code this module emits must start with one of the
policy pack's known category prefixes (CLAUDE.md "Offer component
convention") - anything else would silently pass through the policy engine
with no governing rules at all, which is the one mistake here that would be
expensive to unwind later.
"""

from __future__ import annotations

from typing import Any

from churnguard.contracts.competitor import CompetitorComparison
from churnguard.contracts.conversation import ConversationSignals
from churnguard.contracts.customer import AccountContext
from churnguard.contracts.offers import CandidateOffer, OfferComponent

KNOWN_COMPONENT_PREFIXES = ("RET_", "BIL_", "PLN_", "FIN_", "PRC_")

# --- Credit reinstatement (billing-derived, generalizes to any account) ---

REVERSIBLE_CAUSE_COMPONENTS: dict[str, tuple[str, int | None]] = {
    "loyalty_promo_expired": ("RET_LOYALTY_CREDIT_REINSTATEMENT", 12),
    "autopay_discount_lost": ("BIL_AUTOPAY_DISCOUNT_RESTORE", None),
}
GENERIC_REVERSIBLE_CREDIT_CODE = "RET_GENERIC_CREDIT_REINSTATEMENT"

# --- Curated offer-template amounts (not derivable from account data) ---

PLAN_MIGRATION_HOTSPOT_REDUCTION_CODE = "PLN_HOTSPOT_REDUCTION_MIGRATION"
PLAN_MIGRATION_HOTSPOT_REDUCTION_MONTHLY = -38.44

RETENTION_BUNDLE_LOYALTY_CREDIT_CODE = "RET_LOYALTY_CREDIT"
RETENTION_BUNDLE_LOYALTY_CREDIT_MONTHLY = -20.00
RETENTION_BUNDLE_LOYALTY_CREDIT_MONTHS = 12

DEVICE_CREDIT_CODE = "FIN_DEVICE_CREDIT"
DEVICE_CREDIT_MONTHLY = -18.00

COMPETITOR_PRICE_MATCH_CODE = "PRC_COMPETITOR_PRICE_MATCH"


def _validate_component_code(code: str) -> None:
    if not code.startswith(KNOWN_COMPONENT_PREFIXES):
        raise ValueError(
            f"offer component code {code!r} does not start with a known category prefix "
            f"{KNOWN_COMPONENT_PREFIXES} - the policy engine would silently pass it through "
            "with no governing rules"
        )


def _build_credit_reinstatement(account: AccountContext) -> CandidateOffer | None:
    components: list[OfferComponent] = []
    for cause in account.billing.delta_attribution:
        if not cause.reversible:
            continue
        code, duration_months = REVERSIBLE_CAUSE_COMPONENTS.get(
            cause.cause, (GENERIC_REVERSIBLE_CREDIT_CODE, None)
        )
        _validate_component_code(code)
        components.append(
            OfferComponent(
                code=code, monthly=-round(cause.amount, 2), duration_months=duration_months
            )
        )

    if not components:
        return None

    total = round(sum(component.monthly for component in components), 2)
    return CandidateOffer(
        candidate_id="C_credit_reinstatement",
        type="bill_credit",
        components=components,
        total_monthly_impact=total,
    )


def _build_plan_migration(
    account: AccountContext, signals: ConversationSignals
) -> CandidateOffer | None:
    if not signals.churn_signals:
        return None
    if not account.plan_profile.plan_code.endswith("_PLUS"):
        return None

    _validate_component_code(PLAN_MIGRATION_HOTSPOT_REDUCTION_CODE)
    component = OfferComponent(
        code=PLAN_MIGRATION_HOTSPOT_REDUCTION_CODE,
        monthly=PLAN_MIGRATION_HOTSPOT_REDUCTION_MONTHLY,
        duration_months=None,
    )
    return CandidateOffer(
        candidate_id="C_plan_migration",
        type="plan_change",
        components=[component],
        total_monthly_impact=component.monthly,
    )


def _build_retention_bundle(account: AccountContext) -> CandidateOffer | None:
    if not account.device_financing:
        return None

    _validate_component_code(RETENTION_BUNDLE_LOYALTY_CREDIT_CODE)
    _validate_component_code(DEVICE_CREDIT_CODE)
    financed_line = account.device_financing[0]
    components = [
        OfferComponent(
            code=RETENTION_BUNDLE_LOYALTY_CREDIT_CODE,
            monthly=RETENTION_BUNDLE_LOYALTY_CREDIT_MONTHLY,
            duration_months=RETENTION_BUNDLE_LOYALTY_CREDIT_MONTHS,
        ),
        OfferComponent(
            code=DEVICE_CREDIT_CODE,
            monthly=DEVICE_CREDIT_MONTHLY,
            duration_months=financed_line.months_remaining,
        ),
    ]
    total = round(sum(component.monthly for component in components), 2)
    return CandidateOffer(
        candidate_id="C_bundle",
        type="retention_bundle",
        components=components,
        total_monthly_impact=total,
    )


def _build_competitor_price_match(
    account: AccountContext, competitor: CompetitorComparison | None
) -> CandidateOffer | None:
    if competitor is None or not competitor.resolved_offers:
        return None

    best_offer = min(competitor.resolved_offers, key=lambda offer: offer.monthly_price)
    delta = round(best_offer.monthly_price - account.billing.current_bill, 2)
    if delta >= 0:
        return None

    _validate_component_code(COMPETITOR_PRICE_MATCH_CODE)
    component = OfferComponent(
        code=COMPETITOR_PRICE_MATCH_CODE, monthly=delta, duration_months=None
    )
    return CandidateOffer(
        candidate_id="C_competitor_price_match",
        type="plan_discount",
        components=[component],
        total_monthly_impact=delta,
    )


def _renumber(candidates: list[CandidateOffer]) -> list[CandidateOffer]:
    return [
        candidate.model_copy(update={"candidate_id": f"C{index + 1}"})
        for index, candidate in enumerate(candidates)
    ]


def generate_candidates(
    account: AccountContext,
    signals: ConversationSignals,
    competitor: CompetitorComparison | None,
) -> list[CandidateOffer]:
    """Deterministic, rule-based - no LLM involvement anywhere in this path.

    Candidates are proposed in a fixed order (credit reinstatement, plan
    migration, retention bundle, competitor price match) and renumbered
    C1.. from whichever of those actually apply, so a caller never sees a
    gap.
    """
    candidates = [
        candidate
        for candidate in (
            _build_credit_reinstatement(account),
            _build_plan_migration(account, signals),
            _build_retention_bundle(account),
            _build_competitor_price_match(account, competitor),
        )
        if candidate is not None
    ]
    return _renumber(candidates)


def account_digest_from_context(account: AccountContext) -> dict[str, Any]:
    """Build the loose account_digest dict policy.digest.parse_account_digest
    expects, from the Customer 360 agent's own AccountContext - the only
    two pieces of information not already shaped exactly right are
    financing_active (derived from device_financing being non-empty) and
    payment_current (derived from current_past_due being zero).
    """
    return {
        "current_monthly": account.billing.current_bill,
        "active_promo_codes": list(account.active_promotions),
        "financing_active": bool(account.device_financing),
        "payment_current": account.payment_history.current_past_due <= 0.0,
    }


__all__ = [
    "KNOWN_COMPONENT_PREFIXES",
    "account_digest_from_context",
    "generate_candidates",
]
