"""FastAPI surface: recommend -> approve -> execute.

This module is the one place allowed to import across every package
(orchestration/agents to produce a RecommendationSet, approval/ to gate and
persist a decision, execution/ to enforce and apply one) — everything
downstream of it (execution/service.py in particular) still enforces its
own guarantees independently; this module calling things in the right
order is a convenience for a well-behaved caller, not the actual security
boundary. See execution/service.py's docstring and
tests/architecture/test_no_imports.py for where the real boundary is.

An audit row is written for EVERY approval and execution decision,
including rejections and escalations — never only for a happy path.

RecommendationSet persistence: there is no dedicated "recommendation set
store" yet (out of scope for this phase — nothing in the Phase 8 brief asks
for one), so this module keeps a simple process-local in-memory dict keyed
by recommendation_set_id. A later phase that needs cross-process/durable
lookup should replace this, not build around it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from churnguard.approval import gate
from churnguard.approval import store as approval_store
from churnguard.contracts.approval import ApprovalDecision, ExecutionRequest
from churnguard.contracts.conversation import TranscriptTurn
from churnguard.contracts.recommendation import (
    OrchestrationMode,
    RecommendationSet,
    SupervisorInput,
)
from churnguard.execution import service as execution_service
from churnguard.orchestration.bounded import run_bounded_pipeline
from churnguard.telemetry.audit import write_audit_record

router = APIRouter()

_RECOMMENDATION_SETS: dict[str, RecommendationSet] = {}


class RecommendRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ref: str
    transcript_window: list[TranscriptTurn]
    agent_authority_tier: int
    agent_ref: str
    deadline_ms: int = 2000
    policy_pack_version: str
    orchestration_mode: OrchestrationMode = "bounded_pipeline"


@router.post("/calls/{call_id}/recommend", response_model=RecommendationSet)
async def recommend(call_id: str, body: RecommendRequestBody) -> RecommendationSet:
    supervisor_input = SupervisorInput(
        call_id=call_id,
        account_ref=body.account_ref,
        transcript_window=body.transcript_window,
        agent_authority_tier=body.agent_authority_tier,
        agent_ref=body.agent_ref,
        deadline_ms=body.deadline_ms,
        policy_pack_version=body.policy_pack_version,
        orchestration_mode=body.orchestration_mode,
    )
    if supervisor_input.orchestration_mode != "bounded_pipeline":
        raise HTTPException(
            status_code=400, detail="only orchestration_mode='bounded_pipeline' is served today"
        )

    recommendation_set = await run_bounded_pipeline(supervisor_input)
    _RECOMMENDATION_SETS[recommendation_set.recommendation_set_id] = recommendation_set
    return recommendation_set


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_ref: str


@router.post("/approvals", response_model=ApprovalResponse)
async def submit_approval(decision: ApprovalDecision) -> ApprovalResponse:
    recommendation_set = _RECOMMENDATION_SETS.get(decision.recommendation_set_id)
    if recommendation_set is None:
        await write_audit_record(
            trace_id=decision.recommendation_set_id,
            prompt="approval submitted for unknown recommendation_set_id",
            evidence_ids=[],
            policy_pack_version=decision.policy_pack_version,
            decision="rejected:unknown_recommendation_set",
            approver_ref=decision.approver_ref,
        )
        raise HTTPException(status_code=404, detail="unknown recommendation_set_id")

    gate_result = gate.evaluate_gate(decision, recommendation_set)
    if not gate_result.passed:
        await write_audit_record(
            trace_id=decision.recommendation_set_id,
            prompt="; ".join(gate_result.reasons),
            evidence_ids=[],
            policy_pack_version=decision.policy_pack_version,
            decision="rejected:gate",
            approver_ref=decision.approver_ref,
        )
        raise HTTPException(status_code=422, detail=gate_result.reasons)

    approval_ref = await approval_store.persist_approval_decision(decision)
    await write_audit_record(
        trace_id=decision.recommendation_set_id,
        prompt=f"approval decision recorded as {approval_ref}",
        evidence_ids=[],
        policy_pack_version=decision.policy_pack_version,
        decision=decision.decision,
        approver_ref=decision.approver_ref,
    )
    return ApprovalResponse(approval_ref=approval_ref)


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reasons: list[str]
    replayed: bool


@router.post("/execute", response_model=ExecutionResponse)
async def execute(request: ExecutionRequest) -> ExecutionResponse:
    approval = await approval_store.get_approval_decision(request.approval_ref)
    recommendation_set = (
        _RECOMMENDATION_SETS.get(approval.recommendation_set_id) if approval is not None else None
    )

    result = await execution_service.execute(
        request, approval=approval, recommendation=recommendation_set
    )

    await write_audit_record(
        trace_id=request.idempotency_key,
        prompt="; ".join(result.reasons) if result.reasons else "execution accepted",
        evidence_ids=[],
        policy_pack_version=(
            approval.policy_pack_version if approval is not None else "unknown"
        ),
        decision=result.status,
        approver_ref=approval.approver_ref if approval is not None else "UNKNOWN",
    )

    if result.status == "rejected":
        raise HTTPException(
            status_code=422,
            detail={
                "status": result.status,
                "reasons": result.reasons,
                "replayed": result.replayed,
            },
        )

    return ExecutionResponse(status=result.status, reasons=result.reasons, replayed=result.replayed)


__all__ = ["router"]
