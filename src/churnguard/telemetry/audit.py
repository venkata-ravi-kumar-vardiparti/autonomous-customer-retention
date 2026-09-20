"""The audit trail writer: one SQLite table plus a JSONL mirror.

Deliberately independent of `churnguard.data` (Phase 3's brief: "Independent
track - depends only on contracts/"). The Governed Data Layer's SQLite
connection is read-only by construction (data/db.py::get_readonly_connection
opens `file:...?mode=ro`), so an audit writer has no business sharing that
database or that package - it owns its own SQLite file and its own minimal
schema instead.

REDACTION RUNS AT THE WRITER. Every string-shaped field passed in is scrubbed
for bare account-number- and card-PAN-shaped digit runs before it is written
anywhere (SQLite row or JSONL line), using the same digit-run convention as
the Governed Data Layer's leak gate (tests/unit/test_pii_leak.py): a careless
caller cannot bypass this by calling write_audit_record() directly, because
the scrub happens inside it, not before it.

The prompt itself is NEVER stored. write_audit_record() takes the raw prompt
text only to hash it (SHA-256) in memory; the hash is what lands in the
audit record.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import aiosqlite

from churnguard.config import load_settings

_TEN_DIGIT_RUN = re.compile(r"(?<!\d)\d{10}(?!\d)")
_LONG_DIGIT_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
_REDACTED = "[REDACTED]"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id            TEXT PRIMARY KEY,
    trace_id            TEXT NOT NULL,
    occurred_at         TEXT NOT NULL,
    prompt_hash         TEXT NOT NULL,
    evidence_ids        TEXT NOT NULL,
    policy_pack_version TEXT NOT NULL,
    decision            TEXT NOT NULL,
    approver_ref        TEXT NOT NULL
);
"""


def scrub(text: str) -> str:
    """Replace bare 10-digit and 13-19-digit runs (account numbers, card PANs).

    Already-masked refs (ACCT_****4471, CARD_****1234, ...) are untouched:
    they never expose more than 4 trailing digits, well under either
    threshold.
    """
    text = _LONG_DIGIT_RUN.sub(_REDACTED, text)
    text = _TEN_DIGIT_RUN.sub(_REDACTED, text)
    return text


def hash_prompt(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """What actually gets persisted - already redacted, prompt already hashed."""

    audit_id: str
    trace_id: str
    occurred_at: datetime
    prompt_hash: str
    evidence_ids: list[str]
    policy_pack_version: str
    decision: str
    approver_ref: str

    def to_row(self) -> tuple[str, str, str, str, str, str, str, str]:
        return (
            self.audit_id,
            self.trace_id,
            self.occurred_at.isoformat(),
            self.prompt_hash,
            json.dumps(self.evidence_ids),
            self.policy_pack_version,
            self.decision,
            self.approver_ref,
        )

    def to_jsonl(self) -> str:
        payload = asdict(self)
        payload["occurred_at"] = self.occurred_at.isoformat()
        return json.dumps(payload, sort_keys=True)


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)


def _make_audit_id(trace_id: str, occurred_at: datetime) -> str:
    # Deterministic-per-call, not reused: trace_id + timestamp is unique
    # enough for an append-only log and avoids pulling in a uuid dependency
    # just for this.
    return sha256(f"{trace_id}:{occurred_at.isoformat()}".encode()).hexdigest()[:32]


async def write_audit_record(
    *,
    trace_id: str,
    prompt: str,
    evidence_ids: list[str],
    policy_pack_version: str,
    decision: str,
    approver_ref: str,
    now: datetime | None = None,
    db_path: str | None = None,
    jsonl_path: str | None = None,
) -> AuditRecord:
    settings = load_settings()
    resolved_db_path = db_path or settings.audit_db_path
    resolved_jsonl_path = jsonl_path or settings.audit_log_path
    occurred_at = now if now is not None else datetime.now(UTC)

    record = AuditRecord(
        audit_id=_make_audit_id(trace_id, occurred_at),
        trace_id=scrub(trace_id),
        occurred_at=occurred_at,
        prompt_hash=hash_prompt(prompt),
        evidence_ids=[scrub(evidence_id) for evidence_id in evidence_ids],
        policy_pack_version=scrub(policy_pack_version),
        decision=scrub(decision),
        approver_ref=scrub(approver_ref),
    )

    async with aiosqlite.connect(resolved_db_path) as conn:
        await _ensure_schema(conn)
        await conn.execute(
            """
            INSERT INTO audit_log
                (audit_id, trace_id, occurred_at, prompt_hash, evidence_ids,
                 policy_pack_version, decision, approver_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            record.to_row(),
        )
        await conn.commit()

    Path(resolved_jsonl_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(resolved_jsonl_path).open("a", encoding="utf-8") as handle:
        handle.write(record.to_jsonl() + "\n")

    return record


async def read_audit_records(trace_id: str, *, db_path: str | None = None) -> list[AuditRecord]:
    """Test/debug helper: read back every audit row for a trace, oldest first."""
    settings = load_settings()
    resolved_db_path = db_path or settings.audit_db_path

    async with aiosqlite.connect(resolved_db_path) as conn:
        await _ensure_schema(conn)
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT audit_id, trace_id, occurred_at, prompt_hash, evidence_ids,
                   policy_pack_version, decision, approver_ref
            FROM audit_log
            WHERE trace_id = ?
            ORDER BY occurred_at ASC
            """,
            (trace_id,),
        ) as cursor:
            rows = list(await cursor.fetchall())

    return [
        AuditRecord(
            audit_id=row["audit_id"],
            trace_id=row["trace_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            prompt_hash=row["prompt_hash"],
            evidence_ids=json.loads(row["evidence_ids"]),
            policy_pack_version=row["policy_pack_version"],
            decision=row["decision"],
            approver_ref=row["approver_ref"],
        )
        for row in rows
    ]


__all__ = [
    "SCHEMA_SQL",
    "AuditRecord",
    "hash_prompt",
    "read_audit_records",
    "scrub",
    "write_audit_record",
]
