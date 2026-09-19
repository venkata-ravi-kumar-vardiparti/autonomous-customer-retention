"""Example instances of every contract model, for round-trip and schema tests.

All identifiers are masked (ACCT_****4471 style) per the non-negotiable rule
in CLAUDE.md, even in test data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from churnguard.contracts.approval import (
    ApprovalDecision,
    ExecutionOperation,
    ExecutionRequest,
    OfferEdit,
)
from churnguard.contracts.competitor import (
    ClaimReconciliation,
    CompetitorComparison,
    CompetitorQuery,
    Normalization,
    ResolvedCompetitorOffer,
    SnapshotMeta,
    SwitchingCosts,
)
from churnguard.contracts.conversation import (
    ChurnSignal,
    CompetitorClaim,
    Concern,
    ConversationInput,
    ConversationSignals,
    Intent,
    StatedFigure,
    TranscriptTurn,
)
from churnguard.contracts.customer import (
    AccountContext,
    Billing,
    CustomerContextRequest,
    DeltaCause,
    DeviceFinancingLine,
    PaymentHistorySummary,
    PlanProfile,
    VerificationResult,
)
from churnguard.contracts.envelope import AgentEnvelope, AgentResult, EvidenceRef, Telemetry
from churnguard.contracts.offers import CandidateOffer, OfferComponent
from churnguard.contracts.policy import (
    ComputedLimits,
    Disclosure,
    PolicyEvaluationRequest,
    PolicyVerdictSet,
    Verdict,
)
from churnguard.contracts.recommendation import (
    ApprovalRequirements,
    BlockedCandidate,
    ConfidenceAdjustment,
    CustomerImpact,
    Recommendation,
    RecommendationSet,
    SupervisorInput,
    TraceContext,
)

NOW = datetime(2026, 9, 19, 14, 30, 0, tzinfo=UTC)
TODAY = date(2026, 9, 19)


def make_evidence_ref(suffix: str = "0001") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"EVID_****{suffix}",
        source_system="billing_system",
        record_ref=f"REC_****{suffix}",
        as_of=NOW,
        freshness="fresh",
        masked=True,
    )


def make_telemetry() -> Telemetry:
    return Telemetry(
        model="gpt-4.1-mini",
        latency_ms=180,
        tokens_in=512,
        tokens_out=128,
        cost_usd=0.0041,
        cache_hit=False,
    )


def make_transcript_turns() -> list[TranscriptTurn]:
    return [
        TranscriptTurn(ts=NOW, speaker="customer", text="I want to cancel my plan."),
        TranscriptTurn(
            ts=NOW, speaker="agent", text="I'm sorry to hear that, can you tell me why?"
        ),
    ]


def make_conversation_signals() -> ConversationSignals:
    return ConversationSignals(
        intents=[
            Intent(type="cancel_request", confidence=0.92, scope="account", span_refs=["t0"])
        ],
        churn_signals=[ChurnSignal(signal="price_sensitivity", strength="high")],
        competitor_claims=[
            CompetitorClaim(
                carrier="RivalCo",
                price=45.0,
                unit="per_line",
                source="customer_stated",
                span_refs=["t0"],
            )
        ],
        customer_stated_figures=[
            StatedFigure(field="current_bill", value=120.0, precision="approximate")
        ],
        unresolved_concerns=[
            Concern(
                issue="unexpected_fee",
                line_ref="LINE_****02",
                location="last_bill",
                since=TODAY,
                customer_flagged_separate=True,
            )
        ],
        sentiment_trajectory="declining",
        verification_tasks=["confirm_current_bill_amount"],
    )


def make_conversation_input() -> ConversationInput:
    return ConversationInput(
        call_id="CALL_****9001",
        transcript_window=make_transcript_turns(),
        window_start_ts=NOW,
        prior_signals=None,
        locale="en-US",
        guardrail_flags=[],
    )


def make_customer_context_request() -> CustomerContextRequest:
    return CustomerContextRequest(
        account_ref="ACCT_****4471",
        requested_domains=["billing", "device_financing"],
        lookback_months=3,
        line_refs_of_interest=["LINE_****02"],
        verification_tasks=["confirm_current_bill_amount"],
        reason_code="cancel_request",
    )


def make_account_context() -> AccountContext:
    return AccountContext(
        account_ref="ACCT_****4471",
        tenure_months=37,
        line_count=3,
        billing=Billing(
            current_bill=120.0,
            prior_bill=95.0,
            delta=25.0,
            delta_attribution=[
                DeltaCause(
                    cause="promo_expired",
                    amount=25.0,
                    event_date=TODAY,
                    reason="12-month promo ended",
                    reversible=True,
                    evidence_id="EVID_****0002",
                )
            ],
        ),
        payment_history=PaymentHistorySummary(
            on_time_count=34, late_count=1, last_late_date=None, current_past_due=0.0
        ),
        device_financing=[
            DeviceFinancingLine(
                line_ref="LINE_****02",
                device="Phone Model X",
                remaining_balance=210.0,
                monthly_payment=17.5,
                months_remaining=12,
                early_termination_fee=50.0,
            )
        ],
        plan_profile=PlanProfile(
            plan_code="PLAN_UNLIMITED_PLUS",
            plan_name="Unlimited Plus",
            contract_type="month_to_month",
            contract_end_date=None,
        ),
        usage_by_line={"LINE_****02": "high_data"},
        active_promotions=[],
        verification_results=[
            VerificationResult(
                task="confirm_current_bill_amount", status="confirmed", detail="matches $120.00"
            )
        ],
        excluded_fields=["ssn_last4"],
    )


def make_competitor_query() -> CompetitorQuery:
    return CompetitorQuery(
        geography="US-CA",
        carriers=["RivalCo"],
        line_count=3,
        current_plan_profile="PLAN_UNLIMITED_PLUS",
        customer_claim=CompetitorClaim(
            carrier="RivalCo",
            price=45.0,
            unit="per_line",
            source="customer_stated",
            span_refs=["t0"],
        ),
        switching_context="cancel_request",
        max_snapshot_age_days=14,
    )


def make_competitor_comparison() -> CompetitorComparison:
    return CompetitorComparison(
        resolved_offers=[
            ResolvedCompetitorOffer(
                carrier="RivalCo",
                plan_name="RivalCo Unlimited",
                monthly_price=135.0,
                line_count=3,
                includes=["unlimited_data"],
                normalized_monthly_equivalent=45.0,
                source_snapshot_id="SNAP_****01",
            )
        ],
        normalization=Normalization(
            method="per_line_equivalent", assumptions=["excludes_taxes_and_fees"]
        ),
        switching_costs=SwitchingCosts(
            device_payoff_total=210.0,
            early_termination_fees_total=50.0,
            activation_fees=30.0,
            other_costs=0.0,
            total=290.0,
        ),
        breakeven_months=7.5,
        claim_reconciliation=ClaimReconciliation(
            customer_claim=CompetitorClaim(
                carrier="RivalCo",
                price=45.0,
                unit="per_line",
                source="customer_stated",
                span_refs=["t0"],
            ),
            verdict="confirmed",
            resolved_price=45.0,
            explanation="Matches current published per-line price.",
        ),
        snapshot=SnapshotMeta(
            as_of=TODAY,
            age_days=2,
            geography="US-CA",
            capture_method="curated_manual",
            snapshot_id="SNAP_****01",
        ),
        freshness="fresh",
        confidence_penalty=0.0,
    )


def make_candidate_offer() -> CandidateOffer:
    return CandidateOffer(
        candidate_id="CAND_****01",
        type="bill_credit",
        components=[OfferComponent(code="LOYALTY_CREDIT_25", monthly=25.0, duration_months=6)],
        total_monthly_impact=-25.0,
    )


def make_policy_evaluation_request() -> PolicyEvaluationRequest:
    return PolicyEvaluationRequest(
        policy_pack_version="2026.09.1",
        jurisdiction="US-CA",
        channel="voice",
        agent_authority_tier=1,
        account_digest={"tenure_months": 37, "past_due": False},
        candidate_offers=[make_candidate_offer()],
    )


def make_policy_verdict_set() -> PolicyVerdictSet:
    return PolicyVerdictSet(
        policy_pack_version="2026.09.1",
        policy_pack_hash="sha256:abc123",
        evaluated_at=NOW,
        evaluation_mode="deterministic",
        computed_limits=ComputedLimits(
            max_discount_pct=0.2,
            max_monthly_credit=30.0,
            max_bundle_duration_months=12,
            agent_authority_ceiling=2,
            notes=["tier_1_default"],
        ),
        verdicts=[
            Verdict(
                candidate_id="CAND_****01",
                verdict="pass_with_disclosure",
                governing_rules=["RULE_LOYALTY_CREDIT_MAX"],
                approval_tier_required=1,
                required_disclosures=[
                    Disclosure(
                        code="DISC_CREDIT_EXPIRY",
                        text="This credit expires after 6 months.",
                        must_be_read_verbatim=True,
                    )
                ],
                constraint_violations=[],
            )
        ],
        prohibited_actions_triggered=[],
    )


def make_supervisor_input() -> SupervisorInput:
    return SupervisorInput(
        call_id="CALL_****9001",
        account_ref="ACCT_****4471",
        transcript_window=make_transcript_turns(),
        agent_authority_tier=1,
        agent_ref="AGT_****0192",
        deadline_ms=2000,
        policy_pack_version="2026.09.1",
        orchestration_mode="bounded_pipeline",
    )


def make_recommendation_set() -> RecommendationSet:
    return RecommendationSet(
        recommendation_set_id="RECSET_****01",
        call_id="CALL_****9001",
        generated_at=NOW,
        overall_confidence=0.82,
        confidence_adjustments=[
            ConfidenceAdjustment(reason="competitor_snapshot_fresh", delta=0.05)
        ],
        recommendations=[
            Recommendation(
                rank=1,
                offer_id="CAND_****01",
                title="6-month loyalty credit",
                components=[
                    OfferComponent(code="LOYALTY_CREDIT_25", monthly=25.0, duration_months=6)
                ],
                customer_impact=CustomerImpact(
                    monthly_delta=-25.0,
                    annualized_delta=-150.0,
                    description="Bill drops from $120 to $95/month for 6 months.",
                ),
                rationale="Offsets the expired promo that triggered the call.",
                confidence=0.85,
                approval_tier_required=1,
                required_disclosures=[
                    Disclosure(
                        code="DISC_CREDIT_EXPIRY",
                        text="This credit expires after 6 months.",
                        must_be_read_verbatim=True,
                    )
                ],
                evidence_ids=["EVID_****0002"],
                talk_track="I can apply a $25 monthly credit for the next 6 months.",
                hold_condition=None,
            )
        ],
        mandatory_actions=["read_disclosure:DISC_CREDIT_EXPIRY"],
        blocked_candidates=[
            BlockedCandidate(
                candidate_id="CAND_****02",
                reason="exceeds_tier_1_discount_ceiling",
                governing_rules=["RULE_MAX_DISCOUNT_PCT"],
            )
        ],
        agent_context_notes=["Customer previously escalated a billing dispute in month 4."],
        fallbacks_applied=[],
        commitment_status="none",
        approval=ApprovalRequirements(
            min_tier_required=1, requires_second_approver=False, disclosures_pending=[]
        ),
        trace=TraceContext(
            trace_id="TRACE_****01",
            span_id="SPAN_****01",
            agent_calls=["conversation", "customer_360", "competitor", "offer_policy"],
        ),
    )


def make_approval_decision() -> ApprovalDecision:
    return ApprovalDecision(
        recommendation_set_id="RECSET_****01",
        decision="approved",
        selected_offer_id="CAND_****01",
        approver_ref="AGT_****0192",
        approver_tier=1,
        approved_at=NOW,
        edits=[
            OfferEdit(
                component_code="LOYALTY_CREDIT_25",
                field="duration_months",
                old_value="6",
                new_value="3",
            )
        ],
        disclosures_read=["DISC_CREDIT_EXPIRY"],
        policy_pack_version="2026.09.1",
    )


def make_execution_request() -> ExecutionRequest:
    return ExecutionRequest(
        approval_ref="APPR_****01",
        account_ref="ACCT_****4471",
        operations=[
            ExecutionOperation(
                op_code="apply_bill_credit", params={"amount": 25.0, "duration_months": 3}
            )
        ],
        idempotency_key="IDEMP_****01",
    )


def make_agent_envelope() -> AgentEnvelope[ConversationInput]:
    return AgentEnvelope[ConversationInput](
        trace_id="TRACE_****01",
        span_id="SPAN_****01",
        parent_span_id=None,
        call_id="CALL_****9001",
        invoked_by="supervisor",
        policy_pack_version="2026.09.1",
        redaction_level="standard",
        deadline_ms=2000,
        attempt=1,
        payload=make_conversation_input(),
    )


def make_agent_result() -> AgentResult[ConversationSignals]:
    return AgentResult[ConversationSignals](
        status="ok",
        confidence=0.9,
        data=make_conversation_signals(),
        evidence=[make_evidence_ref()],
        missing_evidence=[],
        warnings=[],
        telemetry=make_telemetry(),
    )


ALL_EXAMPLES: dict[str, object] = {
    "AgentEnvelope[ConversationInput]": make_agent_envelope(),
    "AgentResult[ConversationSignals]": make_agent_result(),
    "ConversationInput": make_conversation_input(),
    "ConversationSignals": make_conversation_signals(),
    "CustomerContextRequest": make_customer_context_request(),
    "AccountContext": make_account_context(),
    "CompetitorQuery": make_competitor_query(),
    "CompetitorComparison": make_competitor_comparison(),
    "CandidateOffer": make_candidate_offer(),
    "PolicyEvaluationRequest": make_policy_evaluation_request(),
    "PolicyVerdictSet": make_policy_verdict_set(),
    "SupervisorInput": make_supervisor_input(),
    "RecommendationSet": make_recommendation_set(),
    "ApprovalDecision": make_approval_decision(),
    "ExecutionRequest": make_execution_request(),
}
