"""masking.py: correctness and idempotence (mask(mask(x)) == mask(x))."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from churnguard.data import masking

digit_strings = st.text(alphabet="0123456789", min_size=4, max_size=16)
name_strings = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ '-",
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")


def test_mask_account_number_keeps_last_four() -> None:
    assert masking.mask_account_number("0000004471") == "ACCT_****4471"


def test_mask_pan_keeps_last_four() -> None:
    assert masking.mask_pan("4111111111111234") == "CARD_****1234"


def test_mask_line_ref_is_index_based() -> None:
    assert masking.mask_line_ref(3) == "LINE_****03"
    assert masking.mask_line_ref(12) == "LINE_****12"


def test_mask_name_is_deterministic_and_never_contains_the_raw_name() -> None:
    masked = masking.mask_name("Jordan Nguyen")
    assert masked == masking.mask_name("Jordan Nguyen")
    assert "Jordan" not in masked
    assert "Nguyen" not in masked


@given(digit_strings)
def test_mask_account_number_is_idempotent(raw: str) -> None:
    once = masking.mask_account_number(raw)
    twice = masking.mask_account_number(once)
    assert once == twice


@given(digit_strings)
def test_mask_pan_is_idempotent(raw: str) -> None:
    once = masking.mask_pan(raw)
    twice = masking.mask_pan(once)
    assert once == twice


@given(name_strings)
def test_mask_name_is_idempotent(raw: str) -> None:
    once = masking.mask_name(raw)
    twice = masking.mask_name(once)
    assert once == twice


@given(st.integers(min_value=0, max_value=99))
def test_mask_line_ref_is_idempotent_over_index(index: int) -> None:
    once = masking.mask_line_ref(index)
    assert once == f"LINE_****{index:02d}"
