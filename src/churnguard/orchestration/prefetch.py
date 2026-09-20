"""Kick off the competitor snapshot fetch at call connect - as soon as
geography is known, before the transcript has revealed which carrier the
customer means - so it is already resolved (or well underway) by the time
orchestration/bounded.py's fan-out step needs it. Removes a live
get_snapshots round trip from the pipeline's critical path entirely
(roughly 450ms in a deployment where DB_LATENCY_MS and the Competitor
agent's own reasoning latency are real, not simulated) - see
agents/competitor.py, the consumer of this module.

Keyed by geography alone (carrier isn't named yet) and memoized
in-process for a short TTL, so two calls connecting for the same
geography within the window share one fetch rather than duplicating it.
Backed by competitor_repo.get_snapshots - data/cache.py's boot cache
accelerates that call further if loaded; the two mechanisms compose, they
don't compete.

Deliberate simplification: prefetch always fetches every carrier/age for
the geography (max_snapshot_age_days=None) since neither is known yet at
call-connect time. agents/competitor.py filters the prefetched list by
carrier itself; it does NOT re-apply an age cutoff to a prefetch hit
(ResolvedCompetitorOffer carries no captured_at/age field to filter by
without another lookup, which would defeat the point). Every existing
caller's max_snapshot_age_days is generous (90 days) against snapshots
that are at most ~41 days old in the seeded seed data, so this never
changes behaviour in practice; a caller that skips prefetch (a cache miss)
still gets the full, precise live-query filtering.
"""

from __future__ import annotations

import asyncio
import time

from churnguard.contracts.competitor import ResolvedCompetitorOffer
from churnguard.data.repositories import competitor_repo

_PREFETCH_TTL_SECONDS = 30.0

_tasks: dict[str, asyncio.Task[list[ResolvedCompetitorOffer]]] = {}
_started_at: dict[str, float] = {}


async def _fetch(geography: str) -> list[ResolvedCompetitorOffer]:
    result = await competitor_repo.get_snapshots(geography)
    return result.data


def start_prefetch(geography: str) -> asyncio.Task[list[ResolvedCompetitorOffer]]:
    """Idempotent within the TTL - safe to call again defensively (e.g.
    from orchestration/bounded.py) without ever double-fetching for the
    same geography."""
    now = time.monotonic()
    existing = _tasks.get(geography)
    started = _started_at.get(geography)
    if existing is not None and started is not None and now - started < _PREFETCH_TTL_SECONDS:
        return existing
    task = asyncio.ensure_future(_fetch(geography))
    _tasks[geography] = task
    _started_at[geography] = now
    return task


async def get_prefetched(geography: str) -> list[ResolvedCompetitorOffer] | None:
    """None means "nothing prefetched for this geography" - the caller
    should fall back to its own live fetch. A prefetch that itself failed
    is treated the same as "nothing prefetched" rather than re-raised
    here: this is a cache, and a cache miss is never an error - the
    caller's own fallback path (a fresh live call, itself covered by
    orchestration/deadlines.py in orchestration/bounded.py) is what
    actually handles a genuine repository failure."""
    task = _tasks.get(geography)
    if task is None:
        return None
    try:
        return await task
    except Exception:
        return None


def reset_state() -> None:
    """Test-only: clears every in-flight/completed prefetch."""
    _tasks.clear()
    _started_at.clear()


__all__ = ["get_prefetched", "reset_state", "start_prefetch"]
