"""Shared request-building helpers for BOTH orchestration arms.

Factored out of orchestration/bounded.py in Phase 11 for the same reason
as orchestration/assemble.py and orchestration/fallbacks.py: the
NON-NEGOTIABLE rule ("the ONLY difference between arms is
SupervisorInput.orchestration_mode... anything else invalidates the
comparison") has to be a structural guarantee, not a promise two
independently-written code paths happen to keep. Both
orchestration/bounded.py and orchestration/harness.py build their
CustomerContextRequest/CompetitorQuery/RunContext objects by calling these
exact functions.
"""

from __future__ import annotations

from churnguard.contracts.competitor import CompetitorQuery
from churnguard.contracts.conversation import ConversationSignals
from churnguard.contracts.customer import AccountContext, CustomerContextRequest
from churnguard.orchestration.context import AgentRequest, RunContext
from churnguard.tools.customer_tools import ALL_CUSTOMER_360_DOMAINS

DEFAULT_JURISDICTION = "US-TX"
DEFAULT_GEOGRAPHY = "TX-DFW"
DEFAULT_CHANNEL = "voice"
DEFAULT_CARRIERS = ["RivalCo", "MetroWave"]
DEFAULT_MAX_SNAPSHOT_AGE_DAYS = 90
DEFAULT_SWITCHING_CONTEXT = "live retention call - customer requested cancellation"
DEFAULT_LOOKBACK_MONTHS = 3
CUSTOMER_INPUT_TEXT = "Retrieve and structure this account's data for a live retention call."


def new_run_context(
    request: AgentRequest,
    *,
    trace_id: str,
    agent_span_id: str,
    policy_pack_version: str,
    deadline_ms: int,
    db_path: str,
) -> RunContext:
    return RunContext(
        request=request,
        trace_id=trace_id,
        agent_span_id=agent_span_id,
        policy_pack_version=policy_pack_version,
        deadline_ms=deadline_ms,
        db_path=db_path,
    )


def build_customer_request(
    account_ref: str,
    *,
    line_refs_of_interest: list[str],
    verification_tasks: list[str],
) -> CustomerContextRequest:
    return CustomerContextRequest(
        account_ref=account_ref,
        requested_domains=list(ALL_CUSTOMER_360_DOMAINS),
        lookback_months=DEFAULT_LOOKBACK_MONTHS,
        line_refs_of_interest=line_refs_of_interest,
        verification_tasks=verification_tasks,
        reason_code="cancel_request",
    )


def carriers_from_signals(signals: ConversationSignals) -> list[str]:
    claimed = list(dict.fromkeys(claim.carrier for claim in signals.competitor_claims))
    return claimed or list(DEFAULT_CARRIERS)


def build_competitor_query(
    signals: ConversationSignals,
    account: AccountContext,
    *,
    geography: str,
) -> CompetitorQuery:
    return CompetitorQuery(
        geography=geography,
        carriers=carriers_from_signals(signals),
        line_count=account.line_count,
        current_plan_profile=account.plan_profile.plan_code,
        current_monthly=max(account.billing.current_bill, 0.01),
        customer_claim=signals.competitor_claims[0] if signals.competitor_claims else None,
        switching_context=DEFAULT_SWITCHING_CONTEXT,
        max_snapshot_age_days=DEFAULT_MAX_SNAPSHOT_AGE_DAYS,
        known_device_financing_payoff=round(
            sum(line.remaining_balance for line in account.device_financing), 2
        ),
        known_one_time_switching_fees=round(
            sum(line.early_termination_fee for line in account.device_financing), 2
        ),
    )


__all__ = [
    "CUSTOMER_INPUT_TEXT",
    "DEFAULT_CARRIERS",
    "DEFAULT_CHANNEL",
    "DEFAULT_GEOGRAPHY",
    "DEFAULT_JURISDICTION",
    "DEFAULT_LOOKBACK_MONTHS",
    "DEFAULT_MAX_SNAPSHOT_AGE_DAYS",
    "DEFAULT_SWITCHING_CONTEXT",
    "build_competitor_query",
    "build_customer_request",
    "carriers_from_signals",
    "new_run_context",
]
