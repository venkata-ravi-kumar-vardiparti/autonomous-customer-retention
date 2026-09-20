"""The Competitor agent: curated snapshot -> normalized CompetitorComparison.

Never scrapes and never calls the network - COMPETITOR_TOOLS
(tools/competitor_tools.py) wraps only data/competitor_repo's curated,
timestamped snapshot rows. Every number in the final CompetitorComparison
(the like-for-like monthly figure, switching costs, breakeven, freshness
and its confidence penalty, claim reconciliation) is computed by
offers/normalizer.py, a pure function with no model involvement - the LLM
must never compute a price.

run_competitor_agent() is the one place this is wired together, following
the same "compute deterministically, overwrite the model's copy after the
run" pattern agents/conversation.py uses for verification_tasks: Runner.run
still produces a schema-valid CompetitorComparison (so resolved_offers,
narrative framing, etc. reflect what the model saw from its tool calls),
but every numeric sub-field this module can compute itself is replaced with
the exact value normalizer.py derives before the result is returned - never
left to model compliance.
"""

from __future__ import annotations

from agents import Model

from churnguard.agents.base import AgentSpec, run_agent
from churnguard.contracts.competitor import (
    CompetitorComparison,
    CompetitorQuery,
    Normalization,
)
from churnguard.contracts.envelope import AgentResult
from churnguard.data.repositories import competitor_repo
from churnguard.offers import normalizer
from churnguard.orchestration.context import RunContext
from churnguard.tools.competitor_tools import COMPETITOR_TOOLS

COMPETITOR_MODEL = "gpt-4.1-mini"
"""Small/cheap tier - the large model is reserved for the Supervisor (Phase 7)."""

COMPETITOR_INSTRUCTIONS = """
You are the Competitor agent inside ChurnGuard, a telecom retention
decision-support system. A human retention agent is on a live call; your
output feeds a system that will eventually recommend retention offers to
them, but that is NOT your job.

Call get_competitor_snapshots to see the curated, timestamped snapshot rows
available for this query's geography and carriers - never invent a
competitor plan or price that isn't in a returned snapshot. You may call
get_snapshot_meta on a snapshot_id to check its provenance.

Never compute, estimate, or adjust a price, discount, tax, or fee
yourself - report exactly what a tool returns. Populate resolved_offers
from the snapshot rows you were given, and write a short, factual
explanation of how the customer's claim (if any) compares to what you
found. Numeric fields you cannot derive purely from the tool output
(switching costs, a like-for-like adjusted monthly figure, breakeven,
freshness, confidence_penalty) will be corrected by the calling system
after you respond - fill them with your best available reading of the
tool output rather than leaving them blank.
"""


def detect_missing_resolved_offers(output: CompetitorComparison) -> list[str]:
    return [] if output.resolved_offers else ["resolved_offers"]


COMPETITOR_SPEC = AgentSpec(
    name="competitor",
    output_type=CompetitorComparison,
    instructions=COMPETITOR_INSTRUCTIONS,
    tools=COMPETITOR_TOOLS,
    model=COMPETITOR_MODEL,
    detect_missing_evidence=detect_missing_resolved_offers,
)


def _build_input_text(query: CompetitorQuery) -> str:
    claim_json = query.customer_claim.model_dump_json() if query.customer_claim else "null"
    return (
        f"geography: {query.geography}\n"
        f"carriers: {query.carriers}\n"
        f"line_count: {query.line_count}\n"
        f"current_plan_profile: {query.current_plan_profile}\n"
        f"current_monthly: {query.current_monthly}\n"
        f"customer_claim: {claim_json}\n"
        f"switching_context: {query.switching_context}\n"
        f"max_snapshot_age_days: {query.max_snapshot_age_days}\n"
    )


async def run_competitor_agent(
    query: CompetitorQuery,
    run_context: RunContext,
    *,
    model_override: str | Model | None = None,
) -> AgentResult[CompetitorComparison]:
    result = await run_agent(
        COMPETITOR_SPEC, _build_input_text(query), run_context, model_override=model_override
    )
    if result.data is None:
        return result

    snapshots = await competitor_repo.get_snapshots(
        query.geography, carriers=query.carriers, max_snapshot_age_days=query.max_snapshot_age_days
    )
    if not snapshots.data:
        return result

    primary = min(snapshots.data, key=lambda offer: offer.normalized_monthly_equivalent)
    meta = await competitor_repo.get_snapshot_meta(primary.source_snapshot_id)

    per_line_price = round(primary.monthly_price / primary.line_count, 2)
    normalized = normalizer.normalize_like_for_like_monthly(
        competitor_per_line_price=per_line_price,
        line_count=query.line_count,
        geography=query.geography,
        includes_hotspot="hotspot" in primary.includes,
    )
    resolved_offer = primary.model_copy(
        update={
            "monthly_price": normalized.like_for_like_monthly,
            "line_count": query.line_count,
            "normalized_monthly_equivalent": normalized.per_line_equivalent,
        }
    )

    switching_costs = normalizer.compute_switching_costs(
        device_financing_payoff=query.known_device_financing_payoff,
        activation_fee=query.known_one_time_switching_fees,
    )
    monthly_savings = round(query.current_monthly - normalized.like_for_like_monthly, 2)
    breakeven_months = normalizer.compute_breakeven_months(monthly_savings, switching_costs.total)

    claim_reconciliation = normalizer.reconcile_claim(
        query.customer_claim,
        options=[
            normalizer.RateOption(
                label=primary.plan_name, per_line_rate=per_line_price, line_count=primary.line_count
            )
        ],
        account_line_count=query.line_count,
    )

    freshness = normalizer.classify_competitor_freshness(meta.age_days)
    confidence_penalty = normalizer.confidence_penalty_for_freshness(freshness)

    updated_data = result.data.model_copy(
        update={
            "resolved_offers": [resolved_offer],
            "normalization": Normalization(
                method="like_for_like_monthly", assumptions=normalized.assumptions
            ),
            "switching_costs": switching_costs,
            "breakeven_months": breakeven_months,
            "claim_reconciliation": claim_reconciliation,
            "snapshot": meta,
            "freshness": freshness,
            "confidence_penalty": confidence_penalty,
        }
    )

    status = "stale" if freshness == "stale" else result.status
    confidence = max(0.0, round(result.confidence - confidence_penalty, 4))
    return result.model_copy(
        update={"data": updated_data, "status": status, "confidence": confidence}
    )


__all__ = [
    "COMPETITOR_INSTRUCTIONS",
    "COMPETITOR_MODEL",
    "COMPETITOR_SPEC",
    "detect_missing_resolved_offers",
    "run_competitor_agent",
]
