"""execution/service.py: the five mandatory rejection paths, each with its
own negative test (ACCEPTANCE 1), plus a matching happy-path acceptance
test so the negative tests aren't vacuous.

execution/service.py never imports approval/gate.py (see that module's
docstring) - these tests call churnguard.execution.service.execute()
directly with plain contract objects, exactly the way a genuinely separate
execution process would receive them.
"""

from __future__ import annotations

import uuid

import pytest

from churnguard.execution import service
from tests.unit.approval_fixtures import (
    DISCLOSURE_HOTSPOT,
    make_approval_decision,
    make_execution_request,
    make_recommendation,
    make_recommendation_set,
)


@pytest.fixture(autouse=True)
def _isolated_execution_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """execution.service.execute() defaults its db_path from Settings -
    point it at a per-test tmp file so these tests never touch (or share
    state via) a real churnguard_execution.db in the repo root."""
    monkeypatch.setenv("CHURNGUARD_EXECUTION_DB_PATH", str(tmp_path / "execution.db"))


def _unique_key() -> str:
    return f"idem-{uuid.uuid4().hex}"


async def test_a_fully_compliant_execution_is_accepted() -> None:
    recommendation_set = make_recommendation_set()
    approval = make_approval_decision()
    request = make_execution_request(idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "accepted"
    assert result.reasons == []
    assert not result.replayed


# --- 1. Missing or unknown approval_ref ------------------------------------


async def test_rejects_missing_approval() -> None:
    request = make_execution_request(idempotency_key=_unique_key())

    result = await service.execute(request, approval=None, recommendation=None)

    assert result.status == "rejected"
    assert any("unknown or missing approval_ref" in reason for reason in result.reasons)


# --- 2. approver_tier < approval_tier_required -----------------------------


async def test_rejects_insufficient_approver_tier() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1", approval_tier_required=2)]
    )
    approval = make_approval_decision(selected_offer_id="C1", approver_tier=1)
    request = make_execution_request(offer_id="C1", idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "rejected"
    assert any("below the required" in reason for reason in result.reasons)


# --- 3. selected_offer_id differing from the approved one ------------------


async def test_rejects_offer_id_mismatch_between_operation_and_approval() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1")]
    )
    approval = make_approval_decision(selected_offer_id="C1")
    request = make_execution_request(offer_id="C2", idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "rejected"
    assert any("differs from the approved offer" in reason for reason in result.reasons)


# --- 4. commitment_status is not "none" at approval time -------------------


async def test_rejects_when_recommendation_set_commitment_status_is_not_none() -> None:
    recommendation_set = make_recommendation_set(corrupt_commitment_status="committed")
    approval = make_approval_decision()
    request = make_execution_request(idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "rejected"
    assert any("commitment_status" in reason for reason in result.reasons)


# --- 5. a required_disclosure not present in disclosures_read --------------


async def test_rejects_when_a_required_disclosure_was_not_acknowledged() -> None:
    recommendation_set = make_recommendation_set(
        recommendations=[
            make_recommendation(
                offer_id="C2", approval_tier_required=1, required_disclosures=[DISCLOSURE_HOTSPOT]
            )
        ]
    )
    approval = make_approval_decision(selected_offer_id="C2", disclosures_read=[])
    request = make_execution_request(offer_id="C2", idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "rejected"
    assert any("required disclosures not acknowledged" in reason for reason in result.reasons)


# --- additional defense-in-depth guards, beyond the required five ----------


async def test_rejects_when_referenced_approval_is_not_approved() -> None:
    recommendation_set = make_recommendation_set()
    approval = make_approval_decision(decision="rejected", selected_offer_id=None)
    request = make_execution_request(idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "rejected"
    assert any("not 'approved'" in reason for reason in result.reasons)


async def test_rejects_when_recommendation_set_cannot_be_resolved() -> None:
    approval = make_approval_decision()
    request = make_execution_request(idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=None)

    assert result.status == "rejected"
    assert any("could not be resolved" in reason for reason in result.reasons)


async def test_rejects_a_malformed_account_ref() -> None:
    recommendation_set = make_recommendation_set()
    approval = make_approval_decision()
    request = make_execution_request(account_ref="not-a-masked-ref", idempotency_key=_unique_key())

    result = await service.execute(request, approval=approval, recommendation=recommendation_set)

    assert result.status == "rejected"
    assert any("not a validly masked reference" in reason for reason in result.reasons)
