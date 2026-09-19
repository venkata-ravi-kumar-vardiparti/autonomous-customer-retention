"""Acceptance criteria 1 (read-only enforcement) and 4 (reproducible reseed)."""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite
import pytest

from churnguard.data.db import DEFAULT_DB_LATENCY_MS, get_readonly_connection, run_query
from churnguard.data.seed.generate import SEED, compute_content_hash, seed_database


async def test_agents_cannot_write_over_the_readonly_connection(seeded_db: str) -> None:
    async with get_readonly_connection(seeded_db) as conn:
        # A fully-formed row (every NOT NULL column populated) so the only
        # possible failure reason is the read-only mode, not a constraint.
        with pytest.raises(aiosqlite.OperationalError, match="readonly"):
            await conn.execute(
                """
                INSERT INTO accounts (
                    account_id, holder_full_name, autopay_card_expired, tenure_months,
                    status, jurisdiction, contract_type, plan_code, data_as_of, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "9999999999",
                    "Test Testerson",
                    0,
                    1,
                    "active",
                    "US-TX",
                    "month_to_month",
                    "PLAN_UNLIMITED_PLUS",
                    "2026-09-19T00:00:00+00:00",
                    "2026-09-19T00:00:00+00:00",
                ),
            )


def test_readonly_connection_factory_is_not_exported_from_the_package() -> None:
    import churnguard.data as data_pkg

    assert "get_writable_connection_for_migrations" not in data_pkg.__all__
    assert not hasattr(data_pkg, "get_writable_connection_for_migrations")


async def test_reseeding_produces_an_identical_content_hash(tmp_path: Path) -> None:
    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")
    await seed_database(path_a, seed=SEED)
    await seed_database(path_b, seed=SEED)

    hash_a = await compute_content_hash(path_a)
    hash_b = await compute_content_hash(path_b)
    assert hash_a == hash_b


async def test_db_latency_ms_defaults_to_40(
    monkeypatch: pytest.MonkeyPatch, seeded_db: str
) -> None:
    assert DEFAULT_DB_LATENCY_MS == 40
    monkeypatch.delenv("DB_LATENCY_MS", raising=False)
    async with get_readonly_connection(seeded_db) as conn:
        start = time.monotonic()
        await run_query(conn, "SELECT 1")
        elapsed_ms = (time.monotonic() - start) * 1000
    # Windows' default timer resolution (~15ms) means asyncio.sleep(0.04) can
    # return a little early; assert a meaningful delay happened, not an exact one.
    assert elapsed_ms >= DEFAULT_DB_LATENCY_MS * 0.6


async def test_db_latency_ms_is_injected_per_query(
    monkeypatch: pytest.MonkeyPatch, seeded_db: str
) -> None:
    monkeypatch.setenv("DB_LATENCY_MS", "5")
    async with get_readonly_connection(seeded_db) as conn:
        start = time.monotonic()
        await run_query(conn, "SELECT 1")
        elapsed_ms = (time.monotonic() - start) * 1000
    assert elapsed_ms >= 3.0


async def test_db_latency_ms_zero_means_no_sleep(
    monkeypatch: pytest.MonkeyPatch, seeded_db: str
) -> None:
    monkeypatch.setenv("DB_LATENCY_MS", "0")
    async with get_readonly_connection(seeded_db) as conn:
        start = time.monotonic()
        await run_query(conn, "SELECT 1")
        elapsed_ms = (time.monotonic() - start) * 1000
    assert elapsed_ms < 40
