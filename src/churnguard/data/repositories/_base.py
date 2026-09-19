"""Shared plumbing for repositories. Not part of the public repository API."""

from __future__ import annotations

import re

import aiosqlite

from churnguard.data.db import get_readonly_connection, run_query

_ACCOUNT_REF_RE = re.compile(r"^ACCT_\*\*\*\*(\d{4})$")


async def fetch_rows(sql: str, params: tuple[object, ...] = ()) -> list[aiosqlite.Row]:
    async with get_readonly_connection() as conn:
        return await run_query(conn, sql, params)


async def resolve_account_id(account_ref: str) -> str:
    """Resolve a masked account_ref (ACCT_****4471) to the raw internal account_id.

    Agents never hold a raw account_id — only the masked ref. Resolution is a
    real read (and pays the same injected latency as any other query).
    """
    match = _ACCOUNT_REF_RE.match(account_ref)
    if not match:
        raise ValueError(f"not a valid masked account_ref: {account_ref!r}")
    last4 = match.group(1)
    rows = await fetch_rows(
        "SELECT account_id FROM accounts WHERE account_id LIKE ?", (f"%{last4}",)
    )
    if len(rows) == 0:
        raise LookupError(f"account_ref {account_ref!r} did not resolve to any account")
    if len(rows) > 1:
        raise LookupError(f"account_ref {account_ref!r} resolved to more than one account")
    account_id: str = rows[0]["account_id"]
    return account_id
