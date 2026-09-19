"""Plan profile, device financing and usage-by-line reads."""

from __future__ import annotations

from datetime import UTC, datetime

from churnguard.contracts.customer import DeviceFinancingLine, PlanProfile
from churnguard.data.masking import mask_account_number, mask_line_ref
from churnguard.data.provenance import RepoResult, make_evidence
from churnguard.data.repositories._base import fetch_rows, resolve_account_id

SOURCE_CRM = "crm"
SOURCE_DEVICE_LEDGER = "device_financing_ledger"
SOURCE_USAGE = "usage_mediation"


async def get_plan_profile(account_ref: str) -> RepoResult[PlanProfile]:
    account_id = await resolve_account_id(account_ref)
    return await _get_plan_profile_by_id(account_id)


async def _get_plan_profile_by_id(account_id: str) -> RepoResult[PlanProfile]:
    rows = await fetch_rows(
        """
        SELECT p.plan_code, p.plan_name, a.contract_type, a.contract_end_date, a.data_as_of
        FROM accounts a JOIN plans p ON p.plan_code = a.plan_code
        WHERE a.account_id = ?
        """,
        (account_id,),
    )
    if not rows:
        raise LookupError(f"no plan profile for account_id {account_id!r}")
    row = rows[0]
    as_of = datetime.fromisoformat(row["data_as_of"])
    profile = PlanProfile(
        plan_code=row["plan_code"],
        plan_name=row["plan_name"],
        contract_type=row["contract_type"],
        contract_end_date=row["contract_end_date"],
    )
    evidence = make_evidence(
        evidence_id=f"EVID_PLAN_{account_id[-4:]}",
        source_system=SOURCE_CRM,
        record_ref=f"plans/{row['plan_code']}",
        as_of=as_of,
        masked=False,
    )
    return RepoResult(data=profile, evidence=[evidence])


async def get_device_financing(account_ref: str) -> RepoResult[list[DeviceFinancingLine]]:
    account_id = await resolve_account_id(account_ref)
    return await _get_device_financing_by_id(account_id)


async def _get_device_financing_by_id(account_id: str) -> RepoResult[list[DeviceFinancingLine]]:
    rows = await fetch_rows(
        """
        SELECT l.line_index, df.device_fin_id, df.device_name, df.remaining_balance,
               df.monthly_payment, df.months_remaining, df.early_termination_fee,
               (SELECT data_as_of FROM accounts WHERE account_id = ?) AS data_as_of
        FROM account_edges ae_line
        JOIN lines l ON l.line_id = ae_line.to_node AND ae_line.edge_type = 'has_line'
        JOIN account_edges ae_fin
            ON ae_fin.from_node = l.line_id AND ae_fin.edge_type = 'financed_by'
        JOIN device_financing df ON df.device_fin_id = ae_fin.to_node
        WHERE ae_line.from_node = ?
        ORDER BY l.line_index
        """,
        (account_id, account_id),
    )
    lines: list[DeviceFinancingLine] = []
    evidence = []
    for row in rows:
        as_of = datetime.fromisoformat(row["data_as_of"])
        lines.append(
            DeviceFinancingLine(
                line_ref=mask_line_ref(row["line_index"]),
                device=row["device_name"],
                remaining_balance=row["remaining_balance"],
                monthly_payment=row["monthly_payment"],
                months_remaining=row["months_remaining"],
                early_termination_fee=row["early_termination_fee"],
            )
        )
        evidence.append(
            make_evidence(
                evidence_id=f"EVID_DEVFIN_{row['device_fin_id']}",
                source_system=SOURCE_DEVICE_LEDGER,
                record_ref=f"device_financing/{row['device_fin_id']}",
                as_of=as_of,
                masked=False,
            )
        )
    return RepoResult(data=lines, evidence=evidence)


async def get_usage_by_line(account_ref: str) -> RepoResult[dict[str, str | None]]:
    account_id = await resolve_account_id(account_ref)
    return await _get_usage_by_line_by_id(account_id)


async def _get_usage_by_line_by_id(account_id: str) -> RepoResult[dict[str, str | None]]:
    rows = await fetch_rows(
        """
        SELECT l.line_index, u.usage_level,
               (SELECT data_as_of FROM accounts WHERE account_id = ?) AS data_as_of
        FROM account_edges ae
        JOIN lines l ON l.line_id = ae.to_node AND ae.edge_type = 'has_line'
        LEFT JOIN (
            SELECT line_id, usage_level, usage_date,
                   ROW_NUMBER() OVER (PARTITION BY line_id ORDER BY usage_date DESC) AS rn
            FROM usage_daily
        ) u ON u.line_id = l.line_id AND u.rn = 1
        WHERE ae.from_node = ?
        ORDER BY l.line_index
        """,
        (account_id, account_id),
    )
    usage_by_line: dict[str, str | None] = {}
    evidence = []
    now = datetime.now(UTC)
    for row in rows:
        line_ref = mask_line_ref(row["line_index"])
        usage_by_line[line_ref] = row["usage_level"]
        as_of = datetime.fromisoformat(row["data_as_of"]) if row["data_as_of"] else now
        evidence.append(
            make_evidence(
                evidence_id=f"EVID_USAGE_{account_id[-4:]}_{row['line_index']}",
                source_system=SOURCE_USAGE,
                record_ref=f"usage_daily/{mask_account_number(account_id)}/{row['line_index']}",
                as_of=as_of,
                masked=True,
            )
        )
    return RepoResult(data=usage_by_line, evidence=evidence)


__all__ = ["get_device_financing", "get_plan_profile", "get_usage_by_line"]
