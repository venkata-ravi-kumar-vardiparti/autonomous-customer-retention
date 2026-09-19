"""Assembles the full AccountContext, the Customer 360 agent's payload.

This is the one repository that composes the others (billing_repo,
catalog_repo, promo_repo) rather than querying tables directly, so an
account_ref is resolved to a raw account_id exactly once per call.
"""

from __future__ import annotations

from datetime import datetime

from churnguard.contracts.customer import (
    AccountContext,
    CustomerContextRequest,
    VerificationResult,
)
from churnguard.data.masking import mask_account_number
from churnguard.data.provenance import RepoResult, make_evidence
from churnguard.data.repositories import billing_repo, catalog_repo, promo_repo
from churnguard.data.repositories._base import fetch_rows, resolve_account_id

SOURCE_CRM = "crm"

# Columns that could proxy for a protected characteristic (age, family/marital
# status via marketing segmentation, geography-linked demographics via a
# ZIP+4 extension) are never selected by any repository query. Listed here so
# AccountContext.excluded_fields can say so explicitly rather than by omission.
EXCLUDED_FIELDS = ["date_of_birth", "zip_plus4", "marketing_segment"]


async def get_account_context(request: CustomerContextRequest) -> RepoResult[AccountContext]:
    account_id = await resolve_account_id(request.account_ref)

    account_rows = await fetch_rows(
        "SELECT tenure_months, data_as_of FROM accounts WHERE account_id = ?",
        (account_id,),
    )
    if not account_rows:
        raise LookupError(f"no account for account_ref {request.account_ref!r}")
    account_row = account_rows[0]

    line_count_rows = await fetch_rows(
        "SELECT COUNT(*) AS line_count FROM account_edges "
        "WHERE from_node = ? AND edge_type = 'has_line'",
        (account_id,),
    )
    line_count = line_count_rows[0]["line_count"]

    billing_result = await billing_repo._get_billing_by_id(account_id)
    payment_history_result = await billing_repo._get_payment_history_by_id(account_id)
    plan_profile_result = await catalog_repo._get_plan_profile_by_id(account_id)
    device_financing_result = await catalog_repo._get_device_financing_by_id(account_id)
    usage_result = await catalog_repo._get_usage_by_line_by_id(account_id)
    promotions_result = await promo_repo._get_active_promotions_by_id(account_id)

    verification_results = [
        VerificationResult(task=task, status="pending_agent_verification", detail=None)
        for task in request.verification_tasks
    ]

    account_evidence = make_evidence(
        evidence_id=f"EVID_ACCT_{account_id[-4:]}",
        source_system=SOURCE_CRM,
        record_ref=f"accounts/{mask_account_number(account_id)}",
        as_of=datetime.fromisoformat(account_row["data_as_of"]),
        masked=True,
    )

    account_context = AccountContext(
        account_ref=mask_account_number(account_id),
        tenure_months=account_row["tenure_months"],
        line_count=line_count,
        billing=billing_result.data,
        payment_history=payment_history_result.data,
        device_financing=device_financing_result.data,
        plan_profile=plan_profile_result.data,
        usage_by_line=usage_result.data,
        active_promotions=promotions_result.data,
        verification_results=verification_results,
        excluded_fields=list(EXCLUDED_FIELDS),
    )

    evidence = [
        account_evidence,
        *billing_result.evidence,
        *payment_history_result.evidence,
        *plan_profile_result.evidence,
        *device_financing_result.evidence,
        *usage_result.evidence,
        *promotions_result.evidence,
    ]
    return RepoResult(data=account_context, evidence=evidence)


__all__ = ["get_account_context"]
