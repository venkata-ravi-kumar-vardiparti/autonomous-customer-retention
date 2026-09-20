"""ACCEPTANCE 2: replaying the same idempotency_key is a no-op that returns
the ORIGINAL result - never re-validated, never re-applied, never a double
credit - even if the second call's inputs differ from the first.
"""

from __future__ import annotations

import uuid

import pytest

from churnguard.execution import service
from tests.unit.approval_fixtures import (
    make_approval_decision,
    make_execution_request,
    make_recommendation,
    make_recommendation_set,
)


@pytest.fixture(autouse=True)
def _isolated_execution_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same isolation as test_execution_rejections.py - and critically, the
    SAME env var value stays in effect for every service.execute() call
    within one test, which is what lets the idempotency replay tests below
    hit the same ledger file across multiple calls."""
    monkeypatch.setenv("CHURNGUARD_EXECUTION_DB_PATH", str(tmp_path / "execution.db"))


async def test_replaying_an_accepted_idempotency_key_returns_the_original_result() -> None:
    key = f"idem-{uuid.uuid4().hex}"
    recommendation_set = make_recommendation_set()
    approval = make_approval_decision()
    request = make_execution_request(idempotency_key=key)

    first = await service.execute(request, approval=approval, recommendation=recommendation_set)
    assert first.status == "accepted"
    assert not first.replayed

    second = await service.execute(request, approval=approval, recommendation=recommendation_set)
    assert second.status == "accepted"
    assert second.replayed
    assert second.reasons == first.reasons
    assert second.idempotency_key == first.idempotency_key
    assert second.approval_ref == first.approval_ref


async def test_replaying_a_rejected_idempotency_key_returns_the_original_rejection() -> None:
    key = f"idem-{uuid.uuid4().hex}"
    request = make_execution_request(idempotency_key=key)

    first = await service.execute(request, approval=None, recommendation=None)
    assert first.status == "rejected"

    second = await service.execute(request, approval=None, recommendation=None)
    assert second.status == "rejected"
    assert second.replayed
    assert second.reasons == first.reasons


async def test_replay_never_double_applies_even_with_a_different_second_request() -> None:
    """The defining property: a second call with the SAME idempotency_key
    but a materially different (and, on its own, invalid) request/approval
    still returns the first call's result untouched - it is never
    re-validated against the new inputs, let alone re-applied."""
    key = f"idem-{uuid.uuid4().hex}"
    recommendation_set = make_recommendation_set(
        recommendations=[make_recommendation(offer_id="C1")]
    )
    approval = make_approval_decision(selected_offer_id="C1")
    first_request = make_execution_request(offer_id="C1", idempotency_key=key)

    first = await service.execute(
        first_request, approval=approval, recommendation=recommendation_set
    )
    assert first.status == "accepted"

    # Same idempotency_key, but this second attempt is deliberately invalid
    # on its own (mismatched offer, no approval) - a real double-spend shape.
    second_request = make_execution_request(offer_id="C9", idempotency_key=key)
    second = await service.execute(second_request, approval=None, recommendation=None)

    assert second.replayed
    assert second.status == first.status == "accepted"
    assert second.reasons == first.reasons == []


async def test_different_idempotency_keys_are_independent() -> None:
    recommendation_set = make_recommendation_set()
    approval = make_approval_decision()

    first_key = f"idem-{uuid.uuid4().hex}"
    second_key = f"idem-{uuid.uuid4().hex}"

    first = await service.execute(
        make_execution_request(idempotency_key=first_key),
        approval=approval,
        recommendation=recommendation_set,
    )
    second = await service.execute(
        make_execution_request(idempotency_key=second_key),
        approval=approval,
        recommendation=recommendation_set,
    )

    assert not first.replayed
    assert not second.replayed
    assert first.idempotency_key != second.idempotency_key
