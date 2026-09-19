"""Acceptance criterion 4: malformed account_digest is rejected cleanly, never defaulted.

Extra tests beyond the phase brief's named test files, since this behaviour
deserves focused coverage of its own.
"""

from __future__ import annotations

from typing import Any

import pytest

from churnguard.policy.digest import PolicyDigestError, parse_account_digest

VALID_DIGEST: dict[str, Any] = {
    "current_monthly": 198.43,
    "active_promo_codes": [],
    "financing_active": True,
    "payment_current": True,
}


def test_valid_digest_parses() -> None:
    parsed = parse_account_digest(VALID_DIGEST)
    assert parsed.current_monthly == 198.43
    assert parsed.active_promo_codes == []
    assert parsed.financing_active is True
    assert parsed.payment_current is True


@pytest.mark.parametrize(
    "missing_key", ["current_monthly", "active_promo_codes", "financing_active", "payment_current"]
)
def test_missing_required_key_raises(missing_key: str) -> None:
    digest = {k: v for k, v in VALID_DIGEST.items() if k != missing_key}
    with pytest.raises(PolicyDigestError, match=missing_key):
        parse_account_digest(digest)


def test_wrong_type_for_current_monthly_raises() -> None:
    digest = dict(VALID_DIGEST, current_monthly="not a number")
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_negative_current_monthly_raises() -> None:
    digest = dict(VALID_DIGEST, current_monthly=-5.00)
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_zero_current_monthly_raises() -> None:
    digest = dict(VALID_DIGEST, current_monthly=0)
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_bool_current_monthly_raises() -> None:
    # bool is a subclass of int in Python; must not be accepted as a number.
    digest = dict(VALID_DIGEST, current_monthly=True)
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_non_bool_financing_active_raises() -> None:
    digest = dict(VALID_DIGEST, financing_active="yes")
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_non_bool_payment_current_raises() -> None:
    digest = dict(VALID_DIGEST, payment_current=1)
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_non_list_active_promo_codes_raises() -> None:
    digest = dict(VALID_DIGEST, active_promo_codes="PROMO_LOYALTY12")
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_active_promo_codes_with_non_string_element_raises() -> None:
    digest = dict(VALID_DIGEST, active_promo_codes=[123])
    with pytest.raises(PolicyDigestError):
        parse_account_digest(digest)


def test_non_dict_digest_raises() -> None:
    with pytest.raises(PolicyDigestError):
        parse_account_digest("not a dict")  # type: ignore[arg-type]


def test_multiple_malformed_fields_report_all_errors() -> None:
    digest = dict(VALID_DIGEST, current_monthly="bad", payment_current="bad")
    with pytest.raises(PolicyDigestError) as exc_info:
        parse_account_digest(digest)
    assert "current_monthly" in str(exc_info.value)
    assert "payment_current" in str(exc_info.value)


def test_engine_surfaces_digest_error_for_malformed_request() -> None:
    from churnguard.contracts.offers import CandidateOffer, OfferComponent
    from churnguard.contracts.policy import PolicyEvaluationRequest
    from churnguard.policy import engine

    request = PolicyEvaluationRequest(
        policy_pack_version="2026.09.1",
        jurisdiction="US-TX",
        channel="voice",
        agent_authority_tier=1,
        account_digest={"current_monthly": 198.43},  # missing 3 required keys
        candidate_offers=[
            CandidateOffer(
                candidate_id="C1",
                type="bill_credit",
                components=[OfferComponent(code="RET_X", monthly=-1.0, duration_months=None)],
                total_monthly_impact=-1.0,
            )
        ],
    )
    with pytest.raises(PolicyDigestError):
        engine.evaluate(request)
