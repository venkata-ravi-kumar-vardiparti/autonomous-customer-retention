"""Cost arithmetic against a fixed, hand-computed price table."""

from __future__ import annotations

import pytest

from churnguard.telemetry.cost import (
    PRICE_TABLE_USD_PER_MILLION_TOKENS,
    UnknownModelPriceError,
    compute_cost_usd,
)


def test_price_table_has_the_expected_fixed_entries() -> None:
    assert PRICE_TABLE_USD_PER_MILLION_TOKENS["gpt-4.1"].input_per_million_usd == 2.00
    assert PRICE_TABLE_USD_PER_MILLION_TOKENS["gpt-4.1"].output_per_million_usd == 8.00


def test_zero_tokens_costs_nothing() -> None:
    assert compute_cost_usd("gpt-4.1-mini", 0, 0) == 0.0


def test_one_million_each_way_matches_the_table_rate_directly() -> None:
    assert compute_cost_usd("gpt-4.1", 1_000_000, 1_000_000) == pytest.approx(10.00)


def test_mixed_token_counts_hand_computed() -> None:
    # gpt-4o-mini: 0.15 / 1M in, 0.60 / 1M out
    # 500_000 in -> 0.075 ; 250_000 out -> 0.15 ; total 0.225
    assert compute_cost_usd("gpt-4o-mini", 500_000, 250_000) == pytest.approx(0.225)


def test_cost_rounds_to_six_decimal_places() -> None:
    # gpt-4.1-nano: 0.10 / 1M in -> 7 tokens = 0.0000007, rounds to 0.000001
    assert compute_cost_usd("gpt-4.1-nano", 7, 0) == pytest.approx(0.000001)


def test_unknown_model_raises_rather_than_defaulting_to_free() -> None:
    with pytest.raises(UnknownModelPriceError):
        compute_cost_usd("not-a-real-model", 100, 100)
