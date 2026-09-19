"""Provenance stamping for data-layer reads.

Most contract payloads (Billing, PlanProfile, PaymentHistorySummary,
DeviceFinancingLine, AccountContext, ...) carry no source_system/as_of
fields of their own — contracts/ is frozen and stays that way. EvidenceRef
(contracts/envelope.py) is the one type built to carry that shape, and
AgentResult.evidence: list[EvidenceRef] is exactly where it's meant to
land in later phases.

So: every repository method returns a RepoResult[T], pairing the untouched
contract payload with the EvidenceRef(s) for the record(s) it read. Nothing
in contracts/ changes; provenance travels alongside, not inside.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from churnguard.contracts.envelope import EvidenceRef
from churnguard.data.freshness import classify_freshness


@dataclass(frozen=True)
class RepoResult[T]:
    data: T
    evidence: list[EvidenceRef]


def make_evidence(
    *,
    evidence_id: str,
    source_system: str,
    record_ref: str,
    as_of: datetime,
    masked: bool,
    now: datetime | None = None,
) -> EvidenceRef:
    reference_now = now if now is not None else datetime.now(UTC)
    return EvidenceRef(
        evidence_id=evidence_id,
        source_system=source_system,
        record_ref=record_ref,
        as_of=as_of,
        freshness=classify_freshness(as_of, reference_now),
        masked=masked,
    )
