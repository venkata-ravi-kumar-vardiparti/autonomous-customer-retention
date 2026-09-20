"""Integration: offers/generator.py -> policy.engine.evaluate, against real
seeded accounts (via the Governed Data Layer, no fakes) and real competitor
snapshot rows.

Acceptance criterion 3: 100% of generated candidates are accepted as valid
input by policy.engine.evaluate - every candidate the generator proposes
gets back exactly one Verdict, for every seeded account, whether or not
that account happens to trigger every category.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from churnguard.contracts.competitor import (
    ClaimReconciliation,
    CompetitorComparison,
    Normalization,
    ResolvedCompetitorOffer,
)
from churnguard.contracts.conversation import ChurnSignal, ConversationSignals
from churnguard.contracts.customer import CustomerContextRequest
from churnguard.contracts.policy import PolicyEvaluationRequest
from churnguard.data import masking
from churnguard.data.repositories import competitor_repo, customer_repo
from churnguard.data.seed.generate import SEED, build_all_accounts
from churnguard.offers import generator, normalizer
from churnguard.policy import engine
from churnguard.tools.customer_tools import ALL_CUSTOMER_360_DOMAINS

FIXED_NOW = datetime(2026, 9, 19, tzinfo=UTC)
POLICY_PACK_VERSION = "2026.09.1"

_SCENARIOS = build_all_accounts(random.Random(SEED))
_GOLDEN_REFS = [masking.mask_account_number(account.account_id) for account in _SCENARIOS[:10]]
WORKED_EXAMPLE_REF = _GOLDEN_REFS[0]


async def _fetch_account_context(account_ref: str):
    request = CustomerContextRequest(
        account_ref=account_ref,
        requested_domains=ALL_CUSTOMER_360_DOMAINS,
        lookback_months=3,
        line_refs_of_interest=[],
        verification_tasks=[],
        reason_code="cancel_request",
    )
    result = await customer_repo.get_account_context(request)
    return result.data


async def _build_competitor_comparison(line_count: int) -> CompetitorComparison:
    snapshots = await competitor_repo.get_snapshots("TX-DFW", carriers=["RivalCo"])
    primary = min(snapshots.data, key=lambda offer: offer.normalized_monthly_equivalent)
    meta = await competitor_repo.get_snapshot_meta(primary.source_snapshot_id)

    per_line_price = round(primary.monthly_price / primary.line_count, 2)
    normalized = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=per_line_price,
        line_count=line_count,
        geography="TX-DFW",
        includes_hotspot="hotspot" in primary.includes,
    )
    switching_costs = normalizer.compute_switching_costs(
        device_financing_payoff=0.0, activation_fee=50.0
    )
    freshness = normalizer.classify_competitor_freshness(meta.age_days)

    return CompetitorComparison(
        resolved_offers=[
            ResolvedCompetitorOffer(
                carrier=primary.carrier,
                plan_name=primary.plan_name,
                monthly_price=normalized.like_for_like_monthly,
                line_count=line_count,
                includes=primary.includes,
                normalized_monthly_equivalent=normalized.per_line_equivalent,
                source_snapshot_id=primary.source_snapshot_id,
            )
        ],
        normalization=Normalization(
            method="like_for_like_monthly", assumptions=normalized.assumptions
        ),
        switching_costs=switching_costs,
        # This helper only needs a schema-valid CompetitorComparison to feed the
        # generator - it doesn't assert an exact breakeven figure, so monthly
        # savings isn't threaded through from the account's own current bill.
        breakeven_months=normalizer.compute_breakeven_months(0.0, switching_costs.total),
        claim_reconciliation=ClaimReconciliation(
            customer_claim=None,
            verdict="unverifiable",
            resolved_price=None,
            explanation="No competitor claim to reconcile.",
        ),
        snapshot=meta,
        freshness=freshness,
        confidence_penalty=normalizer.confidence_penalty_for_freshness(freshness),
    )


def _signals_with_churn_signal() -> ConversationSignals:
    return ConversationSignals(
        intents=[],
        churn_signals=[ChurnSignal(signal="explicit_cancel_threat", strength="high")],
        competitor_claims=[],
        customer_stated_figures=[],
        unresolved_concerns=[],
        sentiment_trajectory="frustrated",
        verification_tasks=[],
    )


@pytest.mark.parametrize("account_ref", _GOLDEN_REFS)
async def test_every_generated_candidate_is_accepted_by_the_policy_engine(
    account_ref: str,
) -> None:
    account = await _fetch_account_context(account_ref)
    competitor = await _build_competitor_comparison(account.line_count)
    signals = _signals_with_churn_signal()

    candidates = generator.generate_candidates(account, signals, competitor)
    digest = generator.account_digest_from_context(account)

    request = PolicyEvaluationRequest(
        policy_pack_version=POLICY_PACK_VERSION,
        jurisdiction="US-TX",
        channel="voice",
        agent_authority_tier=2,
        account_digest=digest,
        candidate_offers=candidates,
    )
    result = engine.evaluate(request, now=FIXED_NOW)

    assert len(result.verdicts) == len(candidates)
    assert {v.candidate_id for v in result.verdicts} == {c.candidate_id for c in candidates}
    assert result.evaluation_mode == "deterministic"


async def _reference_competitor_comparison() -> CompetitorComparison:
    """The real seeded TX-DFW RivalCo snapshot ($45/line) doesn't happen to
    beat this account's bill once normalized, so C4 never triggers against
    it - unlike test_generator.py, this test wants to also exercise the
    C4-blocked path end-to-end. Reuse the same $30/line curated-snapshot
    figures the Phase 6 brief's worked example gives, exactly like
    test_generator.py's hand-crafted CompetitorComparison; only the account
    itself comes from the real seeded DB here.
    """
    normalized = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=30.00, line_count=4, geography="TX-DFW", includes_hotspot=False
    )
    switching_costs = normalizer.compute_switching_costs(
        device_financing_payoff=312.40, activation_fee=140.00
    )
    return CompetitorComparison(
        resolved_offers=[
            ResolvedCompetitorOffer(
                carrier="Verizon",
                plan_name="Unlimited Welcome",
                monthly_price=normalized.like_for_like_monthly,
                line_count=4,
                includes=["unlimited_data"],
                normalized_monthly_equivalent=normalized.per_line_equivalent,
                source_snapshot_id="SNAP_****01",
            )
        ],
        normalization=Normalization(
            method="like_for_like_monthly", assumptions=normalized.assumptions
        ),
        switching_costs=switching_costs,
        breakeven_months=normalizer.compute_breakeven_months(
            round(198.43 - normalized.like_for_like_monthly, 2), switching_costs.total
        ),
        claim_reconciliation=ClaimReconciliation(
            customer_claim=None,
            verdict="unverifiable",
            resolved_price=None,
            explanation="No competitor claim to reconcile.",
        ),
        snapshot=await _stale_snapshot_meta(),
        freshness="stale",
        confidence_penalty=0.08,
    )


async def _stale_snapshot_meta():
    real_snapshots = await competitor_repo.get_snapshots("TX-DFW", carriers=["RivalCo"])
    meta = await competitor_repo.get_snapshot_meta(real_snapshots.data[0].source_snapshot_id)
    return meta.model_copy(update={"age_days": 41})


async def test_worked_example_end_to_end_matches_phase2_verdicts() -> None:
    account = await _fetch_account_context(WORKED_EXAMPLE_REF)
    competitor = await _reference_competitor_comparison()
    signals = _signals_with_churn_signal()

    candidates = generator.generate_candidates(account, signals, competitor)
    assert [c.candidate_id for c in candidates] == ["C1", "C2", "C3", "C4"]

    digest = generator.account_digest_from_context(account)
    request = PolicyEvaluationRequest(
        policy_pack_version=POLICY_PACK_VERSION,
        jurisdiction="US-TX",
        channel="voice",
        agent_authority_tier=1,
        account_digest=digest,
        candidate_offers=candidates,
    )
    result = engine.evaluate(request, now=FIXED_NOW)
    by_id = {v.candidate_id: v for v in result.verdicts}

    assert by_id["C1"].verdict == "pass"
    assert by_id["C2"].verdict == "pass_with_disclosure"
    assert by_id["C3"].verdict == "pass"
    assert by_id["C4"].verdict == "blocked"
    assert "PRO-007" in result.prohibited_actions_triggered


async def test_empty_candidate_list_is_trivially_accepted() -> None:
    """An account that triggers no category at all is still valid input -
    the policy engine must not choke on zero candidates."""
    account = await _fetch_account_context(WORKED_EXAMPLE_REF)
    no_delta_billing = account.billing.model_copy(update={"delta_attribution": []})
    no_signal_account = account.model_copy(
        update={"device_financing": [], "billing": no_delta_billing}
    )
    signals = ConversationSignals(
        intents=[],
        churn_signals=[],
        competitor_claims=[],
        customer_stated_figures=[],
        unresolved_concerns=[],
        sentiment_trajectory="neutral",
        verification_tasks=[],
    )
    candidates = generator.generate_candidates(no_signal_account, signals, None)
    assert candidates == []

    digest = generator.account_digest_from_context(no_signal_account)
    request = PolicyEvaluationRequest(
        policy_pack_version=POLICY_PACK_VERSION,
        jurisdiction="US-TX",
        channel="voice",
        agent_authority_tier=1,
        account_digest=digest,
        candidate_offers=candidates,
    )
    result = engine.evaluate(request, now=FIXED_NOW)
    assert result.verdicts == []
