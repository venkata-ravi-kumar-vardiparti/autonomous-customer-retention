"""Billing (current vs. prior, delta attribution) and payment history reads.

The repository never reconciles a mismatch between the stored bill delta
and the sum of its attributed causes — some seeded accounts deliberately
have contradictory billing_events rows, and surfacing that mismatch
untouched is the point: the data layer reports what the legacy system
actually says, it doesn't paper over it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from churnguard.contracts.customer import Billing, DeltaCause, PaymentHistorySummary
from churnguard.data.masking import mask_account_number
from churnguard.data.provenance import RepoResult, make_evidence
from churnguard.data.repositories._base import fetch_rows, resolve_account_id

SOURCE_BILLING = "billing_system"


async def get_billing(account_ref: str) -> RepoResult[Billing]:
    account_id = await resolve_account_id(account_ref)
    return await _get_billing_by_id(account_id)


async def _get_billing_by_id(account_id: str) -> RepoResult[Billing]:
    totals_rows = await fetch_rows(
        """
        SELECT be.event_id, be.bill_period, be.amount,
               (SELECT data_as_of FROM accounts WHERE account_id = ?) AS data_as_of
        FROM account_edges ae
        JOIN billing_events be ON be.event_id = ae.to_node AND ae.edge_type = 'has_billing_event'
        WHERE ae.from_node = ? AND be.kind = 'bill_total'
        """,
        (account_id, account_id),
    )
    totals = {row["bill_period"]: row["amount"] for row in totals_rows}
    if "current" not in totals or "prior" not in totals:
        raise LookupError(f"missing bill totals for account_id {account_id!r}")

    attribution_rows = await fetch_rows(
        """
        SELECT be.event_id, be.cause, be.amount, be.event_date, be.reason, be.reversible
        FROM account_edges ae
        JOIN billing_events be ON be.event_id = ae.to_node AND ae.edge_type = 'has_billing_event'
        WHERE ae.from_node = ? AND be.kind = 'delta_attribution'
        ORDER BY be.event_id
        """,
        (account_id,),
    )

    evidence = [
        make_evidence(
            evidence_id=f"EVID_BILL_TOTAL_{account_id[-4:]}_{row['bill_period']}",
            source_system=SOURCE_BILLING,
            record_ref=f"billing_events/{row['event_id']}",
            as_of=datetime.fromisoformat(row["data_as_of"]),
            masked=False,
        )
        for row in totals_rows
    ]

    delta_attribution: list[DeltaCause] = []
    for row in attribution_rows:
        evidence_id = f"EVID_BILL_{row['event_id']}"
        event_date_value: date | None = (
            date.fromisoformat(row["event_date"]) if row["event_date"] else None
        )
        delta_attribution.append(
            DeltaCause(
                cause=row["cause"],
                amount=row["amount"],
                event_date=event_date_value,
                reason=row["reason"],
                reversible=bool(row["reversible"]),
                evidence_id=evidence_id,
            )
        )
        as_of = (
            datetime.combine(event_date_value, datetime.min.time(), tzinfo=UTC)
            if event_date_value
            else datetime.now(UTC)
        )
        evidence.append(
            make_evidence(
                evidence_id=evidence_id,
                source_system=SOURCE_BILLING,
                record_ref=f"billing_events/{row['event_id']}",
                as_of=as_of,
                masked=False,
            )
        )

    billing = Billing(
        current_bill=totals["current"],
        prior_bill=totals["prior"],
        delta=round(totals["current"] - totals["prior"], 2),
        delta_attribution=delta_attribution,
    )
    return RepoResult(data=billing, evidence=evidence)


async def get_payment_history(account_ref: str) -> RepoResult[PaymentHistorySummary]:
    account_id = await resolve_account_id(account_ref)
    return await _get_payment_history_by_id(account_id)


async def _get_payment_history_by_id(account_id: str) -> RepoResult[PaymentHistorySummary]:
    rows = await fetch_rows(
        """
        SELECT payment_on_time_count, payment_late_count, payment_last_late_date,
               payment_current_past_due, data_as_of
        FROM accounts
        WHERE account_id = ?
        """,
        (account_id,),
    )
    if not rows:
        raise LookupError(f"no account for account_id {account_id!r}")
    row = rows[0]
    summary = PaymentHistorySummary(
        on_time_count=row["payment_on_time_count"],
        late_count=row["payment_late_count"],
        last_late_date=(
            date.fromisoformat(row["payment_last_late_date"])
            if row["payment_last_late_date"]
            else None
        ),
        current_past_due=row["payment_current_past_due"],
    )
    evidence = make_evidence(
        evidence_id=f"EVID_PAYHIST_{account_id[-4:]}",
        source_system=SOURCE_BILLING,
        record_ref=f"accounts/{mask_account_number(account_id)}#payment_history",
        as_of=datetime.fromisoformat(row["data_as_of"]),
        masked=True,
    )
    return RepoResult(data=summary, evidence=[evidence])


__all__ = ["get_billing", "get_payment_history"]
