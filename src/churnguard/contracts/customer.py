"""Payloads for the Customer 360 agent: account, billing, usage, financing.

ASSUMPTIONS (see CLAUDE.md "ASSUMPTIONS TO CONFIRM"): PlanProfile,
PaymentHistorySummary, DeviceFinancingLine and VerificationResult are not
spelled out field-by-field in the phase brief; their shapes below were
designed to cover what a retention decision needs and are flagged for
confirmation before Phase 1 consumes them.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class DeltaCause(BaseModel):
    """One attributed cause of a bill-over-bill change."""

    model_config = ConfigDict(extra="forbid")

    cause: str
    amount: float
    event_date: date | None
    reason: str | None
    reversible: bool
    evidence_id: str


class Billing(BaseModel):
    """Current vs. prior bill, with the delta attributed to specific causes."""

    model_config = ConfigDict(extra="forbid")

    current_bill: float = Field(ge=0.0)
    prior_bill: float = Field(ge=0.0)
    delta: float
    delta_attribution: list[DeltaCause]


class PaymentHistorySummary(BaseModel):
    """Rolling payment reliability picture used for eligibility checks."""

    model_config = ConfigDict(extra="forbid")

    on_time_count: int = Field(ge=0)
    late_count: int = Field(ge=0)
    last_late_date: date | None
    current_past_due: float = Field(ge=0.0)


class DeviceFinancingLine(BaseModel):
    """Outstanding device financing on one line, incl. early-termination cost."""

    model_config = ConfigDict(extra="forbid")

    line_ref: str
    device: str
    remaining_balance: float = Field(ge=0.0)
    monthly_payment: float = Field(ge=0.0)
    months_remaining: int = Field(ge=0)
    early_termination_fee: float = Field(ge=0.0)


class PlanProfile(BaseModel):
    """The customer's current plan and contract terms."""

    model_config = ConfigDict(extra="forbid")

    plan_code: str
    plan_name: str
    contract_type: str
    contract_end_date: date | None


class VerificationResult(BaseModel):
    """Outcome of a verification task requested against the account."""

    model_config = ConfigDict(extra="forbid")

    task: str
    status: str
    detail: str | None


class AccountContext(BaseModel):
    """Structured output of the Customer 360 agent for one account."""

    model_config = ConfigDict(extra="forbid")

    account_ref: str
    tenure_months: int = Field(ge=0)
    line_count: int = Field(ge=0)
    billing: Billing
    payment_history: PaymentHistorySummary
    device_financing: list[DeviceFinancingLine]
    plan_profile: PlanProfile
    usage_by_line: dict[str, str | None]
    active_promotions: list[str]
    verification_results: list[VerificationResult]
    excluded_fields: list[str]


class CustomerContextRequest(BaseModel):
    """Request payload for the Customer 360 agent."""

    model_config = ConfigDict(extra="forbid")

    account_ref: str
    requested_domains: list[str]
    lookback_months: int = Field(ge=0)
    line_refs_of_interest: list[str]
    verification_tasks: list[str]
    reason_code: str


__all__ = [
    "AccountContext",
    "Billing",
    "CustomerContextRequest",
    "DeltaCause",
    "DeviceFinancingLine",
    "PaymentHistorySummary",
    "PlanProfile",
    "VerificationResult",
]
