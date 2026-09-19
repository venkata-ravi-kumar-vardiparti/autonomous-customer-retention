"""Acceptance criterion 3: flipping each limit changes both the verdicts and the hash.

"Verdicts" is read as the full PolicyVerdictSet output — computed_limits is
part of that output too, so a limit that doesn't gate any pass/block
decision (max_bundle_duration_months) still has to change the output via
computed_limits, or this test would rightly fail.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from churnguard.policy import engine, loader
from tests.unit.policy_fixtures import make_reference_request

FIXED_NOW = datetime(2026, 9, 19, tzinfo=UTC)
PACK_PATH = loader.PACKS_DIR / "v2026.09.1.yaml"

MUTATED_LIMITS: dict[str, Any] = {
    "max_monthly_discount_tier1": 10.00,
    "max_monthly_discount_tier2": 15.00,
    "max_discount_pct": 0.10,
    "max_bundle_duration_months": 6,
}


def _load_base_pack_dict() -> dict[str, Any]:
    data = yaml.safe_load(Path(PACK_PATH).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("limit_key", sorted(MUTATED_LIMITS))
def test_flipping_a_limit_changes_hash_and_output(limit_key: str) -> None:
    base_data = _load_base_pack_dict()
    base_pack = loader.load_pack_from_dict(copy.deepcopy(base_data))

    mutated_data = copy.deepcopy(base_data)
    new_value = MUTATED_LIMITS[limit_key]
    assert mutated_data["limits"][limit_key] != new_value, "mutation must actually differ"
    mutated_data["limits"][limit_key] = new_value
    mutated_pack = loader.load_pack_from_dict(mutated_data)

    assert mutated_pack.pack_hash != base_pack.pack_hash

    request = make_reference_request()
    base_output = engine.evaluate_with_pack(request, base_pack, now=FIXED_NOW).model_dump_json()
    mutated_output = engine.evaluate_with_pack(
        request, mutated_pack, now=FIXED_NOW
    ).model_dump_json()
    assert base_output != mutated_output


def test_flipping_a_rule_description_changes_hash_even_though_it_never_changes_a_verdict() -> None:
    """The hash covers the whole pack, not just the limits that gate decisions."""
    base_data = _load_base_pack_dict()
    base_pack = loader.load_pack_from_dict(copy.deepcopy(base_data))

    mutated_data = copy.deepcopy(base_data)
    mutated_data["rules"]["RET-014"]["name"] = "Renamed for this test"
    mutated_pack = loader.load_pack_from_dict(mutated_data)

    assert mutated_pack.pack_hash != base_pack.pack_hash


def test_identical_pack_content_produces_identical_hash() -> None:
    data_a = _load_base_pack_dict()
    data_b = _load_base_pack_dict()
    pack_a = loader.load_pack_from_dict(data_a)
    pack_b = loader.load_pack_from_dict(data_b)
    assert pack_a.pack_hash == pack_b.pack_hash
