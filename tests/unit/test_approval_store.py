"""approval/store.py: persistence and immutability of ApprovalDecision.

Not in the phase brief's explicit test list, but approval/store.py is one
of the two modules the BUILD section asks for - basic persistence coverage
matches this repo's convention of every module getting its own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from churnguard.approval import store
from tests.unit.approval_fixtures import make_approval_decision


async def test_persist_then_retrieve_round_trips(tmp_path: Path) -> None:
    db_path = str(tmp_path / "approvals.db")
    decision = make_approval_decision()

    approval_ref = await store.persist_approval_decision(decision, db_path=db_path)
    assert approval_ref.startswith("APR_")

    retrieved = await store.get_approval_decision(approval_ref, db_path=db_path)
    assert retrieved is not None
    assert retrieved.recommendation_set_id == decision.recommendation_set_id
    assert retrieved.decision == decision.decision
    assert retrieved.selected_offer_id == decision.selected_offer_id
    assert retrieved.approver_ref == decision.approver_ref
    assert retrieved.approver_tier == decision.approver_tier
    assert retrieved.disclosures_read == decision.disclosures_read
    assert retrieved.policy_pack_version == decision.policy_pack_version


async def test_unknown_approval_ref_returns_none(tmp_path: Path) -> None:
    db_path = str(tmp_path / "approvals.db")
    result = await store.get_approval_decision("APR_does_not_exist", db_path=db_path)
    assert result is None


async def test_persisting_the_same_approval_ref_twice_is_rejected(tmp_path: Path) -> None:
    """Immutability: there is no update function at all, and a direct
    attempt to reuse an approval_ref is refused rather than silently
    overwriting the original row."""
    db_path = str(tmp_path / "approvals.db")
    decision = make_approval_decision()
    fixed_now = decision.approved_at

    await store.persist_approval_decision(decision, now=fixed_now, db_path=db_path)
    with pytest.raises(store.DuplicateApprovalRefError):
        await store.persist_approval_decision(decision, now=fixed_now, db_path=db_path)
