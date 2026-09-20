"""Pure functions that turn a competitor headline price into a like-for-like
monthly figure, and derive the switching-cost/breakeven/claim-reconciliation
numbers that sit around it.

Every function here is total and side-effect free: no I/O, no clock, no
model call. This is deliberate - see CLAUDE.md's Offer Policy engine notes
on why `evaluate_with_pack` is pure, and agents/conversation.py's windowing
notes on pushing anything code can compute deterministically out of the
model's hands. The Competitor agent (agents/competitor.py) calls these
functions and then overwrites whatever the model produced for the
corresponding fields - the LLM must never compute a price.
"""

from __future__ import annotations

from dataclasses import dataclass

from churnguard.contracts.competitor import ClaimReconciliation, SwitchingCosts
from churnguard.contracts.conversation import CompetitorClaim
from churnguard.contracts.envelope import Freshness

ESTIMATED_TAX_RATE_BY_GEOGRAPHY: dict[str, float] = {
    "TX-DFW": 0.19,
}
DEFAULT_ESTIMATED_TAX_RATE = 0.15
"""Estimated combined sales/telecom tax+fee rate, applied to a competitor's
base plan price (before any feature-parity addon - taxes are assessed on
the service charge, not on a hand-added parity adjustment). A small,
versioned, geography-keyed table, same pattern as policy/packs/*.yaml's
curated limits - not looked up from a live tax service."""

HOTSPOT_PARITY_ADDON_USD = 10.00
"""Flat monthly addon bringing a competitor plan without mobile hotspot
data up to feature parity with a plan that includes it."""

FRESH_MAX_DAYS = 29
AGING_MAX_DAYS = 30
"""Deliberately stricter than data/freshness.py's 29/40 boundary: competitor
*pricing* moves faster than account data, so a comparison older than a
month is already stale, not merely aging. An independent module gets an
independent threshold - see CLAUDE.md's "Competitor agent & offer
generation" notes."""

CONFIDENCE_PENALTY_BY_FRESHNESS: dict[Freshness, float] = {
    "fresh": 0.0,
    "aging": 0.03,
    "stale": 0.08,
}
"""Magnitude to subtract from AgentResult.confidence - stored as a
non-negative amount (CompetitorComparison.confidence_penalty is
Field(ge=0.0, le=1.0)), not a signed delta."""

CLAIM_MATCH_TOLERANCE_USD = 0.50


@dataclass(frozen=True)
class NormalizedOffer:
    """Result of normalize_like_for_like_monthly - not a contract type."""

    like_for_like_monthly: float
    per_line_equivalent: float
    assumptions: list[str]


def normalize_like_for_like_monthly(
    *,
    competitor_per_line_price: float,
    line_count: int,
    geography: str,
    includes_hotspot: bool,
) -> NormalizedOffer:
    """Adjust a competitor's per-line headline price to a like-for-like
    total for `line_count` lines: feature-parity addons and estimated
    taxes/fees, both applied on top of the plain per-line x line-count base.
    """
    base_monthly = round(competitor_per_line_price * line_count, 2)
    assumptions = [
        f"base: {competitor_per_line_price:.2f}/line x {line_count} lines = {base_monthly:.2f}"
    ]

    adjusted = base_monthly
    if not includes_hotspot:
        adjusted = round(adjusted + HOTSPOT_PARITY_ADDON_USD, 2)
        assumptions.append(f"hotspot_parity_addon: +{HOTSPOT_PARITY_ADDON_USD:.2f}")

    tax_rate = ESTIMATED_TAX_RATE_BY_GEOGRAPHY.get(geography, DEFAULT_ESTIMATED_TAX_RATE)
    estimated_taxes_fees = round(base_monthly * tax_rate, 2)
    like_for_like_monthly = round(adjusted + estimated_taxes_fees, 2)
    assumptions.append(
        f"estimated_taxes_fees: +{estimated_taxes_fees:.2f} ({tax_rate:.0%} of base, {geography})"
    )

    return NormalizedOffer(
        like_for_like_monthly=like_for_like_monthly,
        per_line_equivalent=round(like_for_like_monthly / line_count, 2),
        assumptions=assumptions,
    )


def compute_switching_costs(
    *,
    device_financing_payoff: float,
    activation_fee: float,
    early_termination_fees_total: float = 0.0,
    other_costs: float = 0.0,
) -> SwitchingCosts:
    total = round(
        device_financing_payoff + early_termination_fees_total + activation_fee + other_costs, 2
    )
    return SwitchingCosts(
        device_payoff_total=round(device_financing_payoff, 2),
        early_termination_fees_total=round(early_termination_fees_total, 2),
        activation_fees=round(activation_fee, 2),
        other_costs=round(other_costs, 2),
        total=total,
    )


def compute_breakeven_months(monthly_savings: float, switching_costs_total: float) -> float | None:
    """None when switching wouldn't actually save money - "breakeven" is
    meaningless (or infinite) without positive monthly savings."""
    if monthly_savings <= 0:
        return None
    return round(switching_costs_total / monthly_savings, 1)


@dataclass(frozen=True)
class RateOption:
    """One published per-line rate to reconcile a customer's claim against."""

    label: str
    per_line_rate: float
    line_count: int


def reconcile_claim(
    claim: CompetitorClaim | None,
    *,
    options: list[RateOption],
    account_line_count: int,
) -> ClaimReconciliation:
    """Find the option whose per-line rate is closest to the claimed price.

    If that nearest option's line_count doesn't match the account's actual
    line count, the claim is matching a different pricing tier than what
    the account would actually get (e.g. a single-line rate quoted against
    a 4-line family plan) - the claim can't be confirmed, and the
    explanation says exactly which tier it matched instead. Otherwise the
    claim is judged against the account's own applicable rate: within
    CLAIM_MATCH_TOLERANCE_USD is "confirmed"; higher is "overstated"
    (the customer believes the competitor is pricier than they are);
    lower is "understated".
    """
    if claim is None or not options:
        return ClaimReconciliation(
            customer_claim=claim,
            verdict="unverifiable",
            resolved_price=None,
            explanation="No competitor claim/rate to reconcile.",
        )

    nearest = min(options, key=lambda opt: abs(opt.per_line_rate - claim.price))
    applicable = next((opt for opt in options if opt.line_count == account_line_count), None)

    if nearest.line_count != account_line_count:
        resolved_price = applicable.per_line_rate if applicable is not None else None
        explanation = (
            f"The ${claim.price:.2f} figure matches {claim.carrier}'s {nearest.label} rate "
            f"(${nearest.per_line_rate:.2f}/line at {nearest.line_count} line"
            f"{'s' if nearest.line_count != 1 else ''}), not the rate that applies to this "
            f"account's {account_line_count}-line plan"
            + (f" (${applicable.per_line_rate:.2f}/line)." if applicable is not None else ".")
        )
        return ClaimReconciliation(
            customer_claim=claim,
            verdict="unverifiable",
            resolved_price=resolved_price,
            explanation=explanation,
        )

    rate = nearest.per_line_rate
    if abs(claim.price - rate) <= CLAIM_MATCH_TOLERANCE_USD:
        verdict = "confirmed"
    elif claim.price > rate:
        verdict = "overstated"
    else:
        verdict = "understated"

    return ClaimReconciliation(
        customer_claim=claim,
        verdict=verdict,
        resolved_price=rate,
        explanation=(
            f"{claim.carrier}'s published rate at {account_line_count} lines is "
            f"${rate:.2f}/line, vs. the customer's claimed ${claim.price:.2f}."
        ),
    )


def classify_competitor_freshness(age_days: int) -> Freshness:
    if age_days <= FRESH_MAX_DAYS:
        return "fresh"
    if age_days <= AGING_MAX_DAYS:
        return "aging"
    return "stale"


def confidence_penalty_for_freshness(freshness: Freshness) -> float:
    return CONFIDENCE_PENALTY_BY_FRESHNESS[freshness]


__all__ = [
    "AGING_MAX_DAYS",
    "CLAIM_MATCH_TOLERANCE_USD",
    "CONFIDENCE_PENALTY_BY_FRESHNESS",
    "DEFAULT_ESTIMATED_TAX_RATE",
    "ESTIMATED_TAX_RATE_BY_GEOGRAPHY",
    "FRESH_MAX_DAYS",
    "HOTSPOT_PARITY_ADDON_USD",
    "NormalizedOffer",
    "RateOption",
    "classify_competitor_freshness",
    "compute_breakeven_months",
    "compute_switching_costs",
    "confidence_penalty_for_freshness",
    "normalize_like_for_like_monthly",
    "reconcile_claim",
]
