"""The Customer 360 agent: retrieve and structure account/billing/usage
data. Retrieve-and-structure only - it never interprets, recommends, or
computes a price; that's the Offer Policy engine's and the Supervisor's job
in later phases.
"""

from __future__ import annotations

from churnguard.agents.base import AgentSpec
from churnguard.contracts.customer import AccountContext
from churnguard.tools.customer_tools import ALL_CUSTOMER_360_DOMAINS, CUSTOMER_360_TOOLS

CUSTOMER_360_MODEL = "gpt-4.1-mini"
"""Small/cheap tier - the large model is reserved for the Supervisor (Phase 7)."""

CUSTOMER_360_INSTRUCTIONS = f"""
You are the Customer 360 agent inside ChurnGuard, a telecom retention
decision-support system. A human retention agent is on a live call; your
output feeds a system that will eventually recommend retention offers to
them, but that is NOT your job.

Your ONLY job is to retrieve data using your tools and structure it into
the required output shape:
- Never interpret what the data means for the customer's likelihood to
  churn, and never speculate about intent.
- Never recommend a retention offer, a next step, or any course of action.
- Never compute, estimate, or adjust a price, discount, or bill amount
  yourself - report exactly what a tool returns, unmodified.
- Never fabricate a value. If a tool refuses a domain or a value is
  genuinely absent from what a tool returned, reflect that honestly (for
  example, leave a line's usage entry null) rather than guessing.

Call get_account_summary, then call every other tool available to you, so
your structured output reflects everything you have access to. Domains:
{", ".join(ALL_CUSTOMER_360_DOMAINS)}.
"""


def detect_missing_usage(output: AccountContext) -> list[str]:
    """A null usage entry is a real, expected data gap, not an error -
    surface it as AgentResult.status="partial" rather than status="ok"
    silently hiding that the picture is incomplete.
    """
    return [
        f"usage_by_line.{line_ref}"
        for line_ref, usage_level in output.usage_by_line.items()
        if usage_level is None
    ]


CUSTOMER_360_SPEC = AgentSpec(
    name="customer_360",
    output_type=AccountContext,
    instructions=CUSTOMER_360_INSTRUCTIONS,
    tools=CUSTOMER_360_TOOLS,
    model=CUSTOMER_360_MODEL,
    detect_missing_evidence=detect_missing_usage,
)


__all__ = [
    "CUSTOMER_360_INSTRUCTIONS",
    "CUSTOMER_360_MODEL",
    "CUSTOMER_360_SPEC",
    "detect_missing_usage",
]
