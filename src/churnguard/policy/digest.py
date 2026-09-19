"""Validate PolicyEvaluationRequest.account_digest before any rule sees it.

account_digest is `dict[str, Any]` in the frozen contract (consumed by
deterministic code, not an LLM structured output). This module is the one
place that turns that loose dict into a typed, validated AccountDigest —
every required key must be present and correctly typed, or evaluation
refuses to proceed. Nothing here silently defaults a missing or malformed
field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PolicyDigestError(ValueError):
    """Raised when account_digest is missing a required key or has the wrong shape."""


@dataclass(frozen=True)
class AccountDigest:
    current_monthly: float
    active_promo_codes: list[str]
    financing_active: bool
    payment_current: bool


def parse_account_digest(raw: dict[str, Any]) -> AccountDigest:
    if not isinstance(raw, dict):
        raise PolicyDigestError(f"account_digest must be a dict, got {type(raw).__name__}")

    required_keys = (
        "current_monthly",
        "active_promo_codes",
        "financing_active",
        "payment_current",
    )
    missing = [key for key in required_keys if key not in raw]
    if missing:
        raise PolicyDigestError(f"account_digest missing required keys: {sorted(missing)}")

    errors: list[str] = []

    current_monthly = raw["current_monthly"]
    if isinstance(current_monthly, bool) or not isinstance(current_monthly, int | float):
        errors.append(f"current_monthly must be a number, got {type(current_monthly).__name__}")
    elif current_monthly <= 0:
        errors.append(f"current_monthly must be > 0, got {current_monthly}")

    active_promo_codes = raw["active_promo_codes"]
    if not isinstance(active_promo_codes, list) or not all(
        isinstance(code, str) for code in active_promo_codes
    ):
        errors.append("active_promo_codes must be a list of strings")

    financing_active = raw["financing_active"]
    if not isinstance(financing_active, bool):
        errors.append(f"financing_active must be a bool, got {type(financing_active).__name__}")

    payment_current = raw["payment_current"]
    if not isinstance(payment_current, bool):
        errors.append(f"payment_current must be a bool, got {type(payment_current).__name__}")

    if errors:
        raise PolicyDigestError("; ".join(errors))

    return AccountDigest(
        current_monthly=float(current_monthly),
        active_promo_codes=list(active_promo_codes),
        financing_active=financing_active,
        payment_current=payment_current,
    )


__all__ = ["AccountDigest", "PolicyDigestError", "parse_account_digest"]
