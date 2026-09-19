"""freshness.py boundary tests, exactly at 29/30/31 days, plus the seeded snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from churnguard.data.freshness import age_days, classify_freshness
from churnguard.data.repositories import competitor_repo
from churnguard.data.seed.generate import SEED_REFERENCE_NOW

NOW = datetime(2026, 9, 19, tzinfo=UTC)


@pytest.mark.parametrize(
    ("days_old", "expected"),
    [
        (29, "fresh"),
        (30, "aging"),
        (31, "aging"),
    ],
)
def test_freshness_boundary(days_old: int, expected: str) -> None:
    as_of = NOW - timedelta(days=days_old)
    assert classify_freshness(as_of, NOW) == expected


def test_age_days_never_negative_for_future_as_of() -> None:
    as_of = NOW + timedelta(days=5)
    assert age_days(as_of, NOW) == 0


def test_zero_days_old_is_fresh() -> None:
    assert classify_freshness(NOW, NOW) == "fresh"


def test_far_past_is_stale() -> None:
    as_of = NOW - timedelta(days=365)
    assert classify_freshness(as_of, NOW) == "stale"


async def test_seeded_tx_dfw_has_a_stale_and_a_fresh_snapshot() -> None:
    """Classified relative to SEED_REFERENCE_NOW (the fixed clock the seed data was
    built around), not real wall-clock time, so this stays deterministic regardless
    of when the suite actually runs.
    """
    result = await competitor_repo.get_snapshots("TX-DFW")
    freshnesses = {
        classify_freshness(evidence.as_of, SEED_REFERENCE_NOW) for evidence in result.evidence
    }
    assert "stale" in freshnesses
    assert "fresh" in freshnesses
