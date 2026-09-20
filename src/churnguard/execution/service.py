"""The execution boundary: consumes ExecutionRequest and, entirely on its
own, enforces every condition that must hold before ChurnGuard's
recommendation becomes a real committed change.

STRUCTURAL ISOLATION — Phase 8's actual objective ("agents cannot commit",
enforced rather than documented): this module imports ONLY
`churnguard.contracts` and `churnguard.data` (for a masked-ref shape check
— `data.masking` is pure, no I/O), plus stdlib/aiosqlite. It never imports
`churnguard.agents`, `churnguard.orchestration`, `churnguard.offers`, or
even `churnguard.approval`. `tests/architecture/test_no_imports.py` checks
this both directions (execution/ cannot import those packages; those
packages cannot import execution/) by walking the AST at CI time — a build
gate, not a convention someone has to remember.

This means the validation logic here necessarily DUPLICATES what
approval/gate.py already checked at approval time. That duplication is
deliberate, not an oversight: this module never trusts that "someone
already checked" — it re-derives every guarantee from the plain
`ExecutionRequest` / `ApprovalDecision` / `RecommendationSet` objects it is
handed, so a caller that somehow reached this function without going
through the approval API (see tests/architecture/test_no_imports.py's
rogue-tool test) still cannot make it accept a bad request.

`approval` and `recommendation` are explicit parameters, not looked up by
this module — a real separate execution service receives a fully-resolved
authorization payload from whatever called it (here, api/routes.py, which
IS allowed to import approval/ and looks the two objects up itself); it
does not reach back into another service's private storage. `None` means
"the caller could not resolve one" — this is exactly how "missing or
unknown approval_ref" surfaces as a rejection.

`ExecutionOperation.params` (contracts/approval.py, deliberately loose —
flagged in CLAUDE.md's Phase 0 notes as Phase 8/9's to define) is expected
to carry an `"offer_id"` key naming which recommendation an operation
executes: `ExecutionRequest` itself has no offer-id field of its own, so
this is what makes "selected offer differs from the approved one" checkable
at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import aiosqlite

from churnguard.config import load_settings
from churnguard.contracts.approval import ApprovalDecision, ExecutionRequest
from churnguard.contracts.recommendation import RecommendationSet
from churnguard.data.masking import is_masked_account_number

ExecutionStatus = Literal["accepted", "rejected"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_ledger (
    idempotency_key TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    approval_ref    TEXT,
    reasons_json    TEXT NOT NULL,
    recorded_at     TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ExecutionResult:
    """Not a contracts/ payload — execution/ never hands anything back
    across the approval boundary; this is the return value of the plain
    Python function below."""

    status: ExecutionStatus
    idempotency_key: str
    approval_ref: str | None
    reasons: list[str]
    replayed: bool = False


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)


def _row_to_result(row: aiosqlite.Row) -> ExecutionResult:
    return ExecutionResult(
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        approval_ref=row["approval_ref"],
        reasons=json.loads(row["reasons_json"]),
        replayed=True,
    )


async def _load_replay(
    conn: aiosqlite.Connection, idempotency_key: str
) -> ExecutionResult | None:
    conn.row_factory = aiosqlite.Row
    async with conn.execute(
        "SELECT * FROM execution_ledger WHERE idempotency_key = ?", (idempotency_key,)
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_result(row) if row is not None else None


async def _store_result(conn: aiosqlite.Connection, result: ExecutionResult) -> None:
    await conn.execute(
        """
        INSERT INTO execution_ledger
            (idempotency_key, status, approval_ref, reasons_json, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            result.idempotency_key,
            result.status,
            result.approval_ref,
            json.dumps(result.reasons),
            datetime.now(UTC).isoformat(),
        ),
    )
    await conn.commit()


def _validate(
    request: ExecutionRequest,
    approval: ApprovalDecision | None,
    recommendation: RecommendationSet | None,
) -> list[str]:
    reasons: list[str] = []

    if not is_masked_account_number(request.account_ref):
        reasons.append(f"account_ref {request.account_ref!r} is not a validly masked reference")

    if approval is None:
        reasons.append(f"unknown or missing approval_ref: {request.approval_ref!r}")
        return reasons

    if approval.decision != "approved":
        reasons.append(
            f"referenced approval decision is {approval.decision!r}, not 'approved' - "
            "nothing to execute"
        )
        return reasons

    if recommendation is None:
        reasons.append("recommendation_set for this approval could not be resolved")
        return reasons

    if recommendation.commitment_status != "none":
        reasons.append(
            "recommendation_set commitment_status is "
            f"{recommendation.commitment_status!r}, expected 'none'"
        )

    matching = next(
        (r for r in recommendation.recommendations if r.offer_id == approval.selected_offer_id),
        None,
    )
    if matching is None:
        reasons.append(
            f"approval.selected_offer_id {approval.selected_offer_id!r} is not an eligible "
            "recommendation in this recommendation_set"
        )
    else:
        required_tier = matching.approval_tier_required or 0
        if approval.approver_tier < required_tier:
            reasons.append(
                f"approver_tier {approval.approver_tier} is below the required {required_tier}"
            )
        missing_disclosures = [
            d.code
            for d in matching.required_disclosures
            if d.code not in approval.disclosures_read
        ]
        if missing_disclosures:
            reasons.append(f"required disclosures not acknowledged: {missing_disclosures}")

    for operation in request.operations:
        op_offer_id = operation.params.get("offer_id")
        if op_offer_id is not None and op_offer_id != approval.selected_offer_id:
            reasons.append(
                f"operation {operation.op_code!r} targets offer_id {op_offer_id!r}, which "
                f"differs from the approved offer {approval.selected_offer_id!r}"
            )

    return reasons


async def execute(
    request: ExecutionRequest,
    *,
    approval: ApprovalDecision | None,
    recommendation: RecommendationSet | None,
    db_path: str | None = None,
) -> ExecutionResult:
    """The one entry point. Idempotent: replaying the same idempotency_key
    always returns the ORIGINAL result — never re-validates, never
    re-applies — "never a double credit", even if the second call's
    request/approval/recommendation arguments differ from the first."""
    resolved_db_path = db_path or load_settings().execution_db_path

    async with aiosqlite.connect(resolved_db_path) as conn:
        await _ensure_schema(conn)
        replay = await _load_replay(conn, request.idempotency_key)
        if replay is not None:
            return replay

        reasons = _validate(request, approval, recommendation)
        result = ExecutionResult(
            status="accepted" if not reasons else "rejected",
            idempotency_key=request.idempotency_key,
            approval_ref=request.approval_ref,
            reasons=reasons,
            replayed=False,
        )
        await _store_result(conn, result)
        return result


__all__ = ["ExecutionResult", "ExecutionStatus", "execute"]
