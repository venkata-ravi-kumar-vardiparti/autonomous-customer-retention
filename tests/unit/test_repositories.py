"""Repository behaviour against the seeded database.

Scenario accounts are located deterministically by rebuilding the same
account list from the same fixed seed (no DB access needed to find them —
build_all_accounts is a pure function of the RNG seed) rather than by
hardcoding a masked ref that depends on internal RNG draw order.
"""

from __future__ import annotations

import random

import pytest

from churnguard.contracts.customer import CustomerContextRequest
from churnguard.data import masking
from churnguard.data.repositories import (
    billing_repo,
    catalog_repo,
    competitor_repo,
    customer_repo,
    notes_repo,
    promo_repo,
)
from churnguard.data.seed.generate import SEED, build_all_accounts

_SCENARIOS = build_all_accounts(random.Random(SEED))
WORKED_EXAMPLE_REF = masking.mask_account_number(_SCENARIOS[0].account_id)
DELINQUENT_REF = masking.mask_account_number(_SCENARIOS[1].account_id)
NO_FINANCING_REF = masking.mask_account_number(_SCENARIOS[2].account_id)
THREE_PROMOS_REF = masking.mask_account_number(_SCENARIOS[3].account_id)
CONTRADICTORY_BILLING_REF = masking.mask_account_number(_SCENARIOS[4].account_id)
CHURNED_REF = masking.mask_account_number(_SCENARIOS[5].account_id)


def test_at_least_25_seeded_accounts() -> None:
    assert len(_SCENARIOS) >= 25


async def test_worked_example_billing() -> None:
    result = await billing_repo.get_billing(WORKED_EXAMPLE_REF)
    billing = result.data
    assert billing.current_bill == pytest.approx(198.43)
    assert billing.prior_bill == pytest.approx(165.20)
    assert billing.delta == pytest.approx(33.23)

    by_cause = {cause.cause: cause for cause in billing.delta_attribution}
    assert by_cause["loyalty_promo_expired"].amount == pytest.approx(20.00)
    assert by_cause["loyalty_promo_expired"].reversible is True
    assert by_cause["autopay_discount_lost"].amount == pytest.approx(8.00)
    assert by_cause["autopay_discount_lost"].reason == "card_expired"
    assert by_cause["autopay_discount_lost"].reversible is True
    assert by_cause["regulatory_fee_increase"].amount == pytest.approx(5.23)
    assert by_cause["regulatory_fee_increase"].reversible is False
    assert result.evidence  # every record carries provenance


async def test_worked_example_device_financing() -> None:
    result = await catalog_repo.get_device_financing(WORKED_EXAMPLE_REF)
    assert len(result.data) == 1
    line = result.data[0]
    assert line.line_ref == "LINE_****03"
    assert line.remaining_balance == pytest.approx(312.40)
    assert line.months_remaining == 10


async def test_worked_example_usage_by_line_has_a_null() -> None:
    result = await catalog_repo.get_usage_by_line(WORKED_EXAMPLE_REF)
    assert result.data["LINE_****03"] is None


async def test_worked_example_account_context() -> None:
    request = CustomerContextRequest(
        account_ref=WORKED_EXAMPLE_REF,
        requested_domains=["billing", "device_financing"],
        lookback_months=3,
        line_refs_of_interest=["LINE_****03"],
        verification_tasks=["confirm_current_bill_amount"],
        reason_code="cancel_request",
    )
    result = await customer_repo.get_account_context(request)
    context = result.data
    assert context.account_ref == WORKED_EXAMPLE_REF
    assert context.tenure_months == 74
    assert context.line_count == 4
    assert context.excluded_fields == ["date_of_birth", "zip_plus4", "marketing_segment"]
    assert context.verification_results[0].task == "confirm_current_bill_amount"
    assert result.evidence


async def test_delinquent_account_has_past_due_balance() -> None:
    result = await billing_repo.get_payment_history(DELINQUENT_REF)
    assert result.data.current_past_due > 0
    assert result.data.late_count > 5


async def test_no_financing_account_has_empty_device_financing() -> None:
    result = await catalog_repo.get_device_financing(NO_FINANCING_REF)
    assert result.data == []


async def test_three_promos_account_has_three_active_promotions() -> None:
    result = await promo_repo.get_active_promotions(THREE_PROMOS_REF)
    assert len(result.data) == 3


async def test_contradictory_billing_is_surfaced_not_reconciled() -> None:
    result = await billing_repo.get_billing(CONTRADICTORY_BILLING_REF)
    billing = result.data
    attributed_total = sum(cause.amount for cause in billing.delta_attribution)
    assert attributed_total != pytest.approx(billing.delta)


async def test_churned_account_is_queryable() -> None:
    request = CustomerContextRequest(
        account_ref=CHURNED_REF,
        requested_domains=["billing"],
        lookback_months=1,
        line_refs_of_interest=[],
        verification_tasks=[],
        reason_code="reactivation_inquiry",
    )
    result = await customer_repo.get_account_context(request)
    assert result.data.account_ref == CHURNED_REF


async def test_competitor_snapshots_tx_dfw_returns_offers() -> None:
    result = await competitor_repo.get_snapshots("TX-DFW")
    assert len(result.data) >= 2
    for offer in result.data:
        assert offer.normalized_monthly_equivalent == pytest.approx(
            offer.monthly_price / offer.line_count
        )


async def test_competitor_snapshots_respect_max_age_filter() -> None:
    result = await competitor_repo.get_snapshots("TX-DFW", max_snapshot_age_days=10)
    for offer in result.data:
        meta = await competitor_repo.get_snapshot_meta(offer.source_snapshot_id)
        assert meta.age_days <= 10


async def test_notes_repo_returns_plain_strings_for_worked_example() -> None:
    result = await notes_repo.get_notes(WORKED_EXAMPLE_REF)
    assert isinstance(result.data, list)
    assert all(isinstance(note, str) for note in result.data)


async def test_unknown_account_ref_raises() -> None:
    with pytest.raises(LookupError):
        await billing_repo.get_billing("ACCT_****0000")


async def test_malformed_account_ref_raises_value_error() -> None:
    with pytest.raises(ValueError):
        await billing_repo.get_billing("not-a-masked-ref")
