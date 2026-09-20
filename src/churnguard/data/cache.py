"""In-memory boot cache for small, boot-stable reference data: plans,
promotions, competitor snapshots. Loaded once via SQLite's own backup API
(sqlite3.Connection.backup - a byte-faithful copy of the on-disk database
into an in-memory connection, not a hand-rolled row-by-row copy), then read
out into plain dict rows for zero-I/O, zero-DB_LATENCY_MS in-process
lookups.

Why these three domains and not the whole database: they're small,
boot-stable "menu" tables read on every single call, unlike
accounts/billing_events/usage_daily/... which are per-customer,
effectively unbounded, and (in a real deployment) live/mutating - those
stay on the read-only live path (data/db.py::get_readonly_connection)
exactly as before this module existed; this cache doesn't touch them.

Measurable win: competitor_repo.get_snapshots/get_snapshot_meta go from
two DB_LATENCY_MS-bearing round trips to zero once this cache is loaded -
see that module and orchestration/prefetch.py, which composes with this
(prefetch still helps when the cache ISN'T loaded, by overlapping a live
fetch with other pipeline work; the cache makes that fetch instant instead
of merely overlapped).

catalog_repo.get_plan_profile and promo_repo.get_active_promotions are
deliberately NOT rewired to consume this cache: both already resolve an
account-specific join (accounts JOIN plans / account_edges JOIN
promo_eligibility JOIN promotions) in a single query, so splitting out only
the reference-table half wouldn't remove a round trip - it would add a
second query for zero latency benefit. Their cache accessors
(get_cached_plan_row, get_cached_promotion_rows) are still provided, since
"catalog, promos... cached at boot" is the phase brief's literal ask and
this is genuinely boot-cached data available for a future account-aware
caching pass - nothing calls those two accessors yet, and that's an
honest, documented boundary, not an oversight.

Every function here is inert (is_loaded() is False, accessors return
nothing) until load_cache() has actually run - a process that never calls
it (every pre-Phase-10 test, and every test that doesn't opt in) behaves
exactly as it always has.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from churnguard.data.db import resolve_db_path


@dataclass
class _CacheState:
    loaded: bool = False
    plans_by_code: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions_by_code: dict[str, dict[str, Any]] = field(default_factory=dict)
    competitor_snapshot_rows: list[dict[str, Any]] = field(default_factory=list)
    competitor_snapshot_rows_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)


_STATE = _CacheState()


def load_cache(db_path: str | None = None) -> None:
    """Synchronous by design: sqlite3's backup API is itself synchronous,
    and this is meant to run once, before the event loop starts serving
    requests (or explicitly, in a test/benchmark setup)."""
    source = sqlite3.connect(db_path or resolve_db_path())
    try:
        memory_conn = sqlite3.connect(":memory:")
        try:
            source.backup(memory_conn)
            memory_conn.row_factory = sqlite3.Row

            plans_by_code = {
                row["plan_code"]: dict(row) for row in memory_conn.execute("SELECT * FROM plans")
            }
            promotions_by_code = {
                row["promo_code"]: dict(row)
                for row in memory_conn.execute("SELECT * FROM promotions")
            }
            # ORDER BY snapshot_id baked in at load time - matching
            # competitor_repo.get_snapshots' live-query ordering exactly,
            # since agents/competitor.py's tie-break (min() on
            # normalized_monthly_equivalent) depends on that ordering to
            # pick the same snapshot whether the cache is loaded or not.
            snapshot_rows = [
                dict(row)
                for row in memory_conn.execute(
                    "SELECT * FROM competitor_snapshots ORDER BY snapshot_id"
                )
            ]
        finally:
            memory_conn.close()
    finally:
        source.close()

    _STATE.plans_by_code = plans_by_code
    _STATE.promotions_by_code = promotions_by_code
    _STATE.competitor_snapshot_rows = snapshot_rows
    _STATE.competitor_snapshot_rows_by_id = {row["snapshot_id"]: row for row in snapshot_rows}
    _STATE.loaded = True


def is_loaded() -> bool:
    return _STATE.loaded


def get_cached_plan_row(plan_code: str) -> dict[str, Any] | None:
    return _STATE.plans_by_code.get(plan_code)


def get_cached_promotion_rows() -> list[dict[str, Any]]:
    return list(_STATE.promotions_by_code.values())


def get_cached_competitor_snapshot_rows(geography: str) -> list[dict[str, Any]]:
    return [row for row in _STATE.competitor_snapshot_rows if row["geography"] == geography]


def get_cached_competitor_snapshot_row(snapshot_id: str) -> dict[str, Any] | None:
    return _STATE.competitor_snapshot_rows_by_id.get(snapshot_id)


def reset_state() -> None:
    """Test-only: clears the cache back to an unloaded state, so one
    test's load_cache() call can never leak into another test."""
    global _STATE
    _STATE = _CacheState()


__all__ = [
    "get_cached_competitor_snapshot_row",
    "get_cached_competitor_snapshot_rows",
    "get_cached_plan_row",
    "get_cached_promotion_rows",
    "is_loaded",
    "load_cache",
    "reset_state",
]
