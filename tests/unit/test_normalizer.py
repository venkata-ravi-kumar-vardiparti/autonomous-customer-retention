"""Pure-function tests for offers/normalizer.py against the Phase 6 worked
example: Verizon "Unlimited Welcome" $30/line w/ autopay, taxes excluded,
41 days old, TX-DFW, 4 lines, current bill 198.43, claimed price $45.00
(ambiguous unit).
"""

from __future__ import annotations

import pytest

from churnguard.contracts.conversation import CompetitorClaim
from churnguard.offers import normalizer

CURRENT_MONTHLY = 198.43
COMPETITOR_PER_LINE_PRICE = 30.00
LINE_COUNT = 4
GEOGRAPHY = "TX-DFW"


def test_like_for_like_monthly_matches_reference_scenario() -> None:
    result = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=COMPETITOR_PER_LINE_PRICE,
        line_count=LINE_COUNT,
        geography=GEOGRAPHY,
        includes_hotspot=False,
    )

    assert result.like_for_like_monthly == pytest.approx(152.80)
    assert result.per_line_equivalent == pytest.approx(38.20)
    assert any("10.00" in a for a in result.assumptions)
    assert any("22.80" in a for a in result.assumptions)


def test_monthly_delta_vs_current_matches_reference_scenario() -> None:
    result = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=COMPETITOR_PER_LINE_PRICE,
        line_count=LINE_COUNT,
        geography=GEOGRAPHY,
        includes_hotspot=False,
    )
    delta = round(result.like_for_like_monthly - CURRENT_MONTHLY, 2)
    assert delta == pytest.approx(-45.63)


def test_like_for_like_monthly_with_hotspot_already_included_skips_addon() -> None:
    result = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=COMPETITOR_PER_LINE_PRICE,
        line_count=LINE_COUNT,
        geography=GEOGRAPHY,
        includes_hotspot=True,
    )
    # base 120.00 + 0 addon + 19% of 120.00 (22.80) = 142.80
    assert result.like_for_like_monthly == pytest.approx(142.80)
    assert not any("hotspot_parity_addon" in a for a in result.assumptions)


def test_default_tax_rate_used_outside_known_geography() -> None:
    result = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=COMPETITOR_PER_LINE_PRICE,
        line_count=LINE_COUNT,
        geography="US-CA",
        includes_hotspot=True,
    )
    # base 120.00 + 0 addon + 15% of 120.00 (18.00) = 138.00
    assert result.like_for_like_monthly == pytest.approx(138.00)


def test_switching_costs_match_reference_scenario() -> None:
    switching_costs = normalizer.compute_switching_costs(
        device_financing_payoff=312.40, activation_fee=140.00
    )
    assert switching_costs.device_payoff_total == pytest.approx(312.40)
    assert switching_costs.activation_fees == pytest.approx(140.00)
    assert switching_costs.early_termination_fees_total == pytest.approx(0.0)
    assert switching_costs.other_costs == pytest.approx(0.0)
    assert switching_costs.total == pytest.approx(452.40)


def test_breakeven_months_matches_reference_scenario() -> None:
    monthly_savings = round(CURRENT_MONTHLY - 152.80, 2)  # 45.63
    breakeven = normalizer.compute_breakeven_months(monthly_savings, 452.40)
    assert breakeven == pytest.approx(9.9)


def test_breakeven_months_is_none_when_there_is_no_saving() -> None:
    assert normalizer.compute_breakeven_months(0.0, 452.40) is None
    assert normalizer.compute_breakeven_months(-5.0, 452.40) is None


def test_claim_reconciliation_flags_single_line_rate_mismatch() -> None:
    claim = CompetitorClaim(
        carrier="Verizon", price=45.00, unit="ambiguous", source="customer_stated", span_refs=["t0"]
    )
    reconciliation = normalizer.reconcile_claim(
        claim,
        options=[
            normalizer.RateOption(label="single-line", per_line_rate=45.00, line_count=1),
            normalizer.RateOption(label="4-line family", per_line_rate=30.00, line_count=4),
        ],
        account_line_count=4,
    )

    assert reconciliation.verdict == "unverifiable"
    assert reconciliation.resolved_price == pytest.approx(30.00)
    assert "single-line" in reconciliation.explanation
    assert "30.00" in reconciliation.explanation


def test_claim_reconciliation_confirms_matching_applicable_rate() -> None:
    claim = CompetitorClaim(
        carrier="Verizon", price=30.00, unit="per_line", source="customer_stated", span_refs=["t0"]
    )
    reconciliation = normalizer.reconcile_claim(
        claim,
        options=[normalizer.RateOption(label="4-line family", per_line_rate=30.00, line_count=4)],
        account_line_count=4,
    )
    assert reconciliation.verdict == "confirmed"
    assert reconciliation.resolved_price == pytest.approx(30.00)


def test_claim_reconciliation_overstated_and_understated() -> None:
    overstated_claim = CompetitorClaim(
        carrier="Verizon", price=40.00, unit="per_line", source="customer_stated", span_refs=["t0"]
    )
    overstated = normalizer.reconcile_claim(
        overstated_claim,
        options=[normalizer.RateOption(label="4-line family", per_line_rate=30.00, line_count=4)],
        account_line_count=4,
    )
    assert overstated.verdict == "overstated"

    understated_claim = overstated_claim.model_copy(update={"price": 20.00})
    understated = normalizer.reconcile_claim(
        understated_claim,
        options=[normalizer.RateOption(label="4-line family", per_line_rate=30.00, line_count=4)],
        account_line_count=4,
    )
    assert understated.verdict == "understated"


def test_claim_reconciliation_unverifiable_with_no_claim() -> None:
    reconciliation = normalizer.reconcile_claim(
        None,
        options=[normalizer.RateOption(label="4-line family", per_line_rate=30.00, line_count=4)],
        account_line_count=4,
    )
    assert reconciliation.verdict == "unverifiable"
    assert reconciliation.resolved_price is None


@pytest.mark.parametrize(
    ("age_days", "expected_freshness", "expected_penalty"),
    [
        (29, "fresh", 0.0),
        (30, "aging", 0.03),
        (31, "stale", 0.08),
    ],
)
def test_freshness_boundary_at_29_30_31_days(
    age_days: int, expected_freshness: str, expected_penalty: float
) -> None:
    freshness = normalizer.classify_competitor_freshness(age_days)
    assert freshness == expected_freshness
    assert normalizer.confidence_penalty_for_freshness(freshness) == pytest.approx(expected_penalty)


def test_reference_scenario_is_stale_at_41_days() -> None:
    freshness = normalizer.classify_competitor_freshness(41)
    assert freshness == "stale"
    assert normalizer.confidence_penalty_for_freshness(freshness) == pytest.approx(0.08)
