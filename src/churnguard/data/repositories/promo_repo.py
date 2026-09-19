"""Active promotion reads."""

from __future__ import annotations

from datetime import datetime

from churnguard.data.provenance import RepoResult, make_evidence
from churnguard.data.repositories._base import fetch_rows, resolve_account_id

SOURCE_PROMO = "promo_engine"


async def get_active_promotions(account_ref: str) -> RepoResult[list[str]]:
    account_id = await resolve_account_id(account_ref)
    return await _get_active_promotions_by_id(account_id)


async def _get_active_promotions_by_id(account_id: str) -> RepoResult[list[str]]:
    rows = await fetch_rows(
        """
        SELECT pe.elig_id, pr.promo_name,
               (SELECT data_as_of FROM accounts WHERE account_id = ?) AS data_as_of
        FROM account_edges ae
        JOIN promo_eligibility pe
            ON pe.elig_id = ae.to_node AND ae.edge_type = 'has_promo_eligibility'
        JOIN promotions pr ON pr.promo_code = pe.promo_code
        WHERE ae.from_node = ? AND pe.status = 'active'
        ORDER BY pe.elig_id
        """,
        (account_id, account_id),
    )
    names = [row["promo_name"] for row in rows]
    evidence = [
        make_evidence(
            evidence_id=f"EVID_PROMO_{row['elig_id']}",
            source_system=SOURCE_PROMO,
            record_ref=f"promo_eligibility/{row['elig_id']}",
            as_of=datetime.fromisoformat(row["data_as_of"]),
            masked=False,
        )
        for row in rows
    ]
    return RepoResult(data=names, evidence=evidence)


__all__ = ["get_active_promotions"]
