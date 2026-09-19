"""Classify how old a data-layer record is, relative to a reference time.

Boundaries: age_days <= FRESH_MAX_DAYS -> "fresh";
            age_days <= AGING_MAX_DAYS -> "aging";
            otherwise                  -> "stale".
"""

from __future__ import annotations

from datetime import datetime

from churnguard.contracts.envelope import Freshness

FRESH_MAX_DAYS = 29
AGING_MAX_DAYS = 40


def age_days(as_of: datetime, now: datetime) -> int:
    """Whole days between `as_of` and `now`, floored at 0."""
    return max((now - as_of).days, 0)


def classify_freshness(as_of: datetime, now: datetime) -> Freshness:
    days = age_days(as_of, now)
    if days <= FRESH_MAX_DAYS:
        return "fresh"
    if days <= AGING_MAX_DAYS:
        return "aging"
    return "stale"
