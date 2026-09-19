"""Acceptance criterion 1: identical input -> byte-identical output, 1000x.

`now` is fixed and passed explicitly — see engine.py's docstring on why a
function that reads the wall clock internally can't claim to be pure.
"""

from __future__ import annotations

from datetime import UTC, datetime

from churnguard.policy import engine
from tests.unit.policy_fixtures import make_reference_request

FIXED_NOW = datetime(2026, 9, 19, tzinfo=UTC)
ITERATIONS = 1000


def test_reference_scenario_is_byte_identical_over_1000_iterations() -> None:
    request = make_reference_request()
    first = engine.evaluate(request, now=FIXED_NOW).model_dump_json()

    for _ in range(ITERATIONS - 1):
        again = engine.evaluate(request, now=FIXED_NOW).model_dump_json()
        assert again == first


def test_determinism_holds_for_a_fresh_request_object_each_time() -> None:
    """Rebuilding the request from scratch each time, not reusing one instance."""
    first = engine.evaluate(make_reference_request(), now=FIXED_NOW).model_dump_json()

    for _ in range(ITERATIONS - 1):
        again = engine.evaluate(make_reference_request(), now=FIXED_NOW).model_dump_json()
        assert again == first


def test_determinism_holds_across_tiers() -> None:
    for tier in (0, 1, 2, 3):
        request = make_reference_request(tier=tier)
        first = engine.evaluate(request, now=FIXED_NOW).model_dump_json()
        for _ in range(50):
            again = engine.evaluate(request, now=FIXED_NOW).model_dump_json()
            assert again == first
