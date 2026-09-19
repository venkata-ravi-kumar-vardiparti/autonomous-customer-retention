"""Per-rule behaviour, and the rule-coverage gate.

RULE COVERAGE (acceptance criterion 2): test_every_pack_rule_id_is_exercised
below is self-contained and order-independent — it doesn't rely on other
tests in this file having already run. It holds its own fixture mapping
every declared rule ID to a candidate/digest/tier known to make that rule
fire, evaluates each directly, and asserts the rule ID shows up in
governing_rules. If the pack ever grows a rule ID with no entry in that
mapping, this test fails immediately. Assert on rule IDs, not line coverage.
"""

from __future__ import annotations

from typing import Any

from churnguard.contracts.offers import CandidateOffer, OfferComponent
from churnguard.contracts.policy import PolicyEvaluationRequest, Verdict
from churnguard.policy import engine, loader
from tests.unit.policy_fixtures import POLICY_PACK_VERSION

BASE_DIGEST: dict[str, Any] = {
    "current_monthly": 200.00,
    "active_promo_codes": [],
    "financing_active": True,
    "payment_current": True,
}


def _evaluate_one(
    candidate: CandidateOffer, *, digest: dict[str, Any] | None = None, tier: int = 1
) -> Verdict:
    request = PolicyEvaluationRequest(
        policy_pack_version=POLICY_PACK_VERSION,
        jurisdiction="US-TX",
        channel="voice",
        agent_authority_tier=tier,
        account_digest=digest if digest is not None else dict(BASE_DIGEST),
        candidate_offers=[candidate],
    )
    result = engine.evaluate(request)
    verdict: Verdict = result.verdicts[0]
    return verdict


def _single_component_candidate(code: str, monthly: float) -> CandidateOffer:
    return CandidateOffer(
        candidate_id="TEST",
        type="bill_credit",
        components=[OfferComponent(code=code, monthly=monthly, duration_months=None)],
        total_monthly_impact=monthly,
    )


# --- RET-014: retention credit within tier authority -----------------------


def test_ret_014_passes_within_tier1_ceiling() -> None:
    verdict = _evaluate_one(_single_component_candidate("RET_LOYALTY_CREDIT", -20.00))
    assert verdict.verdict == "pass"
    assert verdict.governing_rules == ["RET-014"]
    assert verdict.approval_tier_required == 1


def test_ret_014_requires_tier2_between_tier1_and_tier2_ceilings() -> None:
    verdict = _evaluate_one(_single_component_candidate("RET_LOYALTY_CREDIT", -40.00))
    assert verdict.verdict == "pass"
    assert verdict.approval_tier_required == 2


def test_ret_014_blocks_above_tier2_ceiling() -> None:
    verdict = _evaluate_one(_single_component_candidate("RET_LOYALTY_CREDIT", -60.00))
    assert verdict.verdict == "blocked"
    assert "RET-014" in verdict.governing_rules
    assert verdict.approval_tier_required is None


def test_ret_014_blocks_when_stacked_with_active_promo() -> None:
    digest = dict(BASE_DIGEST, active_promo_codes=["PROMO_LOYALTY12"])
    verdict = _evaluate_one(
        _single_component_candidate("RET_LOYALTY_CREDIT", -10.00), digest=digest
    )
    assert verdict.verdict == "blocked"
    assert verdict.governing_rules == ["RET-014"]


# --- RET-002: discount percentage ceiling -----------------------------------


def test_ret_002_blocks_alone_when_percentage_exceeds_ceiling_with_no_category_match() -> None:
    # A component code with no matching prefix: no category rule fires, RET-002
    # (universal) is the only rule that can trigger, isolating it cleanly.
    verdict = _evaluate_one(_single_component_candidate("MISC_ADJUSTMENT", -50.00))
    assert verdict.verdict == "blocked"
    assert verdict.governing_rules == ["RET-002"]


def test_ret_002_does_not_appear_when_under_the_ceiling() -> None:
    verdict = _evaluate_one(_single_component_candidate("MISC_ADJUSTMENT", -10.00))
    assert verdict.verdict == "pass"
    assert verdict.governing_rules == []


# --- BIL-003: auto-restore autopay discount ---------------------------------


def test_bil_003_passes_with_current_payment_method() -> None:
    verdict = _evaluate_one(_single_component_candidate("BIL_AUTOPAY_DISCOUNT_RESTORE", -8.00))
    assert verdict.verdict == "pass"
    assert verdict.governing_rules == ["BIL-003"]
    assert verdict.approval_tier_required == 0


def test_bil_003_blocks_without_a_valid_payment_method() -> None:
    digest = dict(BASE_DIGEST, payment_current=False)
    verdict = _evaluate_one(
        _single_component_candidate("BIL_AUTOPAY_DISCOUNT_RESTORE", -8.00), digest=digest
    )
    assert verdict.verdict == "blocked"
    assert verdict.governing_rules == ["BIL-003"]


# --- PLN-021: plan migration disclosure -------------------------------------


def test_pln_021_always_requires_disclosure() -> None:
    verdict = _evaluate_one(_single_component_candidate("PLN_HOTSPOT_REDUCTION", -38.44))
    assert verdict.verdict == "pass_with_disclosure"
    assert verdict.governing_rules == ["PLN-021"]
    assert verdict.required_disclosures[0].code == "DISC_HOTSPOT_REDUCTION"
    assert verdict.required_disclosures[0].must_be_read_verbatim is True


# --- FIN-009: financing credit requires tier 2 ------------------------------


def test_fin_009_passes_with_active_financing() -> None:
    verdict = _evaluate_one(_single_component_candidate("FIN_DEVICE_CREDIT", -18.00), tier=2)
    assert verdict.verdict == "pass"
    assert verdict.governing_rules == ["FIN-009"]
    assert verdict.approval_tier_required == 2


def test_fin_009_blocks_without_active_financing() -> None:
    digest = dict(BASE_DIGEST, financing_active=False)
    verdict = _evaluate_one(
        _single_component_candidate("FIN_DEVICE_CREDIT", -18.00), digest=digest, tier=2
    )
    assert verdict.verdict == "blocked"
    assert verdict.governing_rules == ["FIN-009"]


def test_fin_009_blocks_above_tier2_ceiling() -> None:
    # A high current_monthly keeps RET-002's percentage ceiling well out of
    # reach, isolating the tier-2 dollar ceiling this test targets.
    digest = dict(BASE_DIGEST, current_monthly=1000.00)
    verdict = _evaluate_one(
        _single_component_candidate("FIN_DEVICE_CREDIT", -60.00), digest=digest, tier=2
    )
    assert verdict.verdict == "blocked"
    assert verdict.governing_rules == ["FIN-009"]


# --- PRO-007: competitor price match prohibited below regional_manager -----


def test_pro_007_blocks_below_regional_manager_tier() -> None:
    verdict = _evaluate_one(
        _single_component_candidate("PRC_COMPETITOR_PRICE_MATCH", -10.00), tier=1
    )
    assert verdict.verdict == "blocked"
    assert "PRO-007" in verdict.governing_rules


def test_pro_007_passes_at_regional_manager_tier() -> None:
    verdict = _evaluate_one(
        _single_component_candidate("PRC_COMPETITOR_PRICE_MATCH", -10.00), tier=3
    )
    assert verdict.verdict == "pass"
    assert verdict.governing_rules == ["PRO-007"]
    assert verdict.approval_tier_required == 3


# --- No component matches any rule ------------------------------------------


def test_unmatched_component_passes_with_no_governing_rules() -> None:
    verdict = _evaluate_one(_single_component_candidate("MISC_ADJUSTMENT", -1.00))
    assert verdict.verdict == "pass"
    assert verdict.governing_rules == []
    assert verdict.approval_tier_required == 0


# --- Coverage gate: self-contained, order-independent -----------------------

# One (candidate, digest override, tier) fixture per declared rule ID, each
# known to make that specific rule appear in governing_rules.
_COVERAGE_FIXTURES: dict[str, tuple[CandidateOffer, dict[str, Any] | None, int]] = {
    "RET-014": (_single_component_candidate("RET_LOYALTY_CREDIT", -20.00), None, 1),
    "RET-002": (_single_component_candidate("MISC_ADJUSTMENT", -50.00), None, 1),
    "BIL-003": (_single_component_candidate("BIL_AUTOPAY_DISCOUNT_RESTORE", -8.00), None, 1),
    "PLN-021": (_single_component_candidate("PLN_HOTSPOT_REDUCTION", -38.44), None, 1),
    "FIN-009": (_single_component_candidate("FIN_DEVICE_CREDIT", -18.00), None, 2),
    "PRO-007": (_single_component_candidate("PRC_COMPETITOR_PRICE_MATCH", -10.00), None, 1),
}


def test_every_pack_rule_id_is_exercised() -> None:
    pack = loader.get_pack(POLICY_PACK_VERSION)
    declared_ids = set(pack.raw["rules"].keys()) | set(pack.raw["prohibited_actions"].keys())

    assert declared_ids == loader.KNOWN_RULE_IDS | loader.KNOWN_PROHIBITED_IDS
    assert declared_ids == set(_COVERAGE_FIXTURES.keys()), (
        "a rule ID was added to the pack without a corresponding coverage fixture"
    )

    for rule_id, (candidate, digest, tier) in _COVERAGE_FIXTURES.items():
        verdict = _evaluate_one(candidate, digest=digest, tier=tier)
        assert rule_id in verdict.governing_rules, (
            f"{rule_id} did not appear in governing_rules for its coverage fixture"
        )
