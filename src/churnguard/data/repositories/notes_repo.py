"""Care-agent account note reads.

Seed note text is synthetic and PII-free by construction — see
data/seed/generate.py. There is no free-text redaction step here; don't
feed real customer-typed notes through this repository unmodified in a
later phase without adding one.
"""

from __future__ import annotations

from datetime import datetime

from churnguard.data.provenance import RepoResult, make_evidence
from churnguard.data.repositories._base import fetch_rows, resolve_account_id

SOURCE_NOTES = "care_notes"


async def get_notes(account_ref: str) -> RepoResult[list[str]]:
    account_id = await resolve_account_id(account_ref)
    return await _get_notes_by_id(account_id)


async def _get_notes_by_id(account_id: str) -> RepoResult[list[str]]:
    rows = await fetch_rows(
        """
        SELECT n.note_id, n.note_text, n.created_at
        FROM account_edges ae
        JOIN account_notes n ON n.note_id = ae.to_node AND ae.edge_type = 'has_note'
        WHERE ae.from_node = ?
        ORDER BY n.created_at
        """,
        (account_id,),
    )
    notes = [row["note_text"] for row in rows]
    evidence = [
        make_evidence(
            evidence_id=f"EVID_NOTE_{row['note_id']}",
            source_system=SOURCE_NOTES,
            record_ref=f"account_notes/{row['note_id']}",
            as_of=datetime.fromisoformat(row["created_at"]),
            masked=False,
        )
        for row in rows
    ]
    return RepoResult(data=notes, evidence=evidence)


__all__ = ["get_notes"]
