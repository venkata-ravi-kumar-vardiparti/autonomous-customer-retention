"""Persist ApprovalDecision to SQLite — immutable once written.

Owns its own SQLite file (`Settings.approval_db_path`, env
`CHURNGUARD_APPROVAL_DB_PATH`), independent of `churnguard.data` (the
Governed Data Layer's connections are read-only by construction — an
approval writer needs a writable one) and of `churnguard.execution`
(Phase 8's "separate processes, no shared imports" rule: execution/service.py
never imports this module, even to look up an approval — see its
docstring). Same "own file, own schema" pattern as telemetry/audit.py.

Immutability is enforced two ways: there is no update/delete function in
this module's public API at all, and `persist_approval_decision` refuses to
overwrite an existing `approval_ref` row (belt-and-suspenders — a genuine
`approval_ref` collision would require a SHA-256 collision, but a defensive
check costs nothing).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

import aiosqlite

from churnguard.config import load_settings
from churnguard.contracts.approval import ApprovalDecision, OfferEdit

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_ref          TEXT PRIMARY KEY,
    recommendation_set_id TEXT NOT NULL,
    decision              TEXT NOT NULL,
    selected_offer_id     TEXT,
    approver_ref          TEXT NOT NULL,
    approver_tier         INTEGER NOT NULL,
    approved_at           TEXT NOT NULL,
    edits_json            TEXT NOT NULL,
    disclosures_read_json TEXT NOT NULL,
    policy_pack_version   TEXT NOT NULL,
    recorded_at           TEXT NOT NULL
);
"""


class DuplicateApprovalRefError(ValueError):
    """Raised on an attempt to persist over an already-recorded approval_ref."""


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)


def _make_approval_ref(decision: ApprovalDecision, recorded_at: datetime) -> str:
    digest = sha256(
        f"{decision.recommendation_set_id}:{decision.approver_ref}:"
        f"{recorded_at.isoformat()}".encode()
    ).hexdigest()[:16]
    return f"APR_{digest}"


async def persist_approval_decision(
    decision: ApprovalDecision, *, now: datetime | None = None, db_path: str | None = None
) -> str:
    """Returns the generated approval_ref — ApprovalDecision itself carries
    no such id; it is minted here, at the point of persistence, the same
    way orchestration/bounded.py mints recommendation_set_id."""
    resolved_db_path = db_path or load_settings().approval_db_path
    recorded_at = now if now is not None else datetime.now(UTC)
    approval_ref = _make_approval_ref(decision, recorded_at)

    async with aiosqlite.connect(resolved_db_path) as conn:
        await _ensure_schema(conn)
        async with conn.execute(
            "SELECT 1 FROM approvals WHERE approval_ref = ?", (approval_ref,)
        ) as cursor:
            existing = await cursor.fetchone()
        if existing is not None:
            raise DuplicateApprovalRefError(approval_ref)

        await conn.execute(
            """
            INSERT INTO approvals
                (approval_ref, recommendation_set_id, decision, selected_offer_id,
                 approver_ref, approver_tier, approved_at, edits_json,
                 disclosures_read_json, policy_pack_version, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_ref,
                decision.recommendation_set_id,
                decision.decision,
                decision.selected_offer_id,
                decision.approver_ref,
                decision.approver_tier,
                decision.approved_at.isoformat(),
                json.dumps([edit.model_dump(mode="json") for edit in decision.edits]),
                json.dumps(decision.disclosures_read),
                decision.policy_pack_version,
                recorded_at.isoformat(),
            ),
        )
        await conn.commit()

    return approval_ref


async def get_approval_decision(
    approval_ref: str, *, db_path: str | None = None
) -> ApprovalDecision | None:
    """None for an unknown approval_ref — this is what
    execution/service.py's "missing or unknown approval_ref" rejection path
    is checking for, one layer up (the caller resolves this and hands the
    result to execution.service.execute, which never queries this store
    itself)."""
    resolved_db_path = db_path or load_settings().approval_db_path

    async with aiosqlite.connect(resolved_db_path) as conn:
        await _ensure_schema(conn)
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM approvals WHERE approval_ref = ?", (approval_ref,)
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return None

    return ApprovalDecision(
        recommendation_set_id=row["recommendation_set_id"],
        decision=row["decision"],
        selected_offer_id=row["selected_offer_id"],
        approver_ref=row["approver_ref"],
        approver_tier=row["approver_tier"],
        approved_at=datetime.fromisoformat(row["approved_at"]),
        edits=[OfferEdit(**edit) for edit in json.loads(row["edits_json"])],
        disclosures_read=json.loads(row["disclosures_read_json"]),
        policy_pack_version=row["policy_pack_version"],
    )


__all__ = [
    "DuplicateApprovalRefError",
    "get_approval_decision",
    "persist_approval_decision",
]
