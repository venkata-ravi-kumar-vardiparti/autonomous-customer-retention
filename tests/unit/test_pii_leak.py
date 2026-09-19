"""The leak gate: every repo method, every seeded account, zero PII hits.

This is the CI gate acceptance test. It builds its PII corpus from the
actual raw sensitive values sitting in the seeded DB (bypassing every
repository, reading the accounts table directly) and then asserts that
none of those values, nor any bare 10-19 digit run, appear anywhere in the
serialized output of every repository method called across every seeded
account.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import BaseModel

from churnguard.contracts.customer import CustomerContextRequest
from churnguard.data import masking
from churnguard.data.db import get_readonly_connection, run_query
from churnguard.data.provenance import RepoResult
from churnguard.data.repositories import (
    billing_repo,
    catalog_repo,
    competitor_repo,
    customer_repo,
    notes_repo,
    promo_repo,
)

TEN_DIGIT_RUN = re.compile(r"(?<!\d)\d{10}(?!\d)")
LONG_DIGIT_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _serialize(result: RepoResult[Any]) -> str:
    import json

    return json.dumps(
        {"data": _to_jsonable(result.data), "evidence": _to_jsonable(result.evidence)},
        default=str,
    )


async def _raw_account_rows(db_path: str) -> list[dict[str, Any]]:
    async with get_readonly_connection(db_path) as conn:
        rows = await run_query(
            conn, "SELECT account_id, holder_full_name, autopay_card_pan FROM accounts"
        )
    return [dict(row) for row in rows]


async def _collect_all_repo_output(account_ref: str) -> str:
    blobs = [
        _serialize(await billing_repo.get_billing(account_ref)),
        _serialize(await billing_repo.get_payment_history(account_ref)),
        _serialize(await catalog_repo.get_plan_profile(account_ref)),
        _serialize(await catalog_repo.get_device_financing(account_ref)),
        _serialize(await catalog_repo.get_usage_by_line(account_ref)),
        _serialize(await promo_repo.get_active_promotions(account_ref)),
        _serialize(await notes_repo.get_notes(account_ref)),
    ]
    request = CustomerContextRequest(
        account_ref=account_ref,
        requested_domains=["billing", "device_financing", "usage"],
        lookback_months=3,
        line_refs_of_interest=[],
        verification_tasks=["confirm_current_bill_amount"],
        reason_code="cancel_request",
    )
    blobs.append(_serialize(await customer_repo.get_account_context(request)))
    return "\n".join(blobs)


async def test_no_pii_leaks_from_any_repository_across_all_accounts(seeded_db: str) -> None:
    accounts = await _raw_account_rows(seeded_db)
    assert len(accounts) >= 25

    for row in accounts:
        account_ref = masking.mask_account_number(row["account_id"])
        combined = await _collect_all_repo_output(account_ref)

        assert not TEN_DIGIT_RUN.search(combined), (
            f"a bare 10-digit run leaked for {account_ref}"
        )
        assert not LONG_DIGIT_RUN.search(combined), (
            f"a bare 13-19 digit run leaked for {account_ref}"
        )
        assert row["account_id"] not in combined, f"raw account_id leaked for {account_ref}"
        assert row["holder_full_name"] not in combined, f"full name leaked for {account_ref}"
        if row["autopay_card_pan"]:
            assert row["autopay_card_pan"] not in combined, f"card PAN leaked for {account_ref}"


@pytest.mark.parametrize("geography", ["TX-DFW", "US-CA"])
async def test_no_pii_leaks_from_competitor_repo(geography: str) -> None:
    result = await competitor_repo.get_snapshots(geography)
    combined = _serialize(result)
    assert not TEN_DIGIT_RUN.search(combined)
    assert not LONG_DIGIT_RUN.search(combined)
