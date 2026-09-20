"""offers/generator.py: must reproduce Phase 2's exact C1..C4 reference
candidates (tests/unit/policy_fixtures.py) when fed the matching worked
example - ACCT_****4471's real billing/financing shape, a churn signal on a
"_PLUS" plan, and a Phase 6 competitor comparison that implies the same
-45.63 monthly delta Phase 2 hardcoded for C4.
"""

from __future__ import annotations

from datetime import date

import pytest

from churnguard.contracts.competitor import (
    ClaimReconciliation,
    CompetitorComparison,
    Normalization,
    ResolvedCompetitorOffer,
    SnapshotMeta,
)
from churnguard.contracts.conversation import ChurnSignal, ConversationSignals
from churnguard.contracts.customer import (
    AccountContext,
    Billing,
    DeltaCause,
    DeviceFinancingLine,
    PaymentHistorySummary,
    PlanProfile,
)
from churnguard.offers import generator, normalizer
from tests.unit.policy_fixtures import (
    make_c1_credit_reinstatement,
    make_c2_plan_migration,
    make_c3_bundle,
    make_c4_competitor_price_match,
)

WORKED_EXAMPLE_REF = "ACCT_****4471"


def _worked_example_account() -> AccountContext:
    return AccountContext(
        account_ref=WORKED_EXAMPLE_REF,
        tenure_months=74,
        line_count=4,
        billing=Billing(
            current_bill=198.43,
            prior_bill=165.20,
            delta=33.23,
            delta_attribution=[
                DeltaCause(
                    cause="loyalty_promo_expired",
                    amount=20.00,
                    event_date=date(2026, 9, 9),
                    reason="12-month loyalty promo ended",
                    reversible=True,
                    evidence_id="EVID_BILL_****4471_1",
                ),
                DeltaCause(
                    cause="autopay_discount_lost",
                    amount=8.00,
                    event_date=date(2026, 9, 11),
                    reason="card_expired",
                    reversible=True,
                    evidence_id="EVID_BILL_****4471_2",
                ),
                DeltaCause(
                    cause="regulatory_fee_increase",
                    amount=5.23,
                    event_date=date(2026, 9, 14),
                    reason="state regulatory fee adjustment",
                    reversible=False,
                    evidence_id="EVID_BILL_****4471_3",
                ),
            ],
        ),
        payment_history=PaymentHistorySummary(
            on_time_count=70, late_count=2, last_late_date=date(2026, 3, 3), current_past_due=0.0
        ),
        device_financing=[
            DeviceFinancingLine(
                line_ref="LINE_****03",
                device="Handset Model A",
                remaining_balance=312.40,
                monthly_payment=31.24,
                months_remaining=10,
                early_termination_fee=150.0,
            )
        ],
        plan_profile=PlanProfile(
            plan_code="PLAN_UNLIMITED_PLUS",
            plan_name="Unlimited Plus",
            contract_type="month_to_month",
            contract_end_date=None,
        ),
        usage_by_line={
            "LINE_****01": "high_data",
            "LINE_****02": "medium_data",
            "LINE_****03": None,
            "LINE_****04": "low_data",
        },
        active_promotions=[],
        verification_results=[],
        excluded_fields=["date_of_birth", "zip_plus4", "marketing_segment"],
    )


def _worked_example_signals() -> ConversationSignals:
    return ConversationSignals(
        intents=[],
        churn_signals=[ChurnSignal(signal="explicit_cancel_threat", strength="high")],
        competitor_claims=[],
        customer_stated_figures=[],
        unresolved_concerns=[],
        sentiment_trajectory="frustrated, then calmed after being heard",
        verification_tasks=[],
    )


def _worked_example_competitor_comparison() -> CompetitorComparison:
    normalized = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=30.00, line_count=4, geography="TX-DFW", includes_hotspot=False
    )
    switching_costs = normalizer.compute_switching_costs(
        device_financing_payoff=312.40, activation_fee=140.00
    )
    monthly_savings = round(198.43 - normalized.like_for_like_monthly, 2)
    breakeven = normalizer.compute_breakeven_months(monthly_savings, switching_costs.total)

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
        breakeven_months=breakeven,
        claim_reconciliation=ClaimReconciliation(
            customer_claim=None,
            verdict="unverifiable",
            resolved_price=None,
            explanation="No competitor claim to reconcile.",
        ),
        snapshot=SnapshotMeta(
            as_of=date(2026, 8, 9),
            age_days=41,
            geography="TX-DFW",
            capture_method="curated_manual",
            snapshot_id="SNAP_****01",
        ),
        freshness="stale",
        confidence_penalty=0.08,
    )


def test_generator_reproduces_phase2_reference_candidates_exactly() -> None:
    account = _worked_example_account()
    signals = _worked_example_signals()
    competitor = _worked_example_competitor_comparison()

    candidates = generator.generate_candidates(account, signals, competitor)
    by_id = {c.candidate_id: c for c in candidates}

    assert list(by_id.keys()) == ["C1", "C2", "C3", "C4"]
    assert by_id["C1"] == make_c1_credit_reinstatement()
    assert by_id["C2"] == make_c2_plan_migration()
    assert by_id["C3"] == make_c3_bundle()
    assert by_id["C4"] == make_c4_competitor_price_match()


def test_credit_reinstatement_skips_irreversible_causes() -> None:
    account = _worked_example_account()
    candidates = generator.generate_candidates(account, _worked_example_signals(), None)
    c1 = next(c for c in candidates if c.candidate_id == "C1")
    codes = [component.code for component in c1.components]
    assert "RET_LOYALTY_CREDIT_REINSTATEMENT" in codes
    assert "BIL_AUTOPAY_DISCOUNT_RESTORE" in codes
    assert len(codes) == 2  # the irreversible regulatory fee never becomes a component


def test_no_churn_signal_means_no_plan_migration_candidate() -> None:
    account = _worked_example_account()
    signals = ConversationSignals(
        intents=[],
        churn_signals=[],
        competitor_claims=[],
        customer_stated_figures=[],
        unresolved_concerns=[],
        sentiment_trajectory="neutral",
        verification_tasks=[],
    )
    candidates = generator.generate_candidates(account, signals, None)
    codes = {component.code for c in candidates for component in c.components}
    assert "PLN_HOTSPOT_REDUCTION_MIGRATION" not in codes


def test_no_device_financing_means_no_bundle_candidate() -> None:
    account = _worked_example_account().model_copy(update={"device_financing": []})
    candidates = generator.generate_candidates(account, _worked_example_signals(), None)
    codes = {component.code for c in candidates for component in c.components}
    assert "FIN_DEVICE_CREDIT" not in codes


def test_no_competitor_comparison_means_no_price_match_candidate() -> None:
    account = _worked_example_account()
    candidates = generator.generate_candidates(account, _worked_example_signals(), None)
    codes = {component.code for c in candidates for component in c.components}
    assert "PRC_COMPETITOR_PRICE_MATCH" not in codes


def test_competitor_comparison_that_is_more_expensive_skips_price_match() -> None:
    account = _worked_example_account()
    expensive_comparison = _worked_example_competitor_comparison().model_copy(
        update={
            "resolved_offers": [
                _worked_example_competitor_comparison().resolved_offers[0].model_copy(
                    update={"monthly_price": 999.00}
                )
            ]
        }
    )
    candidates = generator.generate_candidates(
        account, _worked_example_signals(), expensive_comparison
    )
    codes = {component.code for c in candidates for component in c.components}
    assert "PRC_COMPETITOR_PRICE_MATCH" not in codes


def test_every_component_code_has_a_known_prefix() -> None:
    account = _worked_example_account()
    candidates = generator.generate_candidates(
        account, _worked_example_signals(), _worked_example_competitor_comparison()
    )
    for candidate in candidates:
        for component in candidate.components:
            assert component.code.startswith(generator.KNOWN_COMPONENT_PREFIXES)


def test_unknown_component_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="known category prefix"):
        generator._validate_component_code("XYZ_NOT_A_REAL_CATEGORY")


def test_account_digest_from_context() -> None:
    account = _worked_example_account()
    digest = generator.account_digest_from_context(account)
    assert digest == {
        "current_monthly": 198.43,
        "active_promo_codes": [],
        "financing_active": True,
        "payment_current": True,
    }


def test_competitor_agent_has_no_network_capable_tool() -> None:
    """Acceptance criterion 4: the Competitor agent's tools wrap curated
    snapshot reads only - never scraping, never an HTTP/network call."""
    from churnguard.agents.competitor import COMPETITOR_SPEC

    tool_names = {tool.name for tool in COMPETITOR_SPEC.tools}
    assert tool_names == {"get_competitor_snapshots", "get_snapshot_meta"}

    import ast
    import inspect

    from churnguard.tools import competitor_tools

    tree = ast.parse(inspect.getsource(competitor_tools))
    network_markers = {"requests", "httpx", "urllib", "aiohttp", "socket"}
    data_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in network_markers
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in network_markers
            if node.module.startswith("churnguard.data"):
                data_imports.append(node.module)
                for alias in node.names:
                    assert alias.name == "competitor_repo"

    assert data_imports  # the module does read churnguard.data - just only competitor_repo
