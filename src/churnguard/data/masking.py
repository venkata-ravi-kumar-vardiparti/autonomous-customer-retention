"""Tokenise raw identifiers before they can leave the Governed Data Layer.

Every function here is idempotent: mask(mask(x)) == mask(x). Each function
first checks whether its input already looks like its own masked output and
returns it unchanged if so, rather than re-hashing/re-truncating it.

Masking happens at retrieval, inside the repositories — never at display —
so nothing downstream of data/ ever needs to remember to redact anything.
"""

from __future__ import annotations

import hashlib
import re

_ACCOUNT_MASKED_RE = re.compile(r"^ACCT_\*\*\*\*\d{4}$")
_LINE_MASKED_RE = re.compile(r"^LINE_\*\*\*\*\d{2}$")
_PAN_MASKED_RE = re.compile(r"^CARD_\*\*\*\*\d{4}$")
_NAME_MASKED_RE = re.compile(r"^CUST_[0-9a-f]{8}$")


def _last4_digits(raw: str, *, label: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 4:
        raise ValueError(f"{label} has too few digits to mask: {raw!r}")
    return digits[-4:]


def mask_account_number(raw: str) -> str:
    """ACCT_****4471 — full raw account number in, masked ref out."""
    if _ACCOUNT_MASKED_RE.match(raw):
        return raw
    return f"ACCT_****{_last4_digits(raw, label='account number')}"


def mask_line_ref(line_index: int) -> str:
    """LINE_****01 — derived purely from a 1-based position, never from a raw id."""
    return f"LINE_****{line_index:02d}"


def mask_pan(raw: str) -> str:
    """CARD_****1234 — full raw card PAN in, masked token out."""
    if _PAN_MASKED_RE.match(raw):
        return raw
    return f"CARD_****{_last4_digits(raw, label='card PAN')}"


def mask_name(raw: str) -> str:
    """CUST_<8 hex chars> — deterministic, non-reversible token for a full name."""
    if _NAME_MASKED_RE.match(raw):
        return raw
    digest = hashlib.sha256(raw.strip().casefold().encode("utf-8")).hexdigest()[:8]
    return f"CUST_{digest}"


def is_masked_account_number(value: str) -> bool:
    return bool(_ACCOUNT_MASKED_RE.match(value))


def extract_account_last4(account_ref: str) -> str:
    """Pull the last-4 digits out of a masked account_ref, for internal lookups."""
    match = _ACCOUNT_MASKED_RE.match(account_ref)
    if not match:
        raise ValueError(f"not a valid masked account_ref: {account_ref!r}")
    return account_ref[-4:]


__all__ = [
    "extract_account_last4",
    "is_masked_account_number",
    "mask_account_number",
    "mask_line_ref",
    "mask_name",
    "mask_pan",
]
