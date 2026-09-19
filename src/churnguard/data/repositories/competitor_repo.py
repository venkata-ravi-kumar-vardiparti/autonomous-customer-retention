"""Curated competitor price snapshot reads. No live scraping — ever.

normalized_monthly_equivalent (monthly_price / line_count) is plain
arithmetic on the stored figures, not a policy decision; anything involving
switching costs, breakeven, or claim reconciliation is Competitor-agent
territory (Phase 4), not this repository's job.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from churnguard.contracts.competitor import ResolvedCompetitorOffer, SnapshotMeta
from churnguard.data.freshness import age_days
from churnguard.data.provenance import RepoResult, make_evidence
from churnguard.data.repositories._base import fetch_rows

SOURCE_COMPETITOR = "competitor_intel"


async def get_snapshots(
    geography: str,
    carriers: list[str] | None = None,
    max_snapshot_age_days: int | None = None,
) -> RepoResult[list[ResolvedCompetitorOffer]]:
    rows = await fetch_rows(
        """
        SELECT snapshot_id, carrier, plan_name, monthly_price, line_count,
               includes_json, captured_at, capture_method
        FROM competitor_snapshots
        WHERE geography = ?
        ORDER BY snapshot_id
        """,
        (geography,),
    )
    now = datetime.now(UTC)
    offers: list[ResolvedCompetitorOffer] = []
    evidence = []
    for row in rows:
        if carriers is not None and row["carrier"] not in carriers:
            continue
        captured_at = datetime.fromisoformat(row["captured_at"])
        if max_snapshot_age_days is not None and age_days(captured_at, now) > max_snapshot_age_days:
            continue
        offers.append(
            ResolvedCompetitorOffer(
                carrier=row["carrier"],
                plan_name=row["plan_name"],
                monthly_price=row["monthly_price"],
                line_count=row["line_count"],
                includes=json.loads(row["includes_json"]),
                normalized_monthly_equivalent=round(row["monthly_price"] / row["line_count"], 2),
                source_snapshot_id=row["snapshot_id"],
            )
        )
        evidence.append(
            make_evidence(
                evidence_id=f"EVID_COMP_{row['snapshot_id']}",
                source_system=SOURCE_COMPETITOR,
                record_ref=f"competitor_snapshots/{row['snapshot_id']}",
                as_of=captured_at,
                masked=False,
            )
        )
    return RepoResult(data=offers, evidence=evidence)


async def get_snapshot_meta(snapshot_id: str) -> SnapshotMeta:
    """SnapshotMeta is already a self-describing provenance record — no
    companion EvidenceRef needed."""
    rows = await fetch_rows(
        """
        SELECT snapshot_id, geography, capture_method, captured_at
        FROM competitor_snapshots
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    )
    if not rows:
        raise LookupError(f"no competitor snapshot {snapshot_id!r}")
    row = rows[0]
    captured_at = datetime.fromisoformat(row["captured_at"])
    now = datetime.now(UTC)
    return SnapshotMeta(
        as_of=captured_at.date(),
        age_days=age_days(captured_at, now),
        geography=row["geography"],
        capture_method=row["capture_method"],
        snapshot_id=row["snapshot_id"],
    )


__all__ = ["get_snapshot_meta", "get_snapshots"]
