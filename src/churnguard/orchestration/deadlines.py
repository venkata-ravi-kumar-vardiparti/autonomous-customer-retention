"""Per-agent deadline enforcement via asyncio cancellation.

agents/base.py::run_agent already enforces RunContext.deadline_ms
internally (an asyncio.wait_for around the whole retry-attempt loop),
converting an overrun into DeadlineExceededError - but that only handles
ONE failure mode: running too long. It does nothing about a completely
different one: a repository connection dying mid-call, a malformed row, or
any other exception a tool or the SDK itself might raise. From
orchestration/bounded.py's point of view those two failure modes need the
exact same response - fall back to a degraded contribution from that one
agent, never let it take down the whole RecommendationSet - so this module
gives them one shared entry point instead of two different try/except
shapes scattered through the pipeline.

run_with_deadline() never raises. It always returns a DeadlineOutcome,
whether the coroutine finished, was cancelled for running past its
deadline, or raised. This is what makes a hung repository call and a
thrown exception collapse into the same handling code in
orchestration/bounded.py: check `.completed`, use `.value` or fall back.

Genuine cancellation, not detachment: asyncio.wait_for cancels its wrapped
coroutine on timeout (standard library behaviour) - the slow branch
actually stops running; it is not left orphaned in the background.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeadlineOutcome[T]:
    """Exactly one of `timed_out` / `error is not None` / `completed` is
    the true story of what happened - never partially both."""

    completed: bool
    value: T | None
    elapsed_ms: int
    timed_out: bool
    error: BaseException | None

    @property
    def failed(self) -> bool:
        return not self.completed


async def run_with_deadline[T](coro: Awaitable[T], *, deadline_ms: int) -> DeadlineOutcome[T]:
    start = time.perf_counter()
    try:
        value = await asyncio.wait_for(coro, timeout=deadline_ms / 1000)
    except TimeoutError:
        return DeadlineOutcome(
            completed=False,
            value=None,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            timed_out=True,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
        return DeadlineOutcome(
            completed=False,
            value=None,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            timed_out=False,
            error=exc,
        )
    return DeadlineOutcome(
        completed=True,
        value=value,
        elapsed_ms=int((time.perf_counter() - start) * 1000),
        timed_out=False,
        error=None,
    )


async def gather_with_deadlines(
    tasks: dict[str, tuple[Awaitable[Any], int]],
) -> dict[str, DeadlineOutcome[Any]]:
    """Runs every (coroutine, deadline_ms) pair concurrently. Never raises:
    run_with_deadline() already catches everything per-item, so this
    asyncio.gather can never see a child exception to propagate."""
    keys = list(tasks.keys())
    outcomes = await asyncio.gather(
        *(run_with_deadline(coro, deadline_ms=deadline_ms) for coro, deadline_ms in tasks.values())
    )
    return dict(zip(keys, outcomes, strict=True))


__all__ = ["DeadlineOutcome", "gather_with_deadlines", "run_with_deadline"]
