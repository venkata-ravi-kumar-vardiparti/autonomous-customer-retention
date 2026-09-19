"""aiosqlite connection factory.

Agents (and repositories) get ONLY a read-only URI connection
(`file:<path>?mode=ro`). SQLite enforces this at the OS/VFS level: any
write attempted over such a connection raises an OperationalError, not a
convention we have to trust callers to respect.

The writable connection factory exists solely for schema migration and
seeding (`data/seed/generate.py`) and is deliberately NOT re-exported from
`churnguard.data` — see data/__init__.py's __all__.
"""

from __future__ import annotations

import asyncio
import os

import aiosqlite

from churnguard.config import load_settings

DEFAULT_DB_LATENCY_MS = 40


def resolve_db_path() -> str:
    return load_settings().database_path


def get_readonly_connection(db_path: str | None = None) -> aiosqlite.Connection:
    """A read-only connection. Any write against it raises OperationalError."""
    path = db_path or resolve_db_path()
    uri = f"file:{path}?mode=ro"
    return aiosqlite.connect(uri, uri=True)


def get_writable_connection_for_migrations(db_path: str | None = None) -> aiosqlite.Connection:
    """Writable connection. Seed/migration code only — never import this into an agent path."""
    path = db_path or resolve_db_path()
    return aiosqlite.connect(path)


async def run_query(
    conn: aiosqlite.Connection, sql: str, params: tuple[object, ...] = ()
) -> list[aiosqlite.Row]:
    """Execute a query with the artificial per-query latency the phase brief asks for."""
    latency_ms = int(os.environ.get("DB_LATENCY_MS", str(DEFAULT_DB_LATENCY_MS)))
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000)
    conn.row_factory = aiosqlite.Row
    async with conn.execute(sql, params) as cursor:
        return list(await cursor.fetchall())


__all__ = ["DEFAULT_DB_LATENCY_MS", "get_readonly_connection", "resolve_db_path", "run_query"]
